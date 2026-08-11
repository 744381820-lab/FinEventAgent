"""校验 SSE 输出文件：打印各步骤关键结果。"""
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "demo/sse_out3.txt"
steps = {}
complete = None
for line in open(path, encoding="utf-8"):
    line = line.strip()
    if not line.startswith("data: "):
        continue
    ev = json.loads(line[6:])
    if ev["type"] == "init":
        print("STEPS:", [s["step"] for s in ev["steps"]])
    elif ev["type"] == "step" and ev["status"] == "done":
        steps[ev["step"]] = ev.get("data")
        print(f"[done] {ev['step']}: {ev.get('message')}")
    elif ev["type"] == "complete":
        complete = ev["data"]

d = steps.get("data", {})
print("\n== CalcAgent 衍生指标 ==")
for m in d.get("derived_metrics", []):
    print(f"  {m['key']}: {m['display']} | conf={m['confidence']} | {m['formula']}")
    for inp in m["inputs"]:
        print(f"      <- {inp}")

f = steps.get("fact_check", {})
print("\n== 核验 ==", f.get("status"), f.get("confidence"))
print(" matched:", json.dumps(f.get("matched_facts"), ensure_ascii=False, indent=1))
print(" discrep:", json.dumps(f.get("discrepancies"), ensure_ascii=False, indent=1))

fu = steps.get("fundamental", {})
print("\n== 基本面 ==")
print(" summary:", fu.get("summary"))
for m in fu.get("metric_table", []):
    print(f"  {m['metric']}: {m['magnitude']} ({m['certainty']})")

v = steps.get("valuation", {}).get("valuations", [])
print("\n== 估值 ==")
for x in v:
    print(f"  {x['method']}: impact={x['impact_pct']}% pre={x['pre_event_value']} post={x['post_event_value']} price={x['current_price']}")
    print("   assumptions:", json.dumps(x["assumptions"][:3], ensure_ascii=False))

s = steps.get("strategy", {})
print("\n== 策略 ==", s.get("recommendation"), s.get("confidence"))
print(" logic[1]:", (s.get("options") or [{}, {}])[1].get("logic"))

print("\n== 综合 ==", (complete or {}).get("executive_summary"))
