#!/usr/bin/env python3
"""Weekly Forge Global secondary-market scraper.

For each of the Top-N unicorns (by valuation_m in unicorns.json), look up the
forgeglobal.com URL slug from data/forge_slugs.json and fetch:

    https://forgeglobal.com/<slug>_stock/

Extract:
  - Forge Price            ("Forge Price $632.33  5/6/2026")
  - Forge Price Valuation  ("Forge Price valuation $1.5T")

Fallback when Forge has no daily price ("Forge Price $--"):
  - Last funding-round valuation parsed from the funding history table
    ("Series F $11.75B" etc.) → stored separately as last_round_valuation_m.

Persist to data/forge_data.json. The previous run's values are carried over
into prev_* so the dashboard can show a red/green delta without keeping its
own history.

This script is run from the GitHub Actions runner (which has unrestricted
egress). It does NOT touch unicorns.json — Forge data is rendered as an
overlay on top of the existing canonical record.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / 'data'
TOP_N = 100
# Random-jittered throttle: pace varies 1.2s..2.0s so the request pattern
# looks less mechanical to Forge's CDN.
THROTTLE_MIN = 1.2
THROTTLE_MAX = 2.0
# Retry on transient errors (timeouts, 502/503/504). 4xx (404/403) won't retry.
MAX_RETRIES = 2
RETRY_BACKOFF = (2.0, 4.0)  # seconds after 1st, 2nd retry

# Rotating User-Agent pool — three current Chrome/Safari UAs across macOS/Win/iOS.
USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Version/17.4 Safari/605.1.15',
]
# Realistic browser-fingerprint headers. Sec-Fetch-* + Accept-Encoding signal a real navigation.
BASE_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-User': '?1',
    'Sec-Fetch-Dest': 'document',
    'Upgrade-Insecure-Requests': '1',
    'DNT': '1',
}


# ---------- I/O ----------

def load(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def save(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def _decode(raw: bytes, encoding: str | None) -> str:
    if encoding == 'gzip':
        import gzip
        raw = gzip.decompress(raw)
    elif encoding == 'br':
        try:
            import brotli                                 # type: ignore
            raw = brotli.decompress(raw)
        except Exception:
            pass  # if brotli isn't available, fall through to raw bytes
    elif encoding == 'deflate':
        import zlib
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)   # raw deflate
    return raw.decode('utf-8', errors='replace')


def fetch(url: str) -> str:
    """GET with retries + rotating UA + realistic headers. Raises on final failure."""
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        headers = dict(BASE_HEADERS)
        headers['User-Agent'] = random.choice(USER_AGENTS)
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return _decode(r.read(), r.headers.get('Content-Encoding'))
        except urllib.error.HTTPError as e:
            # 4xx = real failure (404 page doesn't exist). Don't retry these.
            if 400 <= e.code < 500:
                raise
            last_err = e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF[attempt] + random.uniform(0, 1))
    raise last_err  # type: ignore[misc]


# ---------- Parsing ----------

_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')

def _flatten(html: str) -> str:
    txt = _TAG_RE.sub(' ', html)
    txt = txt.replace('&nbsp;', ' ').replace('&amp;', '&')
    return _WS_RE.sub(' ', txt).strip()


PRICE_RE = re.compile(
    r'Forge Price(?!\s*valuation)[^\$]{0,40}\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)',
    re.IGNORECASE,
)
VAL_RE = re.compile(
    r'Forge Price valuation[^\$]{0,40}\$\s*([0-9]+(?:\.[0-9]+)?)\s*([TBM])',
    re.IGNORECASE,
)
# Funding history fallback. Forge renders rows like:
#   "Series F  $11.75B  $187.28  $735MM" (round, post-money, price/share, raised)
# We pick the FIRST post-money valuation on the page (rows are sorted most-recent first).
ROUND_RE = re.compile(
    r'(Series [A-Z][^\s]*|Seed|Tender Offer|Secondary Transaction|Corporate Round|'
    r'Private Equity Round|Pre-IPO|IPO)\s*[^\$\n]{0,40}\$\s*([0-9]+(?:\.[0-9]+)?)\s*([TBM])',
    re.IGNORECASE,
)


def parse_page(html: str):
    """Extract (price, valuation_m, last_round_valuation_m). All can be None."""
    flat = _flatten(html)
    price = None
    valuation_m = None
    last_round_m = None

    pm = PRICE_RE.search(flat)
    if pm:
        try:
            price = float(pm.group(1).replace(',', ''))
        except ValueError:
            pass

    vm = VAL_RE.search(flat)
    if vm:
        n = float(vm.group(1))
        unit = vm.group(2).upper()
        mult = {'T': 1_000_000, 'B': 1_000, 'M': 1}[unit]
        valuation_m = n * mult

    # Funding-round fallback: only consult when there's no Forge valuation.
    if valuation_m is None:
        rm = ROUND_RE.search(flat)
        if rm:
            n = float(rm.group(2))
            unit = rm.group(3).upper()
            mult = {'T': 1_000_000, 'B': 1_000, 'M': 1}[unit]
            last_round_m = n * mult

    return price, valuation_m, last_round_m


# ---------- Driver ----------

def main():
    unicorns = load(DATA / 'unicorns.json', [])
    slug_blob = load(DATA / 'forge_slugs.json', {})
    slug_map = dict(slug_blob.get('matches') or {})
    slug_map.update(slug_blob.get('manual_overrides') or {})

    prev = load(DATA / 'forge_data.json', {'records': {}})
    prev_recs = prev.get('records') or {}

    # Respect overrides.deletions when picking Top N (so Chime-style removals
    # don't take up a slot).
    overrides = load(DATA / 'overrides.json', {})
    deletions = set((overrides or {}).get('deletions') or [])

    pool = [
        d for d in unicorns
        if d.get('company')
        and not d['company'].startswith('Unnamed')
        and d['company'] not in deletions
    ]
    pool.sort(key=lambda d: -(d.get('valuation_m') or 0))
    top = pool[:TOP_N]

    out = {
        'as_of': dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        'top_n': TOP_N,
        'records': {},
        # Bookkeeping: which companies in slug_map have no Forge price data this run.
        # (HTTP 200 but page shows "Forge Price $--".) Lets the dashboard / future
        # runs distinguish "tracked but empty" from "no slug at all".
        'no_data': [],
        'no_slug': [],
        'fetch_errors': [],
    }

    n_ok = n_no_data = n_no_slug = n_err = 0
    for d in top:
        co = d['company']
        slug = slug_map.get(co)
        if not slug:
            n_no_slug += 1
            out['no_slug'].append(co)
            continue
        url = f'https://forgeglobal.com/{slug}_stock/'
        try:
            html = fetch(url)
        except Exception as e:
            print(f'  ERR   {co!r:30}  {type(e).__name__}: {e}')
            n_err += 1
            out['fetch_errors'].append({'company': co, 'slug': slug, 'error': str(e)})
            time.sleep(random.uniform(THROTTLE_MIN, THROTTLE_MAX))
            continue

        price, val_m, last_round_m = parse_page(html)

        if price is None and val_m is None and last_round_m is None:
            # Page loaded but no useful numbers at all — page might be malformed.
            print(f'  EMPTY {co!r:30}  page parsed, no numbers found')
            n_no_data += 1
            out['no_data'].append(co)
            time.sleep(random.uniform(THROTTLE_MIN, THROTTLE_MAX))
            continue

        if price is None and val_m is None:
            # Forge has no daily price, but we got a last-round valuation as fallback.
            print(f'  ROUND {co!r:30}  no Forge price (last round ${last_round_m/1000:.1f}B)')
            n_no_data += 1   # still counts as "no Forge data" from the price POV
            prev_rec = prev_recs.get(co) or {}
            out['records'][co] = {
                'slug': slug,
                'forge_price': None,
                'forge_valuation_m': None,
                'last_round_valuation_m': last_round_m,
                'prev_forge_price': prev_rec.get('forge_price'),
                'prev_forge_valuation_m': prev_rec.get('forge_valuation_m'),
                'prev_last_round_valuation_m': prev_rec.get('last_round_valuation_m'),
                'fetched_at': dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z',
            }
            time.sleep(random.uniform(THROTTLE_MIN, THROTTLE_MAX))
            continue

        prev_rec = prev_recs.get(co) or {}
        out['records'][co] = {
            'slug': slug,
            'forge_price': price,
            'forge_valuation_m': val_m,
            'last_round_valuation_m': last_round_m,
            'prev_forge_price': prev_rec.get('forge_price'),
            'prev_forge_valuation_m': prev_rec.get('forge_valuation_m'),
            'prev_last_round_valuation_m': prev_rec.get('last_round_valuation_m'),
            'fetched_at': dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        }
        n_ok += 1
        v_str = f'${val_m/1000:.1f}B' if val_m else '—'
        p_str = f'${price:,.2f}' if price else '—'
        print(f'  OK    {co!r:30}  {p_str:>12}  /  {v_str}')
        time.sleep(random.uniform(THROTTLE_MIN, THROTTLE_MAX))

    save(DATA / 'forge_data.json', out)
    print()
    print('Forge scrape summary (top {0}):'.format(TOP_N))
    print(f'  ✓ with Forge price : {n_ok}')
    print(f'  · tracked, no price: {n_no_data}  (Forge page shows "$--")')
    print(f'  · no slug mapping  : {n_no_slug}')
    print(f'  ✗ fetch / parse err: {n_err}')


if __name__ == '__main__':
    main()
