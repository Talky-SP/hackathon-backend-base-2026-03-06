"""
Flash tests for AWSAgent: 10 targeted tests with data assertions + LLM-as-judge.

Each test verifies a specific query pattern against known deloitte-84 data.
Fast iteration: ~2-3 minutes total.

Usage:
    AWS_PROFILE=hackathon-equipo1 python -m scripts.flash_test_aws_agent
    AWS_PROFILE=hackathon-equipo1 python -m scripts.flash_test_aws_agent --test 1
    AWS_PROFILE=hackathon-equipo1 python -m scripts.flash_test_aws_agent --no-judge
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("flash_test")


# ---------------------------------------------------------------------------
# Test definitions — based on KNOWN deloitte-84 data
# ---------------------------------------------------------------------------

FLASH_TESTS = [
    {
        "id": 1,
        "name": "supplier_name_lookup",
        "query": "Facturas de gasto del proveedor Deloitte BPS",
        "expected": {
            "should_find_data": True,
            "min_items": 5,
            "max_items": 25,
        },
        "judge_criteria": (
            "Should resolve supplier name 'Deloitte BPS' to its CIF via the Providers table, "
            "then query User_Expenses by CIF. Answer should mention Deloitte BPS invoices with amounts. "
            "If agent queried expenses directly without looking up CIF first but still found data, that's OK too."
        ),
    },
    {
        "id": 2,
        "name": "supplier_cif_direct",
        "query": "Gastos del proveedor con CIF B83504761",
        "expected": {
            "should_find_data": True,
            "min_items": 5,
            "max_items": 25,
        },
        "judge_criteria": (
            "Should use UserIdSupplierCifIndex directly with supplier_cif = 'B83504761'. "
            "This CIF is Deloitte BPS. Answer should list expenses for this supplier."
        ),
    },
    {
        "id": 3,
        "name": "date_range_month",
        "query": "Gastos de enero 2025",
        "expected": {
            "should_find_data": True,
            "min_items": 10,
        },
        "judge_criteria": (
            "Should use UserIdInvoiceDateIndex with invoice_date BETWEEN '2025-01-01' AND '2025-01-31'. "
            "Answer should list January 2025 expenses. Known: ~18 items in Jan 2025."
        ),
    },
    {
        "id": 4,
        "name": "category_filter",
        "query": "Gastos en la categoria COMPRAS",
        "expected": {
            "should_find_data": True,
            "min_items": 10,
        },
        "judge_criteria": (
            "Should query base table with begins_with(categoryDate, 'COMPRAS#'). "
            "Known: ~18 COMPRAS expenses. Answer should list purchase expenses."
        ),
    },
    {
        "id": 5,
        "name": "total_aggregation",
        "query": "Cual es el gasto total en 2025?",
        "expected": {
            "should_find_data": True,
            "phase2_aggregation": True,
        },
        "judge_criteria": (
            "Should fetch all 2025 expenses via UserIdInvoiceDateIndex and aggregate totals. "
            "Known: ~200 items in 2025, total > 500k EUR. Answer MUST include a total amount in EUR."
        ),
    },
    {
        "id": 6,
        "name": "nonexistent_supplier",
        "query": "Facturas del proveedor NTT DATA",
        "expected": {
            "should_find_data": False,
        },
        "judge_criteria": (
            "Should search Providers for 'NTT DATA', find nothing, and gracefully report "
            "no invoices found. MUST NOT hallucinate data or amounts. "
            "Bonus: mention that the provider was not found in the system."
        ),
    },
    {
        "id": 7,
        "name": "bank_transactions",
        "query": "Movimientos bancarios de marzo 2025",
        "expected": {
            "should_find_data": True,
            "min_items": 10,
        },
        "judge_criteria": (
            "Should query Bank_Reconciliations with locationId and begins_with(SK, 'MTXN#2025-03'). "
            "Answer should list March 2025 bank transactions with amounts."
        ),
    },
    {
        "id": 8,
        "name": "multi_table_hoffmann",
        "query": "Gastos del proveedor HOFFMANN EITLE",
        "expected": {
            "should_find_data": True,
            "min_items": 15,
            "max_items": 40,
        },
        "judge_criteria": (
            "Should resolve 'HOFFMANN EITLE' via Providers to get CIF B86610599, "
            "then query expenses. Known: ~28 items, ~110k EUR total. "
            "Answer should mention HOFFMANN EITLE expenses."
        ),
    },
    {
        "id": 9,
        "name": "top_suppliers",
        "query": "Top 3 proveedores por importe total de gastos",
        "expected": {
            "should_find_data": True,
            "phase2_aggregation": True,
        },
        "judge_criteria": (
            "Should fetch all expenses, group by supplier, sum totals, return top 3. "
            "Answer MUST list 3 suppliers with their total amounts. "
            "HOFFMANN EITLE should likely be in top results."
        ),
    },
    {
        "id": 10,
        "name": "list_providers",
        "query": "Lista todos los proveedores",
        "expected": {
            "should_find_data": True,
            "min_items": 30,
        },
        "judge_criteria": (
            "Should query Providers table with locationId. "
            "Known: ~49 providers. Answer should list provider names and CIFs."
        ),
    },
]


# ---------------------------------------------------------------------------
# LLM-as-judge
# ---------------------------------------------------------------------------

FLASH_JUDGE_SYSTEM = """\
You are evaluating an AI financial assistant's response to a specific test case.
Score from 0 to 10 based on the criteria below.

## Test-Specific Criteria
{test_criteria}

## General Criteria
- Answer in correct language (Spanish query -> Spanish answer)
- Numbers and data must be reasonable (no hallucinated amounts)
- If no data was found, must explain gracefully what was searched
- Must be helpful and specific to a CFO user

## Data Assertions
- Expected to find data: {should_find_data}
- Items returned: {sources_count}
{count_notes}

Respond with ONLY a JSON object: {{"score": N, "reason": "brief explanation", "pass": true/false}}
A score >= 6 passes. Below 6 fails.
"""


def judge_flash_answer(test: dict, answer: str, sources_count: int) -> dict:
    """Use gpt-5-mini to evaluate test result quality."""
    from hackathon_backend.services.lambdas.agent.core.config import completion

    expected = test["expected"]
    count_notes = ""
    if "min_items" in expected:
        count_notes += f"- Expected at least {expected['min_items']} items\n"
    if "max_items" in expected:
        count_notes += f"- Expected at most {expected['max_items']} items\n"

    system = FLASH_JUDGE_SYSTEM.format(
        test_criteria=test["judge_criteria"],
        should_find_data=expected["should_find_data"],
        sources_count=sources_count,
        count_notes=count_notes,
    )

    user_msg = (
        f"Query: {test['query']}\n"
        f"Answer: {answer[:500]}\n"
        f"Sources returned: {sources_count}"
    )

    try:
        resp = completion(
            model_id="gpt-5-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
        )
        content = resp.choices[0].message.content or ""
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        parsed = json.loads(content)
        return {
            "score": parsed.get("score", 0),
            "reason": parsed.get("reason", ""),
            "pass": parsed.get("pass", parsed.get("score", 0) >= 6),
        }
    except Exception as exc:
        logger.warning("Judge failed: %s", exc)
        return {"score": -1, "reason": f"Judge error: {exc}", "pass": False}


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_flash_test(test: dict, user_id: str, use_judge: bool) -> dict:
    """Run a single flash test with assertions + optional LLM judge."""
    from hackathon_backend.agents.aws_agent import AWSAgent

    agent = AWSAgent(user_id=user_id)
    start = time.time()
    result = agent.run(test["query"])
    elapsed = time.time() - start

    if not result.success:
        return {
            "pass": False,
            "reason": f"Agent failed: {result.error}",
            "score": 0,
            "sources_count": 0,
            "tool_calls": result.iterations_used,
            "elapsed": elapsed,
            "answer_preview": "",
            "assertions": [f"FAIL: Agent error: {result.error}"],
        }

    data = result.data or {}
    sources_count = len(data.get("sources", []))
    answer = str(data.get("answer", ""))
    expected = test["expected"]

    # Data assertions
    assertions: list[str] = []
    has_fail = False

    # Phase 2 aggregation tests: sources may be 0 because data flows through code execution.
    # For these, check that the answer is non-empty instead.
    is_phase2 = expected.get("phase2_aggregation", False)

    if expected["should_find_data"] and sources_count == 0 and not is_phase2:
        assertions.append(f"FAIL: Expected data but got 0 sources")
        has_fail = True
    if expected["should_find_data"] and is_phase2 and not answer.strip():
        assertions.append(f"FAIL: Phase 2 aggregation returned empty answer")
        has_fail = True
    if not expected["should_find_data"] and sources_count > 0:
        assertions.append(f"WARN: Expected no data but got {sources_count} sources")

    if "min_items" in expected and sources_count < expected["min_items"]:
        assertions.append(f"FAIL: Expected >= {expected['min_items']} items, got {sources_count}")
        has_fail = True
    if "max_items" in expected and sources_count > expected["max_items"]:
        assertions.append(f"WARN: Expected <= {expected['max_items']} items, got {sources_count}")

    # LLM judge
    judge_result = {"score": -1, "reason": "skipped", "pass": True}
    if use_judge:
        judge_result = judge_flash_answer(test, answer, sources_count)

    test_pass = (not has_fail) and judge_result.get("pass", True)

    return {
        "pass": test_pass,
        "score": judge_result["score"],
        "reason": judge_result["reason"],
        "sources_count": sources_count,
        "tool_calls": result.iterations_used,
        "elapsed": elapsed,
        "answer_preview": answer[:150],
        "assertions": assertions,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AWSAgent flash tests")
    parser.add_argument("--test", type=int, help="Run only test N (1-10)")
    parser.add_argument("--no-judge", action="store_true", help="Skip LLM-as-judge (assertions only)")
    parser.add_argument("--user-id", default="deloitte-84", help="User ID for queries")
    args = parser.parse_args()

    print("Initializing models...")
    from hackathon_backend.services.lambdas.agent.core.config import init_all
    init_all()
    print("Models loaded.\n")

    tests = FLASH_TESTS
    if args.test:
        tests = [t for t in FLASH_TESTS if t["id"] == args.test]
        if not tests:
            print(f"Test {args.test} not found (valid: 1-10)")
            sys.exit(1)

    use_judge = not args.no_judge
    results: list[dict] = []
    total_pass = 0
    total_fail = 0

    for test in tests:
        tid = test["id"]
        print(f"\n{'='*65}")
        print(f"  #{tid} [{test['name']}]: {test['query']}")
        print(f"{'='*65}")

        try:
            result = run_flash_test(test, args.user_id, use_judge)
            results.append({"id": tid, **result})

            status = "PASS" if result["pass"] else "FAIL"
            score_str = f"  score={result['score']}/10" if result["score"] >= 0 else ""
            print(f"  {status}  sources={result['sources_count']}  tools={result['tool_calls']}  {result['elapsed']:.1f}s{score_str}")

            if result["assertions"]:
                for a in result["assertions"]:
                    print(f"    {a}")
            if result["score"] >= 0:
                print(f"    Judge: {result['reason'][:100]}")
            if result["answer_preview"]:
                print(f"    Answer: {result['answer_preview'][:100]}...")

            if result["pass"]:
                total_pass += 1
            else:
                total_fail += 1

        except Exception as exc:
            print(f"  EXCEPTION: {exc}")
            traceback.print_exc()
            results.append({"id": tid, "pass": False, "score": 0, "elapsed": 0})
            total_fail += 1

    # Summary
    print(f"\n{'='*65}")
    print(f"  FLASH TEST RESULTS")
    print(f"{'='*65}")

    for r in results:
        tid = r["id"]
        test = next(t for t in FLASH_TESTS if t["id"] == tid)
        status = "PASS" if r.get("pass") else "FAIL"
        score_str = f"{r.get('score', 0):>2}/10" if r.get("score", -1) >= 0 else "  - "
        src = r.get("sources_count", 0)
        tools = r.get("tool_calls", 0)
        elapsed = r.get("elapsed", 0)
        print(f"  #{tid:>2} {test['name']:<26} {status:<4}  {score_str}  sources={src:<4} tools={tools}  {elapsed:.1f}s")

    print(f"{'='*65}")

    valid_scores = [r["score"] for r in results if r.get("score", -1) >= 0]
    if valid_scores:
        avg = sum(valid_scores) / len(valid_scores)
        print(f"  Score: avg={avg:.1f}/10 across {len(valid_scores)} judged tests")

    total_time = sum(r.get("elapsed", 0) for r in results)
    print(f"  Total: {total_pass} passed, {total_fail} failed, {total_time:.1f}s")
    print(f"{'='*65}")

    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
