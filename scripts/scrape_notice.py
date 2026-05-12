#!/usr/bin/env python3
"""Weekly Notice.co secondary-market scraper (companion to scrape_forge.py).

For each of the Top-N unicorns, look up the notice.co URL slug from
data/notice_slugs.json and fetch:

    https://notice.co/c/<slug>

Extract:
  - Notice Price       (from the page <title>: "Anthropic Stock $439.86 | ...")
  - Notice Valuation   (from body text: "Market Cap | $645.01 B")

Persist to data/notice_data.json. Previous values carried into prev_* so the
dashboard can compute red/green deltas without keeping its own history.

Same retry / UA rotation / jitter behavior as scrape_forge.py.
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
THROTTLE_MIN = 1.2
THROTTLE_MAX = 2.0
MAX_RETRIES = 2
RETRY_BACKOFF = (2.0, 4.0)

USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Version/17.4 Safari/605.1.15',
]
# Notice.co is stricter about bot fingerprinting than Forge — server-side runners
# often get a stripped-down page. We send a comprehensive set of headers that
# match a real Chrome navigation, including the Sec-CH-UA client hints.
BASE_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Cache-Control': 'no-cache',
    'Pragma': 'no-cache',
    'Referer': 'https://notice.co/',
    'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"macOS"',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-User': '?1',
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
            import brotli  # type: ignore
            raw = brotli.decompress(raw)
        except Exception:
            pass
    elif encoding == 'deflate':
        import zlib
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode('utf-8', errors='replace')


def fetch(url: str) -> str:
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        headers = dict(BASE_HEADERS)
        headers['User-Agent'] = random.choice(USER_AGENTS)
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return _decode(r.read(), r.headers.get('Content-Encoding'))
        except urllib.error.HTTPError as e:
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
_TITLE_RE = re.compile(r'<title>([^<]+)</title>', re.IGNORECASE)


def _flatten(html: str) -> str:
    txt = _TAG_RE.sub(' ', html)
    txt = txt.replace('&nbsp;', ' ').replace('&amp;', '&')
    return _WS_RE.sub(' ', txt).strip()


# "Anthropic Stock $439.86 | How to Buy, ..." — price embedded in <title>.
TITLE_PRICE_RE = re.compile(
    r'Stock\s*\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)',
    re.IGNORECASE,
)
# "Market Cap $645.01 B"  /  "Market Cap $1.31 T"
CAP_RE = re.compile(
    r'Market Cap\s*\$\s*([0-9]+(?:\.[0-9]+)?)\s*([TBM])',
    re.IGNORECASE,
)


def parse_page(html: str):
    """Returns (price_usd: float|None, valuation_m: float|None)."""
    price = None
    valuation_m = None

    tm = _TITLE_RE.search(html)
    if tm:
        pm = TITLE_PRICE_RE.search(tm.group(1))
        if pm:
            try:
                price = float(pm.group(1).replace(',', ''))
            except ValueError:
                pass

    flat = _flatten(html)
    cm = CAP_RE.search(flat)
    if cm:
        n = float(cm.group(1))
        unit = cm.group(2).upper()
        mult = {'T': 1_000_000, 'B': 1_000, 'M': 1}[unit]
        valuation_m = n * mult

    return price, valuation_m


# ---------- Driver ----------

def main():
    unicorns = load(DATA / 'unicorns.json', [])
    slug_blob = load(DATA / 'notice_slugs.json', {})
    slug_map = dict(slug_blob.get('matches') or {})
    slug_map.update(slug_blob.get('manual_overrides') or {})

    prev = load(DATA / 'notice_data.json', {'records': {}})
    prev_recs = prev.get('records') or {}

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
        url = f'https://notice.co/c/{slug}'
        try:
            html = fetch(url)
        except Exception as e:
            print(f'  ERR   {co!r:30}  {type(e).__name__}: {e}')
            n_err += 1
            out['fetch_errors'].append({'company': co, 'slug': slug, 'error': str(e)})
            time.sleep(random.uniform(THROTTLE_MIN, THROTTLE_MAX))
            continue

        price, val_m = parse_page(html)

        if price is None and val_m is None:
            # Diagnostic: dump page title + a couple body fingerprints so we can
            # tell if Notice is serving a stripped/bot-walled version of the page.
            tm = _TITLE_RE.search(html)
            title = tm.group(1)[:80] if tm else '(no <title>)'
            has_mc  = 'Market Cap' in html
            has_stock = ' Stock $' in html
            print(f'  EMPTY {co!r:30}  title="{title}"  mc={has_mc} stock$={has_stock}')
            n_no_data += 1
            out['no_data'].append(co)
            time.sleep(random.uniform(THROTTLE_MIN, THROTTLE_MAX))
            continue

        prev_rec = prev_recs.get(co) or {}
        out['records'][co] = {
            'slug': slug,
            'notice_price': price,
            'notice_valuation_m': val_m,
            'prev_notice_price': prev_rec.get('notice_price'),
            'prev_notice_valuation_m': prev_rec.get('notice_valuation_m'),
            'fetched_at': dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        }
        n_ok += 1
        v_str = f'${val_m/1000:.1f}B' if val_m else '—'
        p_str = f'${price:,.2f}' if price else '—'
        print(f'  OK    {co!r:30}  {p_str:>12}  /  {v_str}')
        time.sleep(random.uniform(THROTTLE_MIN, THROTTLE_MAX))

    save(DATA / 'notice_data.json', out)
    print()
    print(f'Notice scrape summary (top {TOP_N}):')
    print(f'  ✓ with price/cap   : {n_ok}')
    print(f'  · tracked, no data : {n_no_data}')
    print(f'  · no slug mapping  : {n_no_slug}')
    print(f'  ✗ fetch / parse err: {n_err}')


if __name__ == '__main__':
    main()
