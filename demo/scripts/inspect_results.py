"""第二轮抽查：OCF 单位、空仓策略、核验一致性、噪音劫持细节。"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
D = Path(__file__).resolve().parents[1] / "test_results"


def load(cid: str) -> dict:
    return json.loads((D / f"{cid}.json").read_text(encoding="utf-8"))["result"]


def line(cid, label, value):
    print(f"[{cid}] {label}: {value}")


# T01 OCF 原始数据点（查单位问题）
r = load("T01")
fin = r["financial_context"]["metrics"]
for k in ("经营活动产生的现金流量净额", "经营现金流", "归母净利润", "总市值", "收盘价"):
    dp = fin.get(k)
    if dp:
        line("T01", f"fin[{k}]", (dp.get("display"), dp.get("unit"), dp.get("period"), dp.get("value")))
cov = next(m for m in r["derived_metrics"] if m["key"] == "ocf_coverage")
line("T01", "ocf_coverage inputs", cov["inputs"])
print()

# T02 vs T01 核验不一致
for cid in ("T01", "T02"):
    r = load(cid)
    fc = r["fact_check"]
    line(cid, "fact", (fc["status"], fc["confidence"], fc["official_title"][:40]))
    line(cid, "  matched", fc["matched_facts"][:3])
    line(cid, "  discrepancies", fc["discrepancies"][:3])
print()

# T21 空仓策略
r = load("T21")
line("T21", "recommendation", r["strategy"]["recommendation"])
for o in r["strategy"]["options"]:
    line("T21", f"option[{o['name']}]", (o["action"], o["position_advice"]))
print()

# T23 噪音劫持细节
r = load("T23")
line("T23", "metrics_mentioned", r["event_profile"]["metrics_mentioned"])
line("T23", "search_queries", r["event_profile"]["search_queries"])
line("T23", "sentiment keywords", [k.get("word") or k.get("keyword") for k in (r["sentiment"].get("top_keywords") or [])][:8])
print()

# 分红 vs 财报 估值假设对比：T04（定性分红，无金额→dy缺失→impact 0）
r = load("T04")
line("T04", "derived", [(m["key"], m["display"]) for m in r["derived_metrics"]])
line("T04", "valuation impact", [v["impact_pct"] for v in r["valuations"]])
line("T04", "strategy", r["strategy"]["recommendation"])
print()

# T15 处罚：基本面与策略是否体现风险
r = load("T15")
line("T15", "fundamental.summary", r["fundamental"]["summary"][:160])
line("T15", "strategy", r["strategy"]["recommendation"])
line("T15", "risk_warnings", r["strategy"]["risk_warnings"])
print()

# 耗时分解（所有用例 sentiment/data 步耗时）
import glob
print("--- 分步耗时（秒）---")
print(f"{'case':<5}{'extract':>8}{'data':>6}{'fact':>6}{'sentiment':>10}{'fundam':>8}{'valuat':>8}{'total':>7}")
for f in sorted(glob.glob(str(D / "T*.json"))):
    rec = json.loads(open(f, encoding="utf-8").read())
    ts = rec["step_ts"]

    def dur(step):
        s, d = ts.get(f"{step}:running"), ts.get(f"{step}:done")
        return round(d - s, 1) if s is not None and d is not None else None

    print(f"{rec['case_id']:<5}{str(dur('extract')):>8}{str(dur('data')):>6}{str(dur('fact_check')):>6}"
          f"{str(dur('sentiment')):>10}{str(dur('fundamental')):>8}{str(dur('valuation')):>8}{rec['total_time_s']:>7}")
