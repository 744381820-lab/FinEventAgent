import json
import subprocess
import sys

payload = {
    "company_name": "宁德时代",
    "stock_code": "sz300750",
    "event_description": "宁德时代2025H1营收同比下降12%，但海外业务收入占比从18%提升至32%。",
    "event_type": "财报披露",
    "position_ratio": 5.2,
    "cost_basis": 195.0,
    "investment_horizon": "medium",
    "risk_tolerance": "medium",
}

cmd = [
    "curl.exe", "-s", "-N", "-X", "POST",
    "http://127.0.0.1:8000/api/analyze",
    "-H", "Content-Type: application/json",
    "--data-binary", json.dumps(payload, ensure_ascii=False),
]

proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
for line in proc.stdout:
    line = line.strip()
    if not line.startswith("data: "):
        continue
    try:
        d = json.loads(line[6:])
        if d.get("type") == "init":
            print("INIT steps:", [s["step"] for s in d["steps"]])
        elif d.get("type") == "step":
            msg = (d.get("message") or "")[:70]
            print(f"STEP {d['step']}: {d['status']} - {msg}")
        elif d.get("type") == "complete":
            r = d["data"]
            print("COMPLETE keys:", list(r.keys()))
            print("  profile:", r.get("event_profile", {}).get("event_subtype"))
            print("  fact:", r.get("fact_check", {}).get("status"))
            print("  valuation impact:", r.get("valuation", {}).get("impact_pct"))
            print("  strategy:", r.get("strategy", {}).get("recommendation"))
    except Exception as e:
        print("ERR", e)
proc.wait()
