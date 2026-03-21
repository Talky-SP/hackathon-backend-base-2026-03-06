"""
Local CLI to test the AI CFO Agent flow.

Usage:
    python -m hackathon_backend.services.lambdas.agent.main

Environment variables (optional overrides):
    AWS_PROFILE          - AWS profile (default: hackathon-equipo1)
    AWS_REGION           - AWS region (default: eu-west-3)
    TABLE_ENV_PREFIX     - DynamoDB table prefix (default: Dev)
    AGENT_MODEL          - Default model for orchestration
    CLASSIFIER_MODEL     - Model for intent classification
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


DEFAULT_LOCATION_ID = "demo-location-001"


@observe(name="agent_pipeline")
def run_pipeline(
    question: str,
    location_id: str,
    orchestrator_model: str,
    classifier_model: str,
) -> dict:
    """Full agent pipeline: classify → route → respond."""
    # Step 1: Classify intent
    intent = classify_intent(question, model_id=classifier_model)
    print(f"\n  Intent: {intent}")

    # Step 2: Route based on intent
    if intent == "complex_task":
        from hackathon_backend.agents.task_agent import TaskAgent

        print(f"\n  Routing to TaskAgent (Deep Agent)...")
        agent = TaskAgent(
            user_id=location_id,
            model_id=orchestrator_model,
        )
        result = agent.run(question)
        data = result.data or {}
        return {
            "answer": data.get("answer", "") if result.success else (result.error or "Task failed"),
            "report": data.get("report"),
            "data": data.get("data"),
            "sources": data.get("sources", []),
            "chart": result.chart_html,
            "exports": data.get("exports", []),
            "intent": intent,
            "tool_calls_made": result.iterations_used,
            "model_used": orchestrator_model,
        }

    # Step 3: Fast-chat orchestration
    result = orchestrate(
        user_message=question,
        location_id=location_id,
        model_id=orchestrator_model,
    )
    result["intent"] = intent
    return result


def interactive_mode(location_id: str, orchestrator_model: str, classifier_model: str):
    """Interactive REPL for testing."""
    print("\n" + "=" * 60)
    print("  AI CFO Agent — Interactive Mode")
    print(f"  Location: {location_id}")
    print(f"  Orchestrator model: {orchestrator_model}")
    print(f"  Classifier model: {classifier_model}")
    print("  Type 'quit' to exit, 'model' to change model")
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
            print(f"  Available models: {list(AVAILABLE_MODELS.keys())}")
            new_model = input("  Select model > ").strip()
            if new_model in AVAILABLE_MODELS:
                orchestrator_model = new_model
                print(f"  Switched to: {orchestrator_model}")
            else:
                print(f"  Unknown model. Keeping: {orchestrator_model}")
            continue
        if question.lower() == "models":
            for m in AVAILABLE_MODELS:
                marker = " <-- current" if m == orchestrator_model else ""
                print(f"  - {m}{marker}")
            continue

        print("\n  Processing...")
        result = run_pipeline(question, location_id, orchestrator_model, classifier_model)

        _print_result(result)

        # Flush Langfuse
        _get_langfuse_client().flush()


def _print_result(result: dict):
    """Pretty-print the pipeline result."""
    resp_type = result.get("type", "unknown")
    model = result.get("model_used", "?")

    if resp_type == "direct_answer":
        print(f"\n  [{model}] Direct answer:")
        print(f"  {result['answer']}")
    elif resp_type == "needs_data":
        print(f"\n  [{model}] Needs data from DB agent:")
        print(f"  Question: {result['user_question']}")
        print(f"  Data requests:")
        for i, req in enumerate(result.get("data_requests", []), 1):
            print(f"    {i}. [{req['table']}] {req['description']}")
            if req.get("fields_needed"):
                print(f"       Fields: {', '.join(req['fields_needed'])}")
            if req.get("date_range"):
                dr = req["date_range"]
                print(f"       Date range: {dr.get('from', '?')} -> {dr.get('to', '?')}")
            if req.get("filters"):
                print(f"       Filters: {req['filters']}")
        if result.get("chart_suggestion"):
            print(f"  Chart: {result['chart_suggestion']}")
    elif result.get("intent") == "complex_task":
        print(f"\n  [Deep Agent | {model}] Complex task result:")
        answer = result.get("answer", "")
        if len(answer) > 500:
            print(f"  {answer[:500]}...")
        else:
            print(f"  {answer}")
        if result.get("exports"):
            print(f"  Exports: {result['exports']}")
        if result.get("chart"):
            print(f"  Chart: generated ({len(result['chart'])} chars)")
        if result.get("sources"):
            print(f"  Sources: {len(result['sources'])} items")
        print(f"  Tool calls: {result.get('tool_calls_made', 0)}")
    else:
        print(f"\n  {json.dumps(result, indent=2, ensure_ascii=False)}")


def single_query(question: str, location_id: str, orchestrator_model: str, classifier_model: str):
    """Run a single question and print the result."""
    result = run_pipeline(question, location_id, orchestrator_model, classifier_model)
    _print_result(result)
    print(f"\n  --- Raw JSON ---")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    _get_langfuse_client().flush()


def main():
    parser = argparse.ArgumentParser(description="AI CFO Agent — Local CLI")
    parser.add_argument(
        "-q", "--question",
        help="Single question to ask (omit for interactive mode)",
    )
    parser.add_argument(
        "-l", "--location-id",
        default=DEFAULT_LOCATION_ID,
        help=f"Location/tenant ID (default: {DEFAULT_LOCATION_ID})",
    )
    parser.add_argument(
        "-m", "--model",
        default="claude-sonnet-4.5",
        help="Model for orchestration (default: claude-sonnet-4.5)",
    )
    parser.add_argument(
        "--classifier-model",
        default="gpt-5-mini",
        help="Model for intent classification (default: gpt-5-mini)",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List available models and exit",
    )
    args = parser.parse_args()

    # Initialize everything
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
