"""修复后结果抽查：T07 方向异常、P0 修复确认、P1 场景确认。"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
D = Path(__file__).resolve().parents[1] / "test_results_after"


def load(cid: str) -> dict:
    return json.loads((D / f"{cid}.json").read_text(encoding="utf-8"))["result"]


def line(cid, label, value):
    print(f"[{cid}] {label}: {value}")


# T07 异常排查
r = load("T07")
line("T07", "extractor", r["event_profile"].get("extractor"))
line("T07", "metrics_mentioned", r["event_profile"]["metrics_mentioned"])
line("T07", "keywords", r["event_profile"]["keywords"])
pe = r["valuations"][0]
line("T07", "PE params", pe["params"])
line("T07", "PE assumptions", pe["assumptions"])
line("T07", "fundamental.summary", r["fundamental"]["summary"][:160])
line("T07", "key_judgment", r["fundamental"]["key_judgment"])
print()

# P0-2 OCF 确认
r = load("T01")
line("T01", "derived", [(m["key"], m["display"]) for m in r["derived_metrics"]])
line("T01", "strategy", r["strategy"]["recommendation"])
print()

# P0-3 减持确认
r = load("T11")
line("T11", "derived", [(m["key"], m["display"]) for m in r["derived_metrics"]])
line("T11", "strategy", r["strategy"]["recommendation"])
print()

# P1 极端仓位/空仓
r = load("T20")
line("T20", "recommendation", r["strategy"]["recommendation"])
line("T20", "risk_warnings", r["strategy"]["risk_warnings"])
line("T20", "options[0]", (r["strategy"]["options"][0]["action"], r["strategy"]["options"][0]["position_advice"]))
r = load("T21")
line("T21", "recommendation", r["strategy"]["recommendation"])
for o in r["strategy"]["options"]:
    line("T21", f"option[{o['name']}]", (o["action"], o["position_advice"]))
print()

# 分红率期间对齐：T03 茅台
r = load("T03")
pr = next(m for m in r["derived_metrics"] if m["key"] == "payout_ratio")
line("T03", "payout_ratio", (pr["display"], pr["formula"], pr["inputs"], pr["confidence"]))
print()

# 核验 basis：T01/T22
for cid in ("T01", "T22"):
    r = load(cid)
    line(cid, "fact basis", r["fact_check"].get("basis"))
    line(cid, "fact status", (r["fact_check"]["status"], r["fact_check"]["confidence"]))
print()

# T23 噪音关键词修复确认
r = load("T23")
line("T23", "keywords", r["event_profile"]["keywords"])
line("T23", "search_queries", r["event_profile"]["search_queries"])
