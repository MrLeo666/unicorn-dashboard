#!/usr/bin/env python3
"""Append a weekly valuation snapshot to the history log.

Captures the current valuation state of every known company from three sources:
  wiki   — effective base valuation (unicorns.json + admin overrides applied,
           via _lib.apply_overrides), in $M
  forge  — Forge Global secondary market (forge_valuation_m / forge_price)
  notice — Notice Weekly Update (valuation_b * 1000 / pps)

Output: data/history/valuation_history.jsonl — one JSON object per line:
    {"date": "YYYY-MM-DD",
     "entries": {"<company>": {"wiki_m": ..., "forge_m": ..., "forge_price": ...,
                               "notice_m": ..., "notice_price": ...}}}

Idempotent per day: re-running on the same date replaces (not duplicates)
that day's snapshot.
"""
import json
import sys
from datetime import date

from _lib import apply_overrides, load, data_dir

DATA = data_dir()
OUT = DATA / 'history' / 'valuation_history.jsonl'


def build_entries():
    overrides = load(DATA / 'overrides.json', {'edits': {}, 'additions': [], 'deletions': []})
    base = apply_overrides(load(DATA / 'unicorns.json', []), overrides)
    forge = load(DATA / 'forge_data.json', {}).get('records', {}) or {}
    notice = load(DATA / 'notice_secondary.json', {}).get('records', {}) or {}

    def empty():
        return {'wiki_m': None, 'forge_m': None, 'forge_price': None,
                'notice_m': None, 'notice_price': None}

    entries = {}
    for r in base:
        co = r.get('company') or ''
        if not co or co.startswith('Unnamed'):
            continue
        entries[co] = empty()
        v = r.get('valuation_m')
        entries[co]['wiki_m'] = float(v) if v else None
    for co, fg in forge.items():
        e = entries.setdefault(co, empty())
        if fg.get('forge_valuation_m') is not None:
            e['forge_m'] = float(fg['forge_valuation_m'])
        if fg.get('forge_price') is not None:
            e['forge_price'] = float(fg['forge_price'])
    for co, nt in notice.items():
        e = entries.setdefault(co, empty())
        if nt.get('valuation_b') is not None:
            e['notice_m'] = float(nt['valuation_b']) * 1000
        if nt.get('pps') is not None:
            e['notice_price'] = float(nt['pps'])
    return entries


def main():
    today = date.today().isoformat()
    snapshot = {'date': today, 'entries': build_entries()}

    # Keep all past snapshots except today's (replaced below).
    lines = []
    if OUT.exists():
        for line in OUT.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get('date') == today:
                    continue  # same-day rerun → replaced by the fresh snapshot
            except json.JSONDecodeError:
                print(f'Warning: skipping malformed history line: {line[:80]}', file=sys.stderr)
                continue
            lines.append(line)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines.append(json.dumps(snapshot, ensure_ascii=False))
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    n = len(snapshot['entries'])
    print(f'Snapshot {today}: {n} companies → {OUT.relative_to(DATA.parent)} ({len(lines)} snapshots total)',
          file=sys.stderr)


if __name__ == '__main__':
    main()
