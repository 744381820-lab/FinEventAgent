from __future__ import annotations

import json
import logging
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.sse import sse_client

from .config import settings

# 压低 MCP SDK 内部 SSE 读循环的噪音日志（连接关闭竞态 BrokenResourceError / ReadTimeout，
# 已被业务层 try/except 兜底，刷屏无信息量）
logging.getLogger("mcp").setLevel(logging.CRITICAL)
logging.getLogger("mcp.client.sse").setLevel(logging.CRITICAL)


class JinmenMCPError(RuntimeError):
    pass


class JinmenMCPClient:
    """
    进门 MCP SSE 客户端（真实接入）。

    官方配置：
      URL: https://mcp-server-global.comein.cn/mcp-servers/mcp-server-brm/sse
      Header: x-mcp-key: <个人密钥>
    """

    def __init__(self) -> None:
        self.base_url = settings.jinmen_mcp_url
        self.api_key = settings.jinmen_mcp_key
        self.timeout = settings.jinmen_mcp_timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.base_url)

    async def _with_session(self, fn):
        import asyncio
        headers = {"x-mcp-key": self.api_key}
        async def _run():
            async with sse_client(self.base_url, headers=headers) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await fn(session)
        return await asyncio.wait_for(_run(), timeout=self.timeout)

    async def list_tools(self) -> list[dict[str, Any]]:
        async def _list(session: ClientSession):
            result = await session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in result.tools
            ]

        return await self._with_session(_list)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        async def _call(session: ClientSession):
            result = await session.call_tool(name, arguments)
            is_error = getattr(result, "isError", getattr(result, "is_error", False))
            if is_error:
                text = result.content[0].text if result.content else "unknown error"
                raise JinmenMCPError(f"{name} 调用失败: {text}")
            content = result.content[0] if result.content else None
            if content and content.type == "text":
                text = content.text
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}
            return {"raw": str(content)}

        return await self._with_session(_call)

    # ---------- 领域封装 ----------

    async def get_stock_details(self, queries: list[str]) -> Any:
        return await self.call_tool("get_stock_details", {"queries": queries})

    async def get_financial_snapshot(self, queries: list[str]) -> Any:
        return await self.call_tool(
            "get_financial_snapshot",
            {"queries": queries, "period_type": ["YTD", "Q"]},
        )

    async def get_main_business_segments(
        self,
        full_code: str,
        fiscal_years: list[str],
        report_types: list[str] | None = None,
        item_classify: list[str] | None = None,
        search_data: list[str] | None = None,
    ) -> Any:
        report_types = report_types or ["S1", "A"]
        item_classify = item_classify or ["按产品", "按地区"]
        search_data = search_data or ["营业收入", "营业成本", "毛利率", "收入同比"]
        report_dates = [
            {"fiscalYear": y, "reportType": rt, "type": "year"}
            for y in fiscal_years
            for rt in report_types
        ]
        return await self.call_tool(
            "get_main_business_segments",
            {
                "companyInfos": [{"fullCode": full_code}],
                "itemClassify": item_classify,
                "reportDates": report_dates,
                "searchData": search_data,
            },
        )

    async def search_announcements(
        self, full_code: str, query: str, label_list: str | None = None, top_k: int = 8
    ) -> Any:
        args: dict[str, Any] = {"fullCode": full_code, "query": query, "topK": top_k}
        if label_list:
            args["labelList"] = label_list
        return await self.call_tool("searchAnnouncementReport", args)

    async def search_analyst_comments(
        self, query: str, start_time: str | None = None, top_k: int = 20
    ) -> Any:
        args: dict[str, Any] = {"query": query, "topK": top_k}
        if start_time:
            args["start_time"] = start_time
        return await self.call_tool("searchAnalystComments", args)

    async def search_comein_resource(
        self,
        query: str,
        content_types: list[str] | None = None,
        start_time: str | None = None,
        top_k: int = 20,
    ) -> Any:
        args: dict[str, Any] = {
            "query": query,
            "topK": top_k,
            "filterImage": True,
        }
        if content_types:
            args["contentTypes"] = content_types
        if start_time:
            args["start_time"] = start_time
        return await self.call_tool("searchComeinResource", args)

    async def research_query(
        self,
        keywords: str,
        market: str | None = None,
        stock_code: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Any:
        args: dict[str, Any] = {
            "keywords": keywords,
            "page": page,
            "pageSize": page_size,
        }
        if market and stock_code:
            args["market"] = market
            args["stockCode"] = stock_code
        if start_time:
            args["startTime"] = start_time
        if end_time:
            args["endTime"] = end_time
        return await self.call_tool("research_query", args)

    async def price_performance(self, queries: list[str]) -> Any:
        return await self.call_tool(
            "pricePerformance",
            {
                "queries": queries,
                "include": [
                    "standardized_info",
                    "regular_market",
                    "period_change",
                    "valuation",
                    "market_value",
                ],
            },
        )


def load_fixture(name: str = "catl_2025h1.json") -> dict[str, Any]:
    path = settings.fixture_dir / name
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


jinmen_client = JinmenMCPClient()
