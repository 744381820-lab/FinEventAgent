"""联调：完整分析 + 局部重算 refine。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def consume(url: str, payload: dict) -> list[dict]:
    body = json.dumps(payload, ensure_ascii=False)
    tmp = Path(__file__).with_name("_payload_tmp.json")
    tmp.write_text(body, encoding="utf-8")
    cmd = [
        "curl.exe", "-sN", "-X", "POST", url,
        "-H", "Content-Type: application/json",
        "--data-binary", f"@{tmp}",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    events = []
    buf = ""
    assert proc.stdout is not None
    for line in proc.stdout:
        buf += line
        while "\n\n" in buf:
            chunk, buf = buf.split("\n\n", 1)
            for part in chunk.split("\n"):
                if part.startswith("data: "):
                    try:
                        events.append(json.loads(part[6:]))
                    except Exception as e:
                        print("parse err", e, part[:80])
    proc.wait(timeout=180)
    tmp.unlink(missing_ok=True)
    return events


def main() -> int:
    print("=== ANALYZE ===")
    events = consume(
        "http://localhost:8000/api/analyze",
        {
            "company_name": "宁德时代",
            "stock_code": "sz300750",
            "event_description": "宁德时代2025H1营收同比下降12%，海外业务收入占比从18%提升至32%，毛利率提升至26.1%。",
            "event_type": "财报披露",
            "position_ratio": 5,
            "investment_horizon": "medium",
            "risk_tolerance": "medium",
        },
    )
    session_id = None
    for ev in events:
        t = ev.get("type")
        if t == "init":
            session_id = ev.get("session_id")
            print("session:", session_id)
        elif t == "step":
            print(f"  {ev.get('step')}: {ev.get('status')} - {ev.get('message', '')[:60]}")
        elif t == "complete":
            d = ev.get("data", {})
            session_id = ev.get("session_id") or d.get("session_id") or session_id
            sent = d.get("sentiment") or {}
            fin = d.get("financial_context") or {}
            print("COMPLETE")
            print("  sentiment mentions:", sent.get("total_mentions"), "heat:", sent.get("heat_trend"), "items:", len(sent.get("items") or []))
            print("  emotion:", sent.get("emotion_distribution"))
            print("  mcp metrics:", len((fin.get("metrics") or {})))
            print("  valuations:", len(d.get("valuations") or []))
            print("  strategy:", (d.get("strategy") or {}).get("recommendation"))

    if not session_id:
        print("FAIL: no session_id")
        return 1

    print("\n=== REFINE L4 (仓位 8%) ===")
    events2 = consume(
        "http://localhost:8000/api/refine",
        {
            "session_id": session_id,
            "targets": ["l4"],
            "position_ratio": 8,
            "instruction": "把仓位改成8%",
        },
    )
    for ev in events2:
        t = ev.get("type")
        if t == "init":
            print("refine targets:", ev.get("targets"))
        elif t == "step":
            print(f"  {ev.get('step')}: {ev.get('status')} - {ev.get('message', '')[:60]}")
        elif t == "complete":
            d = ev.get("data", {})
            print("REFINE COMPLETE")
            print("  position:", (d.get("event") or {}).get("position_ratio"))
            print("  strategy:", (d.get("strategy") or {}).get("recommendation"))
        elif t == "error":
            print("ERROR:", ev.get("message"))
            return 1

    print("\n=== REFINE L3 (WACC=9) ===")
    events3 = consume(
        "http://localhost:8000/api/refine",
        {
            "session_id": session_id,
            "targets": ["l3", "l4"],
            "valuation_overrides": {"wacc": 9.0},
            "instruction": "WACC调到9%",
        },
    )
    for ev in events3:
        t = ev.get("type")
        if t == "step":
            print(f"  {ev.get('step')}: {ev.get('status')} - {ev.get('message', '')[:60]}")
        elif t == "complete":
            d = ev.get("data", {})
            vals = d.get("valuations") or []
            dcf = next((v for v in vals if v.get("model_key") == "dcf"), None)
            print("REFINE L3 COMPLETE")
            print("  dcf wacc param:", (dcf or {}).get("params", {}).get("wacc"))
            print("  impact:", (d.get("valuation") or {}).get("impact_pct"))
        elif t == "error":
            print("ERROR:", ev.get("message"))
            return 1

    print("\nALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
