from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from ..config import settings
from ..jinmen_mcp import jinmen_client, load_fixture
from ..schemas import EventInput, EventProfile, SentimentAnalysis, SentimentItem
from .web_search import web_search


def _parse_time(s: Any) -> datetime | None:
    """兼容字符串日期与进门 MCP 的 businessTime 毫秒时间戳。"""
    if s is None or s == "":
        return None
    # epoch ms / s
    if isinstance(s, (int, float)) or (isinstance(s, str) and s.isdigit()):
        try:
            n = int(s)
            if n > 10_000_000_000:  # ms
                n = n / 1000
            if n > 1_000_000_000:  # 合理的 unix 秒
                return datetime.fromtimestamp(n)
        except Exception:
            pass
    text = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except Exception:
            continue
    # 从文本里捞 YYYY-MM-DD
    m = re.search(r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2})", text)
    if m:
        try:
            return datetime.strptime(m.group(1).replace("/", "-"), "%Y-%m-%d")
        except Exception:
            pass
    return None


def _extract_publish_time(item: dict[str, Any]) -> str:
    """从进门/联网条目中尽量提取发布时间。进门真实字段是 businessTime(ms)。"""
    for key in (
        "businessTime",
        "publishTime",
        "publishedAt",
        "published_at",
        "createTime",
        "createdAt",
        "releaseTime",
        "time",
        "date",
    ):
        raw = item.get(key)
        dt = _parse_time(raw)
        if dt:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
    # 正文/标题兜底
    blob = " ".join(str(item.get(k) or "") for k in ("title", "viewpoint", "mainText", "snippet", "summary"))
    dt = _parse_time(blob)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _extract_keywords(text: str, top_n: int = 12) -> list[dict[str, Any]]:
    words = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", text)
    stop = {
        "公司", "业务", "收入", "同比", "增长", "下降", "报告", "上半年", "2025", "2024",
        "时代", "宁德", "影响", "分析", "数据", "公告", "显示", "表示", "认为", "我们",
    }
    words = [w for w in words if w not in stop and len(w) >= 2]
    counter = Counter(words)
    return [{"keyword": k, "frequency": v} for k, v in counter.most_common(top_n)]


def _emotion_level(item: dict[str, Any]) -> str:
    level = (item.get("emotionalLevel") or "").strip()
    if level in {"正面", "中性", "负面"}:
        return level
    text = (item.get("title") or "") + (item.get("viewpoint") or "")
    pos = any(k in text for k in ["超预期", "推荐", "买入", "改善", "高增", "突破"])
    neg = any(k in text for k in ["低于预期", "下滑", "风险", "承压", "减持"])
    if pos and not neg:
        return "正面"
    if neg and not pos:
        return "负面"
    return "中性"


def _channel_type(item: dict[str, Any]) -> str:
    doc_type = (item.get("docType") or "").upper()
    mapping = {
        "DOMESTIC_REPORT": "内资研报",
        "FOREIGN_REPORT": "外资研报",
        "MINUTES": "路演纪要",
        "COMMENT": "分析师点评",
        "ARTICLE": "公众号",
        "ANNOUNCEMENT_REPORT": "公告",
    }
    if doc_type in mapping:
        return mapping[doc_type]
    # 真实数据经常缺 docType，按机构名 / URL 二次分类
    inst = (item.get("institutionName") or "") + " " + (item.get("url") or "")
    text = inst.lower()
    if any(k in inst for k in ["证券", "研究", "研报", "券商"]):
        return "内资研报"
    if any(k in text for k in ["reuters", "bloomberg", "gs.com", "jpmorgan", "morganstanley"]):
        return "外资研报"
    if any(k in inst for k in ["纪要", "路演", "电话会"]):
        return "路演纪要"
    if any(k in text for k in ["weixin", "mp.weixin", "公众号"]):
        return "公众号"
    if any(k in text for k in ["cninfo", "sse.com", "szse.cn", "公告"]):
        return "公告"
    if any(k in text for k in ["sina", "eastmoney", "cls.cn", "yicai", "wallstreetcn", "36kr", "qq.com", "163.com"]):
        return "财经媒体"
    return "其他"


async def run_sentiment(event: EventInput, use_live: bool = True, profile: EventProfile | None = None) -> SentimentAnalysis:
    # 优先用结构化抽取的查询词；未抽取时退回 公司名+事件类型
    if profile and profile.search_queries:
        jinmen_query = profile.search_queries[0]
        web_query = profile.search_queries[min(1, len(profile.search_queries) - 1)]
    else:
        jinmen_query = f"{event.company_name} {event.event_type}"
        web_query = f"{event.company_name} {event.event_type} 营收 净利润 同比"

    jinmen_items: list[dict[str, Any]] = []
    web_items: list[dict[str, Any]] = []

    scfg = settings.sentiment_cfg
    if use_live and jinmen_client.available:
        try:
            res = await jinmen_client.search_comein_resource(
                jinmen_query,
                content_types=["domestic_report", "foreign_report", "minutes", "comment", "article"],
                top_k=scfg["jinmen_top_k"],
            )
            if isinstance(res, list):
                jinmen_items = res
            elif isinstance(res, dict) and "data" in res:
                jinmen_items = res.get("data", [])
        except Exception:
            pass
        try:
            comments = await jinmen_client.search_analyst_comments(jinmen_query, top_k=15)
            if isinstance(comments, list):
                jinmen_items.extend(comments)
        except Exception:
            pass

    # 外部搜索（默认 bing_html 免Key，权重见 demo/config/analysis.json）
    if scfg["web_search_enabled"]:
        try:
            web_items = await web_search(web_query, max_results=scfg["web_search_max_results"])
            # 过滤明显无关（不含公司名）的结果
            web_items = [
                it for it in web_items
                if event.company_name in (it.get("title", "") + it.get("snippet", ""))
                or "CATL" in (it.get("title", "") + it.get("snippet", "")).upper()
            ]
        except Exception:
            web_items = []

    # 统一为 SentimentItem
    items: list[SentimentItem] = []
    for it in jinmen_items[:25]:
        title = it.get("title") or "进门投研内容"
        summary = (it.get("viewpoint") or it.get("mainText") or "")[:160]
        published = _extract_publish_time(it)
        items.append(
            SentimentItem(
                title=title,
                channel=it.get("institutionName") or _channel_type(it),
                channel_type=_channel_type(it),
                published_at=published,
                summary=summary,
                url=it.get("url", "") or it.get("displayUrl") or "",
                heat_score=int((it.get("score") or 0.5) * 100) if isinstance(it.get("score"), (int, float)) else 60,
            )
        )
    for it in web_items:
        published = _extract_publish_time(it) or str(it.get("published_at") or "")
        items.append(
            SentimentItem(
                title=it.get("title", "外部报道"),
                channel=it.get("source", "外部搜索"),
                channel_type="外部搜索",
                published_at=published,
                summary=it.get("snippet", "")[:160],
                url=it.get("url", ""),
                heat_score=50,
            )
        )

    # 统计
    total = len(items)
    jinmen_count = len(jinmen_items)
    web_count = len(web_items)

    channel_counter = Counter([i.channel_type for i in items])
    channel_distribution = [
        {"channel": k, "count": v} for k, v in channel_counter.most_common()
    ]

    time_counter: dict[str, int] = defaultdict(int)
    dated_items = 0
    for i in items:
        dt = _parse_time(i.published_at)
        if dt:
            time_counter[dt.strftime("%Y-%m-%d")] += 1
            dated_items += 1
    time_distribution = [
        {"date": k, "count": v} for k, v in sorted(time_counter.items())[-14:]
    ]

    all_text = " ".join([i.title + " " + i.summary for i in items])
    top_keywords = _extract_keywords(all_text, 12)

    emotion_counter = Counter([_emotion_level(it) for it in jinmen_items])
    if not emotion_counter:
        emotion_counter = Counter({"中性": 1})
    emotion_distribution = [
        {"emotion": k, "count": v} for k, v in emotion_counter.most_common()
    ]

    if total >= 15:
        heat_trend = "高热"
    elif total >= 5:
        heat_trend = "中等"
    else:
        heat_trend = "低热"

    if not items:
        fixture = load_fixture()
        demo = fixture.get("demo_sentiment", {})
        return SentimentAnalysis(
            total_mentions=demo.get("total_mentions", 187),
            jinmen_count=131,
            web_count=56,
            channel_distribution=demo.get("channel_distribution", []),
            time_distribution=demo.get("time_distribution", []),
            top_keywords=demo.get("top_keywords", []),
            emotion_distribution=demo.get("emotion_distribution", []),
            heat_trend="中等",
            items=[],
            source_note="当前为离线样例数据；配置进门 MCP Key 后展示真实统计",
        )

    return SentimentAnalysis(
        total_mentions=total,
        jinmen_count=jinmen_count,
        web_count=web_count,
        channel_distribution=channel_distribution,
        time_distribution=time_distribution,
        top_keywords=top_keywords,
        emotion_distribution=emotion_distribution,
        heat_trend=heat_trend,
        items=items[:20],
        source_note=(
            f"进门投研舆情（权重 {scfg['jinmen_weight']}）"
            + (f" + 联网搜索·{scfg['web_search_provider']}（权重 {scfg['web_weight']}）" if web_count else "")
            + (f"；其中 {dated_items}/{total} 条含发布时间" if total else "")
            + ("；时序样本不足，图可能为空" if total and dated_items == 0 else "")
        ),
    )
