from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class EventInput(BaseModel):
    company_name: str = "宁德时代"
    stock_code: str = "sz300750"
    event_description: str
    event_type: str = "财报披露"
    position_ratio: float = 5.0
    cost_basis: float = 0.0
    investment_horizon: Literal["short", "medium", "long"] = "medium"
    risk_tolerance: Literal["low", "medium", "high"] = "medium"


class EventProfile(BaseModel):
    """对用户输入事件的结构化抽取结果，是所有下游模块的统一数据源。"""

    event_subtype: str = ""          # 细分类型：分红派息 / 财报披露 / 定增 / 减持 / 并购…
    keywords: list[str] = Field(default_factory=list)   # 业务关键词：股息/分红/派息/营收…
    metrics_mentioned: list[dict[str, str]] = Field(default_factory=list)  # [{name,value,direction}]
    subjects: list[str] = Field(default_factory=list)   # 涉及财务科目：分红/营收/净利润/毛利率…
    time_scope: str = ""             # 报告期：2025H1 / 2025Q3 / 2024A…
    one_line: str = ""               # 一句话精炼
    search_queries: list[str] = Field(default_factory=list)  # 给舆情/搜索用的多组查询词
    extractor: str = "rule"          # 抽取方式：llm / rule（LLM 不可用时规则兜底，前端可据此提示）


class FactCheckResult(BaseModel):
    status: Literal["verified", "partial", "unverified"]
    confidence: int
    official_title: str
    official_url: str
    publish_time: str
    matched_facts: list[str] = Field(default_factory=list)
    discrepancies: list[str] = Field(default_factory=list)
    supplemented_data: dict[str, str] = Field(default_factory=dict)
    data_source: str = "进门财经 MCP"
    basis: str = ""   # 判定依据：比对了几条声明、命中几条、公告比对基于哪份文件


class SentimentItem(BaseModel):
    title: str
    channel: str
    channel_type: str
    published_at: str
    summary: str
    url: str = ""
    heat_score: int = 50


class SentimentAnalysis(BaseModel):
    total_mentions: int
    jinmen_count: int
    web_count: int
    channel_distribution: list[dict[str, Any]] = Field(default_factory=list)
    time_distribution: list[dict[str, Any]] = Field(default_factory=list)
    top_keywords: list[dict[str, Any]] = Field(default_factory=list)
    emotion_distribution: list[dict[str, Any]] = Field(default_factory=list)
    heat_trend: str
    items: list[SentimentItem] = Field(default_factory=list)
    source_note: str


class MetricImpact(BaseModel):
    metric: str
    direction: str
    magnitude: str
    period: str
    certainty: str
    explanation: str


class ChainNode(BaseModel):
    id: str
    label: str
    value: str
    detail: str


class ChainLink(BaseModel):
    source: str
    target: str
    label: str


class FundamentalImpact(BaseModel):
    summary: str
    metric_table: list[MetricImpact] = Field(default_factory=list)
    chain_nodes: list[ChainNode] = Field(default_factory=list)
    chain_links: list[ChainLink] = Field(default_factory=list)
    segment_chart: list[dict[str, Any]] = Field(default_factory=list)
    key_judgment: str


class ValuationResult(BaseModel):
    method: str
    model_key: str = "pe"
    current_price: float
    pre_event_value: float
    post_event_value: float
    impact_pct: float
    target_range: list[float]
    params: dict[str, Any] = Field(default_factory=dict)
    sensitivity: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class StrategyOption(BaseModel):
    name: str
    action: str
    position_advice: str
    logic: str
    trigger_condition: str


class PositionStrategy(BaseModel):
    recommendation: str
    confidence: int
    options: list[StrategyOption] = Field(default_factory=list)
    risk_warnings: list[str] = Field(default_factory=list)
    watch_points: list[str] = Field(default_factory=list)


class DataPoint(BaseModel):
    """底层数据点：每个字段都带 值/单位/报告期/来源 四元组，杜绝无溯源数字。"""

    value: Optional[float] = None
    display: str = ""
    unit: str = ""
    period: str = ""
    source: str = ""


class FinancialContext(BaseModel):
    """DataAgent 输出：从进门 MCP 采集的底层数据，统一结构化。"""

    metrics: dict[str, DataPoint] = Field(default_factory=dict)
    announcement_titles: list[str] = Field(default_factory=list)
    note: str = ""


class DerivedMetric(BaseModel):
    """CalcAgent 输出：确定性计算结果，带公式与输入溯源。"""

    key: str
    label: str
    value: Optional[float] = None
    display: str = ""
    formula: str = ""
    inputs: list[str] = Field(default_factory=list)
    confidence: str = "高"  # 高=全部真实数据计算 / 中=部分估算 / 低=数据缺失


class AgentStep(BaseModel):
    step: str
    label: str
    status: Literal["pending", "running", "done", "error"]
    message: str = ""
    progress: int = 0


class AnalysisResult(BaseModel):
    mode: str
    event: EventInput
    event_profile: Optional[EventProfile] = None
    financial_context: Optional[FinancialContext] = None
    derived_metrics: list[DerivedMetric] = Field(default_factory=list)
    fact_check: Optional[FactCheckResult] = None
    sentiment: Optional[SentimentAnalysis] = None
    fundamental: Optional[FundamentalImpact] = None
    valuation: Optional[ValuationResult] = None
    valuations: list[ValuationResult] = Field(default_factory=list)
    strategy: Optional[PositionStrategy] = None
    executive_summary: str = ""
    trace: list[dict[str, Any]] = Field(default_factory=list)
