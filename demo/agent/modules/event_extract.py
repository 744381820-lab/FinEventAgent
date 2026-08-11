from __future__ import annotations

import re
from typing import Any

from ..llm import llm_client
from ..schemas import EventInput, EventProfile

# 业务关键词词典：事件词 → 关联财务科目（规则兜底 + 查询增强用）
_EVENT_LEXICON: dict[str, dict[str, Any]] = {
    "分红": {"subjects": ["股息率", "分红比例", "每股股利", "自由现金流", "未分配利润"], "queries": ["分红 派息 股息"]},
    "派息": {"subjects": ["股息率", "分红比例", "每股股利", "自由现金流"], "queries": ["派息 股息 分红率"]},
    "股息": {"subjects": ["股息率", "分红比例", "每股股利"], "queries": ["股息 派息"]},
    "回购": {"subjects": ["回购金额", "库存股", "每股收益", "净资产"], "queries": ["回购 注销"]},
    "定增": {"subjects": ["募集资金", "股本稀释", "资产负债率", "资本开支"], "queries": ["定增 募资 增发"]},
    "增发": {"subjects": ["募集资金", "股本稀释", "资产负债率"], "queries": ["增发 募资"]},
    "减持": {"subjects": ["股东减持比例", "流通盘", "大宗交易"], "queries": ["减持 大宗交易"]},
    "增持": {"subjects": ["股东增持比例", "流通盘"], "queries": ["增持"]},
    "并购": {"subjects": ["商誉", "对价", "协同效应", "资产负债率"], "queries": ["并购 收购 重组"]},
    "收购": {"subjects": ["商誉", "对价", "协同效应"], "queries": ["收购 并购"]},
    "财报": {"subjects": ["营业收入", "归母净利润", "毛利率", "经营性现金流", "海外收入占比"], "queries": ["营收 净利润 同比"]},
    "年报": {"subjects": ["营业收入", "归母净利润", "毛利率", "分红方案"], "queries": ["年报 营收 净利润 分红"]},
    "半年报": {"subjects": ["营业收入", "归母净利润", "毛利率", "海外收入占比"], "queries": ["半年报 营收 净利润 同比"]},
    "一季报": {"subjects": ["营业收入", "归母净利润", "毛利率"], "queries": ["一季报 营收 净利润"]},
    "三季报": {"subjects": ["营业收入", "归母净利润", "毛利率"], "queries": ["三季报 营收 净利润"]},
    "处罚": {"subjects": ["罚没金额", "或有负债", "声誉风险"], "queries": ["处罚 立案 违规"]},
    "中标": {"subjects": ["合同金额", "订单储备", "营收确认"], "queries": ["中标 订单 合同"]},
    "产能": {"subjects": ["资本开支", "在建工程", "折旧摊销", "产能利用率"], "queries": ["产能 扩产 投产"]},
}

# 财务主题词表：用于从指标名中回收关键词（规则兜底时保证 keywords 覆盖核心科目）
_FIN_VOCAB = ("净利润", "利润", "营收", "营业收入", "毛利率", "现金流", "分红", "派息", "股息",
              "股利", "回购", "减持", "定增", "募资", "订单", "合同", "产能", "商誉", "罚款")

_PERIOD_PAT = re.compile(r"(20\d{2})\s*(?:年)?\s*(H1|H2|S1|S2|Q1|Q2|Q3|Q4|半年报|年报|一季报|三季报|中报|年度)?")
_NUM_PAT = re.compile(r"([+\-]?\d+\.?\d*)\s*(%|％|亿元|亿元|亿|万|元|pct|个百分点)?")


def _rule_extract(event: EventInput) -> EventProfile:
    """纯规则兜底：关键词词典 + 正则提取数字/报告期。"""
    text = event.event_description
    keywords: list[str] = []
    subjects: list[str] = []
    queries: list[str] = []
    subtype = event.event_type

    for word, meta in _EVENT_LEXICON.items():
        if word in text:
            keywords.append(word)
            for s in meta["subjects"]:
                if s not in subjects:
                    subjects.append(s)
            queries.extend(meta["queries"])

    # 报告期
    time_scope = ""
    m = _PERIOD_PAT.search(text)
    if m:
        time_scope = (m.group(1) or "") + (m.group(2) or "")
        time_scope = time_scope.replace("S1", "H1").replace("中报", "H1").replace("半年报", "H1").replace("年报", "A")

    # 数字指标
    metrics: list[dict[str, str]] = []
    for mm in re.finditer(r"([\u4e00-\u9fff]{2,8}?)\s*([+\-]?\d+\.?\d*)\s*(%|亿元|亿|pct|个百分点)", text):
        metrics.append({
            "name": mm.group(1).strip("，。,；;：: "),
            "value": mm.group(2) + (mm.group(3) or ""),
            "direction": "上升" if mm.group(2).startswith("+") else ("下降" if mm.group(2).startswith("-") else "中性"),
        })

    # 把指标名中的财务主题词并入关键词（LLM 兜底不可用时关键词过薄的问题）
    for m in metrics:
        for vocab in _FIN_VOCAB:
            if vocab in m["name"] and vocab not in keywords:
                if vocab == "利润" and "净利润" in keywords:
                    continue
                keywords.append(vocab)

    if not queries:
        queries = [event.event_type]
    # 检索词模板化：公司 + 事件类型 + 核心主题词 优先，词典查询词殿后（避免噪音词带偏舆情）
    core = " ".join((keywords or [event.event_type])[:3])
    candidates = [f"{event.company_name} {subtype} {core}".strip()]
    candidates += [f"{event.company_name} {q}" for q in queries[:2]]
    seen: set[str] = set()
    search_queries = [q for q in candidates if not (q in seen or seen.add(q))][:3]

    return EventProfile(
        event_subtype=subtype,
        keywords=keywords or [event.event_type],
        metrics_mentioned=metrics[:8],
        subjects=subjects[:8],
        time_scope=time_scope,
        one_line=text[:60],
        search_queries=search_queries,
        extractor="rule",
    )


_LLM_PROMPT = """你是金融事件结构化抽取器。把下面的用户输入抽取为 JSON，不要输出任何多余文字。

用户输入：
公司：{company}（{code}）
事件类型（用户预选）：{etype}
事件描述：{desc}

输出 JSON（字段都必须有，拿不准就留空字符串或空数组）：
{{
  "event_subtype": "细分事件类型，如：分红派息/财报披露/定增融资/大股东减持/并购重组/重大合同/监管处罚/产能扩张",
  "keywords": ["业务关键词，3-8个，必须包含描述中出现的财务主题词，例如分红、股息、派息、回购、营收、净利润"],
  "metrics_mentioned": [{{"name": "指标名", "value": "数值含单位", "direction": "上升/下降/中性"}}],
  "subjects": ["受影响的财务科目，如：股息率/分红比例/自由现金流/营业收入/归母净利润/毛利率"],
  "time_scope": "报告期，如 2025H1 / 2025Q3 / 2024A，没有就空",
  "one_line": "一句话概括事件（30字内）",
  "search_queries": ["2-3条用于舆情检索的中文查询词，要贴合事件主题，比如问分红就带'分红 派息 股息率'"]
}}"""


async def extract_event_profile(event: EventInput, use_live: bool = True) -> EventProfile:
    """事件结构化抽取：LLM 优先，规则兜底。"""
    rule_based = _rule_extract(event)

    if use_live and llm_client.feature_enabled("event_extract"):
        try:
            prompt = _LLM_PROMPT.format(
                company=event.company_name,
                code=event.stock_code,
                etype=event.event_type,
                desc=event.event_description,
            )
            data = await llm_client.chat_json([{"role": "user", "content": prompt}])
            # 用 LLM 结果覆盖/合并规则结果
            merged = rule_based.model_dump()
            merged["extractor"] = "llm"
            for k in ("event_subtype", "time_scope", "one_line"):
                if data.get(k):
                    merged[k] = data[k]
            for k in ("keywords", "subjects", "search_queries"):
                if isinstance(data.get(k), list) and data[k]:
                    merged[k] = [str(x) for x in data[k]][:8]
            if isinstance(data.get("metrics_mentioned"), list) and data["metrics_mentioned"]:
                merged["metrics_mentioned"] = [
                    {"name": str(m.get("name", "")), "value": str(m.get("value", "")), "direction": str(m.get("direction", "中性"))}
                    for m in data["metrics_mentioned"][:8]
                    if isinstance(m, dict)
                ]
            return EventProfile(**merged)
        except Exception:
            pass
    return rule_based
