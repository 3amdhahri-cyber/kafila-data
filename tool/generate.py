"""Builds Kafila's offline mosque database from OpenStreetMap.

1. For each country, asks Overpass for all Muslim places of worship
   (with retries over several public servers; a CI job can wait).
2. Saves a compact per-country file OUT/CC.json ([lat, lon, name, address]
   rows) and records progress in done.json.
3. Writes OUT/index.json (name, mosque count, size per country). The app
   lists it and downloads only the countries the user picks.

Run: python3 generate.py [--max-minutes 300] [--out DIR]
Data © OpenStreetMap contributors, ODbL.
"""
import json, math, os, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(ROOT, '..', '..'))
CDIR = os.path.join(ROOT, 'countries')
NAMES = json.load(open(os.path.join(ROOT, 'names.json'), encoding='utf-8'))
DONE = os.path.join(ROOT, 'done.json')
SERVERS = [
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
    'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
    'https://overpass.private.coffee/api/interpreter',
]
UA = 'Kafila-dataset/1.0 (monthly offline extract; prayer times app)'
REFRESH_DAYS = 30

# Priority first (Kafila's first users), then the rest of the world.
PRIORITY = ['DE','BE','NL','LU','FR','AT','CH','DK','GB','IT','ES','SE','NO','PL','CZ','TR','TN','DZ','MA','EG',
            'SA','AE','QA','KW','BH','OM','JO','LB','SY','IQ','PS','YE','LY','SD','ID','MY','SG','BN','PK','BD','IN',
            'BA','AL','XK','MK','BG','RO','HU','HR','SI','RS','ME','GR','CY','IE','PT','FI','EE','LV','LT','SK','UA','RU']
REST = ('AF AO AR AM AU AZ BB BF BI BJ BO BR BS BT BW BY BZ CA CD CF CG CI CL CM CN CO CR CU CV DJ DM DO EC ER ET FJ GA '
        'GE GH GM GN GQ GT GW GY HN HT IR IS JM JP KE KG KH KM KR KZ LA LK LR LS MD MG ML MM MN MR MU MV MW MX MZ NA NE '
        'NG NI NP NZ PA PE PG PH PY RW SB SC SL SN SO SR SS SV SZ TD TG TH TJ TL TM TT TW TZ UG US UY UZ VE VN VU ZA ZM ZW '
        'MT AD MC SM LI IS GI').split()
COUNTRIES = PRIORITY + [c for c in REST if c not in PRIORITY]


def overpass(cc):
    q = (f'[out:json][timeout:600];area["ISO3166-1"="{cc}"][admin_level=2]->.a;'
         'nwr["amenity"="place_of_worship"]["religion"="muslim"](area.a);out center tags;')
    return _run(q)


def _run(q):
    body = urllib.parse.urlencode({'data': q}).encode()
    last = None
    for attempt in range(4):
        for s in SERVERS:
            try:
                req = urllib.request.Request(s, data=body, headers={'User-Agent': UA})
                with urllib.request.urlopen(req, timeout=660) as r:
                    text = r.read().decode('utf-8')
                if text.lstrip().startswith('{'):
                    d = json.loads(text)
                    if 'remark' in d and 'runtime error' in d.get('remark', ''):
                        raise RuntimeError(d['remark'][:120])
                    return d['elements']
                last = f'{s} non-json'
            except Exception as e:  # noqa
                last = f'{s} {type(e).__name__} {str(e)[:80]}'
            time.sleep(5)
        time.sleep(30 * (attempt + 1))
    raise RuntimeError(last)


def overpass_stops(cc):
    q = (f'[out:json][timeout:600];area["ISO3166-1"="{cc}"][admin_level=2]->.a;'
         'nwr["highway"~"^(services|rest_area)$"](area.a);out center tags;')
    return _run(q)


def compact_stops(elements):
    rows, seen = [], set()
    for e in elements:
        lat = e.get('lat', (e.get('center') or {}).get('lat'))
        lon = e.get('lon', (e.get('center') or {}).get('lon'))
        if lat is None or lon is None:
            continue
        key = (round(lat * 200), round(lon * 200))  # ~500 m
        if key in seen:
            continue
        seen.add(key)
        t = e.get('tags', {})
        pr = 1 if (t.get('prayer_room') in ('yes', 'muslim') or t.get('amenity') == 'prayer_room'
                   or 'prayer' in (t.get('description', '') + t.get('note', '')).lower()) else 0
        rows.append([round(lat, 5), round(lon, 5), t.get('name') or '', pr])
    return rows


def compact(elements):
    rows = []
    for e in elements:
        lat = e.get('lat', (e.get('center') or {}).get('lat'))
        lon = e.get('lon', (e.get('center') or {}).get('lon'))
        if lat is None or lon is None:
            continue
        t = e.get('tags', {})
        name = t.get('name') or t.get('name:en') or t.get('name:ar') or ''
        street = ' '.join(x for x in [t.get('addr:street'), t.get('addr:housenumber')] if x)
        addr = ', '.join(x for x in [street, t.get('addr:city')] if x)
        rows.append([round(lat, 5), round(lon, 5), name, addr])
    # Same mosque mapped as node + building: keep one (the named one) per ~80 m.
    rows.sort(key=lambda r: (r[2] == '',))
    out, seen = [], {}
    for r in rows:
        key = (round(r[0] * 140), round(r[1] * 90))  # ~800 m buckets for the lookup
        dup = False
        for k2 in [(key[0] + i, key[1] + j) for i in (-1, 0, 1) for j in (-1, 0, 1)]:
            for o in seen.get(k2, []):
                if abs(o[0] - r[0]) < 0.0008 and abs(o[1] - r[1]) < 0.0012:
                    dup = True
                    break
            if dup:
                break
        if not dup:
            seen.setdefault(key, []).append(r)
            out.append(r)
    return out


def overpass_places(cc):
    q = (f'[out:json][timeout:600];area["ISO3166-1"="{cc}"][admin_level=2]->.a;'
         'node["place"~"^(city|town|village|suburb|quarter)$"]["name"](area.a);out qt;')
    return _run(q)


PLACE_TYPES = {'city': 0, 'town': 1, 'village': 2, 'suburb': 3, 'quarter': 3}


def compact_places(elements):
    """Rows [lat, lon, name, type, other names joined by |] for offline place search."""
    rows = []
    for e in elements:
        t = e.get('tags', {})
        name = t.get('name')
        if not name or 'lat' not in e:
            continue
        typ = PLACE_TYPES.get(t.get('place'), 2)
        alts = []
        if typ <= 1:
            for k in ('name:en', 'name:de', 'name:fr', 'name:ar', 'name:tr', 'int_name'):
                v = t.get(k)
                if v and v != name and v not in alts:
                    alts.append(v)
        rows.append([round(e['lat'], 4), round(e['lon'], 4), name, typ, '|'.join(alts)])
    rows.sort(key=lambda r: (r[3], r[2]))
    return rows


def build_index():
    countries = {}
    for f in sorted(os.listdir(CDIR)):
        if not (len(f) == 7 and f.endswith('.json')):
            continue
        cc = f[:2]
        rows = json.load(open(os.path.join(CDIR, f), encoding='utf-8'))
        if not rows:
            continue
        countries[cc] = {'name': NAMES.get(cc, cc), 'n': len(rows),
                         'kb': max(1, round(os.path.getsize(os.path.join(CDIR, f)) / 1024))}
        pf = os.path.join(CDIR, 'places', f)
        if os.path.exists(pf):
            countries[cc]['places_kb'] = max(1, round(os.path.getsize(pf) / 1024))
    with open(os.path.join(CDIR, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump({'v': 1, 'updated': time.strftime('%Y-%m-%d'), 'countries': countries}, fh,
                  ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    print(f'index: {len(countries)} countries, {sum(c["n"] for c in countries.values())} mosques')


def main():
    global CDIR
    if '--out' in sys.argv:
        CDIR = os.path.abspath(sys.argv[sys.argv.index('--out') + 1])
    max_min = 300
    if '--max-minutes' in sys.argv:
        max_min = int(sys.argv[sys.argv.index('--max-minutes') + 1])
    start = time.time()
    os.makedirs(CDIR, exist_ok=True)
    done = json.load(open(DONE)) if os.path.exists(DONE) else {}
    now = int(time.time())
    for cc in COUNTRIES:
        if now - done.get(cc, 0) < REFRESH_DAYS * 86400:
            continue
        if time.time() - start > max_min * 60:
            print('time budget used, stopping; next run continues')
            break
        t0 = time.time()
        try:
            rows = compact(overpass(cc))
        except Exception as e:  # noqa
            print(f'{cc}: FAILED {e}')
            continue
        json.dump(rows, open(os.path.join(CDIR, cc + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, separators=(',', ':'))
        # A (near) empty result is often a failed area query: retry in 2 days.
        tiny = len(rows) < 3 and cc not in ('AD', 'MC', 'SM', 'LI', 'GI', 'VU', 'SB', 'BT', 'TL', 'DM', 'BB', 'BZ', 'IS')
        done[cc] = int(time.time()) - ((REFRESH_DAYS - 2) * 86400 if tiny else 0)
        json.dump(done, open(DONE, 'w'), indent=0, sort_keys=True)
        print(f'{cc}: {len(rows)} mosques in {time.time() - t0:.0f}s')
    # Motorway service and rest areas (route mode), same refresh rule.
    sdir = os.path.join(CDIR, 'stops')
    os.makedirs(sdir, exist_ok=True)
    sdone_path = os.path.join(ROOT, 'done_stops.json')
    sdone = json.load(open(sdone_path)) if os.path.exists(sdone_path) else {}
    for cc in COUNTRIES:
        if cc not in done or now - sdone.get(cc, 0) < REFRESH_DAYS * 86400:
            continue
        if time.time() - start > max_min * 60:
            break
        try:
            rows = compact_stops(overpass_stops(cc))
        except Exception as e:  # noqa
            print(f'{cc} stops: FAILED {e}')
            continue
        json.dump(rows, open(os.path.join(sdir, cc + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, separators=(',', ':'))
        sdone[cc] = int(time.time())
        json.dump(sdone, open(sdone_path, 'w'), indent=0, sort_keys=True)
        print(f'{cc}: {len(rows)} rest areas')
    # Towns, villages and districts (offline place suggestions).
    pdir = os.path.join(CDIR, 'places')
    os.makedirs(pdir, exist_ok=True)
    pdone_path = os.path.join(ROOT, 'done_places.json')
    pdone = json.load(open(pdone_path)) if os.path.exists(pdone_path) else {}
    for cc in COUNTRIES:
        if cc not in done or now - pdone.get(cc, 0) < REFRESH_DAYS * 86400:
            continue
        if time.time() - start > max_min * 60:
            break
        try:
            rows = compact_places(overpass_places(cc))
        except Exception as e:  # noqa
            print(f'{cc} places: FAILED {e}')
            continue
        json.dump(rows, open(os.path.join(pdir, cc + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, separators=(',', ':'))
        pdone[cc] = int(time.time())
        json.dump(pdone, open(pdone_path, 'w'), indent=0, sort_keys=True)
        print(f'{cc}: {len(rows)} places')
    build_index()
    missing = [c for c in COUNTRIES if c not in done]
    print(f'countries done: {len(done)}/{len(COUNTRIES)}; missing: {" ".join(missing)}')


if __name__ == '__main__':
    main()
