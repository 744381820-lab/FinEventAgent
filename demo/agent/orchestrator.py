"""主控调度 Orchestrator：L1-L4 分层 Agent 的编排、上下文缓存与局部重算。"""
from __future__ import annotations

import time
import uuid
from typing import Any, AsyncGenerator

from .agents.calc_agent import run_calc_agent
from .agents.data_agent import run_data_agent
from .agents.l1_fact_check import L1FactCheckAgent
from .agents.l2_impact import L2ImpactAgent
from .agents.l3_valuation import L3ValuationAgent
from .agents.l4_decision import L4DecisionAgent
from .config import settings
from .schemas import AgentStep, AnalysisResult, EventInput

STEPS = [
    ("l1", "L1 信息核验", 20),
    ("l2", "L2 多维影响", 45),
    ("l3", "L3 估值映射", 75),
    ("l4", "L4 持仓决策", 90),
    ("summary", "综合报告生成", 100),
]

# 会话上下文：支持追加指令局部重算。内存缓存，TTL 1 小时。
_SESSION_STORE: dict[str, dict[str, Any]] = {}
_SESSION_TTL = 3600


def _mode_label(mode: str) -> str:
    return {
        "live": "实时模式（进门 MCP + 内部大模型）",
        "hybrid": "混合模式（进门 MCP + 本地规则）",
        "demo": "演示模式（本地样例数据）",
    }.get(mode, mode)


def _purge_expired() -> None:
    now = time.time()
    dead = [k for k, v in _SESSION_STORE.items() if now - v.get("ts", 0) > _SESSION_TTL]
    for k in dead:
        _SESSION_STORE.pop(k, None)


def _save_session(session_id: str, ctx: dict[str, Any]) -> None:
    _purge_expired()
    ctx["ts"] = time.time()
    _SESSION_STORE[session_id] = ctx


def get_session(session_id: str) -> dict[str, Any] | None:
    _purge_expired()
    return _SESSION_STORE.get(session_id)


def _build_summary(event: EventInput, profile, fact, fundamental, impact_pct: float, strategy) -> str:
    return (
        f"{event.company_name}「{profile.event_subtype or event.event_type}」事件核验状态为「{fact.status}」。"
        f"{fundamental.key_judgment} "
        f"估值影响约 {impact_pct}%，当前建议「{strategy.recommendation}」。"
    )


async def analyze_stream(event: EventInput) -> AsyncGenerator[dict[str, Any], None]:
    """主控调度：L1 → data → L2 → L3 → L4，缓存会话供局部重算。"""
    mode = settings.effective_mode
    use_live = mode in {"live", "hybrid"}
    session_id = uuid.uuid4().hex[:12]
    result = AnalysisResult(mode=mode, event=event)

    yield {
        "type": "init",
        "session_id": session_id,
        "mode": mode,
        "mode_label": _mode_label(mode),
        "architecture": "orchestrator_l1_l4",
        "steps": [
            s.model_dump()
            for s in [AgentStep(step=s[0], label=s[1], status="pending", progress=0) for s in STEPS]
        ],
    }

    # ============ L1 信息核验 Agent ============
    yield {"type": "step", "step": "l1", "status": "running", "message": "L1 信息核验：结构化解析事件并比对官方数据...", "progress": 5}
    l1 = L1FactCheckAgent()
    l1_out = await l1.run(event, use_live=use_live)
    profile = l1_out["profile"]
    fact = l1_out["fact_check"]
    result.event_profile = profile
    result.fact_check = fact
    yield {
        "type": "step",
        "step": "l1",
        "status": "done",
        "message": l1_out["summary"],
        "progress": 20,
        "data": {
            "profile": profile.model_dump(),
            "fact_check": fact.model_dump(),
            "trace": l1_out["trace"],
        },
    }

    # L1 完成后，立即采集底层数据供 L2/L3 使用
    yield {"type": "step", "step": "data", "status": "running", "message": "采集进门 MCP 底层财务数据并进行确定性计算...", "progress": 22}
    fin_ctx = await run_data_agent(event, use_live=use_live)
    derived = run_calc_agent(event, profile, fin_ctx)
    result.financial_context = fin_ctx
    result.derived_metrics = derived
    yield {
        "type": "step",
        "step": "data",
        "status": "done",
        "message": f"采集 {len(fin_ctx.metrics)} 项底层指标，完成 {sum(1 for m in derived if m.value is not None)}/{len(derived)} 项确定性计算",
        "progress": 25,
        "data": {
            "financial_context": fin_ctx.model_dump(),
            "derived_metrics": [m.model_dump() for m in derived],
        },
    }

    # ============ L2 多维影响 Agent ============
    yield {"type": "step", "step": "l2", "status": "running", "message": "L2 多维影响：并行分析基本面、舆情、产业链...", "progress": 30}
    l2 = L2ImpactAgent()
    l2_out = await l2.run(event, use_live=use_live, profile=profile, fin=fin_ctx, derived=derived)
    fundamental = l2_out["fundamental"]
    sentiment = l2_out["sentiment"]
    result.fundamental = fundamental
    result.sentiment = sentiment
    yield {
        "type": "step",
        "step": "l2",
        "status": "done",
        "message": l2_out["summary"],
        "progress": 55,
        "data": {
            "fundamental": fundamental.model_dump(),
            "sentiment": sentiment.model_dump(),
            "industry_chain": l2_out["industry_chain"],
            "impact_matrix": l2_out["impact_matrix"],
            "trace": l2_out["trace"],
        },
    }

    # ============ L3 估值映射 Agent ============
    yield {"type": "step", "step": "l3", "status": "running", "message": "L3 估值映射：将影响翻译为估值参数扰动...", "progress": 60}
    l3 = L3ValuationAgent()
    l3_out = await l3.run(
        event,
        use_live=use_live,
        profile=profile,
        fin=fin_ctx,
        derived=derived,
        fundamental_summary=fundamental.summary,
        fact_status=fact.status,
        impact_matrix=l2_out["impact_matrix"],
    )
    valuations = l3_out["valuations"]
    primary_valuation = l3_out["primary"]
    result.valuations = valuations
    result.valuation = primary_valuation
    yield {
        "type": "step",
        "step": "l3",
        "status": "done",
        "message": l3_out["summary"],
        "progress": 78,
        "data": {
            "valuations": [v.model_dump() for v in valuations],
            "primary": primary_valuation.model_dump() if primary_valuation else None,
            "impact_pct": l3_out["impact_pct"],
            "trace": l3_out["trace"],
        },
    }

    # ============ L4 持仓决策 Agent ============
    yield {"type": "step", "step": "l4", "status": "running", "message": "L4 持仓决策：结合组合约束生成操作建议...", "progress": 82}
    l4 = L4DecisionAgent()
    l4_out = l4.run(
        event,
        fundamental.key_judgment,
        l3_out["impact_pct"],
        profile=profile,
        derived=derived,
        impact_matrix=l2_out["impact_matrix"],
    )
    strategy = l4_out["strategy"]
    result.strategy = strategy
    yield {
        "type": "step",
        "step": "l4",
        "status": "done",
        "message": l4_out["summary"],
        "progress": 92,
        "data": {
            "strategy": strategy.model_dump(),
            "trace": l4_out["trace"],
        },
    }

    # ============ 综合报告 ============
    executive_summary = _build_summary(event, profile, fact, fundamental, l3_out["impact_pct"], strategy)
    result.executive_summary = executive_summary
    result.trace = [
        *l1_out["trace"],
        *l2_out["trace"],
        *l3_out["trace"],
        *l4_out["trace"],
    ]
    yield {"type": "step", "step": "summary", "status": "done", "message": "综合报告已生成", "progress": 100}

    # 缓存会话上下文，供 /api/refine 局部重算
    _save_session(
        session_id,
        {
            "event": event,
            "mode": mode,
            "use_live": use_live,
            "profile": profile,
            "fact": fact,
            "fin_ctx": fin_ctx,
            "derived": derived,
            "fundamental": fundamental,
            "sentiment": sentiment,
            "industry_chain": l2_out["industry_chain"],
            "impact_matrix": l2_out["impact_matrix"],
            "valuations": valuations,
            "primary": primary_valuation,
            "impact_pct": l3_out["impact_pct"],
            "strategy": strategy,
            "result": result,
        },
    )

    payload = result.model_dump()
    payload["session_id"] = session_id
    yield {"type": "complete", "session_id": session_id, "data": payload}


async def refine_stream(
    session_id: str,
    targets: list[str],
    event_overrides: dict[str, Any] | None = None,
    valuation_overrides: dict[str, Any] | None = None,
    instruction: str = "",
) -> AsyncGenerator[dict[str, Any], None]:
    """局部重算：基于会话缓存，仅重跑指定层级。

    targets 可选：l2 / l3 / l4 / data
    - 改仓位/风险偏好 → 只重跑 l4
    - 改估值参数（WACC/增长率）→ 重跑 l3+l4
    - 重跑舆情 → 只重跑 l2
    - 刷新底层数据 → data + 下游
    """
    ctx = get_session(session_id)
    if not ctx:
        yield {"type": "error", "message": "会话已过期或不存在，请重新发起完整分析"}
        return

    event: EventInput = ctx["event"]
    if event_overrides:
        data = event.model_dump()
        data.update({k: v for k, v in event_overrides.items() if v is not None})
        event = EventInput(**data)
        ctx["event"] = event

    use_live = ctx["use_live"]
    mode = ctx["mode"]
    profile = ctx["profile"]
    fact = ctx["fact"]
    fin_ctx = ctx["fin_ctx"]
    derived = ctx["derived"]
    fundamental = ctx["fundamental"]
    sentiment = ctx["sentiment"]
    industry_chain = ctx["industry_chain"]
    impact_matrix = ctx["impact_matrix"]
    valuations = ctx["valuations"]
    primary = ctx["primary"]
    impact_pct = ctx["impact_pct"]
    strategy = ctx["strategy"]

    # 规范化目标：l3 隐含 l4；data 隐含 l2/l3/l4；l2 隐含 l3/l4
    target_set = set(targets or [])
    if "data" in target_set:
        target_set |= {"l2", "l3", "l4"}
    if "l2" in target_set:
        target_set |= {"l3", "l4"}
    if "l3" in target_set or valuation_overrides:
        target_set |= {"l3", "l4"}
    if event_overrides and any(k in (event_overrides or {}) for k in ("position_ratio", "cost_basis", "investment_horizon", "risk_tolerance")):
        target_set.add("l4")
    if not target_set:
        target_set = {"l4"}

    yield {
        "type": "init",
        "session_id": session_id,
        "mode": mode,
        "mode_label": _mode_label(mode),
        "refine": True,
        "targets": sorted(target_set),
        "instruction": instruction,
        "steps": [
            s.model_dump()
            for s in [AgentStep(step=s[0], label=s[1], status="pending", progress=0) for s in STEPS if s[0] in target_set or s[0] == "summary"]
        ],
    }

    # ---- data 刷新 ----
    if "data" in target_set:
        yield {"type": "step", "step": "data", "status": "running", "message": "重新采集进门 MCP 底层数据...", "progress": 10}
        fin_ctx = await run_data_agent(event, use_live=use_live)
        derived = run_calc_agent(event, profile, fin_ctx)
        ctx["fin_ctx"] = fin_ctx
        ctx["derived"] = derived
        yield {
            "type": "step",
            "step": "data",
            "status": "done",
            "message": f"刷新采集 {len(fin_ctx.metrics)} 项底层指标",
            "progress": 20,
            "data": {
                "financial_context": fin_ctx.model_dump(),
                "derived_metrics": [m.model_dump() for m in derived],
            },
        }

    # ---- L2 ----
    if "l2" in target_set:
        yield {"type": "step", "step": "l2", "status": "running", "message": "重跑 L2：基本面 / 舆情 / 产业链...", "progress": 30}
        l2 = L2ImpactAgent()
        l2_out = await l2.run(event, use_live=use_live, profile=profile, fin=fin_ctx, derived=derived)
        fundamental = l2_out["fundamental"]
        sentiment = l2_out["sentiment"]
        industry_chain = l2_out["industry_chain"]
        impact_matrix = l2_out["impact_matrix"]
        ctx["fundamental"] = fundamental
        ctx["sentiment"] = sentiment
        ctx["industry_chain"] = industry_chain
        ctx["impact_matrix"] = impact_matrix
        yield {
            "type": "step",
            "step": "l2",
            "status": "done",
            "message": l2_out["summary"],
            "progress": 50,
            "data": {
                "fundamental": fundamental.model_dump(),
                "sentiment": sentiment.model_dump(),
                "industry_chain": industry_chain,
                "impact_matrix": impact_matrix,
                "trace": l2_out["trace"],
            },
        }

    # ---- L3 ----
    if "l3" in target_set:
        yield {"type": "step", "step": "l3", "status": "running", "message": "重跑 L3：估值参数扰动测算...", "progress": 60}
        l3 = L3ValuationAgent()
        l3_out = await l3.run(
            event,
            use_live=use_live,
            profile=profile,
            fin=fin_ctx,
            derived=derived,
            fundamental_summary=fundamental.summary,
            fact_status=fact.status,
            impact_matrix=impact_matrix,
            param_overrides=valuation_overrides,
        )
        valuations = l3_out["valuations"]
        primary = l3_out["primary"]
        impact_pct = l3_out["impact_pct"]
        ctx["valuations"] = valuations
        ctx["primary"] = primary
        ctx["impact_pct"] = impact_pct
        yield {
            "type": "step",
            "step": "l3",
            "status": "done",
            "message": l3_out["summary"],
            "progress": 75,
            "data": {
                "valuations": [v.model_dump() for v in valuations],
                "primary": primary.model_dump() if primary else None,
                "impact_pct": impact_pct,
                "trace": l3_out["trace"],
            },
        }

    # ---- L4 ----
    if "l4" in target_set:
        yield {"type": "step", "step": "l4", "status": "running", "message": "重跑 L4：持仓策略建议...", "progress": 82}
        l4 = L4DecisionAgent()
        l4_out = l4.run(
            event,
            fundamental.key_judgment,
            impact_pct,
            profile=profile,
            derived=derived,
            impact_matrix=impact_matrix,
        )
        strategy = l4_out["strategy"]
        ctx["strategy"] = strategy
        yield {
            "type": "step",
            "step": "l4",
            "status": "done",
            "message": l4_out["summary"],
            "progress": 92,
            "data": {
                "strategy": strategy.model_dump(),
                "trace": l4_out["trace"],
            },
        }

    # ---- summary ----
    result = AnalysisResult(
        mode=mode,
        event=event,
        event_profile=profile,
        financial_context=fin_ctx,
        derived_metrics=derived,
        fact_check=fact,
        sentiment=sentiment,
        fundamental=fundamental,
        valuation=primary,
        valuations=valuations,
        strategy=strategy,
        executive_summary=_build_summary(event, profile, fact, fundamental, impact_pct, strategy),
        trace=[],
    )
    ctx["result"] = result
    _save_session(session_id, ctx)

    yield {"type": "step", "step": "summary", "status": "done", "message": "局部重算完成", "progress": 100}
    payload = result.model_dump()
    payload["session_id"] = session_id
    payload["refine"] = True
    payload["targets"] = sorted(target_set)
    payload["instruction"] = instruction
    yield {"type": "complete", "session_id": session_id, "data": payload}


async def analyze(event: EventInput) -> AnalysisResult:
    final = None
    async for chunk in analyze_stream(event):
        if chunk.get("type") == "complete":
            final = chunk["data"]
    return AnalysisResult(**{k: v for k, v in final.items() if k in AnalysisResult.model_fields})
