"""DataAgent：统一采集进门 MCP 底层数据，输出带溯源四元组的 FinancialContext。

原则：所有下游模块的数字只能来自这里（或 CalcAgent 的计算结果），
不允许任何模块自行编造金额与比率。
"""
from __future__ import annotations

from typing import Any

from ..jinmen_mcp import jinmen_client
from ..schemas import DataPoint, EventInput, FinancialContext


def _normalize_code(code: str) -> str:
    """补齐交易所前缀：6→sh，0/3→sz，4/8→bj。pricePerformance 等接口要求带前缀。"""
    c = code.strip().lower()
    if c[:2] in ("sh", "sz", "bj"):
        return c
    if c.startswith("6"):
        return "sh" + c
    if c.startswith(("0", "3")):
        return "sz" + c
    if c.startswith(("4", "8")):
        return "bj" + c
    return c


def _to_yi(value: Any, unit: str) -> float | None:
    """统一折算为「亿元」。无法解析返回 None（缺失不硬编）。"""
    try:
        v = float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
    if unit == "元":
        return v / 1e8
    if unit == "万元":
        return v / 1e4
    return v  # 亿元 / % / 元每股等，保持原单位语义由调用方判断


def _fmt_yi(v: float | None) -> str:
    if v is None:
        return "数据缺失"
    return f"{v:,.2f} 亿元"


async def run_data_agent(event: EventInput, use_live: bool = True) -> FinancialContext:
    ctx = FinancialContext()
    if not (use_live and jinmen_client.available):
        ctx.note = "进门 MCP 未配置，底层数据缺失（demo 模式）"
        return ctx
    stock_code = _normalize_code(event.stock_code)

    # 1. 财务快照
    try:
        snapshot = await jinmen_client.get_financial_snapshot([stock_code])
        data = snapshot.get("data", {}).get(stock_code) or snapshot.get("data", {}).get(event.stock_code, {})
        for item in data.get("snapshotItems", []):
            name = item.get("name_cn")
            if not name:
                continue
            unit = item.get("unit", "")
            raw = item.get("value")
            # 每股指标是绝对值（元/股），不能按金额折算为亿元
            if "每股" in name:
                try:
                    per_share_val = float(str(raw).replace(",", ""))
                except (TypeError, ValueError):
                    per_share_val = None
                dp = DataPoint(
                    value=per_share_val,
                    display=f"{raw}{unit}",
                    unit="元/股" if unit == "元" else unit,
                    period=item.get("period", "") or "",
                    source="get_financial_snapshot",
                )
            else:
                yi_val = _to_yi(raw, unit) if unit in {"元", "万元", "亿元"} else None
                # 金额类统一用「亿元」展示，避免 1806246080377.6元 这种裸数字
                disp = f"{yi_val:,.2f} 亿元" if yi_val is not None and unit in {"元", "万元"} else f"{raw}{unit}"
                dp = DataPoint(
                    value=yi_val,
                    display=disp,
                    unit="亿元" if unit in {"元", "万元"} and yi_val is not None else unit,
                    period=item.get("period", "") or "",
                    source="get_financial_snapshot",
                )
            # 百分比类也保留数值，便于计算
            if unit == "%":
                try:
                    dp.value = float(str(raw).replace(",", ""))
                except (TypeError, ValueError):
                    pass
            ctx.metrics[name] = dp
    except Exception as e:
        ctx.note += f"财务快照采集失败：{e}；"

    # 2. 行情（收盘价 / 总市值）
    try:
        perf = await jinmen_client.price_performance([stock_code])
        data = perf.get("data", {}).get(stock_code) or perf.get("data", {}).get(event.stock_code, {})
        close = data.get("regular_market", {}).get("closePrice")
        if close is not None:
            ctx.metrics["收盘价"] = DataPoint(
                value=float(close), display=f"{close} 元", unit="元/股", source="pricePerformance"
            )
        mv = data.get("market_value", {}).get("total_market_value_display", "")
        if mv:
            mv_yi = _to_yi(str(mv).replace("亿", ""), "亿元")
            ctx.metrics["总市值"] = DataPoint(
                value=mv_yi, display=str(mv), unit="亿元", source="pricePerformance"
            )
    except Exception as e:
        ctx.note += f"行情采集失败：{e}；"

    # 3. 最新公告标题（供核验与溯源展示）
    try:
        anns = await jinmen_client.search_announcements(
            stock_code, event.event_type, top_k=5
        )
        if isinstance(anns, list):
            ctx.announcement_titles = [a.get("title", "") for a in anns if a.get("title")][:5]
        elif isinstance(anns, dict):
            for a in anns.get("data", [])[:5] if isinstance(anns.get("data"), list) else []:
                if isinstance(a, dict) and a.get("title"):
                    ctx.announcement_titles.append(a["title"])
    except Exception as e:
        ctx.note += f"公告采集失败：{e}；"

    return ctx


def get_metric(ctx: FinancialContext | None, *names: str) -> DataPoint | None:
    """按候选名取数据点（支持模糊包含），取不到返回 None。

    模糊匹配时跳过口径不符的键：查询名不含「每股」则不命中每股指标，
    查询名不含「单季」则不命中单季度指标（避免总额被每股/单季值污染）。
    """
    if not ctx:
        return None
    for name in names:
        if name in ctx.metrics:
            return ctx.metrics[name]
    for name in names:
        for k, v in ctx.metrics.items():
            if "每股" in k and "每股" not in name:
                continue
            if "单季" in k and "单季" not in name:
                continue
            if name in k or k in name:
                return v
    return None
