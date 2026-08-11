from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import settings


async def web_search(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    provider = settings.sentiment_cfg["web_search_provider"]
    try:
        if provider == "tavily" and settings.tavily_api_key:
            return await _tavily_search(query, max_results)
        if provider == "bing" and settings.bing_search_api_key:
            return await _bing_search(query, max_results)
        if provider in {"duckduckgo", "bing_html", "web"}:
            return await _bing_html_search(query, max_results)
    except Exception:
        pass
    return []


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc
        return host.replace("www.", "") or "外部搜索"
    except Exception:
        return "外部搜索"


async def _bing_html_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """普通联网搜索（cn.bing.com HTML，免 Key，国内可达）。失败时静默返回空列表。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
        resp = await client.get(
            "https://cn.bing.com/search",
            params={"q": query, "count": max_results, "setlang": "zh-CN"},
        )
        resp.raise_for_status()
        html = resp.text

    results: list[dict[str, Any]] = []
    # 结构：<li class="b_algo"...><h2 class=""><a href="url">标题</a></h2>...<div class="b_caption"><p>摘要</p>
    blocks = re.split(r'<li class="b_algo"', html)[1:]
    for block in blocks:
        m_title = re.search(
            r'<h2[^>]*>\s*<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            block,
            re.S,
        )
        if not m_title:
            continue
        url = m_title.group(1)
        title = re.sub(r"<[^>]+>", "", m_title.group(2)).strip()
        m_snip = re.search(r'<p[^>]*class="[^"]*"[^>]*>(.*?)</p>', block, re.S) or re.search(
            r"<p[^>]*>(.*?)</p>", block, re.S
        )
        snippet = re.sub(r"<[^>]+>", "", m_snip.group(1)).strip() if m_snip else ""
        results.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "source": _domain(url),
                "published_at": "",
            }
        )
        if len(results) >= max_results:
            break
    return results


async def _tavily_search(query: str, max_results: int) -> list[dict[str, Any]]:
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": settings.tavily_api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
                "source": r.get("source", "tavily"),
                "published_at": r.get("published_date", ""),
            }
            for r in results
        ]


async def _bing_search(query: str, max_results: int) -> list[dict[str, Any]]:
    url = "https://api.bing.microsoft.com/v7.0/search"
    headers = {"Ocp-Apim-Subscription-Key": settings.bing_search_api_key}
    params = {"q": query, "count": max_results, "mkt": "zh-CN"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        pages = data.get("webPages", {}).get("value", [])
        return [
            {
                "title": p.get("name", ""),
                "url": p.get("url", ""),
                "snippet": p.get("snippet", ""),
                "source": "bing",
                "published_at": p.get("dateLastCrawled", ""),
            }
            for p in pages
        ]
