"""CalcAgent：确定性计算引擎（纯代码，零 LLM）。

铁律：所有金额/比率只能在这里用代码算出，每个结果带 formula + inputs 溯源；
数据缺失时显式标注「数据缺失」+ 低置信度，绝不编数字。
"""
from __future__ import annotations

import re

from ..schemas import DerivedMetric, EventInput, EventProfile, FinancialContext
from .data_agent import get_metric


def _missing(key: str, label: str, formula: str, inputs: list[str]) -> DerivedMetric:
    return DerivedMetric(
        key=key, label=label, value=None, display="数据缺失",
        formula=formula, inputs=inputs, confidence="低",
    )


def _parse_dividend_per_10(text: str) -> float | None:
    """从事件描述解析「每10股派现X元」/「10派X元」。"""
    m = re.search(r"每?\s*10\s*股派(?:现|发现金红利|现金分红)?\s*([\d.]+)\s*元", text)
    if m:
        return float(m.group(1))
    m = re.search(r"10\s*派\s*([\d.]+)", text)
    if m:
        return float(m.group(1))
    return None


def _parse_share_reduction(
    text: str, profile: EventProfile | None = None
) -> tuple[float | None, float | None]:
    """解析减持事件：返回 (减持股数-万股, 占总股本%)。

    比例允许「减持…不超过公司总股本2%」这类中间插词的表述；
    正则失败时用 EventProfile.metrics_mentioned 中含 减持/股本/股份/不超过 的百分比兜底。
    """
    shares = None
    m = re.search(r"([\d.]+)\s*万股", text)
    if m:
        shares = float(m.group(1))
    pct = None
    m = re.search(r"减持[^0-9]{0,12}?([\d.]+)\s*(?:%|％)", text)
    if m:
        pct = float(m.group(1))
    if pct is None and profile:
        for item in profile.metrics_mentioned:
            name, value = item.get("name", ""), item.get("value", "")
            if any(k in name for k in ("减持", "股本", "股份", "不超过")):
                mm = re.match(r"([\d.]+)\s*(?:%|％)", value)
                if mm:
                    pct = float(mm.group(1))
                    break
    return shares, pct


_NEG_WORDS = ("下滑", "下降", "减少", "跌", "降", "亏损", "承压", "下调", "不及预期")
_POS_WORDS = ("增长", "提升", "增加", "涨", "升", "超预期", "改善", "翻倍", "扭亏")
# 结构性占比（如「海外收入占比80%」）不是同比信号，不得进入盈利预测调整
_STRUCT_WORDS = ("占比", "比例", "集中度", "份额")


def extract_event_signals(profile: EventProfile | None) -> dict[str, float]:
    """从 EventProfile.metrics_mentioned 提取方向性信号（小数口径）。

    返回 {"profit": -0.40, "revenue": 0.25, "margin": -0.05} 形式；
    方向优先看指标名中的动词（下滑/增长…），其次 direction 字段，最后看数值符号。
    绝对额（亿元）不产生同比信号。
    """
    out: dict[str, float] = {}
    if not profile:
        return out
    for m in profile.metrics_mentioned:
        name, value, direction = m.get("name", ""), m.get("value", ""), m.get("direction", "")
        if "亿" in value or "万" in value:
            continue
        mm = re.search(r"([+-]?\d+\.?\d*)", value)
        if not mm:
            continue
        v = float(mm.group(1))
        if any(w in name for w in _STRUCT_WORDS):
            continue
        if any(w in name for w in _NEG_WORDS):
            v = -abs(v)
        elif any(w in name for w in _POS_WORDS):
            v = abs(v)
        elif direction == "下降":
            v = -abs(v)
        elif direction == "上升":
            v = abs(v)
        if abs(v) > 200:  # 明显异常值不做信号（如谣言中的 +500%）
            continue
        if "净利" in name or "利润" in name:
            out.setdefault("profit", v / 100)
        elif "营收" in name or "收入" in name:
            out.setdefault("revenue", v / 100)
        elif "毛利" in name:
            out.setdefault("margin", v / 100)
    return out


def is_dividend(profile: EventProfile | None) -> bool:
    if not profile:
        return False
    text = (profile.event_subtype or "") + " " + " ".join(profile.keywords) + " " + " ".join(profile.subjects)
    return any(k in text for k in ["分红", "派息", "股息", "股利"])


def is_reduction(profile: EventProfile | None) -> bool:
    if not profile:
        return False
    text = (profile.event_subtype or "") + " " + " ".join(profile.keywords)
    return "减持" in text


def run_calc_agent(
    event: EventInput,
    profile: EventProfile | None,
    fin: FinancialContext,
) -> list[DerivedMetric]:
    out: list[DerivedMetric] = []
    price_dp = get_metric(fin, "收盘价")
    mcap_dp = get_metric(fin, "总市值")
    profit_dp = get_metric(fin, "归母净利润", "净利润")
    ocf_dp = get_metric(fin, "经营活动现金流量净额", "经营活动产生的现金流量净额", "经营现金流")

    price = price_dp.value if price_dp else None
    mcap = mcap_dp.value if mcap_dp else None  # 亿元
    profit = profit_dp.value if profit_dp else None  # 亿元（H1 口径）
    ocf = ocf_dp.value if ocf_dp else None  # 亿元

    # 总股本（亿股）= 总市值 / 收盘价
    shares = None
    if price and mcap:
        shares = mcap / price
        out.append(DerivedMetric(
            key="total_shares", label="总股本", value=round(shares, 2),
            display=f"{shares:,.2f} 亿股",
            formula="总市值 ÷ 收盘价",
            inputs=[f"总市值 {mcap:,.2f} 亿元（pricePerformance）", f"收盘价 {price} 元（pricePerformance）"],
            confidence="高",
        ))

    if is_dividend(profile):
        per10 = _parse_dividend_per_10(event.event_description)
        dps = per10 / 10 if per10 else None
        if dps is None:
            out.append(_missing("dps", "每股股利", "每10股派现 ÷ 10", ["事件描述中未解析到派现金额"]))
        else:
            out.append(DerivedMetric(
                key="dps", label="每股股利", value=round(dps, 4),
                display=f"{dps:.2f} 元/股",
                formula="每10股派现 ÷ 10",
                inputs=[f"每10股派现 {per10} 元（事件描述）"],
                confidence="高",
            ))

        # 分红总额：优先用事件描述中的官方口径（合计派现X亿），否则 每股股利 × 总股本
        div_total = None
        div_src = ""
        m_total = re.search(r"(?:合计|共计|总)?派(?:现|发现金红利|现金红利)?[^0-9]{0,4}([\d.]+)\s*亿", event.event_description)
        if m_total:
            div_total = float(m_total.group(1))
            div_src = f"合计派现 {div_total:g} 亿元（事件描述）"
            out.append(DerivedMetric(
                key="dividend_total", label="现金分红总额", value=div_total,
                display=f"{div_total:,.2f} 亿元",
                formula="直接引用事件描述的官方派现总额",
                inputs=[div_src],
                confidence="高",
            ))
        elif dps is not None and shares:
            div_total = dps * shares  # 亿元
            div_src = "计算"
            out.append(DerivedMetric(
                key="dividend_total", label="现金分红总额", value=round(div_total, 2),
                display=f"{div_total:,.2f} 亿元",
                formula="每股股利 × 总股本",
                inputs=[f"每股股利 {dps:.2f} 元（计算）", f"总股本 {shares:,.2f} 亿股（计算）"],
                confidence="中",
            ))
        else:
            out.append(_missing("dividend_total", "现金分红总额", "每股股利 × 总股本",
                                ["每股股利" if dps is None else "总股本"]))

        # 分红率 = 分红总额 / 归母净利润（期间对齐：年度分红应对年度净利，
        # 快照为 YTD 口径时年化×2 估算；中期分红直接对 YTD 净利；
        # 对齐后仍 >100% 时保留口径警示）
        if div_total is not None and profit:
            is_interim = any(k in event.event_description for k in ("中期", "半年度", "interim", "Interim"))
            if is_interim:
                profit_base = profit
                period_note = f"{profit_dp.period or 'YTD'}（中期口径，未年化）"
                conf = "中"
            else:
                profit_base = profit * 2
                period_note = f"{profit_dp.period or 'YTD'} 快照年化×2 估算（约 {profit_base:,.2f} 亿元）"
                conf = "低"
            ratio = div_total / profit_base * 100
            note_inputs = [f"分红总额 {div_total:,.2f} 亿元（{div_src or '计算'}）",
                           f"归母净利润 {profit:,.2f} 亿元（{period_note}，财务快照）"]
            if ratio > 100:
                conf = "低"
                note_inputs.append("口径警示：对齐后分红率仍超 100%，请结合事件描述中的官方分红率口径解读")
            out.append(DerivedMetric(
                key="payout_ratio", label="分红率", value=round(ratio, 2),
                display=f"{ratio:.2f}%",
                formula="现金分红总额 ÷ 归母净利润（期间对齐）",
                inputs=note_inputs,
                confidence=conf,
            ))
        else:
            out.append(_missing("payout_ratio", "分红率", "现金分红总额 ÷ 归母净利润",
                                ["分红总额" if div_total is None else "归母净利润"]))

        # 股息率 = 每股股利 / 收盘价
        if dps is not None and price:
            dy = dps / price * 100
            out.append(DerivedMetric(
                key="dividend_yield", label="股息率（本次分红折算）", value=round(dy, 2),
                display=f"{dy:.2f}%",
                formula="每股股利 ÷ 收盘价",
                inputs=[f"每股股利 {dps:.2f} 元（计算）", f"收盘价 {price} 元（pricePerformance）"],
                confidence="高",
            ))
        else:
            out.append(_missing("dividend_yield", "股息率", "每股股利 ÷ 收盘价",
                                ["每股股利" if dps is None else "收盘价"]))

        # 现金流覆盖倍数（现金流 < 0.5亿 视为快照未覆盖该科目，不输出误导性的小数）
        if div_total is not None and ocf and ocf >= 0.5:
            cov = ocf / div_total
            out.append(DerivedMetric(
                key="ocf_coverage", label="经营现金流覆盖倍数", value=round(cov, 2),
                display=f"{cov:.2f}x",
                formula="经营活动现金流净额 ÷ 现金分红总额",
                inputs=[f"经营现金流 {ocf:,.2f} 亿元（财务快照）", f"分红总额 {div_total:,.2f} 亿元（计算）"],
                confidence="中",
            ))
        elif div_total is not None:
            out.append(_missing("ocf_coverage", "经营现金流覆盖倍数",
                                "经营活动现金流净额 ÷ 现金分红总额", ["经营现金流"]))

    elif is_reduction(profile):
        sh_wan, pct = _parse_share_reduction(event.event_description, profile)
        if sh_wan and price:
            amount = sh_wan * 1e4 * price / 1e8  # 亿元
            out.append(DerivedMetric(
                key="reduction_amount", label="减持套现金额", value=round(amount, 2),
                display=f"{amount:,.2f} 亿元",
                formula="减持股数 × 收盘价",
                inputs=[f"减持 {sh_wan:,.0f} 万股（事件描述）", f"收盘价 {price} 元（pricePerformance）"],
                confidence="中",
            ))
        if pct and mcap:
            amt2 = pct / 100 * mcap
            out.append(DerivedMetric(
                key="reduction_mcap_pct", label="减持占总市值比例", value=round(pct, 2),
                display=f"{pct:.2f}%（约 {amt2:,.2f} 亿元）",
                formula="减持比例 × 总市值",
                inputs=[f"减持比例 {pct}%（事件描述）", f"总市值 {mcap:,.2f} 亿元（pricePerformance）"],
                confidence="中",
            ))
        if not out:
            out.append(_missing("reduction_amount", "减持规模", "减持股数 × 收盘价", ["减持股数/收盘价"]))

    else:
        # 财报/经营事件：直接引用快照中的同比指标（真实数据，不计算）
        for key, label in [("营业总收入同比", "营收同比"), ("归母净利润同比", "归母净利润同比"), ("毛利率", "毛利率")]:
            dp = get_metric(fin, key)
            if dp and dp.value is not None:
                out.append(DerivedMetric(
                    key=key, label=label, value=round(dp.value, 2),
                    display=dp.display,
                    formula="直接引用财务快照",
                    inputs=[f"{key} {dp.display}（{dp.period or '最新报告期'}，财务快照）"],
                    confidence="高",
                ))
            else:
                out.append(_missing(key, label, "直接引用财务快照", [key]))

    return out


def get_derived(metrics: list[DerivedMetric], key: str) -> DerivedMetric | None:
    for m in metrics:
        if m.key == key:
            return m
    return None
