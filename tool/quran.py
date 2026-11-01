"""Builds Kafila's Quran files (run by .github/workflows/quran.yml).

quran/ar.json           Arabic text (Tanzil, Uthmani), list of 114 surahs, each a list of ayahs
quran/<lang>.json       one translation per app language, same shape
quran/index.json        surah names and ayah counts, translations with author/source

Sources: Arabic text from Tanzil.net (verbatim, with attribution); translations
and surah info from github.com/fawazahmed0/quran-api.
"""
import json, os, sys, time, urllib.request

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else 'quran')
API = 'https://cdn.jsdelivr.net/gh/fawazahmed0/quran-api@1'
TANZIL = ('https://tanzil.net/pub/download/index.php?marks=true&sajdah=true&rub=false&tatweel=false'
          '&quranType=uthmani&outType=txt-2&agree=true')
LANGS = {  # app language -> (fawazahmed0 language name, preferred edition name parts)
    'en': ('English', ['sahih', 'pickthall', 'yusufali']),
    'de': ('German', ['bubenheim', 'aburida', 'rassoul']),
    'fr': ('French', ['hamidullah', 'hamidul']),
    'tr': ('Turkish', ['diyanet', 'yazir']),
    'id': ('Indonesian', ['indonesianislam', 'indonesian']),
    'ru': ('Russian', ['kuliev', 'porokhova']),
    'zh': ('Chinese', ['majian', 'jian']),
    'vi': ('Vietnamese', ['hassan', 'rowwad']),
}


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'kafila-data/1.0'})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read().decode('utf-8')
        except Exception as e:  # noqa
            print('retry', url, e)
            time.sleep(5 * (i + 1))
    raise RuntimeError(url)


def dump(name, obj):
    with open(os.path.join(OUT, name), 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))
    return round(os.path.getsize(os.path.join(OUT, name)) / 1024)


def shape(verses):
    out = [[] for _ in range(114)]
    for v in verses:
        out[v['chapter'] - 1].append(v['text'])
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    info = json.loads(get(f'{API}/info.json'))
    chapters = info['chapters']
    surahs = [{'n': c['chapter'], 'ar': c.get('arabicname', ''), 'tr': c.get('name', ''),
               'en': c.get('translation', c.get('englishname', '')), 'ayahs': len(c['verses']),
               'type': c.get('revelation', '')} for c in chapters]

    # Arabic: Tanzil first, fawazahmed0 as fallback.
    try:
        rows = [l.split('|', 2) for l in get(TANZIL).splitlines() if l and l[0].isdigit()]
        ar = [[] for _ in range(114)]
        for s, a, t in rows:
            ar[int(s) - 1].append(t)
        assert sum(len(x) for x in ar) == 6236
        ar_src = 'Tanzil Quran Text (Uthmani), tanzil.net — verbatim copy, CC BY-ND 3.0'
    except Exception as e:  # noqa
        print('tanzil failed, fallback', e)
        ar = shape(json.loads(get(f'{API}/editions/ara-quranuthmanihaf.json'))['quran'])
        ar_src = 'Quran text via fawazahmed0/quran-api (ara-quranuthmanihaf)'
    kb = dump('ar.json', ar)
    translations = {}
    editions = json.loads(get(f'{API}/editions.json'))
    for lang, (lname, prefs) in LANGS.items():
        cands = [e for e in editions.values() if e['language'] == lname
                 and not e['name'].endswith(('-la', '-lad')) and e.get('direction', 'ltr') == 'ltr']
        if not cands:
            print(lang, 'no edition'); continue
        cands.sort(key=lambda e: next((i for i, p in enumerate(prefs) if p in e['name'].lower()), 99))
        ed = cands[0]
        try:
            verses = json.loads(get(f"{API}/editions/{ed['name']}.json"))['quran']
            data = shape(verses)
            assert sum(len(x) for x in data) == 6236
        except Exception as e:  # noqa
            print(lang, 'failed', e); continue
        translations[lang] = {'author': ed.get('author', ''), 'edition': ed['name'], 'kb': dump(f'{lang}.json', data),
                              'source': 'github.com/fawazahmed0/quran-api'}
        print(lang, ed['name'], ed.get('author'))
    dump('index.json', {'v': 1, 'updated': time.strftime('%Y-%m-%d'), 'surahs': surahs,
                        'arabic': {'kb': kb, 'source': ar_src}, 'translations': translations})
    print('done', len(translations), 'translations')


if __name__ == '__main__':
    main()
