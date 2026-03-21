"""
Stress-test script for AWSAgent: 20 queries x N runs each.

Checks consistency of metrics + LLM-as-judge answer quality scoring.
Exit 0 = all pass, 1 = any fail.

Usage:
    AWS_PROFILE=hackathon-equipo1 python -m scripts.test_aws_agent_consistency
    AWS_PROFILE=hackathon-equipo1 python -m scripts.test_aws_agent_consistency --runs 3
    AWS_PROFILE=hackathon-equipo1 python -m scripts.test_aws_agent_consistency --quick
    AWS_PROFILE=hackathon-equipo1 python -m scripts.test_aws_agent_consistency --query 1
    AWS_PROFILE=hackathon-equipo1 python -m scripts.test_aws_agent_consistency --quick --no-judge
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import os
import time
import traceback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("consistency_test")

# ---------------------------------------------------------------------------
# Test queries across 5 categories
# ---------------------------------------------------------------------------

TEST_QUERIES = [
    # --- Simple Lookups (Phase 2 skipped) ---
    {
        "id": 1,
        "category": "simple_lookup",
        "query": "Dame los 5 gastos mas recientes del usuario 'deloitte-84'",
        "expect_phase2": False,
        "expect_data": True,
    },
    {
        "id": 2,
        "category": "simple_lookup",
        "query": "Cuantos gastos tiene el usuario 'deloitte-84'?",
        "expect_phase2": True,
        "expect_data": True,
    },
    {
        "id": 3,
        "category": "simple_lookup",
        "query": "Lista los proveedores del usuario 'deloitte-84'",
        "expect_phase2": False,
        "expect_data": True,
    },

    # --- Date Ranges & GSI ---
    {
        "id": 4,
        "category": "date_range",
        "query": "Gastos del usuario 'deloitte-84' con fecha de factura entre '2024-08-01' y '2024-08-31'",
        "expect_phase2": False,
        "expect_data": True,
    },
    {
        "id": 5,
        "category": "date_range",
        "query": "Facturas de gasto de 'deloitte-84' del proveedor con CIF 'TEMP-2E37B4AAAE7814BD'",
        "expect_phase2": False,
        "expect_data": True,
    },
    {
        "id": 6,
        "category": "date_range",
        "query": "Gastos de 'deloitte-84' donde la invoice_date empiece por '2024'",
        "expect_phase2": False,
        "expect_data": True,
    },

    # --- Aggregation & Metrics (Phase 2 used) ---
    {
        "id": 7,
        "category": "aggregation",
        "query": "Cuanto ha gastado en total el usuario 'deloitte-84' en la categoria COMPRAS?",
        "expect_phase2": True,
        "expect_data": True,
    },
    {
        "id": 8,
        "category": "aggregation",
        "query": "Dame el importe total de gastos de 'deloitte-84' en agosto 2024",
        "expect_phase2": True,
        "expect_data": True,
    },
    {
        "id": 9,
        "category": "aggregation",
        "query": "Cual es el gasto medio por factura de 'deloitte-84'?",
        "expect_phase2": True,
        "expect_data": True,
    },

    # --- Multi-Step Analysis (Phase 2 used) ---
    {
        "id": 10,
        "category": "multi_step",
        "query": "Resumen de gastos de 'deloitte-84' agrupados por proveedor, de mayor a menor",
        "expect_phase2": True,
        "expect_data": True,
    },
    {
        "id": 11,
        "category": "multi_step",
        "query": "Top 5 proveedores por importe total para 'deloitte-84'",
        "expect_phase2": True,
        "expect_data": True,
    },
    {
        "id": 12,
        "category": "multi_step",
        "query": "Evolucion mensual de gastos de 'deloitte-84' en 2024",
        "expect_phase2": True,
        "expect_data": True,
    },

    # --- Complex / Edge ---
    {
        "id": 13,
        "category": "complex",
        "query": "Gastos de 'deloitte-84' en agosto 2024 que NO esten conciliados",
        "expect_phase2": False,
        "expect_data": False,  # may be 0 if all reconciled
    },
    {
        "id": 14,
        "category": "complex",
        "query": "Gastos de 'deloitte-84' donde gestorId sea 'talky' e invoice_date de 2024",
        "expect_phase2": False,
        "expect_data": True,
    },
    {
        "id": 15,
        "category": "complex",
        "query": "Cuantas facturas de credito (credit_note) tiene 'deloitte-84'?",
        "expect_phase2": True,
        "expect_data": False,  # may be 0 credit notes
    },

    # --- Edge Cases & GSI Selection ---
    {
        "id": 16,
        "category": "edge_case",
        "query": "Gastos de 'deloitte-84' del Q1 2026 (enero a marzo 2026)",
        "expect_phase2": False,
        "expect_data": False,  # no 2026 data
    },
    {
        "id": 17,
        "category": "gsi_selection",
        "query": "Gastos de 'deloitte-84' con pnl_date entre '2024-01-01' y '2024-12-31' agrupados por mes",
        "expect_phase2": True,
        "expect_data": False,  # pnl_date may not be populated
    },
    {
        "id": 18,
        "category": "edge_case",
        "query": "Facturas de ingreso de 'deloitte-84' con invoice_date en agosto 2024",
        "expect_phase2": False,
        "expect_data": False,  # may not have income invoices
    },
    {
        "id": 19,
        "category": "complex",
        "query": "Movimientos bancarios de 'deloitte-84' pendientes de conciliar",
        "expect_phase2": False,
        "expect_data": False,  # may not have bank data
    },
    {
        "id": 20,
        "category": "aggregation",
        "query": "IVA soportado total (suma de vatDeductibleAmount) de los gastos de 'deloitte-84' en 2024",
        "expect_phase2": True,
        "expect_data": True,
    },
]


# ---------------------------------------------------------------------------
# LLM-as-judge: evaluate answer quality with a separate model
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = """\
You are evaluating an AI financial assistant's response to a user query.
Score from 0 to 10 and explain briefly.

Criteria:
- Does the answer directly address the user's query? (0 if generic like "Data retrieved successfully")
- Is the answer in the correct language? (Spanish query should get Spanish answer)
- Are the numbers and data reasonable for the question asked?
- If no data was found, does the answer explain WHY and what was searched?
- Is the answer helpful and specific to a CFO user?

Context: The user is 'deloitte-84', a Spanish business. Data exists for 2024-2025 but NOT for 2026.

Respond with ONLY a JSON object: {"score": N, "reason": "brief explanation"}
"""


def judge_answer(query_text: str, result: dict, expect_data: bool) -> dict:
    """Use gpt-5-mini to evaluate whether the answer makes sense."""
    from hackathon_backend.services.lambdas.agent.core.config import completion

    answer = result.get("answer_preview", "")
    metrics = result.get("metrics", {})
    sources = result.get("sources_count", 0)

    user_msg = (
        f"Query: {query_text}\n"
        f"Answer: {answer}\n"
        f"Metrics: {json.dumps(metrics)}\n"
        f"Sources returned: {sources}\n"
        f"Expected to have data: {expect_data}"
    )

    try:
        resp = completion(
            model_id="gpt-5-mini",
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
        )
        content = resp.choices[0].message.content or ""
        # Parse JSON from response
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        parsed = json.loads(content)
        return {"score": parsed.get("score", 0), "reason": parsed.get("reason", "")}
    except Exception as exc:
        logger.warning("Judge failed: %s", exc)
        return {"score": -1, "reason": f"Judge error: {exc}"}


def run_single_query(query_info: dict, user_id: str = "deloitte-84") -> dict:
    """Run a single query with a fresh AWSAgent instance. Returns result dict or error."""
    from hackathon_backend.agents.aws_agent import AWSAgent

    agent = AWSAgent(user_id=user_id)
    start = time.time()
    result = agent.run(query_info["query"])
    elapsed = time.time() - start

    if not result.success:
        return {"error": result.error, "success": False, "elapsed": elapsed}

    data = result.data or {}
    return {
        "success": True,
        "metrics": data.get("metrics", {}),
        "sources_count": len(data.get("sources", [])),
        "answer_preview": str(data.get("answer", ""))[:200],
        "has_data": data.get("data") is not None,
        "tool_calls": result.iterations_used,
        "elapsed": elapsed,
    }


def compare_metrics(results: list[dict]) -> tuple[bool, str]:
    """Compare metrics across runs. Returns (all_consistent, detail_message)."""
    successful = [r for r in results if r.get("success")]
    if len(successful) == 0:
        return False, f"All {len(results)} runs failed"
    if len(successful) == 1:
        # Single run (--quick mode): pass if it succeeded
        return True, f"Single run OK. Metrics: {successful[0]['metrics']}"
    if len(successful) < 2:
        return False, f"Only {len(successful)} successful runs out of {len(results)}"

    # Compare metrics dicts (exact numeric match)
    reference = successful[0]["metrics"]
    for i, run in enumerate(successful[1:], 2):
        current = run["metrics"]
        if set(reference.keys()) != set(current.keys()):
            return False, f"Run {i} has different metric keys: {set(current.keys())} vs {set(reference.keys())}"
        for key in reference:
            ref_val = reference[key]
            cur_val = current[key]
            # Allow small float tolerance
            if isinstance(ref_val, (int, float)) and isinstance(cur_val, (int, float)):
                if abs(ref_val - cur_val) > 0.01:
                    return False, f"Run {i} metric '{key}' differs: {cur_val} vs {ref_val}"
            elif ref_val != cur_val:
                return False, f"Run {i} metric '{key}' differs: {cur_val} vs {ref_val}"

    # Compare source counts
    ref_sources = successful[0]["sources_count"]
    for i, run in enumerate(successful[1:], 2):
        if run["sources_count"] != ref_sources:
            return False, f"Run {i} sources_count differs: {run['sources_count']} vs {ref_sources}"

    return True, f"All {len(successful)} runs consistent. Metrics: {reference}"


def main():
    parser = argparse.ArgumentParser(description="AWSAgent consistency stress test")
    parser.add_argument("--runs", type=int, default=5, help="Runs per query (default: 5)")
    parser.add_argument("--quick", action="store_true", help="Single run per query (fast validation)")
    parser.add_argument("--no-judge", action="store_true", help="Skip LLM-as-judge evaluation")
    parser.add_argument("--query", type=int, help="Run only query N (1-20)")
    parser.add_argument("--user-id", default="deloitte-84", help="User ID for queries")
    args = parser.parse_args()

    # Initialize
    print("Initializing models...")
    from hackathon_backend.services.lambdas.agent.core.config import init_all
    init_all()
    print("Models loaded.\n")

    if args.quick:
        args.runs = 1

    queries = TEST_QUERIES
    if args.query:
        queries = [q for q in TEST_QUERIES if q["id"] == args.query]
        if not queries:
            print(f"Query {args.query} not found (valid: 1-15)")
            sys.exit(1)

    total_pass = 0
    total_fail = 0
    failures = []
    all_timings = []
    all_judge_scores = []
    use_judge = not args.no_judge

    for qinfo in queries:
        qid = qinfo["id"]
        print(f"\n{'='*70}")
        print(f"  Q{qid} [{qinfo['category']}]: {qinfo['query'][:60]}...")
        print(f"{'='*70}")

        results = []
        for run_num in range(1, args.runs + 1):
            print(f"  Run {run_num}/{args.runs}...", end=" ", flush=True)
            try:
                result = run_single_query(qinfo, args.user_id)
                results.append(result)
                if result["success"]:
                    judge_str = ""
                    if use_judge and run_num == 1:  # judge only first run
                        verdict = judge_answer(qinfo["query"], result, qinfo.get("expect_data", True))
                        score = verdict["score"]
                        all_judge_scores.append({"id": qid, "score": score, "reason": verdict["reason"]})
                        judge_str = f", judge={score}/10"
                    print(f"OK (tools={result['tool_calls']}, sources={result['sources_count']}, {result['elapsed']:.1f}s{judge_str})")
                    if use_judge and run_num == 1 and score >= 0:
                        print(f"    Judge: {verdict['reason'][:100]}")
                else:
                    # Retry once on error
                    print(f"ERROR: {result['error'][:60]}. Retrying...")
                    result2 = run_single_query(qinfo, args.user_id)
                    results[-1] = result2
                    if result2["success"]:
                        print(f"  Retry OK (tools={result2['tool_calls']})")
                    else:
                        print(f"  Retry FAILED: {result2['error'][:60]}")
            except Exception as exc:
                print(f"EXCEPTION: {exc}")
                traceback.print_exc()
                results.append({"error": str(exc), "success": False})

        # Collect timings
        for r in results:
            if r.get("elapsed"):
                all_timings.append({"id": qid, "elapsed": r["elapsed"]})

        # Check consistency
        consistent, detail = compare_metrics(results)
        if consistent:
            print(f"  PASS: {detail}")
            total_pass += 1
        else:
            print(f"  FAIL: {detail}")
            total_fail += 1
            failures.append({"query_id": qid, "detail": detail})

    # Summary
    print(f"\n{'='*70}")
    print(f"  SUMMARY: {total_pass} passed, {total_fail} failed out of {len(queries)} queries")
    if failures:
        print(f"  Failures:")
        for f in failures:
            print(f"    Q{f['query_id']}: {f['detail']}")
    if all_timings:
        total_time = sum(t["elapsed"] for t in all_timings)
        avg_time = total_time / len(all_timings)
        max_t = max(all_timings, key=lambda t: t["elapsed"])
        print(f"  Timing: total={total_time:.1f}s, avg={avg_time:.1f}s, slowest=Q{max_t['id']} ({max_t['elapsed']:.1f}s)")
    if all_judge_scores:
        valid_scores = [s for s in all_judge_scores if s["score"] >= 0]
        if valid_scores:
            avg_score = sum(s["score"] for s in valid_scores) / len(valid_scores)
            low_scores = [s for s in valid_scores if s["score"] < 5]
            print(f"  Judge: avg={avg_score:.1f}/10 across {len(valid_scores)} queries")
            if low_scores:
                print(f"  WARN — Low judge scores:")
                for s in low_scores:
                    print(f"    Q{s['id']}: {s['score']}/10 — {s['reason'][:80]}")
    print(f"{'='*70}")

    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
