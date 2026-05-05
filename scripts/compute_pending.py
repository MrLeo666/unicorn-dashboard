#!/usr/bin/env python3
"""Compute pending updates from the latest data refresh.

Compares:
  current  = unicorns.json (what the dashboard renders today) + overrides applied
  proposed = a fresh canonical merge of wiki + pb_seed + chinese_names (no overrides)

Outputs:
  data/pending.json  — additions / modifications / removals to be reviewed in admin

The dashboard does NOT consume pending.json. The admin page reads it,
shows diffs, and the user converts approved entries into overrides.json
through the admin UI.
"""
import datetime as dt
from _lib import (
    build_canonical, apply_overrides, diff_records,
    load, save, data_dir,
)

DATA = data_dir()

wiki = load(DATA / 'wiki_raw.json', [])
pb_seed = load(DATA / 'seed_pitchbook.json', [])
zh_map = load(DATA / 'chinese_names.json', {})
overrides = load(DATA / 'overrides.json', {'edits': {}, 'additions': [], 'deletions': []})
unicorns_now = load(DATA / 'unicorns.json', [])

# Current dashboard-state = canonical unicorns.json + admin overrides on top
current = apply_overrides(unicorns_now, overrides)

# Proposed canonical = freshly merged from raw sources, no overrides applied
proposed = build_canonical(wiki, pb_seed, zh_map)

diff = diff_records(current, proposed)
diff['generated_at'] = dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z'

save(DATA / 'pending.json', diff)

n_add = len(diff['additions'])
n_mod = len(diff['modifications'])
n_rem = len(diff['removals'])
total = n_add + n_mod + n_rem
print(f'Pending updates: {total} ({n_add} add / {n_mod} mod / {n_rem} rem)')
for d in diff['additions'][:5]:
    print(f'  + {d["company"]}  ${d["valuation_m"]/1000:g}B')
for m in diff['modifications'][:5]:
    cs = ', '.join(f'{f}: {v["from"]}→{v["to"]}' for f, v in m['changes'].items())
    print(f'  ~ {m["company"]}  {cs}')
for d in diff['removals'][:5]:
    print(f'  − {d["company"]}  ${d["valuation_m"]/1000:g}B')
