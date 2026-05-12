#!/usr/bin/env python3
"""Notice.co secondary-market scraper — CURRENTLY NON-FUNCTIONAL.

❌ STATUS: Notice's Cloudflare layer rejects this script with 403 Forbidden,
   both from GitHub Actions (datacenter IP) and from local macOS (Python TLS
   fingerprint mismatch). Even subprocess `curl` with HTTP/2 + browser-like
   headers gets blocked. The detection happens at JA3 / ALPN / HTTP/2 frame
   ordering, which a regular Python script can't easily reproduce.

✅ WORKAROUND: Use the in-browser scraping path. Open admin.html, click
   "🔄 Refresh Notice", and copy the JavaScript snippet into the DevTools
   console of a logged-in notice.co tab. Browser fetch with the user's real
   session passes Cloudflare and produces the same data/notice_data.json file
   structure this script would have written.

This file is kept for reference (slug map structure, URL pattern, parse regex)
and in case Notice ever relaxes its bot policy. Don't bother running it.

Original spec:
  Input  : data/notice_slugs.json  (company → notice.co slug map)
  URL    : https://notice.co/c/<slug>
  Output : data/notice_data.json   (same schema as forge_data.json but with
                                     notice_price / notice_valuation_m fields)
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
import subprocess
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
    """Fetch HTML via system `curl` rather than Python urllib.

    Notice.co's Cloudflare layer fingerprints the TLS handshake + HTTP version;
    urllib (HTTP/1.1, Python OpenSSL build) gets blocked with 403 even from a
    residential IP. macOS system curl uses LibreSSL and supports HTTP/2, which
    matches Safari's fingerprint closely enough to pass Cloudflare's check.
    """
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        ua = random.choice(USER_AGENTS)
        cmd = [
            'curl', '-sSL', '--http2', '--compressed', '--max-time', '30',
            '-A', ua,
            '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            '-H', 'Accept-Language: en-US,en;q=0.9',
            '-H', 'Cache-Control: no-cache',
            '-H', 'Pragma: no-cache',
            '-H', 'Referer: https://notice.co/',
            '-H', 'Sec-Ch-Ua: "Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            '-H', 'Sec-Ch-Ua-Mobile: ?0',
            '-H', 'Sec-Ch-Ua-Platform: "macOS"',
            '-H', 'Sec-Fetch-Dest: document',
            '-H', 'Sec-Fetch-Mode: navigate',
            '-H', 'Sec-Fetch-Site: same-origin',
            '-H', 'Sec-Fetch-User: ?1',
            '-H', 'Upgrade-Insecure-Requests: 1',
            '-w', '%{http_code}',                            # append HTTP code at end so we can detect 403
            url,
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
            out = res.stdout
            # curl with -w '%{http_code}' tacks the status code at the very end.
            # Peel it off the tail; if it's 4xx we treat as a real failure.
            if len(out) >= 3 and out[-3:].isdigit():
                http = int(out[-3:])
                out = out[:-3]
            else:
                http = 200 if res.returncode == 0 else 0
            if http == 0 or res.returncode != 0:
                raise RuntimeError(f'curl exit {res.returncode}: {(res.stderr or "")[:200]}')
            if 400 <= http < 500:
                raise urllib.error.HTTPError(url, http, f'HTTP {http}', {}, None)
            if 500 <= http < 600:
                last_err = urllib.error.HTTPError(url, http, f'HTTP {http}', {}, None)
            else:
                return out
        except urllib.error.HTTPError:
            raise
        except (subprocess.TimeoutExpired, Exception) as e:
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
