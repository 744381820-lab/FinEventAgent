"""StrategyAgent：持仓策略生成（规则矩阵，数字引用 CalcAgent 结果）。"""
from __future__ import annotations

from ..schemas import DerivedMetric, EventInput, EventProfile, PositionStrategy, StrategyOption
from ..agents.calc_agent import get_derived, is_dividend, is_reduction


def run_strategy(
    event: EventInput,
    fundamental_judgment: str,
    valuation_impact_pct: float,
    profile: EventProfile | None = None,
    derived: list[DerivedMetric] | None = None,
) -> PositionStrategy:
    derived = derived or []
    position = event.position_ratio
    horizon = event.investment_horizon

    # ============ 分红事件 ============
    if is_dividend(profile):
        payout = get_derived(derived, "payout_ratio")
        dy = get_derived(derived, "dividend_yield")
        cov = get_derived(derived, "ocf_coverage")
        payout_t = payout.display if payout else "数据缺失"
        dy_t = dy.display if dy else "数据缺失"
        cov_t = cov.display if cov else "数据缺失"

        # 规则矩阵：覆盖倍数充足 + 股息率有吸引力 → 偏积极
        covered = cov is not None and cov.value is not None and cov.value >= 1.5
        attractive = dy is not None and dy.value is not None and dy.value >= 1.0
        no_position = position <= 0
        concentrated = position >= 20
        if covered and attractive:
            recommendation, confidence = ("建仓/逢低关注", 78) if no_position else (
                ("持有（仓位已高）", 76) if concentrated else ("持有/逢低增持", 80))
        elif covered or attractive:
            recommendation, confidence = ("观察/轻仓试探", 72) if no_position else ("持有", 74)
        else:
            recommendation, confidence = ("观望", 68) if no_position else ("持有观望", 68)

        cons = StrategyOption(
            name="保守策略", action="持有" if not no_position else "观察",
            position_advice=(f"维持 {position}% 仓位" if not no_position else "维持空仓，列入收息观察池"),
            logic="分红属股东回报型事件，不改变经营趋势，无需因单一事件调仓。",
            trigger_condition="若后续经营数据恶化（毛利率/现金流下滑），再考虑降仓",
        )
        neutral = StrategyOption(
            name="中性策略", action="持有/小幅增持" if not no_position else "轻仓建仓",
            position_advice=(f"维持或加至 {min(position + 2, 10)}%" if not no_position else "建仓 2-3% 底仓"),
            logic=f"分红率 {payout_t}、股息率 {dy_t}，现金流覆盖 {cov_t}，股东回报确定性可参考上述溯源数据。",
            trigger_condition="若除权后股息率仍具吸引力且盈利趋势向上，可继续增持",
        )
        if concentrated:
            agg = StrategyOption(
                name="积极策略", action="持有",
                position_advice=f"维持 {position}% 仓位，不加仓",
                logic="单一标的仓位已超 20% 集中度参考线，分红利好不足以作为继续集中的理由。",
                trigger_condition="先通过组合再平衡释放集中度空间，再考虑增持",
            )
        else:
            agg = StrategyOption(
                name="积极策略", action="增持" if not no_position else "建仓",
                position_advice=(f"从 {position}% 加至 {min(position + 4, 12)}%" if not no_position else "分批建仓至 4-6%"),
                logic=f"现金流覆盖倍数 {cov_t}，分红可持续性以该指标为核心依据。",
                trigger_condition="适合投资期限 ≥1 年、看重股东回报的组合" + ("；短线需警惕除权后技术性回调" if horizon == "short" else ""),
            )

        risk_warnings = [
            "分红后除权除息带来的短期价格波动",
            f"若经营现金流覆盖倍数（当前 {cov_t}）持续低于 1x，分红可持续性存疑",
            "行业景气度变化导致盈利下修，分红率被动抬升",
        ]
        if concentrated:
            risk_warnings.append(f"单一标的仓位 {position}% 已超 20% 集中度参考线，注意组合回撤风险")

        return PositionStrategy(
            recommendation=recommendation,
            confidence=confidence,
            options=[cons, neutral, agg],
            risk_warnings=risk_warnings,
            watch_points=[
                "除权除息日后股价表现与股息率变化",
                "下一报告期经营现金流对分红的覆盖能力",
                "公司后续分红政策与回购公告",
            ],
        )

    # ============ 减持事件 ============
    if is_reduction(profile):
        amt = get_derived(derived, "reduction_amount")
        mpct = get_derived(derived, "reduction_mcap_pct")
        amt_t = amt.display if amt else "数据缺失"
        mpct_t = mpct.display if mpct else "数据缺失"
        big = mpct is not None and mpct.value is not None and mpct.value >= 1.0
        no_position = position <= 0
        if no_position:
            recommendation, confidence = "回避/观望", 70
        elif big:
            recommendation, confidence = "谨慎/减仓", 72
        else:
            recommendation, confidence = "持有观望", 66
        target = max(position - 3, 0)
        return PositionStrategy(
            recommendation=recommendation,
            confidence=confidence,
            options=[
                StrategyOption(
                    name="保守策略", action="减仓" if not no_position else "回避",
                    position_advice=(
                        (f"从 {position}% 降至 {target}%" if target > 0 else f"从 {position}% 清仓观察")
                        if not no_position else "当前空仓，回避至减持实施完毕"
                    ),
                    logic=f"减持规模 {amt_t}（占总市值 {mpct_t}），短期抛压与情绪冲击明确，先降仓规避。",
                    trigger_condition="减持实施完毕且股价企稳后再评估回补",
                ),
                StrategyOption(
                    name="中性策略", action="持有",
                    position_advice=f"维持 {position}% 仓位",
                    logic="减持不改变基本面，若减持比例有限且通过大宗交易实施，冲击可控。",
                    trigger_condition="关注减持实际执行节奏与接盘方",
                ),
                StrategyOption(
                    name="积极策略", action="逢低关注",
                    position_advice="暂不操作，等待情绪释放",
                    logic="若减持引发超跌而基本面未变，可能出现错杀机会。",
                    trigger_condition="需确认减持原因为股东自身资金需求而非基本面恶化",
                ),
            ],
            risk_warnings=[
                "减持实施期间的持续抛压",
                "大股东减持可能隐含对公司前景的负面信号",
                "若叠加业绩下修，可能形成戴维斯双杀",
            ],
            watch_points=[
                "减持公告的执行进度披露",
                "大宗交易成交价格与接盘方",
                "公司基本面数据是否同步恶化",
            ],
        )

    # ============ 默认：财报/经营事件（估值影响 × 持仓画像 规则矩阵） ============
    concentrated = position >= 20
    no_position = position <= 0
    risk_low = event.risk_tolerance == "low"

    if valuation_impact_pct > 2:
        if no_position:
            recommendation, confidence = "积极关注/建仓", 76
        elif concentrated:
            recommendation, confidence = "持有（仓位已高，不建议再加）", 74
        else:
            recommendation, confidence = "积极关注/加仓", 78
    elif valuation_impact_pct > -2:
        if no_position:
            recommendation, confidence = "观望，暂不建仓", 70
        elif concentrated:
            recommendation, confidence = "持有观望（仓位偏高，控制加仓）", 71
        elif position >= 10:
            recommendation, confidence = "持有观望（中等仓位，以观察为主）", 72
        else:
            recommendation, confidence = "持有观望（轻仓，可跟踪验证）", 73
    else:
        recommendation, confidence = "谨慎/减仓", 68 if not (risk_low or horizon == "short") else 74
        if no_position:
            recommendation, confidence = "回避/观望", 72

    # 让局部重算可感知：建议文案始终带上当前仓位与风险偏好
    risk_label = {"low": "保守", "medium": "稳健", "high": "激进"}.get(event.risk_tolerance, event.risk_tolerance)
    recommendation = f"{recommendation}｜仓位{position:g}%·{risk_label}"
    if risk_low and valuation_impact_pct < 0 and not no_position:
        confidence = min(confidence + 2, 90)

    aggressive_trigger = "适合投资期限 ≥1 年、风险承受能力较高的组合"
    if horizon == "short":
        aggressive_trigger = "短线投资者建议回避事件后 1-2 周波动"

    # 保守策略：按是否持仓区分「减仓」与「不建仓」
    if no_position:
        cons_action, cons_advice = "不建仓", "当前空仓，事件影响未明朗前不建仓"
        cons = StrategyOption(
            name="保守策略", action=cons_action, position_advice=cons_advice,
            logic="空仓状态下无止损压力，等待事件影响被官方数据验证后再评估。",
            trigger_condition="若官方数据证伪利空，可转入建仓评估",
        )
    else:
        target = max(position - 3, 0)
        cons = StrategyOption(
            name="保守策略", action="减仓",
            position_advice=(f"从 {position}% 降至 {target}%" if target > 0 else f"从 {position}% 清仓观察"),
            logic="规避短期业绩下修带来的情绪波动，保留底仓观察数据持续性。",
            trigger_condition="若后续核心指标进一步恶化，继续降仓",
        )

    # 中性策略
    neutral = StrategyOption(
        name="中性策略", action="持有" if not no_position else "观察",
        position_advice=(f"维持 {position}% 仓位" if not no_position else "维持空仓，跟踪数据验证"),
        logic="短期影响已部分反映在估值中，等待后续数据验证。",
        trigger_condition="若核心指标边际改善，可上调至积极策略",
    )

    # 积极策略：集中度红线限制加仓，空仓改为建仓表述
    if no_position:
        agg = StrategyOption(
            name="积极策略", action="建仓",
            position_advice="分批建仓至 3-5%",
            logic="看好基本面结构改善带来的重估机会，当前估值具备配置价值。",
            trigger_condition=aggressive_trigger,
        )
    elif concentrated:
        agg = StrategyOption(
            name="积极策略", action="持有",
            position_advice=f"维持 {position}% 仓位，不加仓",
            logic="单一标的仓位已超 20% 集中度参考线，即使看好也应优先控制组合风险。",
            trigger_condition="如需加仓，先通过减仓其他标的释放集中度空间",
        )
    else:
        agg = StrategyOption(
            name="积极策略", action="加仓",
            position_advice=f"从 {position}% 分批加至 {min(position + 5, 15)}%",
            logic="看好基本面结构改善带来的重估机会，当前估值具备配置价值。",
            trigger_condition=aggressive_trigger,
        )

    risk_warnings = [
        "核心指标改善不及预期",
        "行业政策与外部环境扰动",
        "市场竞争加剧导致盈利承压",
    ]
    if concentrated:
        risk_warnings.insert(0, f"单一标的仓位 {position}% 已超 20% 集中度参考线，事件冲击对组合净值影响被放大")
    if horizon == "short" and valuation_impact_pct < 0:
        risk_warnings.append("投资期限为短期，事件负面情绪释放期内净值波动风险更高")

    return PositionStrategy(
        recommendation=recommendation,
        confidence=confidence,
        options=[cons, neutral, agg],
        risk_warnings=risk_warnings,
        watch_points=[
            "下一报告期核心财务指标变化",
            "公司公告与行业订单数据",
            "机构持仓与评级调整动向",
        ],
    )
