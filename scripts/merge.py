#!/usr/bin/env python3
"""Promote the current state — including any approved overrides — into a fresh
unicorns.json snapshot.

Run this when you want to "compact" overrides into the canonical dataset
(e.g. after a long stretch of approvals to keep overrides.json clean).
The weekly Action does NOT call this script — see compute_pending.py instead.
"""
from _lib import build_canonical, apply_overrides, load, save, data_dir

DATA = data_dir()

wiki = load(DATA / 'wiki_raw.json', [])
pb_seed = load(DATA / 'seed_pitchbook.json', [])
zh_map = load(DATA / 'chinese_names.json', {})
overrides = load(DATA / 'overrides.json', {'edits': {}, 'additions': [], 'deletions': []})

canonical = build_canonical(wiki, pb_seed, zh_map)
final = apply_overrides(canonical, overrides)

save(DATA / 'unicorns.json', final)

src = {}
for d in final:
    src[d['source']] = src.get(d['source'], 0) + 1
print(f'Merged {len(final)} records → unicorns.json')
for s, n in sorted(src.items()):
    print(f'  {s}: {n}')
