"""
Chart generation tool for the AWSAgent.

Receives raw data + a chart request description, calls the LLM to produce
a Chart.js config JSON, and wraps it in self-contained HTML.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Color palette — primary: #f2764b (warm orange), #ffd2d5 (soft pink)
# ---------------------------------------------------------------------------
COLORS = [
    "#f2764b",  # primary orange
    "#ffd2d5",  # primary pink
    "#f4946e",  # light orange
    "#ffb3b8",  # medium pink
    "#d4563a",  # deep orange
    "#ffe5e7",  # pale pink
    "#c43d28",  # dark orange
    "#ff9ea5",  # rose
]

BORDER_COLORS = [
    "#d4563a",  # deep orange
    "#ffb3b8",  # medium pink
    "#c43d28",  # dark orange
    "#ff9ea5",  # rose
    "#a83220",  # darker orange
    "#ffd2d5",  # soft pink
    "#8c2a1a",  # darkest orange
    "#ff8590",  # deep rose
]

# ---------------------------------------------------------------------------
# Instructions for the AWSAgent system prompt
# ---------------------------------------------------------------------------
CHART_TOOL_INSTRUCTIONS = """\
CHART GENERATION TOOL:
When the user's request involves visualization, charts, or graphs — or when the data
you retrieved would be better understood visually — you MUST include a "chart" field
in your strategy response.

The "chart" field should be a JSON object with:
- "description": A clear description of what to chart (e.g. "bar chart showing monthly
  expense totals for the last 5 months, with months on X axis and EUR amounts on Y axis")
- "type" (optional): preferred chart type — "bar", "line", "pie", or "doughnut"
  If omitted, the chart tool will pick the best type automatically.

Include "chart" whenever:
- The user explicitly asks for a graph/chart/visualization
- The data has a time dimension (monthly, daily trends -> line or bar)
- The data compares categories (suppliers, categories -> bar or pie)
- The data shows proportions (-> pie or doughnut)
"""

# ---------------------------------------------------------------------------
# LLM prompt for chart config generation
# ---------------------------------------------------------------------------
CHART_GEN_PROMPT = """\
You are a Chart.js configuration generator. Given raw data and a chart request,
produce a valid Chart.js v4 configuration JSON object.

COLOR PALETTE (use these colors in order):
Background colors: {colors}
Border colors: {border_colors}

RULES:
- Output ONLY a valid JSON object — no markdown fences, no explanation.
- CRITICAL: Output must be pure JSON. Do NOT include JavaScript functions, callbacks,
  or any non-JSON syntax. No `function()`, no arrow functions `=>`. Only use JSON
  primitives (strings, numbers, booleans, null, arrays, objects).
- The JSON must have keys: "type", "data", "options"
- "type" must be one of: "bar", "line", "pie", "doughnut"
- For bar/line charts: use "data.labels" for X axis, "data.datasets[].data" for Y values
- For pie/doughnut: use "data.labels" for segments, one dataset with "data" array
- Always set "options.responsive" to true
- Always set "options.plugins.title.display" to true with a descriptive title
- For monetary values, include currency formatting hint in axis title
- Use the provided color palette — assign colors from the list in order
- For line charts, set "borderColor" and "backgroundColor" with transparency (append "33" for alpha)
- For multi-dataset charts, use different colors from the palette for each dataset
- Keep the config minimal and clean — no unnecessary options

DATA:
{data}

CHART REQUEST:
{chart_request}

Return ONLY the Chart.js config JSON:
"""

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------
HTML_TEMPLATE = """\
<div style="width:100%;max-width:800px;margin:auto;padding:20px">
  <canvas id="chart-{chart_id}"></canvas>
</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script>
new Chart(document.getElementById('chart-{chart_id}'), {config});
</script>
"""


def generate_chart(
    data: list[dict],
    chart_request: str,
    model: str,
    llm_caller: Callable[[list[dict]], str],
) -> str:
    """
    Generate a self-contained HTML string with a Chart.js chart.

    Args:
        data: Raw data items (list of dicts from DynamoDB or similar).
        chart_request: Natural language description of the desired chart.
        model: Not used directly — the llm_caller already has the model bound.
        llm_caller: A callable that takes a list of message dicts and returns
                    the LLM response string. Typically Agent._call_llm.

    Returns:
        Self-contained HTML string with embedded Chart.js chart.
    """
    logger.info("chart_tool.generate_chart | request: %s", chart_request[:100])

    # Truncate data if very large to stay within token limits
    data_str = json.dumps(data, default=str, indent=2)
    if len(data_str) > 15000:
        data_str = data_str[:15000] + "\n... (truncated)"

    prompt = CHART_GEN_PROMPT.format(
        colors=json.dumps(COLORS),
        border_colors=json.dumps(BORDER_COLORS),
        data=data_str,
        chart_request=chart_request,
    )

    messages = [
        {"role": "user", "content": prompt},
    ]

    raw = llm_caller(messages)

    # Strip markdown fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    # Remove JS-style comments, trailing commas, and JS functions that LLMs sometimes produce
    import re
    cleaned = re.sub(r'//.*?$', '', cleaned, flags=re.MULTILINE)
    # Remove JS function expressions (e.g. function(context) { ... })
    cleaned = re.sub(r'function\s*\([^)]*\)\s*\{[^}]*\}', 'null', cleaned)
    # Remove arrow functions (e.g. (context) => { ... } or context => ...)
    cleaned = re.sub(r'\([^)]*\)\s*=>\s*\{[^}]*\}', 'null', cleaned)
    cleaned = re.sub(r'\w+\s*=>\s*[^,}\]]+', 'null', cleaned)
    cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)

    # Validate it's valid JSON
    config = json.loads(cleaned)

    chart_id = uuid.uuid4().hex[:12]
    html = HTML_TEMPLATE.format(
        chart_id=chart_id,
        config=json.dumps(config, indent=2),
    )

    logger.info("chart_tool.generate_chart | generated chart type=%s", config.get("type", "unknown"))
    return html


def extract_chartjs_config(html: str) -> dict:
    """
    Extract the Chart.js config JSON from generated HTML.
    Useful for testing — lets you pass the config to QuickChart for PNG rendering.
    """
    marker = "new Chart(document.getElementById("
    idx = html.find(marker)
    if idx == -1:
        raise ValueError("Could not find Chart constructor in HTML")
    # Find the config start — it's after the closing ), of getElementById
    paren_close = html.find("),", idx)
    config_start = paren_close + 2
    # Find the closing );
    script_end = html.find("</script>", config_start)
    config_str = html[config_start:script_end].strip()
    if config_str.endswith(");"):
        config_str = config_str[:-2]
    return json.loads(config_str)
