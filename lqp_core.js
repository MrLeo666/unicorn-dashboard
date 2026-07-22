/* ================================================================== *
 *  LQP CORE — 统一数据基座 (v2 · Web Module)
 * ------------------------------------------------------------------ *
 *  v1.0 → v2 变更摘要 (2026-05-19)
 *  - 不再硬编码 SEED_COMPANIES。数据源改为 deploy/data 三件套：
 *      · unicorns.json     —— Wikipedia 周更基线 (~400 家)
 *      · overrides.json    —— admin.html 导出的人工修正 (实时叠加)
 *      · forge_data.json   —— Forge Global 二级市场报价 (Top 100, 周更)
 *    并叠加 LQP 专属层：
 *      · lqp_overlay.json  —— sector映射 / 五维评分 / pipeline / watchlist /
 *                              排除名单 / 尽调区块 / 客户区块
 *  - 评分维度 v1.0 锁定 (待决①结论: 维持五维不变)
 *  - 雷达、尽调台、客户门户均从此模块 import，禁止绕开。
 *
 *  数据流：
 *     unicorns + overrides → 基础公司表
 *           ↓ 过滤 (US/CN, ≥$5B, 排除蚂蚁/陆金所/微众)
 *           ↓ 叠加 lqp_overlay (sector映射 + ratings + 区块)
 *           ↓ 叠加 forge_data (二级市场报价)
 *     → 统一 LQP 公司记录
 * ================================================================== */

export const SCHEMA_VERSION = 2;
export const TODAY = new Date();

/* ------------------------------------------------------------------ *
 *  评分维度 —— v1.0 锁定 (2026-05-19)
 *  待决①结论：维持五维不变。后续工具读取此处标签，禁止私改。
 * ------------------------------------------------------------------ */
export const RADAR_DIMS = Object.freeze([
  { k: "sector",    label: "赛道信心",   hint: "行业景气与 LQP 配置契合度" },
  { k: "quality",   label: "质地·护城河", hint: "竞争壁垒、收入质量、管理层" },
  { k: "liquidity", label: "二级流动性", hint: "老股可交易性 / 受让限制" },
  { k: "ipo",       label: "IPO 能见度", hint: "上市路径与时间窗清晰度" },
  { k: "entry",     label: "进场吸引力", hint: "当前估值相对基本面的性价比" },
]);

/* 受控词表 —— 所有工具共用 */
export const GEOS = { US: "美国", CN: "中国", OTHER: "其他" };

export const SECTORS = [
  "AI·基础模型", "AI·基建与数据", "AI·应用与垂直", "航天科技", "国防科技",
  "金融科技", "机器人", "自动驾驶", "互联网平台", "电商与消费", "医疗科技",
  "半导体", "加密资产", "网络安全", "其他",
];

export const SECTOR_COLOR = {
  "AI·基础模型": "#d4a35a", "AI·基建与数据": "#5fa8a0", "AI·应用与垂直": "#8aa4d6",
  "航天科技": "#6f8fae", "国防科技": "#c98b6b", "金融科技": "#9d8ec0",
  "机器人": "#c47f93", "自动驾驶": "#7fae8a", "互联网平台": "#cdb06a",
  "电商与消费": "#d39b73", "医疗科技": "#6fae9e", "半导体": "#9aa0ab",
  "加密资产": "#a37c5b", "网络安全": "#789ab5", "其他": "#8a8f99",
};

export const PIPELINE = {
  sourced:    "已收录",
  screening:  "筛选中",
  diligence:  "尽调中",
  ic:         "投决会",
  approved:   "已通过",
  invested:   "已投资",
  passed:     "已放弃",
  monitoring: "组合监控",
};

/* ------------------------------------------------------------------ *
 *  Wikipedia industry → LQP sector 的兜底映射
 *  优先级：lqp_overlay.sector_map[company] > 此兜底
 * ------------------------------------------------------------------ */
const INDUSTRY_FALLBACK = {
  "AI/ML": "AI·应用与垂直",   // 兜底标签；具体公司可在 overlay 中重指 "AI·基础模型" 等
  "Robotics": "机器人",
  "Aerospace/Defense": "航天科技",   // 默认航天；defense 类需 overlay 显式指为 国防科技
  "Fintech": "金融科技",
  "Crypto/Web3": "加密资产",
  "Cybersecurity": "网络安全",
  "Semiconductors": "半导体",
  "Biotech/Health": "医疗科技",
  "SaaS/Enterprise": "互联网平台",
  "Consumer": "电商与消费",
  "Climate/Energy": "其他",
  "Other": "其他",
};

/* ------------------------------------------------------------------ *
 *  通用工具函数
 * ------------------------------------------------------------------ */
export const daysSince = (d) =>
  d ? Math.round((TODAY - new Date(d)) / 864e5) : 9999;

export const fmtVal = (vBillion) =>
  vBillion >= 1000
    ? `$${(vBillion / 1000).toFixed(2).replace(/\.?0+$/, "")}T`
    : `$${vBillion}B`;

export function radarScore(company, weights) {
  if (!company.radar?.ratings) return 0;
  const sum = RADAR_DIMS.reduce((s, d) => s + (weights[d.k] || 0), 0) || 1;
  const v = RADAR_DIMS.reduce(
    (s, d) => s + (weights[d.k] || 0) * (company.radar.ratings[d.k] || 0), 0
  ) / sum;
  return Math.round((v / 5) * 100);
}

/* 数据时效 (基于 unicorn_as_of) */
export function freshness(c) {
  if (!c.valuationDate) return { label: "待核实", tone: "warn" };
  const d = daysSince(c.valuationDate);
  if (d <= 180)  return { label: "已核实", tone: "ok" };
  if (d <= 365)  return { label: "需复核", tone: "warn" };
  return { label: "过时", tone: "stale" };
}

/* ------------------------------------------------------------------ *
 *  地缘归属 —— 严格按 LQP "聚焦美/中" 原则识别
 * ------------------------------------------------------------------ */
export function detectGeo(country) {
  if (!country) return null;
  if (country.includes("United States")) return "US";
  const c = country.trim();
  if (c === "China" || c === "Hong Kong / China") return "CN";
  return null; // 非美/中 → 不进入 LQP 雷达
}

/* "Unicorn As Of" 字符串 → YYYY-MM 标准化 */
const MONTHS = ["january","february","march","april","may","june",
                "july","august","september","october","november","december"];
const MONTHS_ABBR = ["jan","feb","mar","apr","may","jun",
                     "jul","aug","sep","oct","nov","dec"];

export function parseUnicornDate(s) {
  if (!s) return "";
  const t = s.trim().toLowerCase();
  // "February 2026", "Dec 2025", "May 2024"
  const m = t.match(/^([a-z]+)\s+(\d{4})$/i);
  if (!m) return "";
  let mi = MONTHS.indexOf(m[1]);
  if (mi < 0) mi = MONTHS_ABBR.indexOf(m[1].slice(0,3));
  if (mi < 0) return "";
  return `${m[2]}-${String(mi+1).padStart(2,"0")}-01`;
}

/* ------------------------------------------------------------------ *
 *  Overlay 合并 (复刻 admin.html / index.html 的逻辑)
 *  raw + overrides → 基础公司表
 *  归档（已 IPO）公司在 mergeOverrides 阶段就被剔除 —— LQP 工具链
 *  关注的是"独角兽"（一级市场未上市），已上市的不再属于雷达 / 尽调范围。
 * ------------------------------------------------------------------ */
function mergeOverrides(raw, overrides) {
  const edits = overrides.edits || {};
  const adds = overrides.additions || [];
  const dels = new Set(overrides.deletions || []);
  const archived = overrides.archived || {};
  const isArch = (n) => Object.prototype.hasOwnProperty.call(archived, n);

  const out = [];
  for (const r of raw) {
    if (dels.has(r.company)) continue;
    if (isArch(r.company)) continue;  // 归档公司不进入 LQP 工具链
    if (edits[r.company]) {
      const merged = Object.assign({}, r, edits[r.company]);
      if (edits[r.company].valuation_m != null && !edits[r.company].valuation_str) {
        merged.valuation_str = "$" + (merged.valuation_m / 1000) + "B";
      }
      out.push(merged);
    } else {
      out.push(r);
    }
  }
  const seen = new Set(out.map(c => c.company));
  for (const a of adds) {
    if (!a.company || seen.has(a.company)) continue;
    if (isArch(a.company)) continue;
    seen.add(a.company);
    out.push(a);
  }
  return out;
}

/* ------------------------------------------------------------------ *
 *  fetch 三件套 + lqp_overlay
 * ------------------------------------------------------------------ */
const BUST = () => "?t=" + Date.now();

export async function fetchSources(basePath = "./data") {
  const safe = async (url, fallback) => {
    try {
      const r = await fetch(url + BUST());
      if (!r.ok) return fallback;
      return await r.json();
    } catch { return fallback; }
  };
  const [unicorns, overrides, forge, notice, overlay] = await Promise.all([
    safe(`${basePath}/unicorns.json`, []),
    safe(`${basePath}/overrides.json`, { edits: {}, additions: [], deletions: [] }),
    safe(`${basePath}/forge_data.json`, { records: {} }),
    safe(`${basePath}/notice_secondary.json`, { records: {} }),
    safe(`${basePath}/lqp_overlay.json`, {
      version: 1, exclusions: [], aliases: {}, sector_map: {}, ratings: {},
      pipeline: {}, watchlist: [], diligence: {}, client: {}, notes: {}
    }),
  ]);
  return { unicorns, overrides, forge, notice, overlay };
}

/* ------------------------------------------------------------------ *
 *  主聚合函数 —— 输出统一 LQP 公司记录
 * ------------------------------------------------------------------ */
/* ------------------------------------------------------------------ *
 *  跨源最高估值选择 —— Wikipedia / Forge / Notice 三源比较
 *  返回 { value, source, pps, date, label } —— value 是 $B；
 *  若所有源都无估值返回 null。
 * ------------------------------------------------------------------ */
export function pickBestValuation({ wiki, forge, notice }) {
  const candidates = [];
  if (wiki && wiki.value != null && wiki.value > 0) {
    candidates.push({ value: wiki.value, source: "wiki", pps: null, date: wiki.date || null, label: "Wikipedia / Admin" });
  }
  if (forge && forge.valuation != null && forge.valuation > 0) {
    candidates.push({
      value: forge.valuation, source: "forge",
      pps: forge.price ?? null,
      date: forge.fetchedAt?.slice(0,10) || null,
      label: "Forge Global",
    });
  }
  if (notice && notice.valuation_b != null && notice.valuation_b > 0) {
    candidates.push({
      value: notice.valuation_b, source: "notice",
      pps: notice.pps ?? null,
      date: notice.date || null,
      label: "Notice Weekly",
    });
  }
  if (!candidates.length) return null;
  // 取估值最大那条
  candidates.sort((a, b) => b.value - a.value);
  return candidates[0];
}

export function buildCompanies({ unicorns, overrides, forge, notice, overlay }) {
  // 1. 基线合并
  const merged = mergeOverrides(unicorns, overrides);

  // 2. 过滤：必须 美/中 + 估值 ≥ $5B + 不在排除名单 + 不是别名重复
  const exclusions = (overlay.exclusions || []).map(s => s.toLowerCase());
  const isExcluded = (name) => exclusions.some(e =>
    e && name.toLowerCase().includes(e)
  );
  // aliases: { "Anysphere": "Cursor" } —— 当主公司存在时，别名条目被吸收
  const aliases = overlay.aliases || {};
  const presentMain = new Set(merged.map(r => r.company));
  const isAlias = (name) => aliases[name] && presentMain.has(aliases[name]);

  const records = [];
  for (const r of merged) {
    const geo = detectGeo(r.country);
    if (!geo) continue;
    const valBn = (r.valuation_m || 0) / 1000;
    if (valBn < 5) continue;
    if (isExcluded(r.company)) continue;
    if (isAlias(r.company)) continue;  // 同公司另一名称已在表中

    // 3. sector 映射：overlay 优先
    const sector = overlay.sector_map?.[r.company]
                 || INDUSTRY_FALLBACK[r.industry]
                 || "其他";

    // 4. 评分：overlay.ratings[company] 优先，否则默认 3/3/3/3/3
    const ratings = overlay.ratings?.[r.company]
                  || { sector: 3, quality: 3, liquidity: 3, ipo: 3, entry: 3 };

    // 5. pipeline / watch
    const pipeline = overlay.pipeline?.[r.company] || "sourced";
    const watch = (overlay.watchlist || []).includes(r.company);

    // 6. Forge 数据
    const fg = forge.records?.[r.company] || null;

    // 7. 整体记录
    records.push({
      schemaVersion: SCHEMA_VERSION,

      // —— 身份 ——
      id:        r.id || ("lqp-" + r.company.toLowerCase().replace(/\s+/g,"-")),
      name:      r.company,
      nameZh:    r.companyZh || "",
      pinyin:    r.pinyinInitial || "",
      sector,
      industryRaw: r.industry,
      geo,
      country:   r.country,
      pipeline,

      // —— 估值层 ——
      valuation:     valBn,
      valuationStr:  r.valuation_str || fmtVal(valBn),
      lastRound:     r.unicorn_as_of || "",
      lastRoundDate: parseUnicornDate(r.unicorn_as_of),
      lastRoundSize: r.vc_raised || "",
      leads:         r.lead_investors || "",
      founders:      r.founders || "",

      // —— 来源层 (强制) ——
      valuationSource: r.source === "wiki" ? "Wikipedia"
                       : r.source === "pb" ? "PitchBook"
                       : r.source === "admin" ? "Admin 手动" : (r.source || ""),
      valuationDate: parseUnicornDate(r.unicorn_as_of),
      verified: !!(r.unicorn_as_of && r.source),
      wikiLink: r.wikiLink || "",
      description: r.description || "",

      // —— 区块层：雷达 ——
      radar: { ratings, watch },

      // —— 区块层：尽调 ——
      diligence: overlay.diligence?.[r.company] || {
        icMemoUrl: "", analyst: "", decision: "",
        valuationScenarios: null, keyRisks: [], updatedAt: ""
      },

      // —— 区块层：客户 ——
      client: overlay.client?.[r.company] || {
        spvName: "", heldByClients: [], teaserUrl: "",
        tradable: false, updatedAt: ""
      },

      // —— 二级市场 (Forge) ——
      forge: fg ? {
        price: fg.forge_price,
        valuation: fg.forge_valuation_m != null ? fg.forge_valuation_m / 1000 : null,
        prevPrice: fg.prev_forge_price,
        prevValuation: fg.prev_forge_valuation_m != null ? fg.prev_forge_valuation_m / 1000 : null,
        fetchedAt: fg.fetched_at,
      } : null,

      // —— Notice 邮件源（Weekly Update，每周一封覆盖 watchlist 全集）——
      notice: (notice?.records?.[r.company]) ? {
        pps:              notice.records[r.company].pps              ?? null,
        ppsChange:        notice.records[r.company].pps_change       ?? null,
        valuation_b:      notice.records[r.company].valuation_b      ?? null,
        newIndications:   notice.records[r.company].new_indications  ?? null,
        date:             notice.records[r.company].date             || null,
        history:          notice.records[r.company].history          || [],
      } : null,

      // —— LQP 备注 ——
      notes: overlay.notes?.[r.company] || "",
    });
  }

  // —— 第二轮：给每条记录计算跨源最高估值 ——
  // 三源比较：Wikipedia/Admin override (records[i].valuation) · Forge · Notice
  // 显示规则：dashboard / 气球看板 / 雷达表 / 一页纸都以 bestValuation 为准
  for (const c of records) {
    c.bestValuation = pickBestValuation({
      wiki: { value: c.valuation, date: c.lastRoundDate },
      forge: c.forge,
      notice: c.notice,
    });
  }

  // 估值降序
  records.sort((a, b) => b.valuation - a.valuation);
  return records;
}

/* ------------------------------------------------------------------ *
 *  便捷入口：一次性加载并构建
 * ------------------------------------------------------------------ */
export async function loadLqp(basePath = "./data") {
  const sources = await fetchSources(basePath);
  return {
    companies: buildCompanies(sources),
    sources,
  };
}

/* ------------------------------------------------------------------ *
 *  入围规则 (待决①锁定：维持当前)
 *    valuation ≥ $5B  AND  综合评分 ≥ 65  AND  geo 属 US/CN
 *  注：US/CN 与 ≥$5B 在 buildCompanies 已过滤，此处只看分数
 * ------------------------------------------------------------------ */
export const SHORTLIST_RULES = Object.freeze({
  minValuation: 5,
  minScore: 65,
  geos: ["US", "CN"],
});

export function isShortlisted(company, weights) {
  const score = radarScore(company, weights);
  return company.valuation >= SHORTLIST_RULES.minValuation
      && score >= SHORTLIST_RULES.minScore
      && SHORTLIST_RULES.geos.includes(company.geo);
}

/* ------------------------------------------------------------------ *
 *  默认雷达权重 —— 工具的本地配置，不进底座
 * ------------------------------------------------------------------ */
export const DEFAULT_WEIGHTS = Object.freeze({
  sector: 20, quality: 25, liquidity: 15, ipo: 20, entry: 20,
});
