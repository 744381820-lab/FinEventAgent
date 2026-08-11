"""L1-L4 分层 Agent 单元测试与端到端集成测试。"""
from __future__ import annotations

import asyncio
import json
import pytest

from demo.agent.schemas import EventInput
from demo.agent.agents.l1_fact_check import L1FactCheckAgent
from demo.agent.agents.l2_impact import L2ImpactAgent
from demo.agent.agents.l3_valuation import L3ValuationAgent
from demo.agent.agents.l4_decision import L4DecisionAgent
from demo.agent.orchestrator import analyze


# ---------- Fixtures ----------

@pytest.fixture
def sample_event() -> EventInput:
    return EventInput(
        company_name="宁德时代",
        stock_code="sz300750",
        event_description="宁德时代2025H1营收同比下降12%，但海外业务收入占比从18%提升至32%。毛利率逆势提升2.3个百分点至26.1%。",
        event_type="财报披露",
        position_ratio=5.2,
        cost_basis=195.0,
        investment_horizon="medium",
        risk_tolerance="medium",
    )


@pytest.fixture
def dividend_event() -> EventInput:
    return EventInput(
        company_name="宁德时代",
        stock_code="sz300750",
        event_description="宁德时代公告2026年中期分红方案，每10股派现14.11元，合计派现约61.8亿元。",
        event_type="分红派息",
        position_ratio=8.0,
        cost_basis=180.0,
        investment_horizon="long",
        risk_tolerance="low",
    )


# ---------- L1 单测 ----------

@pytest.mark.asyncio
async def test_l1_fact_check_agent(sample_event):
    agent = L1FactCheckAgent()
    out = await agent.run(sample_event, use_live=False)
    assert "profile" in out
    assert "fact_check" in out
    assert out["profile"].event_subtype
    assert out["fact_check"].status in {"verified", "partial", "unverified"}
    assert 0 <= out["fact_check"].confidence <= 100
    assert len(out["trace"]) >= 2


@pytest.mark.asyncio
async def test_l1_dividend_event(dividend_event):
    agent = L1FactCheckAgent()
    out = await agent.run(dividend_event, use_live=False)
    assert out["profile"].event_subtype in {"分红派息", "分红"}
    assert "分红" in out["profile"].keywords or "派息" in out["profile"].keywords


# ---------- L2 单测 ----------

@pytest.mark.asyncio
async def test_l2_impact_agent(sample_event):
    l1 = L1FactCheckAgent()
    l1_out = await l1.run(sample_event, use_live=False)
    profile = l1_out["profile"]

    l2 = L2ImpactAgent()
    out = await l2.run(sample_event, use_live=False, profile=profile, fin=None, derived=[])

    assert "fundamental" in out
    assert "sentiment" in out
    assert "industry_chain" in out
    assert "impact_matrix" in out
    assert out["fundamental"].key_judgment
    assert out["sentiment"].total_mentions >= 0
    assert len(out["trace"]) >= 4

    matrix = out["impact_matrix"]
    for dim in ["growth", "profitability", "sentiment", "industry_chain", "uncertainty"]:
        assert dim in matrix
        assert "score" in matrix[dim]
        assert "reason" in matrix[dim]


# ---------- L3 单测 ----------

@pytest.mark.asyncio
async def test_l3_valuation_agent(sample_event):
    l1 = L1FactCheckAgent()
    l1_out = await l1.run(sample_event, use_live=False)
    profile = l1_out["profile"]
    fact = l1_out["fact_check"]

    l2 = L2ImpactAgent()
    l2_out = await l2.run(sample_event, use_live=False, profile=profile, fin=None, derived=[])

    l3 = L3ValuationAgent()
    out = await l3.run(
        sample_event,
        use_live=False,
        profile=profile,
        fin=None,
        derived=[],
        fundamental_summary=l2_out["fundamental"].summary,
        fact_status=fact.status,
        impact_matrix=l2_out["impact_matrix"],
    )

    assert "valuations" in out
    assert len(out["valuations"]) >= 1
    assert "impact_pct" in out
    assert out["primary"] is not None
    assert out["primary"].method


# ---------- L4 单测 ----------

def test_l4_decision_agent(sample_event):
    l4 = L4DecisionAgent()
    out = l4.run(
        sample_event,
        fundamental_judgment="业绩短期承压，长期结构改善",
        valuation_impact_pct=-3.5,
        profile=None,
        derived=[],
        impact_matrix={},
    )
    assert "strategy" in out
    assert out["strategy"].recommendation
    assert 0 <= out["strategy"].confidence <= 100
    assert len(out["strategy"].options) >= 1
    assert len(out["strategy"].risk_warnings) >= 1


# ---------- 集成测试 ----------

@pytest.mark.asyncio
async def test_full_analysis_flow(sample_event):
    """端到端：输入事件 → L1-L4 全链路 → 输出完整 AnalysisResult。"""
    result = await analyze(sample_event)
    assert result.event_profile is not None
    assert result.fact_check is not None
    assert result.fundamental is not None
    assert result.sentiment is not None
    assert result.valuation is not None
    assert result.strategy is not None
    assert result.executive_summary
    assert len(result.trace) >= 4


@pytest.mark.asyncio
async def test_analysis_output_structure_stable(sample_event):
    """同一事件连续运行 2 次，核心字段结构一致。"""
    r1 = await analyze(sample_event)
    r2 = await analyze(sample_event)
    assert r1.event_profile.event_subtype == r2.event_profile.event_subtype
    assert r1.fact_check.status == r2.fact_check.status
    assert r1.strategy.recommendation == r2.strategy.recommendation


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
