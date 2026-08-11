import asyncio
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from mcp import ClientSession
from mcp.client.sse import sse_client

KEY = "cm_ab6d57ecc55549a0ba561c4fe5965b26"
URL = "https://mcp-server-global.comein.cn/mcp-servers/mcp-server-brm/sse"


async def main() -> None:
    headers = {"x-mcp-key": KEY}
    async with sse_client(URL, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"TOOL_COUNT={len(names)}")
            for n in names:
                print(f"- {n}")
            # quick call
            for target in ["get_stock_details", "get_financial_snapshot", "searchAnnouncementReport"]:
                if target in names:
                    try:
                        result = await session.call_tool(target, {"stock_codes": ["300750.SZ"]})
                        text = result.content[0].text if result.content else ""
                        print(f"\n=== {target} sample ===")
                        print(text[:800])
                    except Exception as e:
                        print(f"\n=== {target} error: {e} ===")
                    break


if __name__ == "__main__":
    asyncio.run(main())
