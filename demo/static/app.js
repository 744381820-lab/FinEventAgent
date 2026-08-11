/* FinEventAgent · Kimi 式对话流 + 富详情渲染 + 局部重算 */

const $ = (id) => document.getElementById(id);
const C = {
  brand: "#3B82F6", brandDark: "#2563EB",
  up: "#EF4444", down: "#22C55E", warn: "#F59E0B", violet: "#8B5CF6",
  t1: "#0F172A", t2: "#475569", t3: "#94A3B8", line: "#E2E8F0", page: "#F8FAFC",
};

let state = {
  sessionId: null,
  steps: [],
  result: {},
  layerStatus: {},
  dataStep: null,
  charts: {},
  activeCmd: null,
  refining: false,
  snapshot: null,
};

const fmtInt = (n) => Number(n).toLocaleString("zh-CN");
const fmtPct = (x, sign) => `${sign && x > 0 ? "+" : ""}${Number(x).toFixed(2)}%`;
const arrow = (x) => (x > 0 ? "▲" : x < 0 ? "▼" : "—");
function fmtYiText(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  v = Number(v);
  return v >= 10000 ? (v / 10000).toFixed(2) + "万亿" : fmtInt(Math.round(v)) + "亿";
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const TEMPLATES = [
  { company: "宁德时代", code: "sz300750", type: "财报披露", desc: "宁德时代2025H1营收同比下降12%，但海外业务收入占比从18%提升至32%。毛利率逆势提升2.3个百分点至26.1%。" },
  { company: "比亚迪", code: "sz002594", type: "其他", desc: "比亚迪公告高管变动，CTO 卸任，由内部技术委员会接管研发决策。" },
  { company: "隆基绿能", code: "sh601012", type: "其他", desc: "隆基绿能宣布暂停部分二线基地磷酸铁锂产线扩张，同时增加海外工厂投资。" },
  { company: "某医药公司", code: "sh600000", type: "监管处罚", desc: "某医药公司收到证监会立案调查通知，涉嫌信息披露违规。" },
];

function fillTemplate(i) {
  const t = TEMPLATES[i];
  $("companyName").value = t.company;
  $("stockCode").value = t.code;
  $("eventType").value = t.type;
  $("eventInput").value = t.desc;
  $("eventInput").focus();
}
function toggleParamPanel() { $("paramPanel").classList.toggle("hidden"); }

/* ---------- 初始化 ---------- */
(async function init() {
  try {
    const h = await (await fetch("/api/health")).json();
    const modeLabel = { live: "实时模式", hybrid: "混合模式", demo: "演示模式" }[h.mode] || h.mode;
    const mb = $("modeBadge");
    mb.textContent = modeLabel;
    if (h.mode === "demo") { mb.style.background = "#F1F5F9"; mb.style.color = C.t3; }
    else { mb.style.background = "#F0FDF4"; mb.style.color = "#16A34A"; }
    if (h.jinmen_configured) {
      const m = $("mcpBadge");
      m.classList.remove("hidden");
      m.textContent = "进门 MCP 已接入";
    }
  } catch {
    const mb = $("modeBadge");
    mb.textContent = "后端未连接";
    mb.style.background = "#FEF2F2"; mb.style.color = C.up;
  }
})();

/* ---------- 步骤流 ---------- */
const LAYER_META = {
  l1: { title: "L1 事实核验与事件结构化", color: "#3B82F6", icon: "1" },
  data: { title: "进门 MCP 数据采集与计算", color: "#64748B", icon: "D" },
  l2: { title: "L2 基本面 · 舆情 · 产业链", color: "#8B5CF6", icon: "2" },
  l3: { title: "L3 估值影响量化测算", color: "#F59E0B", icon: "3" },
  l4: { title: "L4 持仓策略与操作建议", color: "#22C55E", icon: "4" },
};

function disposeCharts() {
  Object.values(state.charts).forEach((c) => { try { c.dispose(); } catch {} });
  state.charts = {};
}

function renderStepFlow() {
  const box = $("stepFlow");
  const order = ["l1", "data", "l2", "l3", "l4"];
  disposeCharts();
  box.innerHTML = order.map((key) => {
    const meta = LAYER_META[key];
    const st = key === "data"
      ? (state.dataStep?.status || "pending")
      : (state.layerStatus[key] || "pending");
    // data 步未开始时隐藏
    if (key === "data" && !state.dataStep && st === "pending") return "";
    const iconContent = st === "done" ? "✓" : meta.icon;
    const statusText = st === "done" ? "已完成" : st === "running" ? "执行中…" : "待执行";
    const statusColor = st === "done" ? C.down : st === "running" ? C.brand : C.t3;

    let body = "";
    if (st === "running") {
      body = `<div class="mt-3 pt-3 border-t border-line">
        <div class="skeleton h-4 w-3/4 mb-2"></div>
        <div class="skeleton h-4 w-1/2 mb-2"></div>
        <div class="skeleton h-24 w-full"></div>
      </div>`;
    } else if (st === "done") {
      body = `<div class="mt-3 pt-3 border-t border-line space-y-4">${renderLayerBody(key)}</div>`;
    } else if (key === "data" && state.dataStep?.message) {
      body = `<p class="text-[11px] text-t3 mt-2">${esc(state.dataStep.message)}</p>`;
    }

    return `<div class="card p-4 layer-card ${key}" id="card-${key}">
      <div class="layer-header mb-0">
        <div class="layer-icon ${st}" style="background:${st === "pending" ? "#E2E8F0" : meta.color}">${iconContent}</div>
        <div class="flex-1 min-w-0">
          <p class="text-sm font-medium text-t1">${meta.title}</p>
          <p class="text-[11px]" style="color:${statusColor}">${statusText}${key === "data" && state.dataStep?.message && st === "done" ? " · " + esc(state.dataStep.message) : ""}</p>
        </div>
      </div>
      ${body}
    </div>`;
  }).join("");

  // 延迟初始化图表，等 DOM 就绪
  requestAnimationFrame(() => {
    if (state.layerStatus.l2 === "done") initSentimentCharts();
    if (state.layerStatus.l3 === "done") initValuationCharts();
    if (state.layerStatus.l2 === "done") initImpactChart();
  });
}

/* ========== 各层富详情 ========== */
function renderLayerBody(key) {
  if (key === "l1") return renderL1();
  if (key === "data") return renderData();
  if (key === "l2") return renderL2();
  if (key === "l3") return renderL3();
  if (key === "l4") return renderL4();
  return "";
}

function renderL1() {
  const r = state.result;
  const f = r.fact_check || {};
  const p = r.event_profile || {};
  const map = { verified: ["已核验", C.brand], partial: ["部分吻合", C.warn], unverified: ["未证实", C.up] };
  const [st, sc] = map[f.status] || [f.status || "—", C.t3];

  const metrics = (p.metrics_mentioned || []).map((m) =>
    `<span class="src-tag">${esc(m.name)} ${esc(m.value || "")} ${esc(m.direction || "")}</span>`
  ).join(" ") || '<span class="text-t3">无</span>';

  return `
    <div class="flex items-center gap-3 flex-wrap">
      <span class="pill" style="background:${sc}18;color:${sc}">${st}</span>
      <span class="text-xs text-t3">置信度 <b class="font-num text-t1">${f.confidence ?? "—"}%</b></span>
      <span class="text-xs text-t3">事件子类 <b class="text-t1">${esc(p.event_subtype || "—")}</b></span>
      <span class="text-xs text-t3">抽取器 <b class="text-t1">${esc(p.extractor || "—")}</b></span>
    </div>
    ${p.one_line ? `<p class="text-sm text-t1 bg-page rounded-lg p-3">${esc(p.one_line)}</p>` : ""}
    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
      <div class="bg-page rounded-lg p-3">
        <p class="text-[11px] text-t3 mb-2">已吻合（${(f.matched_facts || []).length}）</p>
        <ul class="space-y-1 text-xs">${(f.matched_facts || []).map((x) => `<li class="text-t1">· ${esc(x)}</li>`).join("") || '<li class="text-t3">无</li>'}</ul>
      </div>
      <div class="bg-page rounded-lg p-3">
        <p class="text-[11px] text-t3 mb-2">存在出入（${(f.discrepancies || []).length}）</p>
        <ul class="space-y-1 text-xs">${(f.discrepancies || []).map((x) => `<li class="text-t1">· ${esc(x)}</li>`).join("") || '<li class="text-t3">无</li>'}</ul>
      </div>
    </div>
    <div>
      <p class="text-[11px] text-t3 mb-1">结构化抽取 · 提及指标</p>
      <div class="flex flex-wrap gap-1.5">${metrics}</div>
    </div>
    ${(p.keywords || []).length ? `<div>
      <p class="text-[11px] text-t3 mb-1">关键词</p>
      <div class="flex flex-wrap gap-1.5">${p.keywords.map((k) => `<span class="src-tag">${esc(k)}</span>`).join("")}</div>
    </div>` : ""}
    <div class="flex items-center gap-3 text-xs">
      <span class="text-t3">${esc(f.basis || "")}</span>
      <button onclick="showTrace('l1')" class="text-brand hover:underline shrink-0">查看官方来源 →</button>
    </div>`;
}

function renderData() {
  const fin = state.result.financial_context || {};
  const metrics = fin.metrics || {};
  const derived = state.result.derived_metrics || [];
  const entries = Object.entries(metrics).slice(0, 16);
  const sources = new Set(Object.values(metrics).map((m) => m.source).filter(Boolean));

  return `
    <div class="flex items-center gap-2 flex-wrap text-xs mb-1">
      <span class="pill" style="background:#EFF6FF;color:#3B82F6">进门 MCP</span>
      <span class="text-t3">采集 <b class="font-num text-t1">${Object.keys(metrics).length}</b> 项底层指标</span>
      <span class="text-t3">来源：${[...sources].slice(0, 3).map(esc).join(" · ") || "—"}</span>
    </div>
    ${fin.note ? `<p class="text-[11px] text-t3">${esc(fin.note)}</p>` : ""}
    <div class="bg-page rounded-lg p-3 max-h-64 overflow-y-auto">
      ${entries.length ? entries.map(([k, m]) => `
        <div class="metric-row">
          <div class="min-w-0">
            <span class="text-t1">${esc(k)}</span>
            ${m.period ? `<span class="src-tag ml-1">${esc(m.period)}</span>` : ""}
          </div>
          <div class="text-right shrink-0">
            <span class="font-num text-t1">${esc(m.display || (m.value ?? "—"))}${m.unit && !String(m.display || "").includes(m.unit) ? " " + esc(m.unit) : ""}</span>
            ${m.source ? `<div class="text-[10px] text-t3">${esc(m.source)}</div>` : ""}
          </div>
        </div>`).join("") : '<p class="text-t3 text-xs">暂无底层指标（可能为演示模式）</p>'}
    </div>
    ${derived.length ? `
      <div>
        <p class="text-[11px] text-t3 mb-2">确定性计算结果（CalcAgent）</p>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
          ${derived.map((d) => `
            <div class="bg-page rounded-lg p-3">
              <div class="flex justify-between items-start">
                <span class="text-xs text-t1 font-medium">${esc(d.label || d.key)}</span>
                <span class="src-tag">${esc(d.confidence)}</span>
              </div>
              <p class="font-num text-lg text-t1 mt-1">${esc(d.display || (d.value ?? "—"))}</p>
              ${d.formula ? `<p class="text-[10px] text-t3 mt-1">公式：${esc(d.formula)}</p>` : ""}
              ${(d.inputs || []).length ? `<p class="text-[10px] text-t3">输入：${d.inputs.map(esc).join(" · ")}</p>` : ""}
            </div>`).join("")}
        </div>
      </div>` : ""}
    ${(fin.announcement_titles || []).length ? `
      <div>
        <p class="text-[11px] text-t3 mb-1">相关公告标题</p>
        <ul class="text-xs space-y-1">${fin.announcement_titles.slice(0, 5).map((t) => `<li>· ${esc(t)}</li>`).join("")}</ul>
      </div>` : ""}`;
}

function renderL2() {
  const r = state.result;
  const f = r.fundamental || {};
  const s = r.sentiment || {};
  const chain = r.industry_chain || {};
  const matrix = r.impact_matrix || {};

  return `
    <!-- 基本面 -->
    <div>
      <p class="text-xs font-medium text-t1 mb-2">基本面影响</p>
      <p class="text-sm text-t1 bg-page rounded-lg p-3 mb-2">${esc(f.key_judgment || f.summary || "—")}</p>
      ${f.summary && f.summary !== f.key_judgment ? `<p class="text-xs text-t2 mb-2">${esc(f.summary)}</p>` : ""}
      ${(f.metric_table || []).length ? `
        <div class="overflow-x-auto rounded-lg border border-line">
          <table class="w-full text-xs">
            <thead><tr class="bg-page text-t3 text-left">
              <th class="py-2 px-3">指标</th><th class="px-2">方向</th><th class="px-2 text-right">幅度</th><th class="px-2">确定性</th><th class="px-3">说明</th>
            </tr></thead>
            <tbody>${f.metric_table.map((m) => {
              const dc = (m.direction || "").includes("上") || (m.direction || "").includes("正") ? C.up
                : (m.direction || "").includes("下") || (m.direction || "").includes("负") ? C.down : C.t3;
              return `<tr class="border-t border-line">
                <td class="py-2 px-3 text-t1">${esc(m.metric)}</td>
                <td class="px-2" style="color:${dc}">${esc(m.direction)}</td>
                <td class="px-2 text-right font-num">${esc(m.magnitude)}</td>
                <td class="px-2 text-t3">${esc(m.certainty)}</td>
                <td class="px-3 text-t3">${esc(m.explanation || "")}</td>
              </tr>`;
            }).join("")}</tbody>
          </table>
        </div>` : ""}
    </div>

    <!-- 舆情 -->
    <div>
      <div class="flex items-center justify-between mb-2">
        <p class="text-xs font-medium text-t1">舆情分析</p>
        <span class="text-[11px] text-t3">${esc(s.source_note || "")} · 热度 ${esc(s.heat_trend || "—")} · ${s.total_mentions || 0} 条</span>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
        <div class="bg-page rounded-lg p-3 text-center">
          <p class="font-num text-lg text-t1">${s.total_mentions || 0}</p>
          <p class="text-[10px] text-t3">总提及</p>
        </div>
        <div class="bg-page rounded-lg p-3 text-center">
          <p class="font-num text-lg text-t1">${s.jinmen_count || 0}</p>
          <p class="text-[10px] text-t3">进门投研</p>
        </div>
        <div class="bg-page rounded-lg p-3 text-center">
          <p class="font-num text-lg text-t1">${s.web_count || 0}</p>
          <p class="text-[10px] text-t3">联网搜索</p>
        </div>
        <div class="bg-page rounded-lg p-3 text-center">
          <p class="font-num text-lg text-t1">${esc(s.heat_trend || "—")}</p>
          <p class="text-[10px] text-t3">热度趋势</p>
        </div>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
        <div class="bg-page rounded-lg p-2"><div id="chartEmotion" class="chart-box-sm"></div></div>
        <div class="bg-page rounded-lg p-2"><div id="chartChannel" class="chart-box-sm"></div></div>
        <div class="bg-page rounded-lg p-2"><div id="chartTime" class="chart-box-sm"></div></div>
      </div>
      ${(s.top_keywords || []).length ? `
        <div class="flex flex-wrap gap-1.5 mb-3">
          ${s.top_keywords.slice(0, 12).map((k) =>
            `<span class="src-tag">${esc(k.keyword)} <b class="text-brand">${k.frequency}</b></span>`
          ).join("")}
        </div>` : ""}
      ${(s.items || []).length ? `
        <div class="max-h-56 overflow-y-auto space-y-2">
          ${s.items.slice(0, 8).map((it) => `
            <div class="border border-line rounded-lg p-3">
              <div class="flex items-start justify-between gap-2">
                <p class="text-xs text-t1 font-medium leading-snug">${esc(it.title)}</p>
                <span class="src-tag shrink-0">${esc(it.channel_type || it.channel)}</span>
              </div>
              <p class="text-[11px] text-t3 mt-1 line-clamp-2">${esc(it.summary)}</p>
              <div class="flex items-center gap-2 mt-1 text-[10px] text-t3">
                <span>${esc(it.published_at || "")}</span>
                ${it.url ? `<a href="${esc(it.url)}" target="_blank" class="text-brand hover:underline">原文 ↗</a>` : ""}
              </div>
            </div>`).join("")}
        </div>` : '<p class="text-xs text-t3">暂无舆情条目</p>'}
    </div>

    <!-- 产业链 + 影响矩阵 -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
      <div class="bg-page rounded-lg p-3">
        <p class="text-xs font-medium text-t1 mb-2">产业链传导</p>
        <div class="space-y-2 text-xs">
          <div><span class="text-t3">上游：</span><span class="text-t1">${esc(chain.upstream || "—")}</span></div>
          <div><span class="text-t3">下游：</span><span class="text-t1">${esc(chain.downstream || "—")}</span></div>
          <div><span class="text-t3">竞品：</span><span class="text-t1">${esc(chain.competitors || "—")}</span></div>
          <div><span class="text-t3">方向：</span>
            <span class="pill" style="background:${chain.direction === "positive" ? "#F0FDF4" : chain.direction === "negative" ? "#FEF2F2" : "#F1F5F9"};color:${chain.direction === "positive" ? C.down : chain.direction === "negative" ? C.up : C.t3}">
              ${esc(chain.direction || "neutral")}
            </span>
          </div>
        </div>
      </div>
      <div class="bg-page rounded-lg p-2">
        <p class="text-xs font-medium text-t1 mb-1 px-1">多维影响矩阵</p>
        <div id="chartImpact" class="chart-box-sm"></div>
      </div>
    </div>`;
}

function renderL3() {
  const r = state.result;
  const vals = r.valuations || [];
  const primary = r.valuation || vals[0];
  if (!primary) return '<p class="text-xs text-t3">估值数据缺失</p>';
  const c = primary.impact_pct >= 0 ? C.up : C.down;

  return `
    <div class="flex items-end gap-4 flex-wrap mb-2">
      <div>
        <p class="text-[11px] text-t3">主估值影响</p>
        <p class="font-num text-3xl font-semibold" style="color:${c}">${arrow(primary.impact_pct)} ${fmtPct(primary.impact_pct, true)}</p>
      </div>
      <div class="text-xs text-t3 pb-1">
        <div>${esc(primary.method)}</div>
        <div>现价 ¥${primary.current_price ? Number(primary.current_price).toFixed(2) : "—"}</div>
        <div>目标区间 ${fmtYiText(primary.target_range?.[0])} ~ ${fmtYiText(primary.target_range?.[1])}</div>
      </div>
    </div>
    <div class="bg-page rounded-lg p-2 mb-3"><div id="chartValuation" class="chart-box"></div></div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
      ${vals.map((v) => {
        const vc = v.impact_pct >= 0 ? C.up : C.down;
        return `<div class="border border-line rounded-lg p-3">
          <div class="flex justify-between items-start">
            <p class="text-xs font-medium text-t1">${esc(v.method)}</p>
            <span class="font-num text-sm font-semibold" style="color:${vc}">${fmtPct(v.impact_pct, true)}</span>
          </div>
          <p class="text-[11px] text-t3 mt-1">${fmtYiText(v.pre_event_value)} → ${fmtYiText(v.post_event_value)}</p>
          ${v.params && Object.keys(v.params).length ? `
            <div class="mt-2 space-y-0.5">${Object.entries(v.params).map(([k, val]) =>
              `<div class="metric-row"><span class="text-t3">${esc(k)}</span><span class="font-num text-t1">${esc(val)}</span></div>`
            ).join("")}</div>` : ""}
          ${(v.assumptions || []).length ? `
            <div class="mt-2">
              <p class="text-[10px] text-t3 mb-1">核心假设</p>
              <ul class="text-[11px] space-y-0.5">${v.assumptions.map((a) => `<li>· ${esc(a)}</li>`).join("")}</ul>
            </div>` : ""}
          ${(v.sensitivity || []).length ? `
            <div class="mt-2">
              <p class="text-[10px] text-t3 mb-1">敏感性</p>
              <ul class="text-[11px] space-y-0.5">${v.sensitivity.map((a) => `<li>· ${esc(a)}</li>`).join("")}</ul>
            </div>` : ""}
        </div>`;
      }).join("")}
    </div>
    <button onclick="pickCmdByKey('rerun_l3')" class="text-brand text-[11px] hover:underline mt-1">调整估值参数重算 →</button>`;
}

function renderL4() {
  const s = state.result.strategy || {};
  const toneMap = { 增持: C.up, 买入: C.up, 持有: C.warn, 观望: C.warn, 减持: C.down, 卖出: C.down };
  const hit = Object.entries(toneMap).find(([k]) => (s.recommendation || "").includes(k));
  const rc = hit ? hit[1] : C.brand;
  const ev = state.result.event || {};

  return `
    <div class="flex items-center gap-3 flex-wrap mb-2">
      <span class="pill text-sm" style="background:${rc}18;color:${rc}">${esc(s.recommendation || "—")}</span>
      <span class="text-xs text-t3">置信度 <b class="font-num text-t1">${s.confidence ?? "—"}%</b></span>
      <span class="text-xs text-t3">当前仓位 <b class="font-num text-t1">${ev.position_ratio ?? "—"}%</b></span>
      <span class="text-xs text-t3">风险偏好 <b class="text-t1">${esc(ev.risk_tolerance || "—")}</b></span>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
      ${(s.options || []).map((o) => `
        <div class="border border-line rounded-lg p-3">
          <p class="text-xs font-medium" style="color:${C.brand}">${esc(o.name)}</p>
          <p class="text-sm font-semibold text-t1 mt-1">${esc(o.action)}</p>
          <p class="text-xs text-t3 mt-2">${esc(o.logic)}</p>
          <p class="text-[11px] text-t3 mt-2">仓位：${esc(o.position_advice)}</p>
          ${o.trigger_condition ? `<p class="text-[11px] text-t3 mt-1">触发：${esc(o.trigger_condition)}</p>` : ""}
        </div>`).join("") || '<p class="text-xs text-t3">暂无情景选项</p>'}
    </div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
      <div class="rounded-lg p-3" style="background:#FEF2F2">
        <p class="text-[11px] font-medium mb-1" style="color:${C.up}">风险提示</p>
        <ul class="space-y-0.5 text-xs">${(s.risk_warnings || []).map((x) => `<li>· ${esc(x)}</li>`).join("") || "<li class='text-t3'>无</li>"}</ul>
      </div>
      <div class="rounded-lg p-3" style="background:#EFF6FF">
        <p class="text-[11px] font-medium mb-1" style="color:${C.brand}">后续观察</p>
        <ul class="space-y-0.5 text-xs">${(s.watch_points || []).map((x) => `<li>· ${esc(x)}</li>`).join("") || "<li class='text-t3'>无</li>"}</ul>
      </div>
    </div>
    <button onclick="pickCmdByKey('rerun_l4')" class="text-brand text-[11px] hover:underline mt-1">调整持仓参数重算 →</button>`;
}

/* ========== ECharts ========== */
function initSentimentCharts() {
  const s = state.result.sentiment || {};
  // 情绪饼图
  const elE = $("chartEmotion");
  if (elE && window.echarts) {
    const ch = echarts.init(elE);
    state.charts.emotion = ch;
    const emo = s.emotion_distribution || [];
    const colorMap = { 正面: C.down, 中性: C.t3, 负面: C.up };
    ch.setOption({
      title: { text: "情绪分布", left: "center", top: 4, textStyle: { fontSize: 11, color: C.t2 } },
      tooltip: { trigger: "item" },
      series: [{
        type: "pie", radius: ["40%", "68%"], center: ["50%", "58%"],
        label: { fontSize: 10 },
        data: emo.map((e) => ({ name: e.emotion, value: e.count, itemStyle: { color: colorMap[e.emotion] || C.brand } })),
      }],
    });
  }
  // 渠道柱状
  const elC = $("chartChannel");
  if (elC && window.echarts) {
    const ch = echarts.init(elC);
    state.charts.channel = ch;
    const chn = (s.channel_distribution || []).slice(0, 6);
    ch.setOption({
      title: { text: "渠道分布", left: "center", top: 4, textStyle: { fontSize: 11, color: C.t2 } },
      tooltip: { trigger: "axis" },
      grid: { left: 8, right: 8, top: 32, bottom: 28, containLabel: true },
      xAxis: { type: "category", data: chn.map((c) => c.channel), axisLabel: { fontSize: 9, rotate: 20 } },
      yAxis: { type: "value", splitLine: { lineStyle: { color: C.line } } },
      series: [{ type: "bar", data: chn.map((c) => c.count), itemStyle: { color: C.violet, borderRadius: [4, 4, 0, 0] }, barWidth: 16 }],
    });
  }
  // 时间趋势
  const elT = $("chartTime");
  if (elT && window.echarts) {
    const td = s.time_distribution || [];
    if (!td.length) {
      elT.innerHTML = `<div class="h-full flex items-center justify-center text-[11px] text-t3 px-3 text-center">暂无可用发布时间<br/>无法绘制热度时序</div>`;
    } else {
      const ch = echarts.init(elT);
      state.charts.time = ch;
      ch.setOption({
        title: { text: "热度时序", left: "center", top: 4, textStyle: { fontSize: 11, color: C.t2 } },
        tooltip: { trigger: "axis" },
        grid: { left: 8, right: 12, top: 32, bottom: 20, containLabel: true },
        xAxis: { type: "category", data: td.map((t) => (t.date || "").slice(5)), axisLabel: { fontSize: 9 } },
        yAxis: { type: "value", splitLine: { lineStyle: { color: C.line } }, minInterval: 1 },
        series: [{
          type: "line", data: td.map((t) => t.count), smooth: true,
          areaStyle: { color: "rgba(59,130,246,.12)" },
          lineStyle: { color: C.brand }, itemStyle: { color: C.brand },
        }],
      });
    }
  }
}

function initImpactChart() {
  const el = $("chartImpact");
  if (!el || !window.echarts) return;
  const matrix = state.result.impact_matrix || {};
  const keys = Object.keys(matrix);
  if (!keys.length) return;
  const ch = echarts.init(el);
  state.charts.impact = ch;
  const labels = { growth: "成长", profitability: "盈利", sentiment: "舆情", industry_chain: "产业链", uncertainty: "不确定性" };
  ch.setOption({
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const p = params[0];
        const k = keys[p.dataIndex];
        return `${labels[k] || k}: ${p.value}<br/>${matrix[k]?.reason || ""}`;
      },
    },
    grid: { left: 50, right: 12, top: 10, bottom: 10 },
    xAxis: { type: "value", min: -10, max: 10, splitLine: { lineStyle: { color: C.line } } },
    yAxis: { type: "category", data: keys.map((k) => labels[k] || k), axisLabel: { fontSize: 10 } },
    series: [{
      type: "bar",
      data: keys.map((k) => {
        const v = matrix[k]?.score ?? 0;
        return { value: v, itemStyle: { color: v > 0 ? C.up : v < 0 ? C.down : C.t3, borderRadius: 3 } };
      }),
      barWidth: 12,
    }],
  });
}

function initValuationCharts() {
  const el = $("chartValuation");
  if (!el || !window.echarts) return;
  const vals = state.result.valuations || [];
  if (!vals.length) return;
  const ch = echarts.init(el);
  state.charts.valuation = ch;
  ch.setOption({
    tooltip: { trigger: "axis" },
    legend: { data: ["事件前", "事件后"], top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 50, right: 20, top: 36, bottom: 28 },
    xAxis: { type: "category", data: vals.map((v) => (v.method || "").replace(/估值模型\d+\s*·\s*/, "").slice(0, 12)) },
    yAxis: { type: "value", name: "亿元", nameTextStyle: { fontSize: 10 }, splitLine: { lineStyle: { color: C.line } } },
    series: [
      { name: "事件前", type: "bar", data: vals.map((v) => v.pre_event_value), itemStyle: { color: "#94A3B8", borderRadius: [4, 4, 0, 0] }, barGap: "20%" },
      { name: "事件后", type: "bar", data: vals.map((v) => v.post_event_value), itemStyle: { color: C.brand, borderRadius: [4, 4, 0, 0] } },
    ],
  });
}

/* ========== SSE 主流程 ========== */
async function startAnalyze() {
  const desc = $("eventInput").value.trim();
  if (!desc) { $("eventInput").focus(); return; }

  const payload = {
    company_name: $("companyName").value.trim() || "宁德时代",
    stock_code: $("stockCode").value.trim() || "sz300750",
    event_description: desc,
    event_type: $("eventType").value,
    position_ratio: parseFloat($("posRatio").value) || 0,
    cost_basis: parseFloat($("costBasis").value) || 0,
    investment_horizon: $("horizon").value,
    risk_tolerance: $("risk").value,
  };

  $("homeSection").classList.add("hidden");
  $("chatSection").classList.remove("hidden");
  $("userMsg").textContent = `【${payload.event_type}】${payload.company_name}：${desc}`;
  $("summaryCard").classList.add("hidden");
  $("followUpBar").classList.add("hidden");
  $("exportBtn").classList.add("hidden");
  $("newBtn").classList.remove("hidden");
  $("analyzeBtn").disabled = true;

  state = { sessionId: null, steps: [], result: { event: payload }, layerStatus: {}, dataStep: null, charts: {}, activeCmd: null, refining: false, snapshot: null };
  renderStepFlow();
  if ($("refineHistory")) $("refineHistory").innerHTML = "";
  cancelAction();

  try {
    await consumeSSE("/api/analyze", payload);
  } catch (e) {
    console.error(e);
    addErrorMsg("分析中断：" + e.message);
  } finally {
    $("analyzeBtn").disabled = false;
  }
}

async function consumeSSE(url, payload) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n\n");
    buf = lines.pop();
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      handleEvent(JSON.parse(line.slice(6)));
    }
  }
}

function handleEvent(ev) {
  if (ev.type === "error") {
    addErrorMsg(ev.message || "未知错误");
    return;
  }
  if (ev.type === "init") {
    state.sessionId = ev.session_id || state.sessionId;
    state.steps = ev.steps || [];
    if (ev.refine) {
      // 局部重算：把目标层重置为 running 前的 pending
      (ev.targets || []).forEach((t) => {
        if (t === "data") state.dataStep = { status: "pending", message: "" };
        else state.layerStatus[t] = "pending";
      });
    }
    return;
  }
  if (ev.type === "step") {
    if (ev.step === "data") {
      state.dataStep = { status: ev.status, message: ev.message };
      if (ev.status === "done" && ev.data) {
        state.result.financial_context = ev.data.financial_context;
        state.result.derived_metrics = ev.data.derived_metrics;
      }
      renderStepFlow();
      return;
    }
    if (["l1", "l2", "l3", "l4"].includes(ev.step)) {
      state.layerStatus[ev.step] = ev.status;
      if (ev.status === "done" && ev.data) handleStepData(ev.step, ev.data);
      renderStepFlow();
      // 滚动到当前层
      requestAnimationFrame(() => {
        const card = $(`card-${ev.step}`);
        if (card && ev.status === "running") card.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    }
    return;
  }
  if (ev.type === "complete") {
    state.sessionId = ev.session_id || state.sessionId;
    const d = ev.data || {};
    state.result = { ...state.result, ...d };
    ["l1", "l2", "l3", "l4"].forEach((k) => {
      if (state.result[{ l1: "fact_check", l2: "fundamental", l3: "valuation", l4: "strategy" }[k]]) {
        state.layerStatus[k] = "done";
      }
    });
    if (state.result.financial_context) {
      state.dataStep = { status: "done", message: state.dataStep?.message || "数据采集完成" };
    }
    renderStepFlow();
    if (d.executive_summary) {
      $("summaryText").textContent = d.executive_summary;
      $("summaryCard").classList.remove("hidden");
    }
    $("followUpBar").classList.remove("hidden");
    $("exportBtn").classList.remove("hidden");

    // 局部重算：追加前后对比卡
    if (d.refine && state.snapshot) {
      appendDiffCard(state.snapshot, snapshotResult(), d.instruction || "局部重算", d.targets || []);
      state.snapshot = null;
      cancelAction();
    }

    state.refining = false;
    if ($("confirmRefineBtn")) {
      $("confirmRefineBtn").disabled = !state.activeCmd;
      $("confirmRefineBtn").textContent = "确认重算";
    }
  }
}

function handleStepData(step, data) {
  if (step === "l1") {
    state.result.event_profile = data.profile;
    state.result.fact_check = data.fact_check;
  } else if (step === "l2") {
    state.result.fundamental = data.fundamental;
    state.result.sentiment = data.sentiment;
    state.result.industry_chain = data.industry_chain;
    state.result.impact_matrix = data.impact_matrix;
  } else if (step === "l3") {
    state.result.valuations = data.valuations;
    state.result.valuation = data.primary;
  } else if (step === "l4") {
    state.result.strategy = data.strategy;
  }
}

/* ========== 溯源 ========== */
function toggleTraceDrawer(open) {
  $("traceDrawer").style.transform = open ? "translateX(0)" : "translateX(100%)";
}
function showTrace(layer) {
  const r = state.result;
  let html = "";
  if (layer === "l1" && r.fact_check) {
    const f = r.fact_check;
    html = `<div class="space-y-3 text-xs">
      <p class="text-t3">信息来源：${esc(f.data_source || "进门财经 MCP")}</p>
      <div class="bg-page rounded-lg p-3">
        <p class="text-t1 font-medium mb-1">${esc(f.official_title || "官方公告")}</p>
        <p class="text-t3 mb-1">${esc(f.publish_time || "")}</p>
        ${f.official_url ? `<a href="${esc(f.official_url)}" target="_blank" class="text-brand hover:underline">查看原文 ↗</a>` : ""}
      </div>
      <div><p class="text-t3 mb-1">判定依据</p><p class="text-t2">${esc(f.basis || "—")}</p></div>
      <div>
        <p class="text-t3 mb-1">官方数据补充</p>
        ${Object.entries(f.supplemented_data || {}).map(([k, v]) =>
          `<div class="metric-row"><span class="text-t3">${esc(k)}</span><span class="font-num text-t1">${esc(v)}</span></div>`
        ).join("") || '<p class="text-t3">无</p>'}
      </div>
    </div>`;
  }
  $("traceContent").innerHTML = html || '<p class="text-t3 text-xs">暂无溯源信息</p>';
  toggleTraceDrawer(true);
}

/* ========== 结构化继续分析 ========== */
function snapshotResult() {
  const r = state.result || {};
  return {
    recommendation: r.strategy?.recommendation,
    confidence: r.strategy?.confidence,
    position: r.event?.position_ratio,
    risk: r.event?.risk_tolerance,
    impact: r.valuation?.impact_pct,
    heat: r.sentiment?.heat_trend,
    mentions: r.sentiment?.total_mentions,
    metrics: Object.keys(r.financial_context?.metrics || {}).length,
    summary: r.executive_summary,
  };
}

function pickAction(el) {
  document.querySelectorAll(".action-card").forEach((c) => c.classList.remove("active"));
  el.classList.add("active");
  state.activeCmd = el.dataset.cmd;
  $("cancelRefineBtn").classList.remove("hidden");
  $("confirmRefineBtn").disabled = false;
  renderRefinePanel(state.activeCmd);
  updateRefinePreview();
}

function cancelAction() {
  state.activeCmd = null;
  document.querySelectorAll(".action-card").forEach((c) => c.classList.remove("active"));
  $("refinePanel").classList.add("hidden");
  $("refinePanel").innerHTML = "";
  $("refinePreview").classList.add("hidden");
  $("cancelRefineBtn").classList.add("hidden");
  $("confirmRefineBtn").disabled = true;
}

function pickCmdByKey(key) {
  const el = document.querySelector(`.action-card[data-cmd="${key}"]`);
  if (el) {
    pickAction(el);
    $("followUpBar").scrollIntoView({ behavior: "smooth", block: "end" });
  }
}

function renderRefinePanel(cmd) {
  const panel = $("refinePanel");
  if (!cmd) { panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden");
  const ev = state.result.event || {};
  if (cmd === "rerun_l4") {
    panel.innerHTML = `
      <p class="text-t1 font-medium mb-2">调整持仓假设（仅重算 L4）</p>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
        <label class="flex flex-col gap-1"><span class="text-t3">仓位 %</span>
          <input id="rfPos" type="number" min="0" max="100" step="0.5" value="${ev.position_ratio ?? 5}"
            oninput="updateRefinePreview()" class="h-8 bg-white border border-line rounded-lg px-2 font-num"/></label>
        <label class="flex flex-col gap-1"><span class="text-t3">成本价</span>
          <input id="rfCost" type="number" value="${ev.cost_basis ?? 0}"
            oninput="updateRefinePreview()" class="h-8 bg-white border border-line rounded-lg px-2 font-num"/></label>
        <label class="flex flex-col gap-1"><span class="text-t3">期限</span>
          <select id="rfHorizon" onchange="updateRefinePreview()" class="h-8 bg-white border border-line rounded-lg px-2">
            <option value="short">短期</option><option value="medium">中期</option><option value="long">长期</option>
          </select></label>
        <label class="flex flex-col gap-1"><span class="text-t3">风险偏好</span>
          <select id="rfRisk" onchange="updateRefinePreview()" class="h-8 bg-white border border-line rounded-lg px-2">
            <option value="low">保守</option><option value="medium">稳健</option><option value="high">激进</option>
          </select></label>
      </div>
      <p class="text-[10px] text-t3 mt-2">上游 L1/L2/L3 结论保持不变，只按新持仓约束生成操作建议。</p>`;
    if (ev.investment_horizon) $("rfHorizon").value = ev.investment_horizon;
    if (ev.risk_tolerance) $("rfRisk").value = ev.risk_tolerance;
  } else if (cmd === "rerun_l3") {
    const pe = state.result.valuations?.find((v) => v.model_key === "pe");
    const dcf = state.result.valuations?.find((v) => v.model_key === "dcf");
    panel.innerHTML = `
      <p class="text-t1 font-medium mb-2">调整估值参数（重算 L3 → L4）</p>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
        <label class="flex flex-col gap-1"><span class="text-t3">盈利预测调整</span>
          <input id="rfRev" type="number" step="0.01" value="${pe?.params?.profit_revision ?? -0.05}"
            oninput="updateRefinePreview()" class="h-8 bg-white border border-line rounded-lg px-2 font-num"/></label>
        <label class="flex flex-col gap-1"><span class="text-t3">WACC %</span>
          <input id="rfWacc" type="number" step="0.1" value="${dcf?.params?.wacc ?? 8.5}"
            oninput="updateRefinePreview()" class="h-8 bg-white border border-line rounded-lg px-2 font-num"/></label>
        <label class="flex flex-col gap-1"><span class="text-t3">一阶段增速 %</span>
          <input id="rfG1" type="number" step="0.1" value="${dcf?.params?.growth1 ?? 8}"
            oninput="updateRefinePreview()" class="h-8 bg-white border border-line rounded-lg px-2 font-num"/></label>
        <label class="flex flex-col gap-1"><span class="text-t3">二阶段增速 %</span>
          <input id="rfG2" type="number" step="0.1" value="${dcf?.params?.growth2 ?? 5}"
            oninput="updateRefinePreview()" class="h-8 bg-white border border-line rounded-lg px-2 font-num"/></label>
      </div>
      <p class="text-[10px] text-t3 mt-2">盈利预测调整用小数：-0.05 表示 -5%。L1/L2 事实与舆情不变。</p>`;
  } else if (cmd === "rerun_l2") {
    panel.innerHTML = `
      <p class="text-t1 font-medium mb-2">刷新舆情 / 基本面（重算 L2 → L3 → L4）</p>
      <p class="text-t3">将重新调用进门 MCP 舆情搜索与基本面分析，再级联更新估值与持仓建议。L1 事实核验与底层财务快照保持不变。</p>`;
  } else if (cmd === "refresh_data") {
    panel.innerHTML = `
      <p class="text-t1 font-medium mb-2">刷新进门 MCP 底层数据（全链路下游）</p>
      <p class="text-t3">重新采集财务指标并重算 L2/L3/L4。适合怀疑底层数据过期时使用；耗时更长。</p>`;
  }
}

function updateRefinePreview() {
  const box = $("refinePreview");
  if (!state.activeCmd) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const map = {
    rerun_l4: () => {
      const pos = $("rfPos")?.value ?? "—";
      const risk = $("rfRisk")?.selectedOptions?.[0]?.text || "—";
      return `将重算 <b>L4</b>：仓位 ${pos}% · 风险 ${risk} → 更新持仓建议与情景仓位`;
    },
    rerun_l3: () => {
      const w = $("rfWacc")?.value ?? "—";
      const r = $("rfRev")?.value ?? "—";
      return `将重算 <b>L3 → L4</b>：WACC ${w}% · 盈利预测调整 ${r} → 更新估值影响与持仓建议`;
    },
    rerun_l2: () => "将重算 <b>L2 → L3 → L4</b>：刷新舆情/基本面，并级联估值与策略",
    refresh_data: () => "将重算 <b>Data → L2 → L3 → L4</b>：刷新进门底层指标后全链路更新",
  };
  box.innerHTML = (map[state.activeCmd] || (() => ""))();
}

function buildRefinePayload() {
  if (!state.sessionId || !state.activeCmd) return null;
  const payload = {
    session_id: state.sessionId,
    instruction: "",
    targets: [],
    valuation_overrides: {},
  };
  if (state.activeCmd === "rerun_l4") {
    payload.targets = ["l4"];
    payload.position_ratio = parseFloat($("rfPos")?.value);
    payload.cost_basis = parseFloat($("rfCost")?.value) || 0;
    payload.investment_horizon = $("rfHorizon")?.value;
    payload.risk_tolerance = $("rfRisk")?.value;
    payload.instruction = `改持仓假设：仓位${payload.position_ratio}% / ${payload.risk_tolerance}`;
  } else if (state.activeCmd === "rerun_l3") {
    payload.targets = ["l3", "l4"];
    payload.valuation_overrides = {
      profit_revision: parseFloat($("rfRev")?.value),
      wacc: parseFloat($("rfWacc")?.value),
      growth1: parseFloat($("rfG1")?.value),
      growth2: parseFloat($("rfG2")?.value),
    };
    payload.instruction = `调估值参数：WACC=${payload.valuation_overrides.wacc}%`;
  } else if (state.activeCmd === "rerun_l2") {
    payload.targets = ["l2", "l3", "l4"];
    payload.instruction = "刷新舆情与基本面";
  } else if (state.activeCmd === "refresh_data") {
    payload.targets = ["data", "l2", "l3", "l4"];
    payload.instruction = "刷新进门 MCP 底层数据";
  }
  return payload;
}

function appendDiffCard(before, after, instruction, targets) {
  const rows = [];
  const push = (label, a, b, fmt) => {
    const fa = fmt ? fmt(a) : (a ?? "—");
    const fb = fmt ? fmt(b) : (b ?? "—");
    const changed = String(fa) !== String(fb);
    rows.push(`<div class="metric-row">
      <span class="text-t3">${label}</span>
      <span class="${changed ? "text-t1 font-medium" : "diff-same"}">${esc(fa)} ${changed ? "→" : "="} ${esc(fb)}</span>
    </div>`);
  };
  push("持仓建议", before.recommendation, after.recommendation);
  push("置信度", before.confidence, after.confidence, (v) => (v == null ? "—" : v + "%"));
  push("仓位", before.position, after.position, (v) => (v == null ? "—" : v + "%"));
  push("估值影响", before.impact, after.impact, (v) => (v == null ? "—" : fmtPct(v, true)));
  push("舆情热度", before.heat, after.heat);
  push("舆情条数", before.mentions, after.mentions);
  push("MCP 指标数", before.metrics, after.metrics);

  const changedCount = [
    before.recommendation !== after.recommendation,
    before.confidence !== after.confidence,
    before.position !== after.position,
    before.impact !== after.impact,
    before.heat !== after.heat,
    before.mentions !== after.mentions,
    before.metrics !== after.metrics,
  ].filter(Boolean).length;

  const div = document.createElement("div");
  div.className = "card p-4 diff-card fade-in";
  div.innerHTML = `
    <div class="flex items-center justify-between gap-2 mb-2">
      <div class="flex items-center gap-2">
        <span class="pill" style="background:#EFF6FF;color:#3B82F6">局部重算</span>
        <span class="text-xs text-t1 font-medium">${esc(instruction)}</span>
      </div>
      <span class="text-[11px] text-t3">目标 [${(targets || []).join(", ")}] · ${changedCount} 项变化</span>
    </div>
    <div class="bg-page rounded-lg p-3 text-xs">${rows.join("")}</div>
    ${after.summary && after.summary !== before.summary
      ? `<p class="text-xs text-t2 mt-3 leading-relaxed"><b>新结论：</b>${esc(after.summary)}</p>`
      : (changedCount === 0
        ? `<p class="text-[11px] text-t3 mt-2">参数变化未触发建议档位切换；请查看上方 L3/L4 卡片中的仓位情景数字是否已更新。</p>`
        : "")}
  `;
  $("refineHistory").appendChild(div);
  div.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function confirmRefine() {
  if (!state.sessionId) {
    addErrorMsg("会话不存在，请先完成一次完整分析");
    return;
  }
  if (state.refining || !state.activeCmd) return;
  const payload = buildRefinePayload();
  if (!payload) return;

  state.snapshot = snapshotResult();
  state.refining = true;
  $("confirmRefineBtn").disabled = true;
  $("confirmRefineBtn").textContent = "重算中…";
  $("refineHint").textContent = `正在重算 ${(payload.targets || []).join(" → ")}…`;

  // 标记目标层为 running，给用户即时反馈
  (payload.targets || []).forEach((t) => {
    if (t === "data") state.dataStep = { status: "running", message: "重新采集…" };
    else if (["l2", "l3", "l4"].includes(t)) state.layerStatus[t] = "running";
  });
  renderStepFlow();

  try {
    await consumeSSE("/api/refine", payload);
  } catch (e) {
    console.error(e);
    addErrorMsg("局部重算失败：" + e.message);
  } finally {
    state.refining = false;
    $("confirmRefineBtn").textContent = "确认重算";
    $("confirmRefineBtn").disabled = !state.activeCmd;
    $("refineHint").textContent = "仅重跑选中层级，保留上游结论";
  }
}

/* 兼容旧入口 */
function sendFollowUp() { confirmRefine(); }
function pickCmd(el) { pickAction(el); }

/* ========== 工具 ========== */
function resetHome() {
  disposeCharts();
  state = { sessionId: null, steps: [], result: {}, layerStatus: {}, dataStep: null, charts: {}, activeCmd: null, refining: false, snapshot: null };
  $("chatSection").classList.add("hidden");
  $("homeSection").classList.remove("hidden");
  $("followUpBar").classList.add("hidden");
  $("exportBtn").classList.add("hidden");
  $("newBtn").classList.add("hidden");
  $("summaryCard").classList.add("hidden");
  $("stepFlow").innerHTML = "";
  if ($("refineHistory")) $("refineHistory").innerHTML = "";
  cancelAction();
}

function addErrorMsg(text) {
  const div = document.createElement("div");
  div.className = "card p-4 text-xs border";
  div.style.borderColor = C.up + "55";
  div.style.color = C.up;
  div.textContent = text;
  $("stepFlow").appendChild(div);
}

function exportReport() {
  const r = state.result;
  const lines = [
    "# FinEventAgent 分析报告", "",
    `**公司**：${r.event?.company_name || "—"}`,
    `**事件**：${r.event?.event_description || "—"}`,
    `**核验**：${r.fact_check?.status || "—"}（${r.fact_check?.confidence || 0}%）`,
    `**舆情**：${r.sentiment?.total_mentions || 0} 条 · ${r.sentiment?.heat_trend || "—"}`,
    "", "## 综合结论", r.executive_summary || "—",
    "", "## 持仓建议",
    `**建议**：${r.strategy?.recommendation || "—"}`,
    `**置信度**：${r.strategy?.confidence || 0}%`,
    "", "## 估值影响",
    ...(r.valuations || []).map((v) => `- ${v.method}：${v.impact_pct}%`),
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `FinEventAgent_${r.event?.company_name || "report"}_${new Date().toISOString().slice(0, 10)}.md`;
  a.click();
}

window.fillTemplate = fillTemplate;
window.toggleParamPanel = toggleParamPanel;
window.startAnalyze = startAnalyze;
window.confirmRefine = confirmRefine;
window.cancelAction = cancelAction;
window.pickAction = pickAction;
window.pickCmdByKey = pickCmdByKey;
window.updateRefinePreview = updateRefinePreview;
window.showTrace = showTrace;
window.toggleTraceDrawer = toggleTraceDrawer;
window.exportReport = exportReport;
window.resetHome = resetHome;
