#!/usr/bin/env python3
"""Scrape the Wikipedia 'List of unicorn startup companies' table.

Output: data/wiki_raw.json
"""
import json
import re
import sys
from pathlib import Path
import requests
from bs4 import BeautifulSoup

URL = 'https://en.wikipedia.org/wiki/List_of_unicorn_startup_companies'
USER_AGENT = 'unicorn-dashboard-bot/1.0 (auto-update from public Wikipedia article)'

OUT = Path(__file__).resolve().parent.parent / 'data' / 'wiki_raw.json'


def parse_val(s: str) -> float:
    s = re.sub(r'\[\d+\]', '', s).strip()
    rng = re.match(r'(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)', s)
    if rng:
        return (float(rng.group(1)) + float(rng.group(2))) / 2
    m = re.match(r'(\d+(?:\.\d+)?)', s)
    return float(m.group(1)) if m else 0.0


def clean(s: str) -> str:
    return re.sub(r'\[\d+\]', '', s).strip()


def main():
    print(f'Fetching {URL}', file=sys.stderr)
    r = requests.get(URL, timeout=30, headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    tables = soup.select('table.wikitable')

    # The page has multiple wikitables; the active-unicorns one has 'Founder' in its header.
    table = None
    for t in tables:
        headers = ' '.join(th.get_text(' ', strip=True) for th in t.select('th'))
        if 'Company' in headers and 'Valuation' in headers and 'Founder' in headers:
            table = t
            break
    if table is None:
        raise SystemExit('active-unicorns table not found on Wikipedia page')

    rows = []
    for tr in table.select('tbody tr')[1:]:
        cells = tr.select('td')
        if len(cells) < 6:
            continue
        a = cells[0].select_one('a:not(.image)')
        company = cells[0].get_text(strip=True)
        if not company:
            continue
        val_b = parse_val(cells[1].get_text())
        if val_b <= 0:
            continue
        wiki_link = ''
        if a is not None:
            href = a.get('href', '')
            if href.startswith('/wiki/'):
                wiki_link = 'https://en.wikipedia.org' + href
        date_str = re.sub(r'\s*\(\d{4}-\d{2}\)', '', clean(cells[2].get_text()))
        rows.append({
            'company': company,
            'wikiLink': wiki_link,
            'valuation_b': val_b,
            'valuation_str': f'${val_b:g}B',
            'valuation_date': date_str,
            'industry_raw': clean(cells[3].get_text()),
            'country': clean(cells[4].get_text()),
            'founders': clean(cells[5].get_text()),
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Wrote {len(rows)} records to {OUT.relative_to(OUT.parent.parent)}', file=sys.stderr)


if __name__ == '__main__':
    main()
