"""L3 估值映射 Agent：将基本面影响翻译为估值参数扰动，输出价值重估区间。"""
from __future__ import annotations

from typing import Any

from ..modules.valuation import run_valuations
from ..schemas import DerivedMetric, EventInput, EventProfile, FinancialContext, ValuationResult


class L3ValuationAgent:
    """L3 估值映射 Agent：回答「这些影响如何改变合理估值中枢」。"""

    name = "L3 估值映射"
    description = "将基本面与情绪影响翻译为估值模型参数扰动，测算内在价值重估区间"

    async def run(
        self,
        event: EventInput,
        use_live: bool = True,
        profile: EventProfile | None = None,
        fin: FinancialContext | None = None,
        derived: list[DerivedMetric] | None = None,
        fundamental_summary: str = "",
        fact_status: str = "",
        impact_matrix: dict[str, Any] | None = None,
        param_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        valuations = await run_valuations(
            event,
            fundamental_summary,
            use_live=use_live,
            profile=profile,
            fin=fin,
            derived=derived,
            fact_status=fact_status,
            param_overrides=param_overrides,
        )
        primary = valuations[0] if valuations else None

        return {
            "valuations": valuations,
            "primary": primary,
            "impact_pct": primary.impact_pct if primary else 0.0,
            "summary": f"估值影响 {primary.impact_pct}%（{primary.method}）" if primary else "估值数据缺失",
            "trace": [
                {"agent": self.name, "action": "run_valuations", "output": [v.model_dump() for v in valuations]},
                {"agent": self.name, "action": "param_overrides", "output": param_overrides or {}},
            ],
        }

    async def rerun_with_params(
        self,
        event: EventInput,
        base_result: dict[str, Any],
        param_overrides: dict[str, Any],
        **kwargs,
    ) -> dict[str, Any]:
        """人工干预后局部重算：仅重跑 L3，保留 L1/L2 结果。"""
        return await self.run(event, param_overrides=param_overrides, **kwargs)
