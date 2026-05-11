#!/usr/bin/env python3
"""Weekly Forge Global secondary-market scraper.

For each of the Top-N unicorns (by valuation_m in unicorns.json), look up the
forgeglobal.com URL slug from data/forge_slugs.json and fetch:

    https://forgeglobal.com/<slug>_stock/

Extract:
  - Forge Price            ("Forge Price | $632.33 | 5/6/2026")
  - Forge Price Valuation  ("Forge Price valuation | $1.5T")

Persist to data/forge_data.json. The previous run's values are carried over
into prev_forge_price / prev_forge_valuation_m so the dashboard can show a
red/green delta without keeping its own history.

This script is run from the GitHub Actions runner (which has unrestricted
egress). It does NOT touch unicorns.json — Forge data is rendered as an
overlay on top of the existing canonical record.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / 'data'
TOP_N = 100
THROTTLE_SEC = 1.6

UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)
HEADERS = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}


# ---------- I/O ----------

def load(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def save(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        # Forge sends gzip sometimes via CDN; urllib won't auto-decode.
        if r.headers.get('Content-Encoding') == 'gzip':
            import gzip
            raw = gzip.decompress(raw)
        return raw.decode('utf-8', errors='replace')


# ---------- Parsing ----------

# Strip HTML tags so we can run the same regex against rendered text & raw HTML.
_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')

def _flatten(html: str) -> str:
    txt = _TAG_RE.sub(' ', html)
    txt = txt.replace('&nbsp;', ' ').replace('&amp;', '&')
    return _WS_RE.sub(' ', txt).strip()


# "Forge Price 632.33 5/6/2026" — capture the price right after the label.
PRICE_RE = re.compile(
    r'Forge Price(?!\s*valuation)[^\$]{0,40}\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)',
    re.IGNORECASE,
)

# "Forge Price valuation $1.5T"  /  "Forge Price valuation $250B"
VAL_RE = re.compile(
    r'Forge Price valuation[^\$]{0,40}\$\s*([0-9]+(?:\.[0-9]+)?)\s*([TBM])',
    re.IGNORECASE,
)


def parse_page(html: str):
    """Returns (price_usd: float|None, valuation_m: float|None)."""
    flat = _flatten(html)
    price = None
    valuation_m = None

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
        # valuation_m is "millions" — same unit used in unicorns.json
        mult = {'T': 1_000_000, 'B': 1_000, 'M': 1}[unit]
        valuation_m = n * mult

    return price, valuation_m


# ---------- Driver ----------

def main():
    unicorns = load(DATA / 'unicorns.json', [])
    slug_blob = load(DATA / 'forge_slugs.json', {})
    slug_map = dict(slug_blob.get('matches') or {})
    slug_map.update(slug_blob.get('manual_overrides') or {})

    prev = load(DATA / 'forge_data.json', {'records': {}})
    prev_recs = prev.get('records') or {}

    pool = [d for d in unicorns if not (d.get('company') or '').startswith('Unnamed')]
    pool.sort(key=lambda d: -(d.get('valuation_m') or 0))
    top = pool[:TOP_N]

    out = {
        'as_of': dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        'top_n': TOP_N,
        'records': {},
    }

    n_ok = n_skip = n_fail = 0
    for d in top:
        co = d['company']
        slug = slug_map.get(co)
        if not slug:
            n_skip += 1
            continue
        url = f'https://forgeglobal.com/{slug}_stock/'
        try:
            html = fetch(url)
        except Exception as e:
            print(f'  FAIL  {co!r:30}  {e}')
            n_fail += 1
            time.sleep(THROTTLE_SEC)
            continue

        price, val_m = parse_page(html)
        if price is None and val_m is None:
            print(f'  EMPTY {co!r:30}  ({url})')
            n_fail += 1
            time.sleep(THROTTLE_SEC)
            continue

        prev_rec = prev_recs.get(co) or {}
        out['records'][co] = {
            'slug': slug,
            'forge_price': price,
            'forge_valuation_m': val_m,
            'prev_forge_price': prev_rec.get('forge_price'),
            'prev_forge_valuation_m': prev_rec.get('forge_valuation_m'),
            'fetched_at': dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        }
        n_ok += 1
        v_str = f'${val_m/1000:.1f}B' if val_m else '—'
        p_str = f'${price:,.2f}' if price else '—'
        print(f'  OK    {co!r:30}  {p_str:>12}  /  {v_str}')
        time.sleep(THROTTLE_SEC)

    save(DATA / 'forge_data.json', out)
    print()
    print(f'Forge scrape: {n_ok} ok / {n_skip} no-slug / {n_fail} failed (top {TOP_N})')


if __name__ == '__main__':
    main()
