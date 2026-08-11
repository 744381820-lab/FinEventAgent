"""L2 多维影响 Agent：并行基本面/舆情/产业链分析，融合输出影响矩阵。"""
from __future__ import annotations

import asyncio
from typing import Any

from ..modules.fundamental import run_fundamental
from ..modules.sentiment import run_sentiment
from ..schemas import DerivedMetric, EventInput, EventProfile, FinancialContext


class L2ImpactAgent:
    """L2 多维影响 Agent：回答「基本面、舆情、产业链如何被撼动」。"""

    name = "L2 多维影响"
    description = "并行分析基本面、舆情、产业链，融合输出多维影响矩阵"

    async def run(
        self,
        event: EventInput,
        use_live: bool = True,
        profile: EventProfile | None = None,
        fin: FinancialContext | None = None,
        derived: list[DerivedMetric] | None = None,
    ) -> dict[str, Any]:
        # 基本面与舆情可并行；产业链当前用规则/舆情数据近似，后续可接入知识图谱
        fundamental_task = asyncio.create_task(
            run_fundamental(event, use_live=use_live, profile=profile, fin=fin, derived=derived)
        )
        sentiment_task = asyncio.create_task(
            run_sentiment(event, use_live=use_live, profile=profile)
        )

        fundamental = await fundamental_task
        sentiment = await sentiment_task

        # 产业链影响：基于事件类型与关键词的规则判断（占位，可扩展）
        industry_chain = self._infer_industry_chain(profile, fundamental.key_judgment)

        # 融合：多维影响评分矩阵
        impact_matrix = self._fuse_impact(fundamental, sentiment, industry_chain)

        return {
            "fundamental": fundamental,
            "sentiment": sentiment,
            "industry_chain": industry_chain,
            "impact_matrix": impact_matrix,
            "summary": f"基本面：{fundamental.key_judgment[:30]}...；舆情：{sentiment.heat_trend}（{sentiment.total_mentions}条）",
            "trace": [
                {"agent": self.name, "action": "fundamental", "output": fundamental.model_dump()},
                {"agent": self.name, "action": "sentiment", "output": sentiment.model_dump()},
                {"agent": self.name, "action": "industry_chain", "output": industry_chain},
                {"agent": self.name, "action": "fuse_impact", "output": impact_matrix},
            ],
        }

    def _infer_industry_chain(self, profile: EventProfile | None, key_judgment: str) -> dict[str, Any]:
        """基于事件类型的产业链传导规则判断（可扩展为知识图谱）。"""
        text = (profile.event_subtype or "") + " " + " ".join(profile.keywords) if profile else ""
        if any(k in text for k in ["产能", "扩产", "投产"]):
            return {
                "upstream": "资本开支增加，上游设备/材料需求短期提升",
                "downstream": "供给增加，长期可能加剧价格竞争",
                "competitors": "头部扩产对二线厂商形成挤压",
                "direction": "neutral",
            }
        if any(k in text for k in ["分红", "派息", "回购"]):
            return {
                "upstream": "无直接影响",
                "downstream": "股东回报提升，增强长期资金配置意愿",
                "competitors": "高回报策略可能迫使同行跟进",
                "direction": "positive",
            }
        if any(k in text for k in ["减持", "处罚", "诉讼"]):
            return {
                "upstream": "无直接影响",
                "downstream": "市场情绪偏空，短期流动性承压",
                "competitors": "相对利好竞品，资金可能分流",
                "direction": "negative",
            }
        return {
            "upstream": "待分析",
            "downstream": "待分析",
            "competitors": "待分析",
            "direction": "neutral",
        }

    def _fuse_impact(self, fundamental, sentiment, industry_chain) -> dict[str, Any]:
        """融合三个子 Agent 输出，生成影响评分矩阵（-10 ~ +10）。"""
        # 基本面评分：基于 key_judgment 的语义规则
        kj = fundamental.key_judgment
        if "明显承压" in kj or "下修" in kj:
            fundamental_score = -3
        elif "改善" in kj or "上修" in kj:
            fundamental_score = 3
        else:
            fundamental_score = 0

        # 舆情评分：基于热度与情绪
        if sentiment.heat_trend == "高热":
            sentiment_score = 2 if any("正面" in str(e) for e in sentiment.emotion_distribution) else -2
        elif sentiment.heat_trend == "低热":
            sentiment_score = 0
        else:
            sentiment_score = 1 if any("正面" in str(e) for e in sentiment.emotion_distribution) else -1

        # 产业链评分
        chain_score = {"positive": 2, "negative": -2}.get(industry_chain["direction"], 0)

        return {
            "growth": {"score": fundamental_score, "reason": kj[:50]},
            "profitability": {"score": fundamental_score, "reason": "基于基本面关键判断"},
            "sentiment": {"score": sentiment_score, "reason": f"舆情热度{sentiment.heat_trend}，情绪分布见详情"},
            "industry_chain": {"score": chain_score, "reason": industry_chain["downstream"]},
            "uncertainty": {"score": -1 if sentiment_score < 0 else 0, "reason": "事件不确定性评估"},
        }
