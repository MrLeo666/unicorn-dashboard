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
│   ├── notice_secondary.json   # ✱ Notice Weekly Update 二级报价 (email pipeline, weekly)
│   ├── news.json               # ✱ 🆕 Google News RSS：Top 100 每家最新 3 条 (weekly)
│   ├── candidates.json         # ✱ 🆕 候选发现：new_candidates + valuation_jumps (weekly)
│   ├── history/
│   │   └── valuation_history.jsonl  # ✱ 🆕 估值历史快照（每周一行 JSONL，幂等追加）
│   ├── lqp_overlay.json        # 🆕 ✱ LQP 专属层 (sector_map / ratings / pipeline / watchlist /
│   │                           #         diligence / client / exclusions / aliases / notes)
│   ├── seed_pitchbook.json     # frozen PitchBook-only rows from the initial seed
│   └── chinese_names.json      # manual mapping: English → {zh, pinyin}
├── scripts/
│   ├── _lib.py                 # shared merge / diff helpers
│   ├── scrape_wikipedia.py     # HTTP + BeautifulSoup
│   ├── scrape_forge.py         # weekly Forge Global secondary-market scrape (Top 100)
│   ├── scrape_notice_email.py  # Notice Weekly Update 邮件抓取 (Gmail API)
│   ├── scrape_news.py          # 🆕 weekly Google News RSS scrape (Top 100, 每家 3 条)
│   ├── discover_candidates.py  # 🆕 候选发现引擎：新独角兽 + 估值跳变 → candidates.json
│   ├── snapshot_history.py     # 🆕 每周估值快照 → history/valuation_history.jsonl (幂等)
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

  parallel data products (同一次 Action，see "监控与发现闭环"):
    scrape_forge / scrape_notice_email / scrape_news / discover_candidates /
    snapshot_history → forge_data.json · notice_secondary.json · news.json ·
    candidates.json · history/valuation_history.jsonl

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

## 监控与发现闭环 (Monitoring & Discovery, 2026-08-17/18)

在 Wikipedia/Forge/Notice 之上叠了一整套"新闻 → 发现 → 审核 → 历史"管道，全部由
weekly Action 驱动，产出全部落在 `data/` 下走同一个 auto-commit。

### 📰 新闻管道 → `data/news.json`

`scrape_news.py` 每周对**估值 Top 100** 的公司逐一请求 Google News RSS
（默认查询 `"公司名" when:180d`，脚本顶部的 `NEWS_QUERY_OVERRIDES` 表处理
Scale/Ripple/Bolt/Block/Discord 等歧义名），每家保留最新 3 条
（title / url / source / date，标题剥离尾部来源后缀）。请求间隔 0.3s、单请求
15s 超时；某家失败时保留上一周的数据。

看板消费方式：**点击气泡**弹出详情面板，底部「最新新闻」区块列出这 3 条
（可点击新标签打开）；无新闻显示「暂无近期新闻」，文件缺失显示「新闻数据尚未生成」。

### 🦄 候选发现引擎 → `data/candidates.json`

`discover_candidates.py` 做两件事，输出**独立于 `pending.json`**（后者每周被
`compute_pending.py` 整体重写，不能混）：

- **新独角兽候选**：跑 5 条通用融资查询（3 英文：`raises/valued at/new unicorn`；
  2 中文：`融资 独角兽 估值` / `完成 轮融资 估值`，均 `when:30d`），用保守正则从标题
  提取「公司 + 估值」对（亿美元 ×100M、billion ×1000M 统一换算成 $M），
  剥离描述性前缀（"Defense Startup X" → "X"）、截断逗号同位语、停用词过滤，
  与有效基座（unicorns + overrides）去重。**宁缺毋滥**：提不出干净配对的标题直接丢弃。
- **存量估值跳变**：扫 `news.json` 已有公司标题里的估值句式，与当前有效估值
  差异 >20% 即记为 `valuation_jumps` 条目。

### ✅ admin.html 候选审核

Review 面板在原有 pending 三区之外新增两区（**没有 Approve-all**，启发式数据
必须逐条人工确认）：

- 🦄 **候选新公司**（紫边）：✓ → 预填 `STATE.additions`（company + valuation，
  country/industry 留空待 Edit 补全）；✗ 仅本地忽略。
- 📈 **估值跳变**（橙边）：当前 → 检测估值（+xx%）+ 标题链接；✓ → 写
  `STATE.edits[company].valuation_m`；✗ 仅本地忽略。
- 已在 STATE 里的候选按钮置灰「已加入」；批准后照常走 **Publish** 推 GitHub，
  无新增发布路径。Reject 不持久化（candidates.json 每周重生成，刷新后会复现）。

### 📈 估值历史 + Movers 视图

`snapshot_history.py` 每周把三源估值（有效基座 wiki_m / Forge forge_m+price /
Notice notice_m+price，按公司英文名汇总）追加一行到
`data/history/valuation_history.jsonl`（JSONL，**幂等**：同日重跑替换当天快照）。

index.html 新增第 4 个视图 pill **Movers**：最新快照 vs 约 4 周前（没有则取最早）
快照的估值变化榜——同公司同源对比（优先级 forge > notice > wiki，徽章 F/N/W），
按 |变化%| 降序取前 50，顶部摘要卡显示期间上涨/下跌家数与对比区间。
**涨跌配色沿用 Forge 惯例：涨红（#ff5a5a）跌绿（#3edc81）**。
快照不足两个时整个视图显示「数据积累中，下周开始出榜」。

### 🟡 跳变公司黄圈提醒

`candidates.json` 的 valuation_jumps 公司（必须在看板上有气泡）在气泡视图中叠加
**#f5c842 金色外圈**（复用既有 admin 高亮 / radar 短名单的色值体系），点击打开详情
面板后消失。已看状态存 localStorage（`unicornSeenCandidates_v1`），指纹 =
`generated_at + company`——**只针对当前这一批**，下周 candidates.json 重生成后
仍是候选的公司自动重新亮圈。new_candidates 未入基座前没有气泡，不画圈。
Table 视图里这些公司名前同步显示金色圆点，点击行查看后消失。

### 🖱 index.html 交互变更

- **hover 不再弹 tooltip**：悬停只冻结/高亮气泡，无任何浮层。
- **点击气泡**在**点击位置附近**弹出统一详情面板（默认右下偏移 12px，视口边界
  自动翻转），内容 = 公司全部信息（估值/国家/行业/投资人/创始人/简介）+
  Forge/Notice 二级市场分区 + Wiki/Google 外链 + 最新新闻区块。
- 关闭：面板 × 按钮 / Esc / 点空白 / 切换视图；点其他气泡切换面板。

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

Every Monday at 08:00 UTC, the GitHub Action runs the full pipeline:

1. `scrape_wikipedia.py` → `wiki_raw.json` (committed)
2. `compute_pending.py` → `pending.json` (committed) — does **NOT** modify
   `unicorns.json`; the dashboard renders unchanged
3. `scrape_forge.py` → `forge_data.json` (continue-on-error)
4. `scrape_notice_email.py` → `notice_secondary.json` (continue-on-error; needs Gmail secrets)
5. `scrape_news.py` → `news.json` (continue-on-error)
6. `discover_candidates.py` → `candidates.json` (continue-on-error)
7. `snapshot_history.py` → `history/valuation_history.jsonl` (hard-fail; local-only, no network)
8. Commit & push everything under `data/` that changed

Steps 3–6 are `continue-on-error` so a single external-source failure never blocks
the rest of the refresh or the commit.

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
