"""后端测试评估器：读取 demo/test_results/*.json，输出逐用例评分与问题清单。

维度：管道完整性 / EventProfile 抽取准确性 / 确定性计算 / 核验 / 舆情 /
基本面 / 估值合理性 / 策略一致性 / 摘要质量 / 耗时。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

RESULTS_DIR = Path(__file__).resolve().parents[1] / "test_results"

DIVIDEND_WORDS = ["分红", "派息", "股息", "股利", "派现"]
EARNINGS_WORDS = ["财报", "年报", "季报", "半年报", "业绩", "营收", "净利润"]


def profile_text(p: dict) -> str:
    return " ".join([
        p.get("event_subtype") or "",
        " ".join(p.get("keywords") or []),
        " ".join(p.get("subjects") or []),
    ])


def classify_ok(expect_class: str, p: dict) -> tuple[bool, str]:
    t = profile_text(p)
    if expect_class == "dividend":
        ok = any(w in t for w in DIVIDEND_WORDS)
        return ok, "分红类识别" + ("命中" if ok else f"未命中: {p.get('event_subtype')}")
    if expect_class == "reduction":
        ok = "减持" in t
        return ok, "减持类识别" + ("命中" if ok else f"未命中: {p.get('event_subtype')}")
    if expect_class == "earnings":
        ok = any(w in t for w in EARNINGS_WORDS) and not any(w in t for w in DIVIDEND_WORDS[:2])
        return ok, "财报类识别" + ("命中" if ok else f"存疑: {p.get('event_subtype')}")
    return True, "无分类要求"


def evaluate(record: dict) -> dict:
    cid = record["case_id"]
    expect = record.get("expect", {})
    issues: list[str] = []
    warns: list[str] = []
    score = 100.0

    def fail(msg: str, pts: float = 10):
        nonlocal score
        issues.append(msg)
        score -= pts

    def warn(msg: str, pts: float = 3):
        nonlocal score
        warns.append(msg)
        score -= pts

    r = record.get("result")
    if record.get("error"):
        fail(f"请求级错误: {record['error']}", 60)
    if not r:
        return {"case_id": cid, "name": record.get("name"), "score": max(score, 0), "issues": issues or ["无 complete 结果"], "warns": warns, "stats": {}}

    # ---- 1. 管道完整性（8 步全部 done）----
    done_steps = {s["step"] for s in record["steps"] if s["status"] == "done"}
    expected_steps = {"extract", "data", "fact_check", "sentiment", "fundamental", "valuation", "strategy", "summary"}
    missing = expected_steps - done_steps
    if missing:
        fail(f"管道步骤缺失: {sorted(missing)}", 8 * len(missing))

    # ---- 2. EventProfile 抽取 ----
    p = r.get("event_profile") or {}
    ok, msg = classify_ok(expect.get("profile_class", "any"), p)
    if not ok:
        fail(f"事件分类错误: {msg}", 15)
    if not p.get("keywords"):
        warn("keywords 为空", 4)
    elif len(p["keywords"]) <= 1:
        warn(f"keywords 过薄: {p['keywords']}", 3)
    kw_any = expect.get("keywords_any")
    if kw_any and not any(k in profile_text(p) or any(k in q for q in (p.get("search_queries") or [])) for k in kw_any):
        warn(f"期望关键词 {kw_any} 均未出现在 keywords/subjects/search_queries", 4)
    ts = expect.get("time_scope")
    if ts and ts not in (p.get("time_scope") or ""):
        warn(f"time_scope 期望含「{ts}」，实际「{p.get('time_scope')}」", 4)
    tsc = expect.get("time_scope_contains")
    if tsc and tsc not in (p.get("time_scope") or ""):
        warn(f"time_scope 期望含「{tsc}」，实际「{p.get('time_scope')}」", 4)
    if not p.get("search_queries"):
        warn("search_queries 为空", 3)

    # ---- 3. 数据采集与确定性计算 ----
    fin = r.get("financial_context") or {}
    n_metrics = len(fin.get("metrics") or {})
    if n_metrics < 5:
        warn(f"底层指标仅 {n_metrics} 项", 4)
    derived = r.get("derived_metrics") or []
    for key in expect.get("derived_keys", []):
        m = next((x for x in derived if x["key"] == key), None)
        if m is None:
            fail(f"期望计算指标缺失: {key}", 8)
        elif m.get("value") is None:
            warn(f"指标 {key} 值为空（数据缺失）", 4)
    cov = next((x for x in derived if x["key"] == "ocf_coverage"), None)
    if cov and cov.get("value") == 0:
        warn("ocf_coverage=0，疑似经营现金流单位/口径异常", 5)

    # ---- 4. 核验 ----
    fc = r.get("fact_check") or {}
    if fc.get("status") not in {"verified", "partial", "unverified"}:
        fail(f"fact_check.status 非法: {fc.get('status')}", 8)
    if expect.get("expect_discrepancy"):
        if fc.get("status") == "verified" and not fc.get("discrepancies"):
            fail("错误数字用例应被核验识别，但结果为 verified 且无 discrepancies", 15)
        elif not fc.get("discrepancies"):
            warn("错误数字用例 discrepancies 为空", 5)

    # ---- 5. 舆情 ----
    st = r.get("sentiment") or {}
    if (st.get("total_mentions") or 0) <= 0:
        warn("舆情捕获量为 0", 5)
    if not st.get("items"):
        warn("舆情明细 items 为空", 3)
    if not st.get("channel_distribution"):
        warn("渠道分布为空", 2)

    # ---- 6. 基本面 ----
    fb = r.get("fundamental") or {}
    if not fb.get("summary"):
        fail("基本面 summary 为空", 8)
    if not fb.get("key_judgment"):
        fail("基本面 key_judgment 为空", 6)
    if not fb.get("metric_table"):
        warn("metric_table 为空", 3)
    # 硬编码检查：非财报事件不应出现"营收下滑"类模板残留
    if expect.get("profile_class") not in {"earnings"}:
        for field in ("summary", "key_judgment"):
            if "营收下滑" in (fb.get(field) or ""):
                fail(f"基本面 {field} 出现模板残留「营收下滑」", 8)

    # ---- 7. 估值 ----
    vals = r.get("valuations") or []
    if len(vals) < 2:
        fail(f"估值模型数量 {len(vals)} < 2", 8)
    for v in vals:
        pct = v.get("impact_pct")
        if pct is None or abs(pct) > 50:
            fail(f"估值影响异常: {v.get('model_key')} impact_pct={pct}", 10)
    if vals and len({v.get("impact_pct") for v in vals}) == 1 and len(vals) > 1:
        warn(f"两个估值模型 impact_pct 完全相同（{vals[0].get('impact_pct')}），模型区分度低", 5)

    # ---- 8. 策略一致性 ----
    sg = r.get("strategy") or {}
    rec = sg.get("recommendation") or ""
    if not rec:
        fail("策略 recommendation 为空", 10)
    if len(sg.get("options") or []) < 3:
        warn(f"策略选项不足 3 个: {len(sg.get('options') or [])}", 3)
    if len(sg.get("risk_warnings") or []) < 2:
        warn("风险提示不足 2 条", 2)
    direction = expect.get("strategy_direction", "any")
    if direction == "hold" and not any(w in rec for w in ["持有", "增持"]):
        fail(f"分红/回报类事件策略应偏持有，实际「{rec}」", 10)
    if direction == "cautious" and not any(w in rec for w in ["谨慎", "减仓", "观望"]):
        warn(f"利空/极端仓位用例策略未偏谨慎，实际「{rec}」", 6)

    # ---- 9. 摘要 ----
    es = r.get("executive_summary") or ""
    if not es:
        fail("executive_summary 为空", 6)
    elif record["payload"]["company_name"] not in es:
        warn("摘要未包含公司名", 2)

    # ---- 10. 耗时 ----
    if (record.get("total_time_s") or 0) > 60:
        warn(f"端到端耗时 {record['total_time_s']}s > 60s", 4)

    stats = {
        "total_time_s": record.get("total_time_s"),
        "subtype": p.get("event_subtype"),
        "fact_status": fc.get("status"),
        "mentions": st.get("total_mentions"),
        "impacts": [v.get("impact_pct") for v in vals],
        "recommendation": rec,
        "n_fin_metrics": n_metrics,
        "n_derived": len(derived),
    }
    return {"case_id": cid, "name": record.get("name"), "group": record.get("group"),
            "score": round(max(score, 0), 1), "issues": issues, "warns": warns, "stats": stats}


def main() -> None:
    import os
    results_dir = Path(os.environ.get("EVAL_DIR", str(RESULTS_DIR)))
    files = sorted(results_dir.glob("T*.json"))
    if not files:
        print("无测试结果"); return
    evals = [evaluate(json.loads(f.read_text(encoding="utf-8"))) for f in files]

    print("=" * 100)
    print(f"{'ID':<5}{'用例':<26}{'分':>6}  {'耗时s':>6} {'subtype':<10}{'核验':<10}{'舆情':>5} {'估值影响%':<14}{'策略'}")
    print("-" * 100)
    for e in evals:
        s = e["stats"]
        impacts = ",".join(str(x) for x in (s.get("impacts") or []))
        print(f"{e['case_id']:<5}{(e['name'] or '')[:24]:<26}{e['score']:>6}  {s.get('total_time_s') or 0:>6} "
              f"{(s.get('subtype') or '')[:9]:<10}{(s.get('fact_status') or ''):<10}{s.get('mentions') or 0:>5} "
              f"{impacts:<14}{s.get('recommendation') or ''}")

    print("=" * 100)
    scores = [e["score"] for e in evals]
    times = [e["stats"].get("total_time_s") or 0 for e in evals]
    print(f"均分 {sum(scores)/len(scores):.1f} | 最低 {min(scores)} | 满分 {sum(1 for s in scores if s >= 99)}/{len(scores)}")
    print(f"耗时 avg {sum(times)/len(times):.1f}s / max {max(times)}s / min {min(times)}s")

    print("\n--- 问题清单（issues = 严重 / warns = 轻微）---")
    for e in evals:
        if e["issues"] or e["warns"]:
            print(f"\n[{e['case_id']}] {e['name']}  (score {e['score']})")
            for i in e["issues"]:
                print(f"  [ISSUE] {i}")
            for w in e["warns"]:
                print(f"  [warn]  {w}")

    (results_dir / "_eval.json").write_text(json.dumps(evals, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n评估明细已写入 {results_dir / '_eval.json'}")


if __name__ == "__main__":
    main()
