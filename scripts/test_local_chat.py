#!/usr/bin/env python3
"""
Local interactive test for TaskAgent — runs in the terminal, no browser needed.

Usage:
    AWS_PROFILE=hackathon-equipo1 python -m scripts.test_local_chat
    AWS_PROFILE=hackathon-equipo1 python -m scripts.test_local_chat --model claude-sonnet-4.5
    AWS_PROFILE=hackathon-equipo1 python -m scripts.test_local_chat --fast   # AWSAgent only
    AWS_PROFILE=hackathon-equipo1 python -m scripts.test_local_chat --user deloitte-84

Press Ctrl+C to exit. Type 'help' for sample prompts.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

SAMPLE_PROMPTS = {
    "simple": [
        "Dame los 5 gastos más recientes",
        "¿Cuántos proveedores tiene el usuario?",
        "Top 3 proveedores por importe total",
    ],
    "deep": [
        "Resumen de gastos por proveedor, top 10 por importe total. Genera gráfico de barras y Excel.",
        "Análisis de antigüedad (aging) de pagos pendientes. Clasifica por buckets 0-30, 31-60, 61-90, >90 días.",
        "Genera el borrador del Modelo 303 (IVA) del último trimestre con datos disponibles.",
        "Prepara un P&L mensual con gastos, ingresos y nóminas del último trimestre.",
        "Análisis de rentabilidad por proveedor: quién nos cuesta más y en qué categorías.",
    ],
    "export": [
        "Exporta todos los gastos a Excel agrupados por mes y proveedor.",
        "Genera un CSV con todas las facturas de gasto del último año.",
    ],
    "chart": [
        "Gráfico de evolución mensual de gastos en 2024.",
        "Gráfico de tarta con distribución de gastos por categoría.",
    ],
}


def print_help():
    print("\n  === Sample Prompts ===")
    for cat, prompts in SAMPLE_PROMPTS.items():
        print(f"\n  [{cat.upper()}]")
        for i, p in enumerate(prompts, 1):
            print(f"    {i}. {p}")
    print("\n  Commands: help, quit, mode [fast|deep], export, sources")
    print()


def run_fast(agent, query: str):
    """Run AWSAgent (fast mode)."""
    start = time.time()
    result = agent.run(query)
    elapsed = time.time() - start

    print(f"\n  ({'OK' if result.success else 'FAIL'}) [{elapsed:.1f}s, {result.iterations_used} tool calls]")

    if not result.success:
        print(f"  Error: {result.error}")
        return result

    data = result.data or {}
    answer = data.get("answer", "")
    if answer:
        # Truncate long answers
        if len(answer) > 1000:
            print(f"  {answer[:1000]}...")
        else:
            print(f"  {answer}")

    metrics = data.get("metrics", {})
    if metrics:
        print(f"  Metrics: {json.dumps(metrics, default=str)}")

    sources = data.get("sources", [])
    if sources:
        print(f"  Sources: {len(sources)} items")

    if result.chart_html:
        chart_path = os.path.join("test_output", "last_chart.html")
        os.makedirs("test_output", exist_ok=True)
        with open(chart_path, "w") as f:
            f.write(f"<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
                    f"<body style='padding:20px'>{result.chart_html}</body></html>")
        print(f"  Chart saved: {chart_path}")

    return result


def run_deep(agent_cls, user_id: str, model_id: str, query: str):
    """Run TaskAgent (deep mode) with progress output."""
    def progress_cb(event: str, data: dict):
        desc = data.get("description", data.get("plan", data.get("step", data.get("filename", ""))))
        print(f"    [{event}] {str(desc)[:80]}", flush=True)

    export_dir = os.path.join("test_output", "task_exports")
    os.makedirs(export_dir, exist_ok=True)

    agent = agent_cls(
        user_id=user_id,
        model_id=model_id,
        progress_callback=progress_cb,
        export_dir=export_dir,
    )

    start = time.time()
    result = agent.run(query)
    elapsed = time.time() - start

    print(f"\n  ({'OK' if result.success else 'FAIL'}) [{elapsed:.1f}s, {result.iterations_used} tool calls]")

    if not result.success:
        print(f"  Error: {result.error}")
        return result

    data = result.data or {}
    answer = data.get("answer", "")
    if answer:
        if len(answer) > 1500:
            print(f"  {answer[:1500]}...\n  [truncated, {len(answer)} chars total]")
        else:
            print(f"  {answer}")

    metrics = data.get("metrics", {})
    if metrics:
        print(f"\n  Metrics: {json.dumps(metrics, default=str, indent=2)}")

    sources = data.get("sources", [])
    if sources:
        print(f"  Sources: {len(sources)} items")

    exports = data.get("exports", [])
    if exports:
        print(f"  Exports:")
        for p in exports:
            print(f"    - {p}")

    if result.chart_html:
        chart_path = os.path.join("test_output", "last_chart.html")
        os.makedirs("test_output", exist_ok=True)
        with open(chart_path, "w") as f:
            f.write(f"<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
                    f"<body style='padding:20px'>{result.chart_html}</body></html>")
        print(f"  Chart saved: {chart_path} (open in browser)")

    # Save full result for inspection
    result_path = os.path.join("test_output", "last_result.json")
    with open(result_path, "w") as f:
        json.dump({"success": result.success, "data": data, "error": result.error,
                    "iterations_used": result.iterations_used}, f, indent=2, default=str, ensure_ascii=False)
    print(f"  Full result: {result_path}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Local TaskAgent test chat")
    parser.add_argument("--user", default="deloitte-84", help="userId (default: deloitte-84)")
    parser.add_argument("--model", default="claude-sonnet-4.5", help="Brain model (default: claude-sonnet-4.5)")
    parser.add_argument("--fast", action="store_true", help="Use AWSAgent only (fast mode)")
    parser.add_argument("-q", "--query", help="Run single query and exit")
    args = parser.parse_args()

    print("Initializing models...")
    from hackathon_backend.services.lambdas.agent.core.config import init_all, AVAILABLE_MODELS
    init_all()
    print(f"Models: {list(AVAILABLE_MODELS.keys())}")

    mode = "fast" if args.fast else "deep"

    # Set up fast agent
    from hackathon_backend.agents.aws_agent import AWSAgent
    from hackathon_backend.agents.task_agent import TaskAgent
    fast_agent = AWSAgent(user_id=args.user)

    # Single query mode
    if args.query:
        if mode == "fast":
            run_fast(fast_agent, args.query)
        else:
            run_deep(TaskAgent, args.user, args.model, args.query)
        return

    # Interactive mode
    print(f"\n{'='*60}")
    print(f"  Talky Local Chat — {mode.upper()} mode")
    print(f"  User: {args.user} | Model: {args.model}")
    print(f"  Type 'help' for sample prompts, 'quit' to exit")
    print(f"  Type 'mode fast' or 'mode deep' to switch")
    print(f"{'='*60}")

    while True:
        try:
            query = input(f"\n [{mode}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break
        if query.lower() == "help":
            print_help()
            continue
        if query.lower().startswith("mode "):
            new_mode = query.split()[1].lower()
            if new_mode in ("fast", "deep"):
                mode = new_mode
                print(f"  Switched to {mode} mode")
            else:
                print(f"  Unknown mode. Use: fast, deep")
            continue

        print(f"  Processing ({mode})...", flush=True)

        try:
            if mode == "fast":
                run_fast(fast_agent, query)
            else:
                run_deep(TaskAgent, args.user, args.model, query)
        except Exception as exc:
            print(f"  EXCEPTION: {exc}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
