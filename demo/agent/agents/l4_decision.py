"""L4 持仓决策 Agent：结合组合约束，输出情景化操作建议。"""
from __future__ import annotations

from typing import Any

from ..modules.strategy import run_strategy
from ..schemas import DerivedMetric, EventInput, EventProfile, PositionStrategy


class L4DecisionAgent:
    """L4 持仓决策 Agent：回答「给定组合与约束，应该怎么做」。"""

    name = "L4 持仓决策"
    description = "结合持仓、风控与估值结论，生成可执行的操作建议"

    def run(
        self,
        event: EventInput,
        fundamental_judgment: str,
        valuation_impact_pct: float,
        profile: EventProfile | None = None,
        derived: list[DerivedMetric] | None = None,
        impact_matrix: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        strategy = run_strategy(
            event,
            fundamental_judgment,
            valuation_impact_pct,
            profile=profile,
            derived=derived,
        )
        return {
            "strategy": strategy,
            "summary": f"建议：{strategy.recommendation}（置信度 {strategy.confidence}%）",
            "trace": [
                {"agent": self.name, "action": "run_strategy", "output": strategy.model_dump()},
            ],
        }
