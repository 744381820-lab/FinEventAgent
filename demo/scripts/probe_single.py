import asyncio
import io
import json
import sys
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from demo.agent.jinmen_mcp import jinmen_client


def unwrap_exc(e: BaseException, depth: int = 0) -> None:
    indent = "  " * depth
    print(f"{indent}{type(e).__name__}: {e}")
    if hasattr(e, "exceptions"):
        for sub in e.exceptions:
            unwrap_exc(sub, depth + 1)


async def probe_one(name: str, coro) -> None:
    print(f"\n===== {name} =====")
    try:
        data = await coro
        text = json.dumps(data, ensure_ascii=False, indent=2)
        print(text[:1500])
    except BaseException as e:
        unwrap_exc(e)


async def main() -> None:
    if not jinmen_client.available:
        print("jinmen mcp not configured")
        return

    await probe_one("get_stock_details", jinmen_client.get_stock_details(["sz300750"]))


if __name__ == "__main__":
    asyncio.run(main())
