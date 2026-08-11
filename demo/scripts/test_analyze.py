import asyncio
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from demo.agent.orchestrator import analyze
from demo.agent.schemas import EventInput


async def main() -> None:
    event = EventInput(
        company_name="宁德时代",
        stock_code="sz300750",
        event_description="宁德时代2025H1营收同比下降12%，但海外业务收入占比从18%提升至32%",
        event_type="财报披露",
        position_ratio=5.0,
        investment_horizon="medium",
        risk_tolerance="medium",
    )
    result = await analyze(event)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2)[:3500])


if __name__ == "__main__":
    asyncio.run(main())
