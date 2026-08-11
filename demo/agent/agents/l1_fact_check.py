"""L1 信息核验 Agent：确认事实，输出结构化事件卡与置信度。"""
from __future__ import annotations

from typing import Any

from ..modules.event_extract import extract_event_profile
from ..modules.fact_check import run_fact_check
from ..schemas import EventInput, EventProfile, FactCheckResult, FinancialContext


class L1FactCheckAgent:
    """L1 信息核验 Agent：把非结构事件转为结构化事实集，并完成交叉验证。"""

    name = "L1 信息核验"
    description = "确认事实：事件分类、数据核验、置信度评估"

    async def run(
        self,
        event: EventInput,
        use_live: bool = True,
        fin: FinancialContext | None = None,
    ) -> dict[str, Any]:
        profile = await extract_event_profile(event, use_live=use_live)
        fact = await run_fact_check(event, use_live=use_live, profile=profile, fin=fin)
        return {
            "profile": profile,
            "fact_check": fact,
            "summary": f"事件分类：{profile.event_subtype}；核验状态：{fact.status}（置信度 {fact.confidence}%）",
            "trace": [
                {"agent": self.name, "action": "extract_profile", "output": profile.model_dump()},
                {"agent": self.name, "action": "fact_check", "output": fact.model_dump()},
            ],
        }
