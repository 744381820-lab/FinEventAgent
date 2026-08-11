"""FundamentalAgent：基本面影响分析。

数字纪律：metric_table 中的金额/比率全部来自 CalcAgent 的 DerivedMetric
（带 formula + inputs 溯源）；LLM 只做文字解读，且输出经溯源校验，
发现未溯源的新数字即回退到模板表述。
"""
from __future__ import annotations

import re

from ..config import settings
from ..jinmen_mcp import jinmen_client
from ..llm import llm_client
from ..schemas import (
    ChainLink,
    ChainNode,
    DerivedMetric,
    EventInput,
    EventProfile,
    FinancialContext,
    FundamentalImpact,
    MetricImpact,
)
from ..agents.calc_agent import extract_event_signals, get_derived, is_dividend, is_reduction
from ..agents.data_agent import get_metric


def _dp_text(dp) -> str:
    return dp.display if dp else "数据缺失"


def _narrate_safe(text: str, allowed_numbers: set[str]) -> str | None:
    """LLM 输出校验：提取文本中的数字，若出现不在白名单内的「新数字」则判为失真，返回 None。"""
    nums = set(re.findall(r"\d+\.?\d*", text))
    # 允许小整数（序号、年份尾数等）与溯源白名单
    suspicious = {n for n in nums if n not in allowed_numbers and float(n) >= 10}
    return None if suspicious else text


def _allowed_numbers(derived: list[DerivedMetric], event: EventInput, fin: FinancialContext | None) -> set[str]:
    allowed = set(re.findall(r"\d+\.?\d*", event.event_description))
    for m in derived:
        allowed.update(re.findall(r"\d+\.?\d*", m.display))
        for inp in m.inputs:
            allowed.update(re.findall(r"\d+\.?\d*", inp))
    if fin:
        for dp in fin.metrics.values():
            allowed.update(re.findall(r"\d+\.?\d*", dp.display))
    return allowed


async def run_fundamental(
    event: EventInput,
    use_live: bool = True,
    profile: EventProfile | None = None,
    fin: FinancialContext | None = None,
    derived: list[DerivedMetric] | None = None,
) -> FundamentalImpact:
    derived = derived or []
    period = (profile.time_scope if profile else "") or "最新报告期"

    # ============ 分红事件：全部用 CalcAgent 真实计算结果 ============
    if is_dividend(profile):
        dps = get_derived(derived, "dps")
        div_total = get_derived(derived, "dividend_total")
        payout = get_derived(derived, "payout_ratio")
        dy = get_derived(derived, "dividend_yield")
        cov = get_derived(derived, "ocf_coverage")
        roe_dp = get_metric(fin, "ROE", "净资产收益率")

        def _row(m: DerivedMetric | None, metric: str, direction: str, certainty: str, note: str) -> MetricImpact:
            if m is None or m.value is None:
                return MetricImpact(metric=metric, direction="中性", magnitude="数据缺失",
                                    period=period, certainty="低", explanation=f"{note}；底层数据缺失，未做估算")
            return MetricImpact(
                metric=metric, direction=direction, magnitude=m.display,
                period=period, certainty=m.confidence,
                explanation=f"{note}；计算口径：{m.formula}（{'；'.join(m.inputs)}）",
            )

        metric_table = [
            _row(payout, "分红率", "上升", "中", "以本次分红总额对最新报告期归母净利润计算"),
            _row(dps, "每股股利", "上升", "高", "由事件描述的每10股派现折算"),
            _row(dy, "股息率", "正面", "高", "按最新收盘价折算的本次分红股息率"),
            _row(cov, "现金流覆盖", "正面", "中", "经营现金流对分红的覆盖能力"),
        ]
        if roe_dp:
            metric_table.append(MetricImpact(
                metric="ROE", direction="正面", magnitude="被动提升",
                period="中期", certainty="中",
                explanation=f"分红降低净资产规模，同等盈利下 ROE 被动抬升；最新财报值：{roe_dp.display}",
            ))

        chain_nodes = [
            ChainNode(id="event", label="分红事件", value=(profile.one_line[:24] if profile else "分红派息"), detail=profile.event_subtype if profile else "分红派息"),
            ChainNode(id="dps", label="每股股利", value=dps.display if dps else "—", detail="事件描述折算"),
            ChainNode(id="total", label="分红总额", value=div_total.display if div_total else "数据缺失", detail="每股股利 × 总股本"),
            ChainNode(id="payout", label="分红率", value=payout.display if payout else "数据缺失", detail="分红总额 ÷ 归母净利润"),
            ChainNode(id="fcf", label="现金流覆盖", value=cov.display if cov else "数据缺失", detail="经营现金流 ÷ 分红总额"),
            ChainNode(id="signal", label="市场信号", value="股东回报", detail="回报确定性变化"),
        ]
        chain_links = [
            ChainLink(source="event", target="dps", label="折算"),
            ChainLink(source="dps", target="total", label="×总股本"),
            ChainLink(source="total", target="payout", label="÷净利润"),
            ChainLink(source="total", target="fcf", label="依赖"),
            ChainLink(source="payout", target="signal", label="传递"),
        ]
        segment_chart = []
        if payout and payout.value is not None:
            p = min(round(payout.value), 100)
            segment_chart = [
                {"segment": "留存收益", "pre_ratio": 100, "post_ratio": 100 - p},
                {"segment": "分红派息", "pre_ratio": 0, "post_ratio": p},
            ]
        summary = (
            f"本次分红每股股利 {dps.display if dps else '未知'}，"
            f"现金分红总额约 {div_total.display if div_total else '未知'}，"
            f"对最新报告期归母净利润的分红率约 {payout.display if payout else '未知'}，"
            f"按最新收盘价折算股息率 {dy.display if dy else '未知'}。"
            f"经营现金流对分红的覆盖倍数 {cov.display if cov else '未知'}。"
            "以上数字均由底层财务数据确定性计算得出，口径见各指标溯源。"
        )
        key_judgment = "分红属于『股东回报型』事件，不改变经营趋势，影响集中在股东回报结构与市场预期。"

    # ============ 减持事件 ============
    elif is_reduction(profile):
        amt = get_derived(derived, "reduction_amount")
        mpct = get_derived(derived, "reduction_mcap_pct")
        metric_table = [
            MetricImpact(metric="减持规模", direction="负面",
                         magnitude=amt.display if amt and amt.value is not None else "数据缺失",
                         period=period, certainty=amt.confidence if amt else "低",
                         explanation=f"计算口径：{amt.formula}（{'；'.join(amt.inputs)}）" if amt else "底层数据缺失"),
            MetricImpact(metric="占总市值比例", direction="负面",
                         magnitude=mpct.display if mpct and mpct.value is not None else "数据缺失",
                         period=period, certainty=mpct.confidence if mpct else "低",
                         explanation="衡量抛压相对市场承接能力的量级"),
            MetricImpact(metric="流动性冲击", direction="负面", magnitude="短期承压",
                         period="1-4周", certainty="中", explanation="大宗交易方式对二级市场直接冲击有限，但情绪面偏空"),
        ]
        chain_nodes = [
            ChainNode(id="event", label="减持事件", value=(profile.one_line[:24] if profile else "股东减持"), detail="大股东减持"),
            ChainNode(id="scale", label="减持规模", value=amt.display if amt else "数据缺失", detail="减持股数 × 收盘价"),
            ChainNode(id="pressure", label="抛压", value=mpct.display if mpct else "数据缺失", detail="占总市值比例"),
            ChainNode(id="sentiment", label="市场情绪", value="偏空", detail="信号意义负面"),
        ]
        chain_links = [
            ChainLink(source="event", target="scale", label="测算"),
            ChainLink(source="scale", target="pressure", label="形成"),
            ChainLink(source="pressure", target="sentiment", label="压制"),
        ]
        segment_chart = []
        if amt and amt.value is not None and mpct and mpct.value is not None:
            scale_txt = f"减持规模约 {amt.display}，占总市值 {mpct.display}"
        elif mpct and mpct.value is not None:
            scale_txt = f"减持规模占总市值 {mpct.display}"
        else:
            scale_txt = "减持规模未知"
        summary = f"股东拟{scale_txt}。减持不改变公司基本面，但短期形成抛压与负面情绪，需关注减持节奏与承接力。"
        key_judgment = "减持属于『情绪与流动性型』利空，基本面无直接变化，影响集中在短期股价与市场预期。"

    # ============ 财报/经营事件：同比指标直接引用快照真实值 ============
    else:
        rev_yoy = get_derived(derived, "营业总收入同比")
        profit_yoy = get_derived(derived, "归母净利润同比")
        gm = get_derived(derived, "毛利率")

        def _ref(m: DerivedMetric | None, metric: str, good_when_up: bool = True) -> MetricImpact:
            if m is None or m.value is None:
                return MetricImpact(metric=metric, direction="中性", magnitude="数据缺失",
                                    period=period, certainty="低", explanation="财务快照未覆盖该指标，未做估算")
            direction = ("上升" if m.value >= 0 else "下降") if good_when_up else "中性"
            return MetricImpact(
                metric=metric, direction=direction, magnitude=m.display,
                period=period, certainty=m.confidence,
                explanation=f"直接引用进门财务快照（{'；'.join(m.inputs)}）",
            )

        metric_table = [
            _ref(rev_yoy, "营业收入同比"),
            _ref(profit_yoy, "归母净利润同比"),
            _ref(gm, "毛利率", good_when_up=False),
        ]
        chain_nodes = [
            ChainNode(id="event", label="财报事件", value=event.event_description[:24], detail="输入事件"),
            ChainNode(id="rev", label="营收同比", value=rev_yoy.display if rev_yoy else "数据缺失", detail="财务快照"),
            ChainNode(id="profit", label="净利润同比", value=profit_yoy.display if profit_yoy else "数据缺失", detail="财务快照"),
            ChainNode(id="gm", label="毛利率", value=gm.display if gm else "数据缺失", detail="财务快照"),
        ]
        chain_links = [
            ChainLink(source="event", target="rev", label="披露"),
            ChainLink(source="rev", target="profit", label="传导"),
            ChainLink(source="gm", target="profit", label="影响"),
        ]
        segment_chart = []
        # 事件自身声明（用户输入口径）与官方快照对照呈现，避免「事件说了什么」在分析中消失
        claims_txt = ""
        if profile and profile.metrics_mentioned:
            claims_txt = "、".join(
                f"{m.get('name', '')}{m.get('value', '')}" for m in profile.metrics_mentioned[:4]
            )
        signals = extract_event_signals(profile)
        summary = (
            (f"事件披露：{claims_txt}（用户输入口径，真实性见核验环节）。" if claims_txt else "")
            + f"官方最新报告期数据：营收同比 {rev_yoy.display if rev_yoy else '未知'}，"
            f"归母净利润同比 {profit_yoy.display if profit_yoy else '未知'}，"
            f"毛利率 {gm.display if gm else '未知'}（进门财务快照）。"
        )
        if signals.get("profit", 0) <= -0.15 or signals.get("revenue", 0) <= -0.15:
            key_judgment = "事件披露数据指向业绩明显承压，盈利预测存在下修风险，需以后续官方披露与订单数据验证。"
        elif signals.get("profit", 0) >= 0.10 or signals.get("revenue", 0) >= 0.10:
            key_judgment = "事件披露数据指向业绩改善，若经核验属实，盈利预测与估值中枢存在上修空间。"
        elif signals:
            key_judgment = "事件披露数据指向业绩温和变化，对基本面趋势影响有限，关注后续数据持续性。"
        else:
            key_judgment = "以官方披露口径为准评估业绩趋势，事件描述与官方数据的差异已在核验环节标注。"

    # ============ LLM 精炼（仅解读，数字注入 + 溯源校验） ============
    if use_live and llm_client.feature_enabled("fundamental_narrative"):
        try:
            derived_text = "\n".join(
                f"- {m.label}: {m.display}（{m.formula}；{'；'.join(m.inputs)}）" for m in derived
            )
            prompt = f"""你是一位资深行业研究员。基于以下事件与【已计算好的指标】，写一段 120 字以内的基本面影响总结，并给出一句『关键判断』。

事件类型：{profile.event_subtype if profile else event.event_type}
事件：{event.event_description}

【已计算好的指标】（数字只允许引用这里，禁止编造任何新数字）：
{derived_text}

要求输出 JSON：{{"summary": "...", "key_judgment": "..."}}"""
            temp = float(settings.llm_cfg.get("temperature_narrative") or 0.2)
            data = await llm_client.chat_json(
                [{"role": "user", "content": prompt}],
                temperature=temp,
            )
            allowed = _allowed_numbers(derived, event, fin)
            s = data.get("summary")
            k = data.get("key_judgment")
            if s:
                safe = _narrate_safe(s, allowed)
                if safe:
                    summary = safe
            if k:
                safe_k = _narrate_safe(k, allowed)
                if safe_k:
                    key_judgment = safe_k
        except Exception:
            pass

    return FundamentalImpact(
        summary=summary,
        metric_table=metric_table,
        chain_nodes=chain_nodes,
        chain_links=chain_links,
        segment_chart=segment_chart,
        key_judgment=key_judgment,
    )
