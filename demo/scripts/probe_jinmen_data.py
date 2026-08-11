import asyncio
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from demo.agent.jinmen_mcp import jinmen_client


async def main() -> None:
    print("MODE=", jinmen_client.available)
    if not jinmen_client.available:
        print("jinmen mcp not configured")
        return

    tasks = [
        ("get_stock_details", jinmen_client.get_stock_details(["sz300750"])),
        (
            "get_financial_snapshot",
            jinmen_client.get_financial_snapshot(["sz300750"]),
        ),
        (
            "search_announcements",
            jinmen_client.search_announcements(
                "sz300750",
                "2025年半年度报告 营业收入 海外收入占比",
                label_list="2025S1",
                top_k=5,
            ),
        ),
        (
            "search_analyst_comments",
            jinmen_client.search_analyst_comments(
                "宁德时代 2025半年报 海外收入占比 毛利率", start_time="2025-08-01", top_k=10
            ),
        ),
        (
            "price_performance",
            jinmen_client.price_performance(["sz300750"]),
        ),
    ]

    for name, coro in tasks:
        print(f"\n\n===== {name} =====")
        try:
            data = await coro
            text = json.dumps(data, ensure_ascii=False, indent=2)
            print(text[:2200])
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
            if hasattr(e, "exceptions"):
                for sub in e.exceptions:
                    print(f"  SUB: {type(sub).__name__}: {sub}")


if __name__ == "__main__":
    asyncio.run(main())
