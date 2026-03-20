"""
Local CLI to test the AI CFO Agent flow end-to-end.

Usage:
    python -m hackathon_backend.services.lambdas.agent.main
    python -m hackathon_backend.services.lambdas.agent.main -q "¿Cuánto facturé?" -l deloitte-84
    python -m hackathon_backend.services.lambdas.agent.main -m claude-opus-4.6 -l deloitte-84

Environment variables (optional overrides):
    AWS_PROFILE          - AWS profile (default: hackathon-equipo1)
    AWS_REGION           - AWS region (default: eu-west-3)
    TABLE_ENV_PREFIX     - DynamoDB table prefix (default: Dev)
"""
from __future__ import annotations

import argparse
import json
import sys
import os

# Fix Windows console encoding for Unicode output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from langfuse import observe, get_client as _get_langfuse_client

from hackathon_backend.services.lambdas.agent.core.config import (
    init_all,
    AVAILABLE_MODELS,
)
from hackathon_backend.services.lambdas.agent.core.prompts import sync_prompts_to_langfuse
from hackathon_backend.services.lambdas.agent.core.classifier import classify_intent
from hackathon_backend.services.lambdas.agent.core.orchestrator import orchestrate
from hackathon_backend.services.lambdas.agent.core.query_agent import run_query_agent


DEFAULT_LOCATION_ID = "deloitte-84"


@observe(name="agent_pipeline")
def run_pipeline(
    question: str,
    location_id: str,
    orchestrator_model: str,
    classifier_model: str,
) -> dict:
    """
    Full agent pipeline:
      1. Classify intent (fast_chat vs complex_task)
      2. Orchestrate (direct answer vs needs_data)
      3. If needs_data → AWSAgent fetches from DynamoDB → LLM generates final answer
    """
    # Step 1: Classify intent
    intent = classify_intent(question, model_id=classifier_model)
    print(f"\n  [1/3] Intent: {intent}")

    if intent == "complex_task":
        return {
            "type": "complex_task",
            "answer": (
                "Esta es una tarea compleja que requiere procesamiento en segundo plano. "
                "El sistema de tareas asíncronas aún no está implementado. "
                "Por ahora, solo se soportan consultas rápidas (fast_chat)."
            ),
            "intent": intent,
            "chart": None,
            "model_used": classifier_model,
        }

    # Step 2: Orchestrate — decide if direct answer or needs data
    orch_result = orchestrate(
        user_message=question,
        location_id=location_id,
        model_id=orchestrator_model,
    )
    orch_result["intent"] = intent

    if orch_result["type"] == "direct_answer":
        print(f"  [2/3] Direct answer (no DB needed)")
        return orch_result

    # Step 3: Needs data → run the specialized query agent
    data_requests = orch_result.get("data_requests", [])
    chart_suggestion = orch_result.get("chart_suggestion")
    print(f"  [2/3] Needs data: {len(data_requests)} request(s)")

    for i, req in enumerate(data_requests, 1):
        print(f"        {i}. [{req['table']}] {req['description']}")

    print(f"  [3/3] Query Agent planning and executing...")
    agent_result = run_query_agent(
        user_question=question,
        data_requests=data_requests,
        location_id=location_id,
        model_id=orchestrator_model,
        chart_suggestion=chart_suggestion,
    )

    return {
        "type": "full_answer",
        "answer": agent_result["answer"],
        "chart": agent_result.get("chart"),
        "sources": agent_result.get("sources", []),
        "intent": intent,
        "model_used": orchestrator_model,
    }


def _print_result(result: dict):
    """Pretty-print the pipeline result."""
    resp_type = result.get("type", "unknown")

    print("\n" + "=" * 60)
    if resp_type == "direct_answer":
        print(f"  DIRECT ANSWER ({result.get('model_used', '?')})")
        print("=" * 60)
        print(f"\n{result['answer']}")

    elif resp_type == "full_answer":
        print(f"  ANSWER WITH DATA ({result.get('model_used', '?')})")
        print("=" * 60)
        print(f"\n{result['answer']}")
        if result.get("chart"):
            print(f"\n  Chart: {json.dumps(result['chart'], indent=2, ensure_ascii=False)}")
        sources = result.get("sources", [])
        if sources:
            print(f"\n  Sources ({len(sources)} documents):")
            for s in sources[:10]:
                supplier = s.get("supplier") or s.get("client_name", "?")
                total = s.get("total", "?")
                date = s.get("invoice_date", "?")
                recon = "PAID" if s.get("reconciled") else "UNPAID"
                print(f"    - {supplier} | {date} | {total} EUR | {recon}")
                print(f"      ID: {s.get('categoryDate', '?')}")

    elif resp_type == "complex_task":
        print("  COMPLEX TASK (not yet implemented)")
        print("=" * 60)
        print(f"\n{result['answer']}")

    else:
        print("  RAW RESPONSE")
        print("=" * 60)
        print(json.dumps(result, indent=2, ensure_ascii=False))


def single_query(question: str, location_id: str, orchestrator_model: str, classifier_model: str):
    """Run a single question and print the result."""
    result = run_pipeline(question, location_id, orchestrator_model, classifier_model)
    _print_result(result)
    _get_langfuse_client().flush()


def interactive_mode(location_id: str, orchestrator_model: str, classifier_model: str):
    """Interactive REPL for testing."""
    print("\n" + "=" * 60)
    print("  AI CFO Agent — Interactive Mode")
    print(f"  Location: {location_id}")
    print(f"  Orchestrator: {orchestrator_model}")
    print(f"  Classifier: {classifier_model}")
    print("  Commands: 'model', 'models', 'location', 'quit'")
    print("=" * 60)

    while True:
        try:
            question = input("\n You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break
        if question.lower() == "model":
            print(f"  Available: {list(AVAILABLE_MODELS.keys())}")
            new_model = input("  Select model > ").strip()
            if new_model in AVAILABLE_MODELS:
                orchestrator_model = new_model
                print(f"  -> Switched to: {orchestrator_model}")
            else:
                print(f"  -> Unknown. Keeping: {orchestrator_model}")
            continue
        if question.lower() == "models":
            for m in AVAILABLE_MODELS:
                marker = " <-- current" if m == orchestrator_model else ""
                print(f"  - {m}{marker}")
            continue
        if question.lower() == "location":
            new_loc = input(f"  Current: {location_id}. New locationId > ").strip()
            if new_loc:
                location_id = new_loc
                print(f"  -> Location set to: {location_id}")
            continue

        result = run_pipeline(question, location_id, orchestrator_model, classifier_model)
        _print_result(result)
        _get_langfuse_client().flush()


def main():
    parser = argparse.ArgumentParser(description="AI CFO Agent — Local CLI")
    parser.add_argument("-q", "--question", help="Single question (omit for interactive)")
    parser.add_argument("-l", "--location-id", default=DEFAULT_LOCATION_ID,
                        help=f"Location/tenant ID (default: {DEFAULT_LOCATION_ID})")
    parser.add_argument("-m", "--model", default="claude-sonnet-4.5",
                        help="Orchestrator model (default: claude-sonnet-4.5)")
    parser.add_argument("--classifier-model", default="gpt-5-mini",
                        help="Classifier model (default: gpt-5-mini)")
    parser.add_argument("--list-models", action="store_true", help="List models and exit")
    args = parser.parse_args()

    print("Initializing models and Langfuse...")
    init_all()
    sync_prompts_to_langfuse()
    print(f"Models loaded: {list(AVAILABLE_MODELS.keys())}")

    if args.list_models:
        for m in AVAILABLE_MODELS:
            print(f"  - {m}")
        return

    if args.question:
        single_query(args.question, args.location_id, args.model, args.classifier_model)
    else:
        interactive_mode(args.location_id, args.model, args.classifier_model)


if __name__ == "__main__":
    main()
