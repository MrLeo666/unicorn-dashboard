#!/usr/bin/env python3
"""Discover candidate unicorns and valuation jumps from Google News RSS.

Part A — new-unicorn discovery: run a handful of generic funding/valuation
queries (EN + ZH), extract (company, valuation) pairs from headlines with
conservative regexes, drop anything already in the effective base
(unicorns.json + overrides.json) or too ambiguous.

Part B — valuation-jump detection: scan the per-company headlines already
collected in data/news.json for "…at $V billion valuation" style phrases;
if the detected valuation differs from the company's current effective
valuation by more than 20%, flag it.

Heuristic by design — false positives are expected and everything lands in a
human review queue. Output (rewritten each run):

    data/candidates.json
    {
      "generated_at": "<ISO-8601>",
      "new_candidates":  [{"company", "valuation_m", "headline", "url", "source", "date"}],
      "valuation_jumps": [{"company", "current_m", "detected_m", "headline", "url", "source", "date"}]
    }
"""
import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from _lib import apply_overrides, load, normalize, data_dir

DATA = data_dir()
OUT = DATA / 'candidates.json'

USER_AGENT = 'unicorn-dashboard-bot/1.0 (candidate discovery from public Google News RSS)'
RSS_URL = 'https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en'
REQUEST_TIMEOUT = 15
SLEEP_S = 0.3
ITEMS_PER_QUERY = 20
JUMP_THRESHOLD = 0.20   # |detected - current| / current

# Generic discovery queries. Each is run against Google News RSS; the first
# ITEMS_PER_QUERY items of every feed are scanned.
DISCOVERY_QUERIES = [
    '"raises" "$" "Series" "unicorn" when:30d',
    '"valued at" "$" "billion" startup when:30d',
    '"new unicorn" when:30d',
    '融资 独角兽 估值 when:30d',
    '完成 轮融资 估值 when:30d',
]

# (regex, valuation-multiplier-to-$M). Applied to headlines; group 1 must be
# the company name, group 2 the numeric valuation. Conservative on purpose:
# headlines that don't yield a clean pair are dropped.
EN_VALUE = r'\$([\d.]+)\s*(?:billion|B)\b'
PATTERNS = [
    # "X raises $80M Series C at a $2 billion valuation"
    (re.compile(r'^(.+?)\s+raises?\s+\$[\d.]+\s*[MB]?\b.*?at\s+(?:a\s+)?' + EN_VALUE, re.I), 1000),
    # "X valued at $3 billion"
    (re.compile(r'^(.+?)\s+(?:is\s+|now\s+)?valued\s+at\s+' + EN_VALUE, re.I), 1000),
    # "X closes Series C at $2.5B" / "X hits $5 billion valuation"
    (re.compile(r'^(.+?)\s+(?:closes|lands|secures|nabs|snags)\s+.*?at\s+(?:a\s+)?' + EN_VALUE, re.I), 1000),
    (re.compile(r'^(.+?)\s+(?:hits|reaches|tops|crosses)\s+' + EN_VALUE + r'\s+valuation', re.I), 1000),
    # "X seeks/eyes $10 billion valuation"
    (re.compile(r'^(.+?)\s+(?:seeks|eyes|targets|in\s+talks\s+at)\s+(?:a\s+)?' + EN_VALUE + r'\s+valuation', re.I), 1000),
    # 中文："X 完成 N 亿美元融资，估值达 V 亿美元" / "X 估值达 V 亿美元"
    (re.compile(r'^(.+?)完成.*?轮融资[，,、]?\s*估值(?:达|达到|超|超过|至|为)?\s*([\d.]+)\s*亿美元'), 100),
    (re.compile(r'^(.+?)估值(?:达|达到|超|超过|升至|达到约)\s*([\d.]+)\s*亿美元'), 100),
]

# Bare/common words that regexes love to capture but are never company names.
STOPWORDS = {
    'startup', 'startups', 'company', 'companies', 'firm', 'firms', 'ai', 'crypto',
    'tech', 'fintech', 'unicorn', 'unicorns', 'report', 'analysis', 'news', 'the',
    'this', 'that', 'how', 'why', 'what', 'exclusive', 'breaking', 'update', 'weekly',
    'daily', 'market', 'markets', 'stock', 'stocks', 'fund', 'funds', 'vc', 'vcs',
    'google', 'apple', 'microsoft', 'amazon', 'meta', 'openai', 'spacex', 'by',
    '公司', '企业', '融资', '估值', '独角兽', '报道', '独家', '快讯', '获悉', '消息',
}
MIN_NAME_LEN = 3
MAX_NAME_LEN = 60
STOPWORDS_NORM = {normalize(w) for w in STOPWORDS}

# Descriptor prefixes the regexes capture along with the real company name:
# "Defense Startup Hadrian" → "Hadrian", "Digital Bank Revolut" → "Revolut".
EN_DESCRIPTOR_PREFIX = re.compile(
    r"^(?:[\w&.'-]+\s+)*?(?:startup|company|firm|platform|app|bank|maker|developer|provider|unicorn)s?\s+",
    re.I,
)
# 中文："瑞典 AI 编程初创公司 Lovable" → "Lovable"（仅当有可辨识的 ASCII 尾巴时）
ZH_DESCRIPTOR_PREFIX = re.compile(
    r"^[一-鿿\sa-zA-Z]*?(?:初创公司|初创企业|独角兽企业|公司|平台)\s*([A-Za-z][\w.&' -]*)$"
)


def clean_name(name: str) -> str:
    """Strip headline descriptor prefixes so dedup/base-matching see the real name."""
    zh = ZH_DESCRIPTOR_PREFIX.match(name)
    if zh:
        name = zh.group(1)
    # Drop appositive tails: "Revolut, Europe's Most Valuable Startup" → "Revolut"
    name = name.split(',')[0].strip()
    prev = None
    while prev != name:
        prev = name
        name = EN_DESCRIPTOR_PREFIX.sub('', name).strip()
    return name


def clean_title(title: str, source: str) -> str:
    """Google News titles end with ' - <source name>'; strip that suffix."""
    if source and title.endswith(' - ' + source):
        return title[: -(len(source) + 3)].strip()
    if ' - ' in title:
        head, _, _ = title.rpartition(' - ')
        if head.strip():
            return head.strip()
    return title.strip()


def parse_date(pub_date: str) -> str:
    try:
        return parsedate_to_datetime(pub_date).date().isoformat()
    except (TypeError, ValueError):
        return ''


def fetch_items(query: str):
    """Return [{title, url, source, date}] for one RSS query."""
    url = RSS_URL.format(q=urllib.parse.quote(query))
    r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for item in root.findall('.//channel/item')[:ITEMS_PER_QUERY]:
        title_el, link_el = item.find('title'), item.find('link')
        source_el, pub_el = item.find('source'), item.find('pubDate')
        source = (source_el.text or '').strip() if source_el is not None else ''
        title = clean_title((title_el.text or '') if title_el is not None else '', source)
        link = (link_el.text or '').strip() if link_el is not None else ''
        if not title or not link:
            continue
        out.append({
            'title': title, 'url': link, 'source': source,
            'date': parse_date((pub_el.text or '') if pub_el is not None else ''),
        })
    return out


def extract_candidate(text: str):
    """Try every pattern; return (company, valuation_m) or None."""
    for rx, mult in PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        name = clean_name(m.group(1).strip(' \t\"\'“”‘’：:，,—–-'))
        try:
            val = float(m.group(2)) * mult
        except (ValueError, IndexError):
            continue
        if val < 500:      # below unicorn territory anyway; also kills noise
            continue
        if not (MIN_NAME_LEN <= len(name) <= MAX_NAME_LEN):
            continue
        # Name must start with a letter/CJK char and contain no sentence punctuation
        if not re.match(r'^[A-Za-z0-9一-鿿]', name):
            continue
        if re.search(r'[.!?。！？]', name):
            continue
        if normalize(name) in STOPWORDS_NORM:
            continue
        return name, val
    return None


def effective_base():
    """Effective valuation map (normalized name → (display name, valuation_m))."""
    overrides = load(DATA / 'overrides.json', {'edits': {}, 'additions': [], 'deletions': []})
    base = apply_overrides(load(DATA / 'unicorns.json', []), overrides)
    out = {}
    for r in base:
        co = r.get('company') or ''
        if not co or co.startswith('Unnamed'):
            continue
        out[normalize(co)] = (co, float(r.get('valuation_m') or 0))
    return out


def discover_new(base_map):
    """Part A: generic discovery queries → candidates not in the base."""
    found = {}   # normalized name → best candidate record
    for q in DISCOVERY_QUERIES:
        try:
            items = fetch_items(q)
        except Exception as e:
            print(f'Query failed [{q}]: {e}', file=sys.stderr)
            time.sleep(SLEEP_S)
            continue
        for it in items:
            ext = extract_candidate(it['title'])
            if not ext:
                continue
            name, val = ext
            key = normalize(name)
            if not key or key in base_map:
                continue
            # Dedup: keep the higher valuation
            if key in found and found[key]['valuation_m'] >= val:
                continue
            found[key] = {
                'company': name, 'valuation_m': round(val, 1),
                'headline': it['title'], 'url': it['url'],
                'source': it['source'], 'date': it['date'],
            }
        time.sleep(SLEEP_S)
    return sorted(found.values(), key=lambda c: -c['valuation_m'])


def detect_jumps(base_map):
    """Part B: scan data/news.json headlines for valuation phrases on known companies."""
    news = load(DATA / 'news.json', {}).get('news', {}) or {}
    jumps = []
    for company, items in news.items():
        cur = base_map.get(normalize(company))
        if not cur or not cur[1]:
            continue
        current_m = cur[1]
        best = None
        for it in items or []:
            ext = extract_candidate(it.get('title') or '')
            if not ext:
                continue
            _, val = ext
            if current_m > 0 and abs(val - current_m) / current_m > JUMP_THRESHOLD:
                if best is None or abs(val - current_m) > abs(best['detected_m'] - current_m):
                    best = {'detected_m': round(val, 1), 'headline': it.get('title', ''),
                            'url': it.get('url', ''), 'source': it.get('source', ''),
                            'date': it.get('date', '')}
        if best:
            jumps.append({'company': company, 'current_m': current_m, **best})
    jumps.sort(key=lambda j: -abs(j['detected_m'] - j['current_m']) / j['current_m'])
    return jumps


def main():
    base_map = effective_base()
    new_candidates = discover_new(base_map)
    valuation_jumps = detect_jumps(base_map)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'new_candidates': new_candidates,
        'valuation_jumps': valuation_jumps,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Wrote {len(new_candidates)} new candidates + {len(valuation_jumps)} valuation jumps '
          f'to {OUT.relative_to(DATA.parent)}', file=sys.stderr)


if __name__ == '__main__':
    main()
