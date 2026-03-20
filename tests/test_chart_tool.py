"""
Visual test suite for the chart generation tool.

Calls generate_chart() with pre-written realistic datasets, saves the output
as both HTML files (interactive) and PNG images (via QuickChart) for inspection.

Usage:
    python -m tests.test_chart_tool

Output:
    test_output/charts/*.html  — open in browser for interactive charts
    test_output/charts/*.png   — static images for quick visual check

Requires:
    pip install quickchart-io litellm
"""
from __future__ import annotations

import json
import os
import sys

from hackathon_backend.agents.chart_tool import generate_chart, extract_chartjs_config

# ---------------------------------------------------------------------------
# Sample datasets — realistic financial data, no DynamoDB needed
# ---------------------------------------------------------------------------
SAMPLE_DATASETS = {
    "monthly_expenses_bar": {
        "description": "Bar chart of monthly expenses over 5 months",
        "data": [
            {"month": "2025-10", "total": 4520.50, "supplier": "ElevenLabs"},
            {"month": "2025-11", "total": 3890.00, "supplier": "ElevenLabs"},
            {"month": "2025-12", "total": 5210.75, "supplier": "ElevenLabs"},
            {"month": "2026-01", "total": 4100.00, "supplier": "ElevenLabs"},
            {"month": "2026-02", "total": 6340.25, "supplier": "ElevenLabs"},
        ],
        "chart_request": "Bar chart showing monthly expense totals for ElevenLabs over the last 5 months, with months on X axis and EUR amounts on Y axis",
    },
    "expense_categories_pie": {
        "description": "Pie chart of expenses by category",
        "data": [
            {"category": "Software", "total": 12500.00},
            {"category": "Office", "total": 3200.00},
            {"category": "Travel", "total": 5800.00},
            {"category": "Marketing", "total": 8900.00},
            {"category": "Legal", "total": 2100.00},
        ],
        "chart_request": "Pie chart showing expense distribution by category",
    },
    "income_vs_expenses_line": {
        "description": "Line chart comparing income vs expenses over 6 months",
        "data": [
            {"month": "2025-09", "income": 15000, "expenses": 12000},
            {"month": "2025-10", "income": 18000, "expenses": 13500},
            {"month": "2025-11", "income": 16500, "expenses": 14200},
            {"month": "2025-12", "income": 22000, "expenses": 16800},
            {"month": "2026-01", "income": 19000, "expenses": 15000},
            {"month": "2026-02", "income": 21000, "expenses": 17500},
        ],
        "chart_request": "Line chart comparing monthly income vs expenses trend over the last 6 months",
    },
    "top_suppliers_horizontal_bar": {
        "description": "Horizontal bar chart of top 5 suppliers by spend",
        "data": [
            {"supplier": "ElevenLabs", "total": 24061.50},
            {"supplier": "AWS", "total": 18500.00},
            {"supplier": "Google Cloud", "total": 12300.00},
            {"supplier": "Slack", "total": 4800.00},
            {"supplier": "Figma", "total": 3600.00},
        ],
        "chart_request": "Horizontal bar chart showing top 5 suppliers ranked by total spend in EUR",
    },
    "quarterly_revenue_doughnut": {
        "description": "Doughnut chart of revenue by quarter",
        "data": [
            {"quarter": "Q1 2025", "revenue": 45000},
            {"quarter": "Q2 2025", "revenue": 52000},
            {"quarter": "Q3 2025", "revenue": 48000},
            {"quarter": "Q4 2025", "revenue": 61000},
        ],
        "chart_request": "Doughnut chart showing revenue distribution by quarter",
    },
}


def _make_llm_caller(model: str):
    """Create an llm_caller function using the project's model registry.

    Uses AWS Secrets Manager-backed config from the lambda agent module,
    which supports Azure OpenAI, Azure Anthropic, and Vertex AI models.
    """
    from hackathon_backend.services.lambdas.agent.core.config import (
        init_models,
        completion,
        AVAILABLE_MODELS,
    )

    if not AVAILABLE_MODELS:
        print("  Initializing model registry via AWS Secrets Manager...")
        init_models()
        print(f"  Available models: {list(AVAILABLE_MODELS.keys())}")

    if model not in AVAILABLE_MODELS:
        print(f"  Model '{model}' not in registry, falling back to 'claude-sonnet-4.5'")
        model = "claude-sonnet-4.5"

    def caller(messages: list[dict]) -> str:
        response = completion(
            model_id=model,
            messages=messages,
            temperature=0.2,
        )
        return response.choices[0].message.content

    return caller


def _save_png_via_quickchart(config: dict, output_path: str):
    """Render a Chart.js config to PNG using the QuickChart API."""
    try:
        from quickchart import QuickChart
    except ImportError:
        print("  [SKIP PNG] quickchart-io not installed. Run: pip install quickchart-io")
        return False

    qc = QuickChart()
    qc.width = 800
    qc.height = 500
    qc.device_pixel_ratio = 2
    qc.config = config
    qc.to_file(output_path)
    return True


def run_tests(model: str = "claude-sonnet-4.5"):
    """Generate charts for all sample datasets and save outputs."""
    output_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "test_output",
        "charts",
    )
    os.makedirs(output_dir, exist_ok=True)

    llm_caller = _make_llm_caller(model)
    results = []

    for name, dataset in SAMPLE_DATASETS.items():
        print(f"\n{'='*60}")
        print(f"  Generating: {name}")
        print(f"  Description: {dataset['description']}")
        print(f"{'='*60}")

        try:
            html = generate_chart(
                data=dataset["data"],
                chart_request=dataset["chart_request"],
                model=model,
                llm_caller=llm_caller,
            )

            # Save HTML
            html_path = os.path.join(output_dir, f"{name}.html")
            # Wrap in a full HTML page for standalone viewing
            full_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{name}</title></head>
<body style="background:#f5f5f5;font-family:sans-serif;padding:20px">
<h2>{dataset['description']}</h2>
{html}
</body></html>"""
            with open(html_path, "w") as f:
                f.write(full_html)
            print(f"  HTML saved: {html_path}")

            # Extract config and save PNG
            config = extract_chartjs_config(html)
            png_path = os.path.join(output_dir, f"{name}.png")
            if _save_png_via_quickchart(config, png_path):
                print(f"  PNG saved:  {png_path}")

            results.append({"name": name, "status": "ok", "chart_type": config.get("type")})

        except Exception as exc:
            print(f"  ERROR: {exc}")
            results.append({"name": name, "status": "error", "error": str(exc)})

    # Summary
    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    for r in results:
        status = "OK" if r["status"] == "ok" else "FAIL"
        extra = f" (type={r.get('chart_type', '?')})" if status == "OK" else f" ({r.get('error', '')})"
        print(f"  [{status}] {r['name']}{extra}")
    print(f"\nOutput directory: {output_dir}")


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "claude-sonnet-4.5"
    run_tests(model=model)
