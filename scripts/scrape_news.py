#!/usr/bin/env python3
"""Fetch the latest Google News RSS items for the Top 100 unicorns by valuation.

For each of the Top 100 companies in data/unicorns.json (sorted by valuation_m
descending), query Google News RSS and keep the first 3 items. Failed companies
keep their previous entries from the existing data/news.json (if any).

Output: data/news.json
    {
      "generated_at": "<ISO-8601>",
      "news": {
        "<company>": [ {"title": ..., "url": ..., "source": ..., "date": "YYYY-MM-DD"}, ... ]
      }
    }
"""
import json
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
UNICORNS = DATA_DIR / 'unicorns.json'
OUT = DATA_DIR / 'news.json'

USER_AGENT = 'unicorn-dashboard-bot/1.0 (auto-update from public Google News RSS)'
RSS_URL = 'https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en'

TOP_N = 100
ITEMS_PER_COMPANY = 3
REQUEST_TIMEOUT = 15
SLEEP_S = 0.3

# Custom queries for ambiguous company names. Key = company name in
# unicorns.json; value = the query sent to Google News (a value of None skips
# the company entirely). Default query: '"<company>" when:180d'.
NEWS_QUERY_OVERRIDES = {
    'Scale': '"Scale AI"',
    'Ripple': '"Ripple Labs"',
    'Bolt': '"Bolt" fintech',
    'Checkout.com': '"Checkout.com"',
    'Block': '"Block Inc" Square',
    'Discord': '"Discord" app',
    'Ramp': '"Ramp" corporate card',
    'Harvey': '"Harvey" legal AI',
    'Moonshot': '"Moonshot AI"',
    'Cursor': '"Cursor" Anysphere',
    'Clear': '"Clear" identity verification',
    'Labs': None,  # truncated name in source data — not a meaningful query
}


def build_query(company: str):
    """Return the Google News query for a company, or None to skip it."""
    if company in NEWS_QUERY_OVERRIDES:
        return NEWS_QUERY_OVERRIDES[company]
    return f'"{company}" when:180d'


def parse_date(pub_date: str) -> str:
    """RSS pubDate ('Mon, 10 Feb 2026 08:00:00 GMT') → 'YYYY-MM-DD'."""
    try:
        dt = parsedate_to_datetime(pub_date)
        return dt.date().isoformat()
    except (TypeError, ValueError):
        return ''


def clean_title(title: str, source: str) -> str:
    """Google News titles end with ' - <source name>'; strip that suffix."""
    if source and title.endswith(' - ' + source):
        return title[: -(len(source) + 3)].strip()
    if ' - ' in title:
        head, _, _ = title.rpartition(' - ')
        if head.strip():
            return head.strip()
    return title.strip()


def fetch_news(company: str, query: str):
    """Return up to ITEMS_PER_COMPANY news items for one company."""
    url = RSS_URL.format(q=urllib.parse.quote(query))
    r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.findall('.//channel/item')[:ITEMS_PER_COMPANY]:
        title_el = item.find('title')
        link_el = item.find('link')
        source_el = item.find('source')
        pub_el = item.find('pubDate')
        title = title_el.text or '' if title_el is not None else ''
        link = link_el.text or '' if link_el is not None else ''
        source = source_el.text or '' if source_el is not None else ''
        pub_date = pub_el.text or '' if pub_el is not None else ''
        if not title or not link:
            continue
        items.append({
            'title': clean_title(title, source),
            'url': link.strip(),
            'source': source.strip(),
            'date': parse_date(pub_date),
        })
    return items


def main():
    records = json.loads(UNICORNS.read_text(encoding='utf-8'))
    top = sorted(records, key=lambda x: x.get('valuation_m') or 0, reverse=True)[:TOP_N]

    # Previous results, kept for companies whose fetch fails this run.
    previous = {}
    if OUT.exists():
        try:
            previous = json.loads(OUT.read_text(encoding='utf-8')).get('news', {}) or {}
        except (json.JSONDecodeError, OSError) as e:
            print(f'Warning: could not read existing {OUT.name}: {e}', file=sys.stderr)

    news = {}
    ok = failed = skipped = 0
    for rec in top:
        company = rec.get('company') or ''
        if not company:
            continue
        query = build_query(company)
        if query is None:
            skipped += 1
            print(f'Skipped {company} (no meaningful query)', file=sys.stderr)
            if company in previous:
                news[company] = previous[company]
            continue
        try:
            news[company] = fetch_news(company, query)
            ok += 1
        except Exception as e:  # network / HTTP / XML errors — keep going
            failed += 1
            print(f'Failed {company}: {e}', file=sys.stderr)
            if company in previous:
                news[company] = previous[company]
        time.sleep(SLEEP_S)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'news': news,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    with_items = sum(1 for v in news.values() if v)
    print(
        f'Wrote news for {len(news)} companies ({with_items} with items) to '
        f'{OUT.relative_to(OUT.parent.parent)} — {ok} fetched, {failed} failed, {skipped} skipped',
        file=sys.stderr,
    )


if __name__ == '__main__':
    main()
