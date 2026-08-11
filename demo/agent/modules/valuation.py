"""ValuationAgent：估值影响测算。

数字纪律：模型参数优先使用 FinancialContext 中的真实数据（归母净利润等），
取不到时才回退到 analysis.json 的分析师假设，并在 assumptions 中明确标注口径。
"""
from __future__ import annotations

from ..config import settings
from ..jinmen_mcp import jinmen_client
from ..schemas import DerivedMetric, EventInput, EventProfile, FinancialContext, ValuationResult
from ..agents.calc_agent import extract_event_signals, get_derived, is_dividend, is_reduction
from ..agents.data_agent import get_metric

# 事件传导参数：单期业绩信号 → 全年盈利预测调整的折算系数（保守处理）
_PASS_THROUGH = 0.25
_REVISION_CAP = 0.25
# 无数值信号时按事件类型的定性默认调整
_QUAL_REVISION: list[tuple[tuple[str, ...], float, str]] = [
    (("处罚", "立案", "违规"), -0.08, "监管处罚类事件，盈利预测定性下修并计提问责风险"),
    (("诉讼", "仲裁", "制裁"), -0.04, "诉讼/制裁类事件，盈利预测定性下修"),
    (("中标", "订单", "合同", "供货", "定点"), 0.03, "重大合同/中标，定性上修收入预期"),
    (("定增", "增发", "募资"), -0.03, "定增存在股本摊薄，盈利预测定性下修"),
    (("回购",), 0.02, "回购注销提升每股收益，定性小幅上修"),
    (("并购", "收购", "重组"), -0.01, "并购存在整合风险，定性中性偏谨慎"),
    (("产品", "量产", "新技术", "发布"), 0.02, "新产品/技术催化，定性小幅上修"),
    (("产能", "扩产", "投产"), 0.01, "产能扩张，长期定性中性偏积极"),
]


# 明确负面事件类型：即便事件描述中出现正号数字（多为风险敞口表述），盈利预测也只做负向调整
_NEGATIVE_EVENT_WORDS = ("诉讼", "仲裁", "处罚", "立案", "违规", "制裁", "风险警示", "退市", "召回")


def _event_adjustment(profile: EventProfile | None, fact_status: str = "") -> dict:
    """从 EventProfile 推导事件对盈利预测/PE 的调整，全部假设写入 notes 溯源。"""
    signals = extract_event_signals(profile)
    notes: list[str] = []
    if signals:
        pr, rv = signals.get("profit"), signals.get("revenue")
        if pr is not None and rv is not None:
            raw = 0.6 * pr + 0.4 * rv
        elif pr is not None:
            raw = pr
        else:
            raw = rv if rv is not None else 0.0
        evt_text = ((profile.event_subtype or "") + " " + " ".join(profile.keywords)) if profile else ""
        if any(w in evt_text for w in _NEGATIVE_EVENT_WORDS) and raw > 0:
            notes.append(f"「{profile.event_subtype}」属负面事件类型，正号数字视为风险敞口而非业绩增量")
            raw = -raw
        revision = max(-_REVISION_CAP, min(_REVISION_CAP, raw * _PASS_THROUGH))
        src = "、".join(f"{k} {v:+.0%}" for k, v in signals.items())
        notes.append(f"事件数值信号（{src}）× 单期传导系数 {_PASS_THROUGH} → 盈利预测调整 {revision:+.1%}")
        pe_adj = 0.0
        if raw >= 0.15:
            pe_adj += 1.0
        elif raw <= -0.25:
            pe_adj -= 2.0
        elif raw <= -0.10:
            pe_adj -= 1.0
        if (signals.get("margin") or 0) < 0:
            pe_adj -= 0.5
            notes.append("毛利率信号走弱，PE 额外折价 0.5 倍")
        if pe_adj:
            notes.append(f"业绩动能 {'超预期' if pe_adj > 0 else '承压'}，PE 调整 {pe_adj:+.1f} 倍")
    else:
        # 无数值信号：按事件类型定性默认
        text = ((profile.event_subtype or "") + " " + " ".join(profile.keywords)) if profile else ""
        revision, pe_adj = 0.0, 0.0
        for words, rev, note in _QUAL_REVISION:
            if any(w in text for w in words):
                revision, pe_adj = rev, 0.0
                notes.append(f"事件描述无数值信号，按事件类型定性处理：{note}（{rev:+.0%}）")
                break
        if not notes:
            notes.append("事件无可用数值/类型信号，盈利预测沿用 analysis.json 默认假设")

    if fact_status == "unverified" and signals:
        revision *= 0.5
        notes.append("事件数值未通过真实性核验，盈利预测调整按 50% 折算（保守）")
    return {"revision": revision, "pe_adj": pe_adj, "has_signal": bool(signals), "notes": notes}


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(str(v).replace("%", "").replace(",", ""))
    except Exception:
        return default


async def _market_snapshot(stock_code: str, use_live: bool) -> tuple[float, float]:
    vcfg = settings.valuation_cfg
    price = _safe_float(vcfg.get("fallback_price"), 392.10)
    cap = _safe_float(vcfg.get("fallback_market_cap_yi"), 18141.0)
    if use_live and jinmen_client.available:
        try:
            perf = await jinmen_client.price_performance([stock_code])
            data = perf.get("data", {}).get(stock_code, {})
            price = _safe_float(data.get("regular_market", {}).get("closePrice"), price)
            mv = data.get("market_value", {}).get("total_market_value_display", "")
            if mv:
                cap = _safe_float(str(mv).replace("亿", ""), cap)
        except Exception:
            pass
    return price, cap


def _run_pe(cfg: dict, real_profit: float | None, profit_period: str, adj: dict) -> ValuationResult:
    """PE 模型：有真实净利润则用真实值（标注口径），否则用配置假设；
    盈利预测调整与 PE 溢价优先来自事件信号，无信号时回退配置默认。"""
    if real_profit:
        # 快照为 YTD 口径（如 H1），年化 ×2 并明确标注为估算
        pre_profit = real_profit * 2
        profit_src = f"归母净利润 {real_profit:,.2f} 亿元（{profit_period or '最新报告期'}，财务快照，年化×2为估算）"
        profit_conf = "估算"
    else:
        pre_profit = _safe_float(cfg.get("pre_net_profit_yi"), 480.0)
        profit_src = f"归母净利润 {pre_profit:,.0f} 亿元（分析师假设，见 analysis.json）"
        profit_conf = "假设"
    if adj.get("has_signal"):
        revision = adj["revision"]
        rev_src = "事件信号驱动（见假设说明）"
    else:
        revision = adj["revision"] if adj["revision"] else _safe_float(cfg.get("profit_revision"), -0.05)
        rev_src = "analysis.json 默认假设" if not adj["revision"] else "事件类型定性调整"
    post_profit = pre_profit * (1 + revision)
    pre_pe = _safe_float(cfg.get("pre_pe"), 25.0)
    post_pe = _safe_float(cfg.get("post_pe"), 26.0) + adj.get("pe_adj", 0.0)
    pre_value = pre_profit * pre_pe
    post_value = post_profit * post_pe
    return ValuationResult(
        method=cfg.get("label", "估值模型1 · PE 估值法"),
        model_key="pe",
        current_price=0.0,
        pre_event_value=round(pre_value, 0),
        post_event_value=round(post_value, 0),
        impact_pct=round((post_value - pre_value) / pre_value * 100, 2),
        target_range=[round(post_value * 0.9, 0), round(post_value * 1.1, 0)],
        params={
            "pre_net_profit_yi": round(pre_profit, 2),
            "profit_source": profit_conf,
            "profit_revision": round(revision, 4),
            "profit_revision_source": rev_src,
            "pre_pe": pre_pe,
            "post_pe": round(post_pe, 2),
        },
        sensitivity=[
            f"PE 每变动 1 倍，估值约变动 {post_profit:,.0f} 亿元",
            f"净利润预测每变动 1%，估值约变动 {post_pe:,.0f} 亿元",
        ],
        assumptions=[profit_src] + list(adj.get("notes", [])) + list(cfg.get("assumptions", [])),
    )


def _run_dcf(cfg: dict, adj: dict) -> ValuationResult:
    """DCF 模型：事件信号通过增长率微调传导（g1 全量、g2 半量），保持长逻辑稳定。"""
    revision = adj.get("revision", 0.0)
    g_delta = revision * 0.15  # 盈利预测调整向长期增速的传导打折
    wacc = _safe_float(cfg.get("wacc"), 8.5) / 100
    g1 = _safe_float(cfg.get("growth1"), 8.0) / 100 + g_delta
    g2 = _safe_float(cfg.get("growth2"), 5.0) / 100 + g_delta / 2
    gt = _safe_float(cfg.get("terminal_growth"), 2.5) / 100
    fcf = _safe_float(cfg.get("fcf0_yi"), 350.0)
    y1, y2 = int(cfg.get("years1", 3)), int(cfg.get("years2", 7))

    def _dcf(g1_, g2_):
        f = fcf
        pv_ = 0.0
        for y in range(1, y1 + 1):
            f *= 1 + g1_
            pv_ += f / ((1 + wacc) ** y)
        for y in range(y1 + 1, y1 + y2 + 1):
            f *= 1 + g2_
            pv_ += f / ((1 + wacc) ** y)
        tv = f * (1 + gt) / (wacc - gt)
        pv_ += tv / ((1 + wacc) ** (y1 + y2))
        return pv_

    base_g1, base_g2 = _safe_float(cfg.get("growth1"), 8.0) / 100, _safe_float(cfg.get("growth2"), 5.0) / 100
    post_value = _dcf(g1, g2)
    # 事件前估值用同一 DCF 在原始增速下重算，方向才能随事件正负对称
    pre_value = _dcf(base_g1, base_g2) if revision else post_value * _safe_float(cfg.get("pre_event_discount"), 0.98)
    growth_note = (
        f"事件盈利预测调整 {revision:+.1%} → 一阶段增速 {_safe_float(cfg.get('growth1'), 8.0)}%→{g1 * 100:.2f}%、"
        f"二阶段 {g2 * 100:.2f}%"
        if revision
        else "事件无增速传导，沿用 analysis.json 默认增长率"
    )
    return ValuationResult(
        method=cfg.get("label", "估值模型2 · 简化 DCF（三阶段）"),
        model_key="dcf",
        current_price=0.0,
        pre_event_value=round(pre_value, 0),
        post_event_value=round(post_value, 0),
        impact_pct=round((post_value - pre_value) / pre_value * 100, 2),
        target_range=[round(post_value * 0.9, 0), round(post_value * 1.1, 0)],
        params={
            "fcf0_yi": _safe_float(cfg.get("fcf0_yi"), 350.0),
            "wacc": _safe_float(cfg.get("wacc"), 8.5),
            "growth1": round(g1 * 100, 3),
            "growth2": round(g2 * 100, 3),
            "terminal_growth": _safe_float(cfg.get("terminal_growth"), 2.5),
        },
        sensitivity=[
            f"WACC ±1% 影响估值约 ∓{post_value * 0.06:,.0f} 亿元",
            f"永续增长率 ±0.5% 影响估值约 ±{post_value * 0.04:,.0f} 亿元",
        ],
        assumptions=[f"FCF₀ {_safe_float(cfg.get('fcf0_yi'), 350.0):,.0f} 亿元（分析师假设，见 analysis.json）", growth_note]
        + list(adj.get("notes", [])),
    )


_BUILDERS = {"pe": _run_pe, "dcf": _run_dcf}


def _apply_param_overrides(vcfg: dict, overrides: dict | None) -> dict:
    """将人工干预参数合并进估值配置副本（不污染全局 settings）。"""
    if not overrides:
        return vcfg
    cfg = {**vcfg}
    pe = {**cfg.get("pe", {})}
    dcf = {**cfg.get("dcf", {})}
    if "pre_pe" in overrides:
        pe["pre_pe"] = overrides["pre_pe"]
    if "post_pe" in overrides:
        pe["post_pe"] = overrides["post_pe"]
    if "profit_revision" in overrides:
        pe["profit_revision"] = overrides["profit_revision"]
    if "wacc" in overrides:
        dcf["wacc"] = overrides["wacc"]
    if "growth1" in overrides:
        dcf["growth1"] = overrides["growth1"]
    if "growth2" in overrides:
        dcf["growth2"] = overrides["growth2"]
    if "terminal_growth" in overrides:
        dcf["terminal_growth"] = overrides["terminal_growth"]
    if "fcf0_yi" in overrides:
        dcf["fcf0_yi"] = overrides["fcf0_yi"]
    cfg["pe"] = pe
    cfg["dcf"] = dcf
    return cfg


async def run_valuations(
    event: EventInput,
    fundamental_summary: str,
    use_live: bool = True,
    profile: EventProfile | None = None,
    fin: FinancialContext | None = None,
    derived: list[DerivedMetric] | None = None,
    fact_status: str = "",
    param_overrides: dict | None = None,
) -> list[ValuationResult]:
    """按 demo/config/analysis.json 中声明的模型列表依次测算，全部返回。"""
    derived = derived or []
    vcfg = _apply_param_overrides(settings.valuation_cfg, param_overrides)
    price, cap = await _market_snapshot(event.stock_code, use_live)
    models = vcfg.get("models", ["pe", "dcf"])
    adj = _event_adjustment(profile, fact_status)
    if param_overrides:
        # 人工覆盖 profit_revision 时，优先用覆盖值
        if "profit_revision" in param_overrides:
            adj = {
                **adj,
                "revision": float(param_overrides["profit_revision"]),
                "has_signal": True,
                "notes": list(adj.get("notes", [])) + [
                    f"人工覆盖盈利预测调整 → {float(param_overrides['profit_revision']):+.1%}"
                ],
            }
        adj["notes"] = list(adj.get("notes", [])) + [
            "估值参数含人工干预：" + "、".join(f"{k}={v}" for k, v in param_overrides.items())
        ]

    profit_dp = get_metric(fin, "归母净利润", "净利润")
    real_profit = profit_dp.value if profit_dp else None
    profit_period = (profit_dp.period if profit_dp else "") or ""

    results: list[ValuationResult] = []
    for key in models:
        builder = _BUILDERS.get(key)
        if not builder:
            continue
        r = builder(vcfg.get(key, {}), real_profit, profit_period, adj) if key == "pe" else builder(vcfg.get(key, {}), adj)
        r.current_price = price

        if is_dividend(profile):
            # 分红事件：用 CalcAgent 真实计算结果重写假设，估值影响锚定股息率
            payout = get_derived(derived, "payout_ratio")
            div_total = get_derived(derived, "dividend_total")
            dy = get_derived(derived, "dividend_yield")
            cov = get_derived(derived, "ocf_coverage")
            r.assumptions = [
                f"分红率 {payout.display if payout else '数据缺失'}（{payout.formula if payout else ''}）",
                f"现金分红总额 {div_total.display if div_total else '数据缺失'}",
                f"按收盘价折算股息率 {dy.display if dy else '数据缺失'}",
                f"经营现金流覆盖倍数 {cov.display if cov else '数据缺失'}",
                "分红不改变经营趋势，估值影响锚定现金回报与信号意义",
            ]
            r.sensitivity = [
                "股息率每提升 0.1pct，对长期配置资金吸引力增强",
                "若分红率持续超过现金流覆盖能力，未来分红可持续性存疑",
            ]
            # 估值影响 = 股息率 × 信号系数：PE（中短期定价）取 0.5，DCF（长期现金流口径）取 0.4，保守处理
            coef = 0.5 if r.model_key == "pe" else 0.4
            if dy and dy.value is not None:
                r.impact_pct = round(dy.value * coef, 2)
            else:
                r.impact_pct = 0.0
            r.assumptions.append(f"估值影响 = 股息率 × 信号系数 {coef}（{'PE 中短期口径' if r.model_key == 'pe' else 'DCF 长期口径'}）")
            r.target_range = [round(r.post_event_value * 0.95, 0), round(r.post_event_value * 1.08, 0)]
        elif is_reduction(profile):
            amt = get_derived(derived, "reduction_amount")
            mpct = get_derived(derived, "reduction_mcap_pct")
            r.assumptions = [
                f"减持规模 {amt.display}（{amt.formula}）" if amt and amt.value is not None else "减持规模数据缺失",
                f"占总市值 {mpct.display if mpct else '数据缺失'}",
                "减持不改变公司内在价值，影响为情绪与流动性折价",
            ]
            # 减持的估值冲击：经验上约为减持市值占比的 1-2 倍情绪折价，取 1.5 倍
            if mpct and mpct.value is not None:
                r.impact_pct = -round(mpct.value * 1.5, 2)
            else:
                r.impact_pct = -1.0
        results.append(r)
    return results
