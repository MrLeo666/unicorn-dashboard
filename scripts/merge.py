#!/usr/bin/env python3
"""Merge data sources into the final unicorns.json that the dashboard fetches.

Inputs (all under deploy/data/):
  - wiki_raw.json           freshly scraped Wikipedia rows (rebuilt weekly)
  - seed_pitchbook.json     frozen PitchBook-only rows from the initial seed
  - chinese_names.json      manual mapping: { "ByteDance": {"zh": "字节跳动", "pinyin": "Z"}, ... }
  - overrides.json          admin-page exported edits/additions/deletions

Output:
  - unicorns.json           dashboard-ready records, sorted by valuation desc
"""
import json
import re
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / 'data'

# ---------- helpers ----------

def normalize(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r'\s*\([^)]*\)\s*$', '', s)
    s = re.sub(r'\s+(inc|llc|labs?|industries|corp|technology|technologies|company|co|group)$', '', s)
    s = re.sub(r'\s+ai$', '', s)
    s = s.replace('.com', '').replace('.io', '').replace('.ai', '')
    return re.sub(r'[^\w]', '', s)


def classify_industry(text: str) -> str:
    d = (text or '').lower()
    if re.search(r'\brobot|humanoid|drone|autonomous vehicle|unmanned|robotics\b', d): return 'Robotics'
    if re.search(r'\bcrypto|blockchain|web3|stablecoin|defi|wallet|tokeniz|cryptocurr|nft\b', d): return 'Crypto/Web3'
    if re.search(r'\bsatellite|space|aerospace|hypersonic|rocket|missile|defense|military|weapon|launch|orbital\b', d): return 'Aerospace/Defense'
    if re.search(r'\bsemiconductor|chip|processor|nand|silicon|fabric|wafer\b', d): return 'Semiconductors'
    if re.search(r'\bquantum|fusion power\b', d): return 'Climate/Energy' if 'fusion' in d else 'Other'
    if re.search(r'\bai\b|artificial intelligence|llm|machine learning|neural|generative|foundational model|inference|gpu|superintelligence', d): return 'AI/ML'
    if re.search(r'\bbank|payment|fintech|lending|credit|trading|brokerage|insurance|financ|wallet\b', d): return 'Fintech'
    if re.search(r'\bbiotech|drug|medic|pharma|therap|clinical|disease|patient|health|onco|cancer|genom|biopharm|wearable|neurotech\b', d): return 'Biotech/Health'
    if re.search(r'\benergy|battery|solar|nuclear|fusion|grid|electric vehicle|geothermal|emission|carbon|renewable\b', d): return 'Climate/Energy'
    if re.search(r'\bsecurity|cyber|threat|encrypt|firewall|vulnerab|malware|password\b', d): return 'Cybersecurity'
    if re.search(r'\bsaas|workflow|enterprise|productivity|collaboration|crm|automation|software|cloud computing|password manager|data infrastructure|data governance\b', d): return 'SaaS/Enterprise'
    if re.search(r'\bconsumer|retail|e-commerce|ecommerce|fashion|food|beverage|gaming|entertainment|travel|movie|brand|hospitality\b', d): return 'Consumer'
    if re.search(r'\btransportation|logistics|delivery|supply chain|marketplace\b', d): return 'Other'
    return 'Other'


def derive_year(date_str: str) -> str:
    m = re.search(r'(20\d{2})', date_str or '')
    return m.group(1) if m else ''


def slug_from_wiki_url(url: str) -> str:
    m = re.search(r'/wiki/([^?#]+)$', url)
    if m:
        return m.group(1)
    m = re.search(r'title=([^&]+)', url or '')
    return m.group(1) if m else ''


def tier_for(val_m: float) -> str:
    val_b = val_m / 1000
    if val_b < 5:   return 'B'    # tier 1, dot only
    return 'A'                    # rendered with full name (dashboard subdivides further at runtime)


def first_letter(d: dict) -> str:
    py = (d.get('pinyinInitial') or '').upper()
    if py and py.isalpha():
        return py
    co = (d.get('company') or '').lstrip()
    co = re.sub(r'^[^A-Za-z0-9]+', '', co)
    if co and co[0].isalpha():
        return co[0].upper()
    return '#'


# ---------- load ----------

def load(name, default):
    p = DATA / name
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding='utf-8'))


wiki = load('wiki_raw.json', [])
pb_seed = load('seed_pitchbook.json', [])
zh_map = load('chinese_names.json', {})
overrides = load('overrides.json', {'edits': {}, 'additions': [], 'deletions': []})

# ---------- merge wiki + frozen PitchBook seed ----------

merged = []
seen_keys = set()

# Pass 1: Wikipedia rows. Wiki always wins on overlap.
for w in wiki:
    co = w['company'].strip()
    key = normalize(co)
    seen_keys.add(key)
    val_m = float(w['valuation_b']) * 1000
    merged.append({
        'id': f'wiki-{len(merged)}',
        'source': 'wiki',
        'company': co,
        'wikiLink': w.get('wikiLink', ''),
        'wikiSlug': slug_from_wiki_url(w.get('wikiLink', '')),
        'description': w.get('industry_raw', ''),
        'valuation_str': w.get('valuation_str') or f"${w['valuation_b']:g}B",
        'valuation_m': val_m,
        'vc_raised': '—',
        'lead_investors': f"Founders: {w['founders']}" if w.get('founders') else '—',
        'country': (w.get('country') or '').split('/')[0].strip(),
        'unicorn_as_of': w.get('valuation_date', ''),
        'year': derive_year(w.get('valuation_date', '')),
        'industry': classify_industry(w.get('industry_raw', '')),
        'founders': w.get('founders', ''),
    })

# Pass 2: Frozen PitchBook-only rows, only if they're not already covered by Wikipedia.
for r in pb_seed:
    co = (r.get('company') or '').strip() or '__UNNAMED__'
    if co == '__UNNAMED__':
        # Keep as-is; uniqueness key is the id itself
        key = r.get('id') or f'__unnamed_{len(merged)}__'
    else:
        key = normalize(co)
    if key in seen_keys:
        continue
    seen_keys.add(key)
    rec = dict(r)
    rec['id'] = f'pb-{len(merged)}'
    rec['source'] = 'pb'
    merged.append(rec)

# ---------- apply Chinese names ----------

for d in merged:
    country = (d.get('country') or '').lower()
    is_cn = ('china' in country) or ('hong kong' in country)
    if not is_cn:
        # Strip any stale Chinese fields
        d.pop('companyZh', None)
        d.pop('pinyinInitial', None)
        continue
    co = d['company']
    if co in zh_map:
        d['companyZh'] = zh_map[co]['zh']
        d['pinyinInitial'] = zh_map[co].get('pinyin') or co[0].upper()
    elif co.startswith('Unnamed'):
        d['companyZh'] = co
        d['pinyinInitial'] = '?'
    else:
        d['companyZh'] = co
        d['pinyinInitial'] = co[0].upper() if co else '?'

# ---------- apply admin overrides ----------

# Deletions
del_set = {n.strip() for n in (overrides.get('deletions') or [])}
if del_set:
    merged = [d for d in merged if d['company'] not in del_set]

# Edits: keyed by company name, set fields directly
edits = overrides.get('edits') or {}
for d in merged:
    if d['company'] in edits:
        for k, v in edits[d['company']].items():
            d[k] = v
        # If edits change the valuation, recompute valuation_str
        if 'valuation_m' in edits[d['company']] and 'valuation_str' not in edits[d['company']]:
            v_b = d['valuation_m'] / 1000
            d['valuation_str'] = f"${v_b:g}B"

# Additions: brand-new records
for add in (overrides.get('additions') or []):
    if not add.get('company'):
        continue
    rec = {
        'id': f'admin-{len(merged)}',
        'source': 'admin',
        'company': add['company'],
        'description': add.get('description', ''),
        'valuation_str': add.get('valuation_str') or f"${add.get('valuation_m', 0)/1000:g}B",
        'valuation_m': float(add.get('valuation_m', 0) or 0),
        'vc_raised': add.get('vc_raised', '—'),
        'lead_investors': add.get('lead_investors', '—'),
        'country': add.get('country', ''),
        'unicorn_as_of': add.get('unicorn_as_of', ''),
        'year': add.get('year', ''),
        'industry': add.get('industry') or classify_industry(add.get('description', '')),
        'founders': add.get('founders', ''),
        'wikiLink': add.get('wikiLink', ''),
        'wikiSlug': slug_from_wiki_url(add.get('wikiLink', '')),
    }
    if add.get('companyZh'):
        rec['companyZh'] = add['companyZh']
    if add.get('pinyinInitial'):
        rec['pinyinInitial'] = add['pinyinInitial']
    merged.append(rec)

# ---------- final sort + write ----------

merged.sort(key=lambda d: -d['valuation_m'])

out = DATA / 'unicorns.json'
out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8')

print(f'Merged {len(merged)} records → {out.relative_to(DATA.parent)}', file=sys.stderr)
print(f'  source breakdown:', file=sys.stderr)
src = {}
for d in merged:
    src[d['source']] = src.get(d['source'], 0) + 1
for s, n in sorted(src.items()):
    print(f'    {s}: {n}', file=sys.stderr)
