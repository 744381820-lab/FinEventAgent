"""后端批量测试执行器：调用 /api/analyze (SSE)，落盘每轮完整结果与分步耗时。

用法：
    python demo/scripts/run_backend_tests.py [--case T01 T05] [--concurrency 2]
输出：
    demo/test_results/<case_id>.json   —— 完整 AnalysisResult + steps + 耗时
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import httpx

BASE = "http://localhost:8000"
CASES_PATH = Path(__file__).parent / "backend_test_cases.json"
OUT_DIR = Path(__file__).resolve().parents[1] / "test_results"
TIMEOUT = 120.0  # 单用例硬超时 120s，避免进门 MCP 卡死拖垮整轮


async def run_case(client: httpx.AsyncClient, case: dict) -> dict:
    cid = case["id"]
    t0 = time.perf_counter()
    steps: list[dict] = []
    complete: dict | None = None
    error: str | None = None
    step_ts: dict[str, float] = {}

    try:
        async with client.stream("POST", f"{BASE}/api/analyze", json=case["payload"], timeout=TIMEOUT) as resp:
            if resp.status_code != 200:
                error = f"HTTP {resp.status_code}: {(await resp.aread())[:300]!r}"
            else:
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        evt = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    etype = evt.get("type")
                    if etype == "step":
                        now = time.perf_counter() - t0
                        steps.append({
                            "step": evt.get("step"),
                            "status": evt.get("status"),
                            "progress": evt.get("progress"),
                            "message": evt.get("message"),
                            "elapsed_s": round(now, 2),
                        })
                        key = f'{evt.get("step")}:{evt.get("status")}'
                        step_ts[key] = round(now, 2)
                    elif etype == "complete":
                        complete = evt.get("data")
                    elif etype == "error":
                        error = str(evt.get("message") or evt)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    total_s = round(time.perf_counter() - t0, 2)
    record = {
        "case_id": cid,
        "group": case.get("group"),
        "name": case.get("name"),
        "expect": case.get("expect", {}),
        "payload": case["payload"],
        "total_time_s": total_s,
        "error": error,
        "steps": steps,
        "step_ts": step_ts,
        "result": complete,
    }
    (OUT_DIR / f"{cid}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    status = "OK" if (complete and not error) else f"FAIL({error})"
    print(f"[{cid}] {case.get('name')} -> {status} | {total_s}s", flush=True)
    return record


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", nargs="*", default=None, help="只跑指定用例 ID")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--out", default=None, help="输出目录名（默认 demo/test_results）")
    args = parser.parse_args()

    global OUT_DIR
    if args.out:
        OUT_DIR = Path(__file__).resolve().parents[1] / args.out

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if args.case:
        cases = [c for c in cases if c["id"] in set(args.case)]
    OUT_DIR.mkdir(exist_ok=True)

    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient() as client:
        async def guarded(c: dict) -> dict:
            async with sem:
                return await run_case(client, c)

        t0 = time.perf_counter()
        records = await asyncio.gather(*[guarded(c) for c in cases])
        ok = sum(1 for r in records if r["result"] and not r["error"])
        print(f"\n==== 完成 {ok}/{len(records)} 轮，总耗时 {round(time.perf_counter() - t0, 1)}s ====")


if __name__ == "__main__":
    asyncio.run(main())
