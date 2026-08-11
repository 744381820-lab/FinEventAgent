"""探查进门财务快照中现金流/净利润相关字段的真实名称、单位与数值。"""
import asyncio
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from demo.agent.jinmen_mcp import jinmen_client


async def main() -> None:
    snap = await jinmen_client.get_financial_snapshot(["sz300750"])
    data = snap.get("data", {}).get("sz300750", {})
    items = data.get("snapshotItems", [])
    print(f"共 {len(items)} 项 snapshotItems")
    for it in items:
        name = it.get("name_cn", "")
        if any(k in name for k in ("现金", "净利", "流量", "分红", "股利", "股息")):
            print(f"  {name!r:40} value={it.get('value')!r} unit={it.get('unit')!r} period={it.get('period')!r}")


if __name__ == "__main__":
    asyncio.run(main())
