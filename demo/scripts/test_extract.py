import sys, io, asyncio, json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from demo.agent.modules.event_extract import extract_event_profile
from demo.agent.schemas import EventInput


async def main():
    cases = [
        EventInput(
            event_description="宁德时代宣布2025年中期分红方案，拟每10股派现12元，合计分红约53亿元，分红率约30%，同时公布回购计划上限80亿元",
            event_type="其他",
        ),
        EventInput(
            event_description="宁德时代发布2025年半年报，营收1789亿元同比-7.3%，归母净利润305亿元同比+33.4%",
            event_type="财报披露",
        ),
    ]
    for ev in cases:
        p = await extract_event_profile(ev, use_live=True)
        print("=== ", ev.event_description[:30])
        print(json.dumps(p.model_dump(), ensure_ascii=False, indent=1))


asyncio.run(main())
