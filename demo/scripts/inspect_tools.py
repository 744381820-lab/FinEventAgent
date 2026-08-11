import asyncio
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from mcp import ClientSession
from mcp.client.sse import sse_client

KEY = "cm_ab6d57ecc55549a0ba561c4fe5965b26"
URL = "https://mcp-server-global.comein.cn/mcp-servers/mcp-server-brm/sse"

WANTED = {
    "get_stock_details",
    "get_financial_snapshot",
    "searchAnnouncementReport",
    "searchAnalystComments",
    "pricePerformance",
    "research_query",
    "get_main_business_segments",
    "searchComeinResource",
}


async def main() -> None:
    headers = {"x-mcp-key": KEY}
    async with sse_client(URL, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            for t in tools.tools:
                if t.name in WANTED:
                    print(f"\n=== {t.name} ===")
                    print(json.dumps(t.input_schema, ensure_ascii=False, indent=2)[:1400])


if __name__ == "__main__":
    asyncio.run(main())
