# Unicorn Dashboard + LQP 工具链 — Deployable Sub-Project

Self-hosted version of the unicorn bubble dashboard, **plus** LQP Pacific 投资工具链（筛选雷达 + 尽调台）建立在同一套数据基座之上。Wikipedia is the only auto-scraped source. The pipeline has a **two-tier approval flow**:

- **Admin overrides** (your manual edits via `admin.html`) → live on the dashboard the moment you commit `overrides.json`.
- **Auto-scraped changes** (weekly Wikipedia refresh) → land in `pending.json` for review. Nothing reaches the dashboard until you approve them in the admin page.
- **LQP overlay** (五维评分 / pipeline / 尽调 / 客户区块) → 由 `radar.html` 和 `diligence.html` 编辑导出为 `lqp_overlay.json`，与 `overrides.json` 同 commit-and-push 流程。

## Folder structure

```
deploy/
├── index.html                  # 公共气球看板 (fetches data + overrides at runtime)
├── admin.html                  # 数据管理后台 (edit unicorns + overrides + pending)
├── radar.html                  # 🆕 LQP 独角兽筛选雷达 (Top 50 US/CN, 五维评分, 入围气泡图)
├── diligence.html              # 🆕 LQP 尽调与投决工作台 (一页纸 + 三档情景 + IC 备忘录)
├── lqp_core.js                 # 🆕 LQP 统一数据基座 ES 模块 (异步加载 + 合并 + 过滤)
├── data/
│   ├── unicorns.json           # ✱ canonical baseline (Wikipedia 周更)
│   ├── overrides.json          # ✱ admin-page exported edits (live-applied client-side)
│   ├── pending.json            # ✱ proposed changes from latest scrape, awaiting review
│   ├── wiki_raw.json           # ✱ fresh Wikipedia scrape (auto-updated weekly)
│   ├── forge_data.json         # ✱ Forge Global secondary-market overlay (Top 100, weekly)
│   ├── forge_slugs.json        # company → forgeglobal.com URL slug mapping
│   ├── lqp_overlay.json        # 🆕 ✱ LQP 专属层 (sector_map / ratings / pipeline / watchlist /
│   │                           #         diligence / client / exclusions / aliases / notes)
│   ├── seed_pitchbook.json     # frozen PitchBook-only rows from the initial seed
│   └── chinese_names.json      # manual mapping: English → {zh, pinyin}
├── scripts/
│   ├── _lib.py                 # shared merge / diff helpers
│   ├── scrape_wikipedia.py     # HTTP + BeautifulSoup
│   ├── scrape_forge.py         # weekly Forge Global secondary-market scrape (Top 100)
│   ├── compute_pending.py      # runs weekly; writes pending.json
│   ├── merge.py                # promote canonical (run only when compacting overrides)
│   └── requirements.txt
├── .github/workflows/
│   └── update-data.yml         # weekly cron + manual trigger
└── README.md
```

✱ = auto-generated or admin-exported; commit them as you go.

## LQP 工具链 (v1.0, 2026-05-19)

四个页面共用 `deploy/data/*` 作为唯一数据源：

| 页面 | 编辑哪个 JSON | 用途 |
|---|---|---|
| `index.html` | — (只读) | 气球看板：556 家公司 / Top 100 视图 / Forge 二级覆盖 |
| `admin.html` | `overrides.json` | 基础公司主数据 (估值/中文名/添加新公司/删除) |
| `radar.html` | `lqp_overlay.json` | LQP 筛选雷达：Top 50 US/CN · 五维评分 · 入围气泡 · watchlist · pipeline |
| `diligence.html` | `lqp_overlay.json` | 尽调台：公司一页纸 + 牛/基准/熊三档估值情景 + IC 备忘录骨架 → 导出 Markdown |

### 数据基座 lqp_core.js

ES 模块。所有 LQP 页面 `import { loadLqp, ... } from "./lqp_core.js"` 拿到统一公司记录。内部流程：

1. `fetchSources()` 并行拉 `unicorns.json + overrides.json + forge_data.json + lqp_overlay.json`
2. `mergeOverrides()` 把 overrides 应用到 baseline (与 `index.html` 完全一致的逻辑)
3. **过滤**：仅保留 `geo ∈ {US, CN}` + `valuation ≥ $5B` + 不在 `exclusions` + 不是 `aliases` 别名
4. **叠加** LQP overlay：`sector_map` / `ratings` / `pipeline` / `watchlist` / `diligence` / `client` / `notes`
5. **叠加** Forge：`forge.price` / `forge.valuation` / `forge.prevValuation` (若存在)
6. 按估值降序返回

锁定项 (待决①, v1.0)：评分维度固定为五维 — 赛道信心 / 质地·护城河 / 二级流动性 / IPO 能见度 / 进场吸引力。后续工具读 `RADAR_DIMS` 标签，禁止私改。

### lqp_overlay.json 数据契约

```json
{
  "exclusions":   ["Ant Group", "蚂蚁", "Lufax", "陆金所", "WeBank", "微众"],
  "aliases":      { "Anysphere": "Cursor" },
  "sector_map":   { "OpenAI": "AI·基础模型", "Anduril Industries": "国防科技", ... },
  "ratings":      { "SpaceX": { "sector":5, "quality":5, "liquidity":4, "ipo":2, "entry":3 }, ... },
  "pipeline":     { "Databricks": "diligence", ... },
  "watchlist":    ["SpaceX", "OpenAI", "Anthropic", ...],
  "diligence":    { "Databricks": { "icMemoUrl":"", "analyst":"", "decision":"",
                                      "valuationScenarios": {bull,base,bear}, "keyRisks":[] } },
  "client":       { "<company>": { "spvName":"", "heldByClients":[], "tradable":false } },
  "notes":        { "<company>": "..." }
}
```

### LQP 工作流

**评分一家公司：** radar.html → 调权重 → 点公司行的 1-5 评分输入框 → 自动重算综合分和入围气泡 → 完成后 "导出 lqp_overlay.json ↓" → 替换 `deploy/data/lqp_overlay.json` → commit → push。

**给一家公司做尽调：** diligence.html → 选公司 → 一页纸自动渲染 → 填三档估值情景（输入 ARR + 倍数自动算 exit）→ 填 IC 备忘录六段 → 保存到本地 → "导出 IC 备忘录 (Markdown) ↓" 给团队，"导出 lqp_overlay.json ↓" 更新底座。

**加入 watchlist：** radar.html 表格点 ★ → 导出 overlay。

**新增公司：** admin.html → "+ Add company" → 走原有 overrides flow。LQP overlay 会自动覆盖新公司的字段（默认 sector 兜底为 "其他"，需手动指定）。



## Data flow

```
Weekly GitHub Action
  ┌──────────────────────┐
  │ scrape_wikipedia.py  │ → data/wiki_raw.json
  └──────────────────────┘
              │
              ▼
  ┌──────────────────────┐
  │ compute_pending.py   │   builds proposed canonical, diffs against
  │                      │   current dashboard state (unicorns + overrides),
  │                      │   writes pending.json — DOES NOT touch unicorns.json
  └──────────────────────┘
              │
              ▼
        data/pending.json  ─────►  admin.html shows banner & review UI

You (in admin.html)
  · edit / add / delete in the table         → STATE.{edits,additions,deletions}
  · click Approve on pending entries         → same STATE
  · click "Export overrides ↓"               → downloads overrides.json
  · commit overrides.json to repo + push

Dashboard
  · fetches unicorns.json + overrides.json
  · merges client-side every load (deletions + edits + additions)
  · → instant effect; no need to re-run merge.py
```

The dashboard never reads `pending.json`. It's purely an admin staging area.

### Forge Global secondary-market overlay

For the Top 50 unicorns by valuation, the weekly Action also runs `scrape_forge.py`,
which fetches `forgeglobal.com/<slug>_stock/` for each mapped company and extracts:

- **Forge Price** — Forge's daily proprietary indicative price per share
- **Forge Price Valuation** — implied company valuation at that price

Both are written to `data/forge_data.json` (committed). The dashboard renders this
overlay above each bubble's original valuation label, with **red** when the new value
is ≥ the previous run's, and **green** when it's < the previous run's. The slug map is
in `data/forge_slugs.json`; add to `manual_overrides` there to extend coverage to
companies the auto-matcher missed.

## Deploy in 5 minutes (GitHub Pages)

1. Create a new GitHub repo (public free; private needs Pro).
2. Copy this `deploy/` folder into the repo root and push.
3. Repository → Settings → Pages → Source = `Deploy from a branch`, Branch = `main` / `(root)`.
4. Wait ~1 minute, visit `https://<user>.github.io/<repo>/`.
5. Verify the Action: Actions tab → "Update unicorn data" → Run workflow.

## How edits propagate to the dashboard

**Live overrides** (the fast path):

1. Open `admin.html` (locally via http server, or the deployed URL).
2. Edit / Add / Delete any row. Or click **Approve** on a pending update.
3. Click **Export overrides ↓** — downloads `overrides.json`.
4. Replace `data/overrides.json` in the repo with the downloaded file.
5. `git commit -am "data: admin overrides" && git push`.
6. The dashboard picks up the change on next page load (hard-refresh if cached).

**No `merge.py` needed for everyday edits.** That script exists only for periodic "compaction" — folding all approved overrides into a fresh `unicorns.json` snapshot. Run it occasionally to keep `overrides.json` from ballooning:

```bash
python scripts/merge.py
git commit -am "data: compact overrides into canonical"
```

## Pending-review workflow (auto-scraped changes)

Every Monday at 08:00 UTC, the GitHub Action:

1. Re-scrapes Wikipedia → `wiki_raw.json` (committed)
2. Runs `compute_pending.py` → `pending.json` (committed)
3. Does **NOT** modify `unicorns.json`. The dashboard renders unchanged.

When you next open `admin.html`, you'll see a banner:

```
📥 Pending updates from latest data refresh
   3 new · 7 modified · 1 removed
   [Review & approve]
```

Click **Review & approve** to expand a panel showing:

- 🆕 **New companies** — added to Wikipedia since last refresh. Click ✓ Approve to add to `overrides.additions`.
- 🔄 **Modifications** — fields that changed (valuation_m, country, founders, wikiLink). Each row shows old → new. Approve copies the new value into `overrides.edits[company]`.
- ❌ **Proposed removals** — companies Wikipedia removed (acquired, IPO'd, downgraded). Approve adds to `overrides.deletions`.

Each section has a **Bulk Approve** button. Reject buttons just dismiss locally — the entry will reappear on next refresh if Wikipedia still differs.

After approving, click **Export overrides ↓**, commit `overrides.json`, push. Done.

## Override file format

```json
{
  "edits": {
    "ByteDance": { "valuation_m": 350000, "valuation_str": "$350B" }
  },
  "additions": [
    { "company": "NewCo", "valuation_m": 5000, "country": "China",
      "industry": "AI/ML", "companyZh": "新公司", "pinyinInitial": "X" }
  ],
  "deletions": ["DefunctCo"]
}
```

Keys in `edits` are the **English company name** as it appears in `unicorns.json`. Only changed fields need to be specified.

## Manual maintenance: Chinese names

`data/chinese_names.json` is the master mapping. The format:

```json
{
  "ByteDance":  { "zh": "字节跳动", "pinyin": "Z" },
  "DJI":        { "zh": "大疆",     "pinyin": "D" }
}
```

Edit this file when you want a permanent Chinese name (survives even after you compact overrides into canonical). The weekly Action does NOT modify this file.

## Common operations

**One-off local refresh:**
```bash
cd deploy
pip install -r scripts/requirements.txt
python scripts/scrape_wikipedia.py
python scripts/compute_pending.py
python3 -m http.server 8080         # then open http://localhost:8080/admin.html
```

**Force-trigger the live update:** Actions tab → "Update unicorn data" → Run workflow.

**Compact overrides into canonical** (do this every few months to keep `overrides.json` small):
```bash
python scripts/merge.py
git commit -am "data: compact overrides into unicorns.json"
git push
```
After compaction, you can manually empty `overrides.json` (`{"edits":{},"additions":[],"deletions":[]}`) since those edits are now baked in.

**Roll back a bad change:** `git revert <commit>` of the offending commit.

## Watch-outs

- The merge logic in `compute_pending.py` matches companies by **normalized English name**. If Wikipedia renames a company (e.g. "Cognition" → "Cognition Labs"), it'll show as a "removal" of the old name + "addition" of the new. Approve the addition and the removal — or clean up via admin manual edit.
- Modifications only fire on these fields: `valuation_m`, `country`, `founders`, `wikiLink`. Other field drift (description tweaks, etc.) is ignored to keep the review queue manageable.
- Wikipedia's table structure occasionally changes. The scraper finds the table whose header contains "Company / Valuation / Founder" — robust to row reordering. If schema diverges, Action fails loudly.

## Tech stack

Vanilla HTML + Canvas2D + D3 v7. No build step. Python 3.11 + requests + BeautifulSoup for scraping. GitHub Actions for cron. Total dependencies: ~6 packages.
