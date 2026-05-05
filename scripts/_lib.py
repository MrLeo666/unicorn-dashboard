"""Shared helpers for merge.py and compute_pending.py.

Splits the data pipeline into small, composable steps:
  1. build_canonical(wiki, pb_seed, zh_map) → list of records
     (the "what the dashboard would naturally render from raw sources")
  2. apply_overrides(records, overrides) → list of records
     (admin's manual edits/additions/deletions applied on top)
  3. diff_records(current, proposed) → {additions, modifications, removals}
     (used by compute_pending)
"""
import json
import re
from pathlib import Path


# ----- Normalization & classification -----

def normalize(name: str) -> str:
    s = (name or '').lower().strip()
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
    if m: return m.group(1)
    m = re.search(r'title=([^&]+)', url or '')
    return m.group(1) if m else ''


# ----- Build the canonical merge -----

def build_canonical(wiki, pb_seed, zh_map):
    """Merge wiki + frozen PB seed + Chinese-name mapping into the dashboard record list.
    Wikipedia rows always win on overlap (matched by normalized name).
    Does NOT apply admin overrides — that's a separate step.
    """
    merged = []
    seen = set()

    for w in wiki:
        co = w['company'].strip()
        key = normalize(co)
        seen.add(key)
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

    for r in pb_seed:
        co = (r.get('company') or '').strip() or '__UNNAMED__'
        if co == '__UNNAMED__':
            key = r.get('id') or f'__unnamed_{len(merged)}__'
        else:
            key = normalize(co)
        if key in seen:
            continue
        seen.add(key)
        rec = dict(r)
        rec['id'] = f'pb-{len(merged)}'
        rec['source'] = 'pb'
        merged.append(rec)

    # Apply Chinese names
    for d in merged:
        country = (d.get('country') or '').lower()
        is_cn = ('china' in country) or ('hong kong' in country)
        if not is_cn:
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

    merged.sort(key=lambda d: -d['valuation_m'])
    return merged


# ----- Apply admin overrides -----

def apply_overrides(records, overrides):
    """Return a new list with overrides.deletions removed, .edits applied,
       and .additions appended."""
    deletions = set((overrides or {}).get('deletions') or [])
    edits = (overrides or {}).get('edits') or {}
    additions = (overrides or {}).get('additions') or []
    out = []
    for r in records:
        if r['company'] in deletions:
            continue
        if r['company'] in edits:
            r = {**r, **edits[r['company']]}
            if 'valuation_m' in edits[r['company']] and 'valuation_str' not in edits[r['company']]:
                r['valuation_str'] = f"${r['valuation_m']/1000:g}B"
        out.append(r)

    # Append additions as fresh "admin" records
    for add in additions:
        if not add.get('company'):
            continue
        rec = {
            'id': f'admin-{len(out)}',
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
        out.append(rec)

    out.sort(key=lambda d: -d['valuation_m'])
    return out


# ----- Diff records -----

# Fields whose changes count as "interesting" pending updates.
DIFF_FIELDS = ['valuation_m', 'country', 'founders', 'wikiLink']


def _by_company(records):
    """Map company name → record. Skip placeholders/duplicates."""
    out = {}
    for r in records:
        co = r.get('company')
        if not co or co.startswith('Unnamed'):
            continue
        out.setdefault(co, r)
    return out


def diff_records(current, proposed):
    """Compute additions / modifications / removals.

    current  = what the dashboard currently shows (after overrides applied)
    proposed = what a fresh canonical merge would produce (no overrides)
    """
    cur_map = _by_company(current)
    prop_map = _by_company(proposed)

    additions = []
    for co, p in prop_map.items():
        if co not in cur_map:
            additions.append(p)

    removals = []
    for co, c in cur_map.items():
        if co not in prop_map and c.get('source') != 'admin' and c.get('source') != 'pb':
            # Don't propose removing PB-only seed rows (they're never re-scraped) or admin additions
            removals.append(c)

    modifications = []
    for co in cur_map.keys() & prop_map.keys():
        c, p = cur_map[co], prop_map[co]
        changes = {}
        for f in DIFF_FIELDS:
            cv, pv = c.get(f), p.get(f)
            # Numeric tolerance for valuation
            if f == 'valuation_m':
                cn = float(cv or 0); pn = float(pv or 0)
                if abs(cn - pn) > 0.5:    # > $0.5M difference
                    changes[f] = {'from': cn, 'to': pn}
            elif (cv or '') != (pv or ''):
                changes[f] = {'from': cv, 'to': pv}
        if changes:
            modifications.append({
                'company': co,
                'changes': changes,
                'current': c,
                'proposed': p,
            })

    additions.sort(key=lambda d: -d.get('valuation_m', 0))
    removals.sort(key=lambda d: -d.get('valuation_m', 0))
    modifications.sort(key=lambda d: -(d['proposed'].get('valuation_m', 0) or 0))

    return {'additions': additions, 'modifications': modifications, 'removals': removals}


# ----- File I/O helpers -----

def load(path: Path, default):
    if not path.exists(): return default
    return json.loads(path.read_text(encoding='utf-8'))


def save(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / 'data'
