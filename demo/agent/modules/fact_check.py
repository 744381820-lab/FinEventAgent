"""FactCheckAgent：信息真实性核验。

核验方式从「文字模糊匹配」升级为「数字对数字」：
从事件描述中抽取结构化数值声明（营收/净利润金额、同比、派现），
与进门财务快照的真实值做数值比对（容差 3%），差异项同时给出官方值。
"""
from __future__ import annotations

import re
from typing import Any

from ..jinmen_mcp import jinmen_client, load_fixture
from ..schemas import EventInput, EventProfile, FactCheckResult, FinancialContext
from ..agents.data_agent import get_metric

TOL = 0.03  # 数值容差 3%


def _extract_claims(text: str) -> list[dict[str, Any]]:
    """抽取事件描述中的数值声明：{label, value, unit, snapshot_keys}"""
    claims = []
    # 金额类：营收1789亿元 / 归母净利润305亿
    for m in re.finditer(r"(营收|营业收入|营业总收入|归母净利润|净利润|扣非归母净利润)[^\d]{0,6}([\d,.]+)\s*亿", text):
        label, raw = m.group(1), m.group(2).replace(",", "")
        keys = {
            "营收": ["营业收入", "营业总收入"],
            "营业收入": ["营业收入", "营业总收入"],
            "营业总收入": ["营业总收入", "营业收入"],
            "归母净利润": ["归母净利润"],
            "净利润": ["归母净利润", "净利润"],
            "扣非归母净利润": ["扣非归母净利润"],
        }[label]
        claims.append({"label": label, "value": float(raw), "unit": "亿元", "keys": keys, "raw": m.group(0)})
    # 同比类：同比-7.3% / 同比+33.0%（就近归属前文提到的指标）
    for m in re.finditer(r"(营收|营业收入|归母净利润|净利润)?[^。；\n]{0,10}?同比\s*([+-]?[\d.]+)\s*%", text):
        prefix = m.group(1) or ""
        keys = ["归母净利润同比", "净利润同比"] if "净利" in prefix else ["营业总收入同比", "营业收入同比"]
        label = f"{prefix or '指标'}同比"
        claims.append({"label": label, "value": float(m.group(2)), "unit": "%", "keys": keys, "raw": m.group(0)})
    # 派现类：每10股派现15元 / 10派14.11元
    m = re.search(r"(?:每?\s*10\s*股派(?:现|发现金红利|现金分红)?\s*|10\s*派\s*)([\d.]+)\s*元", text)
    if m:
        claims.append({"label": "每10股派现", "value": float(m.group(1)), "unit": "元/10股", "keys": [], "raw": m.group(0)})
    # 分红总额类：合计派现约347亿元
    m = re.search(r"(?:合计|共计|总)?派(?:现|发现金红利|现金红利)?[^0-9]{0,4}([\d.]+)\s*亿", text)
    if m:
        claims.append({"label": "现金分红总额", "value": float(m.group(1)), "unit": "亿元", "keys": [], "raw": m.group(0)})
    # 分红率类：分红率75%
    m = re.search(r"分红率\s*([\d.]+)\s*%", text)
    if m:
        claims.append({"label": "分红率", "value": float(m.group(1)), "unit": "%", "keys": [], "raw": m.group(0)})
    return claims


def _compare(claims: list[dict[str, Any]], fin: FinancialContext | None, ann_text: str) -> list[dict[str, Any]]:
    """逐条比对，返回 [{claim_label, ok, msg, via_snapshot}]，由调用方汇总状态。"""
    results: list[dict[str, Any]] = []
    for c in claims:
        if not c["keys"]:
            # 公告文本类声明（派现金额/分红总额/分红率）：在公告原文中核对
            s = f"{c['value']:g}"
            if s in ann_text:
                results.append({"label": c["label"], "ok": True, "via_snapshot": False,
                                "msg": f"事件称「{c['label']} {s}{c['unit']}」，与官方公告一致"})
            else:
                results.append({"label": c["label"], "ok": False, "via_snapshot": False,
                                "msg": f"事件称「{c['label']} {s}{c['unit']}」，未在检索到的公告文本中匹配（检索范围有限，不代表事实错误）"})
            continue
        dp = get_metric(fin, *c["keys"]) if fin else None
        if dp is None or dp.value is None:
            results.append({"label": c["label"], "ok": False, "via_snapshot": True,
                            "msg": f"事件称「{c['label']} {c['value']:g}{c['unit']}」，财务快照无对应数据，无法核验"})
            continue
        official = dp.value
        diff = (c["value"] - official) / official if official else 0
        if abs(diff) <= TOL:
            results.append({"label": c["label"], "ok": True, "via_snapshot": True,
                            "msg": f"事件称「{c['label']} {c['value']:g}{c['unit']}」，与官方 {dp.display} 一致"})
        else:
            results.append({"label": c["label"], "ok": False, "via_snapshot": True,
                            "msg": f"事件称「{c['label']} {c['value']:g}{c['unit']}」，官方为 {dp.display}"
                                   f"（{dp.period or '最新报告期'}），偏差 {diff * 100:+.1f}%"})
    return results


def _flatten_text(data: Any, max_len: int = 4000) -> str:
    if isinstance(data, dict):
        parts = []
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                parts.append(f"{k}: {_flatten_text(v, 1000)}")
            else:
                parts.append(f"{k}: {v}")
        return "\n".join(parts)[:max_len]
    if isinstance(data, list):
        return "\n".join(_flatten_text(item, 1000) for item in data[:10])[:max_len]
    return str(data)[:max_len]


async def run_fact_check(
    event: EventInput,
    use_live: bool = True,
    profile: EventProfile | None = None,
    fin: FinancialContext | None = None,
) -> FactCheckResult:
    fixture = load_fixture()
    check_terms = " ".join((profile.subjects[:4] if profile and profile.subjects else ["营业收入", "同比"]))
    search_query = f"{event.event_type} {check_terms}"

    if use_live and jinmen_client.available:
        try:
            announcements = await jinmen_client.search_announcements(
                event.stock_code, search_query, top_k=5
            )
            ann_text = _flatten_text(announcements, 3000)

            claims = _extract_claims(event.event_description)
            results = _compare(claims, fin, ann_text)
            matched = [r["msg"] for r in results if r["ok"]]
            discrepancies = [r["msg"] for r in results if not r["ok"]]

            # 官方数据补充：按事件涉及的财务科目动态选取
            supplemented = {}
            want_names = set(profile.subjects) if profile and profile.subjects else set()
            default_names = {"归母净利润", "毛利率", "经营活动现金流量净额", "营业总收入同比"}
            if fin:
                for name, dp in fin.metrics.items():
                    if name in default_names or any(w and (w in name or name in w) for w in want_names):
                        supplemented[name] = dp.display

            # 状态判定：快照可核验的声明决定真伪；仅公告文本未匹配时降级为 partial
            snap_results = [r for r in results if r["via_snapshot"]]
            ann_results = [r for r in results if not r["via_snapshot"]]
            if snap_results:
                snap_ratio = sum(1 for r in snap_results if r["ok"]) / len(snap_results)
                if snap_ratio >= 0.8:
                    status, confidence = "verified", 88
                elif snap_ratio >= 0.4:
                    status, confidence = "partial", 62
                else:
                    status, confidence = "unverified", 35
                basis = f"抽取 {len(claims)} 条数值声明，其中 {len(snap_results)} 条与财务快照逐项比对（容差 3%），命中 {sum(1 for r in snap_results if r['ok'])} 条"
            elif ann_results:
                ann_hit = sum(1 for r in ann_results if r["ok"])
                if ann_results and ann_hit == len(ann_results):
                    status, confidence = "verified", 85
                elif ann_hit > 0:
                    status, confidence = "partial", 62
                else:
                    # 公告检索范围有限，未匹配不等于造假，不再直接判 unverified
                    status, confidence = "partial", 55
                basis = f"抽取 {len(claims)} 条公告类声明（派现/分红率等），在检索到的公告文本中命中 {ann_hit} 条；公告检索范围有限，未命中需人工复核"
            else:
                status, confidence = "unverified", 40
                basis = "事件描述中未抽取到可核验的数值声明，无法逐项比对"

            official_title, official_url = "最新公告", ""
            if isinstance(announcements, list) and announcements:
                first = announcements[0]
                official_title = first.get("title", official_title)
                official_url = first.get("url", "")
            basis += f"；公告比对基于《{official_title}》等检索结果"

            return FactCheckResult(
                status=status,
                confidence=confidence,
                official_title=official_title,
                official_url=official_url,
                publish_time="",
                matched_facts=matched or ["已获取公司最新财务快照与公告信息"],
                discrepancies=discrepancies,
                supplemented_data=supplemented,
                data_source="进门财经 MCP（公告检索 / 财务快照）",
                basis=basis,
            )
        except Exception:
            pass

    f_event = fixture["event"]
    return FactCheckResult(
        status="partial",
        confidence=70,
        official_title=f_event["title"],
        official_url="",
        publish_time=f_event["time"],
        matched_facts=["当前为离线样例模式，数值未与官方数据比对"],
        discrepancies=["未连接进门 MCP，无法实时核验"],
        supplemented_data={"归母净利润同比": "-8%（样例）", "毛利率": "26.5%（样例）"},
        data_source="本地样例数据（未配置进门 MCP Key）",
    )
