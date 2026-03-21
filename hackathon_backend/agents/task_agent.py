"""
TaskAgent — Deep Agent for complex multi-step financial tasks.

Uses Claude Opus as the strategic planning brain, delegating:
- Data retrieval to AWSAgent (Gemini)
- Heavy computation to Gemini code execution
- Sub-analyses to Claude Sonnet
- Charts to chart_tool
- Exports to export_tool
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from typing import Any, Callable

from hackathon_backend.agents.agent import AgentResult, ToolUseAgent
from hackathon_backend.agents.aws_agent import AWSAgent
from hackathon_backend.agents.chart_tool import CHART_TOOL_INSTRUCTIONS, generate_chart
from hackathon_backend.agents.export_tool import generate_export
from hackathon_backend.agents.table_wiki import get_wiki_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

QUERY_DATA_SCHEMA = {
    "name": "query_data",
    "description": (
        "Query financial data from DynamoDB. Describe what data you need in "
        "natural language. A specialized data agent will translate this into "
        "optimized DynamoDB queries and return structured results. "
        "Returns: answer text, structured data, metrics, and source count. "
        "Each call creates a fresh agent — no state carried between calls."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query_description": {
                "type": "string",
                "description": (
                    "Natural language description of what data to fetch. Be specific: "
                    "date ranges, fields needed, filters, groupings. "
                    "Example: 'All expense invoices from Q4 2025 with invoice_date between "
                    "2025-10-01 and 2025-12-31. Fields: total, importe, vatTotalAmount, "
                    "vatDeductibleAmount, vatOperationType, supplier, supplier_cif, "
                    "invoice_date, category, reconciled. For VAT analysis.'"
                ),
            },
            "tables_hint": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Which tables are likely relevant. Options: User_Expenses, "
                    "User_Invoice_Incomes, Bank_Reconciliations, Payroll_Slips, "
                    "Delivery_Notes, Employees, Providers, Customers, Suppliers, "
                    "Companies, Daily_Stats, Monthly_Stats."
                ),
            },
        },
        "required": ["query_description"],
    },
}

COMPUTE_SCHEMA = {
    "name": "compute",
    "description": (
        "Run Python code execution on financial data to compute metrics, "
        "aggregations, cross-references, or forecasts. Provide raw data as JSON "
        "and a detailed computation plan. The computation runs in a sandboxed "
        "Python environment. Use this when you need to combine data from "
        "multiple query_data calls or perform complex calculations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "data_json": {
                "type": "string",
                "description": "JSON string of the data to process (from previous query_data calls).",
            },
            "computation_plan": {
                "type": "string",
                "description": (
                    "Detailed step-by-step computation plan. Be very specific: "
                    "what to calculate, how to group, formulas, expected output. "
                    "Example: '1. Parse expenses and incomes. 2. Group by month (pnl_date). "
                    "3. Revenue = sum(incomes.total), Costs = sum(expenses.total) + "
                    "sum(payroll.gross_amount). 4. Profit = Revenue - Costs. "
                    "5. Return monthly P&L with columns: month, revenue, costs, profit, margin_pct.'"
                ),
            },
            "output_format": {
                "type": "string",
                "description": (
                    "Expected output structure. Example: 'JSON with keys: result (list of "
                    "monthly rows), summary (totals), metrics (total_revenue, total_costs, "
                    "net_margin).'"
                ),
            },
        },
        "required": ["data_json", "computation_plan"],
    },
}

ANALYZE_SUBTASK_SCHEMA = {
    "name": "analyze_subtask",
    "description": (
        "Delegate a focused analysis sub-task to a fast reasoning model. "
        "Use for interpreting patterns, writing risk assessments, or "
        "generating narrative insights from already-computed data. "
        "NOT for data retrieval or computation — use query_data and compute for those."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": "What to analyze. Be specific about the expected output.",
            },
            "context_data": {
                "type": "string",
                "description": "JSON string of relevant data for the analysis.",
            },
            "expected_output": {
                "type": "string",
                "description": "Format and structure of expected output.",
            },
        },
        "required": ["task_description", "context_data"],
    },
}

GENERATE_CHART_SCHEMA = {
    "name": "generate_chart",
    "description": (
        "Generate a Chart.js visualization from structured data. "
        "Use after data has been computed. Supports bar, line, pie, doughnut."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "data_json": {
                "type": "string",
                "description": "JSON array of data items to visualize.",
            },
            "chart_description": {
                "type": "string",
                "description": (
                    "Detailed description of desired chart. Include axis labels, "
                    "data series, groupings. Example: 'Bar chart with months on X axis, "
                    "two series: inflows (green) and outflows (red) in EUR.'"
                ),
            },
            "chart_type": {
                "type": "string",
                "enum": ["bar", "line", "pie", "doughnut"],
                "description": "Chart type.",
            },
        },
        "required": ["data_json", "chart_description"],
    },
}

GENERATE_EXPORT_SCHEMA = {
    "name": "generate_export",
    "description": (
        "Generate a downloadable CSV or Excel file from structured data. "
        "Use for final reports, tables, and data exports. Excel files get "
        "professional styling with Talky branding. Supports multiple sheets."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "data_json": {
                "type": "string",
                "description": (
                    "JSON data to export. Either a list of row dicts (single sheet) "
                    "or a dict of {sheet_name: [rows]} for multi-sheet Excel."
                ),
            },
            "format": {
                "type": "string",
                "enum": ["csv", "xlsx"],
                "description": "Output format. Use xlsx for reports with formatting.",
            },
            "filename": {
                "type": "string",
                "description": "Output filename without extension. Example: 'modelo_303_q1_2026'.",
            },
            "title": {
                "type": "string",
                "description": "Optional title row for Excel sheets.",
            },
        },
        "required": ["data_json", "format", "filename"],
    },
}

ASK_USER_SCHEMA = {
    "name": "ask_user",
    "description": (
        "Ask the user a clarifying question when you need more information "
        "to complete the task. Only use when genuinely stuck or when the task "
        "is ambiguous. Prefer making reasonable assumptions over asking."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user.",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of suggested answers.",
            },
            "context": {
                "type": "string",
                "description": "Brief context about why you're asking.",
            },
        },
        "required": ["question"],
    },
}

GET_CURRENT_DATE_SCHEMA = {
    "name": "get_current_date",
    "description": "Returns today's date and useful derived dates (quarter boundaries, fiscal year, etc.).",
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

# ---------------------------------------------------------------------------
# Phase 2 compute prompt (reused from AWSAgent pattern)
# ---------------------------------------------------------------------------

COMPUTE_PROMPT = """\
Here is financial data to process:

```json
{data_json}
```

Computation plan: {computation_plan}

Expected output format: {output_format}

Compute the requested metrics from the data above and return ONLY a single JSON object \
(inside a ```json code block) with these keys:
- "result": the main computed data (list of rows, table, breakdown, etc.)
- "summary": human-readable summary of findings
- "metrics": numeric key metrics (e.g. total_revenue, total_costs, net_margin, item_count)

IMPORTANT:
- Round monetary amounts to 2 decimal places
- Return ONLY the JSON code block, nothing else
- Use the same language as the computation plan for the summary
"""

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

TASK_AGENT_SYSTEM_PROMPT = """\
You are the Deep Agent (Modo Deep-Agent) for Talky, an AI CFO assistant.
You handle COMPLEX financial tasks that require multi-step data gathering,
cross-referencing multiple data sources, heavy computation, and report generation.

## YOUR ROLE
You are the **strategic brain**. You plan, delegate data retrieval to specialized
agents, validate results, and produce comprehensive financial reports.

## YOUR TOOLS

1. **query_data**: Fetch financial data from DynamoDB. Describe what you need in
   natural language. A specialized data agent handles table selection, GSI optimization,
   and pagination. Returns structured data with items, metrics, and source references.
   Each call is independent — no state carried between calls.

2. **compute**: Run Python computations on data you've gathered. Pass data as JSON
   with a detailed step-by-step computation plan. For cross-referencing datasets,
   aggregations, forecasts, and complex calculations.

3. **analyze_subtask**: Delegate focused analysis to a fast reasoning model.
   Use for pattern interpretation, risk assessment, or narrative generation —
   NOT for data retrieval or computation.

4. **generate_chart**: Create Chart.js visualizations from structured data.
   Use after computation is complete.

5. **generate_export**: Create downloadable CSV or Excel files. Use for
   final reports — Excel files get professional Talky branding.

6. **ask_user**: Ask the user a clarifying question. Only when genuinely needed.

7. **get_current_date**: Get today's date and derived period boundaries.

## YOUR METHODOLOGY

For every complex task, follow this approach:

### Step 1: PLAN
- Call get_current_date first to know the current period
- Identify which data sources are needed
- Determine date ranges and filters
- Plan the computation steps
- Identify expected output format

### Step 2: GATHER DATA
- Call query_data for each data source needed
- Be specific about date ranges and fields
- Request only the fields you need

### Step 3: COMPUTE
- Combine data from multiple query_data calls using compute
- Provide a detailed step-by-step computation plan
- Specify exact calculations and output format

### Step 4: VALIDATE
- Check that metrics make sense (no NaN, no negative totals where unexpected)
- Verify item counts match expectations
- If something looks wrong, re-query or re-compute with corrections

### Step 5: PRESENT
- Generate charts if the data benefits from visualization
- Generate an Excel export with the report data
- Produce your final answer as a structured JSON report

## OUTPUT FORMAT

Your FINAL response (when you stop calling tools) must be a JSON code block:

```json
{{
    "answer": "Human-readable report text in the SAME LANGUAGE as the user's question",
    "report": {{
        "title": "Report title",
        "sections": [
            {{"heading": "...", "content": "...", "data": [...]}}
        ]
    }},
    "data": {{ structured data breakdown }},
    "metrics": {{ key numeric metrics }}
}}
```

## IMPORTANT RULES

1. ALWAYS respond in the **same language** as the user's question.
2. NEVER invent data — only use data returned by query_data.
3. Monetary amounts in EUR, formatted with 2 decimal places.
4. Include source references in your report.
5. If data seems incomplete, acknowledge it and explain what's missing.
6. For forecasts, clearly label projected vs actual figures.
7. Always generate an Excel export for report-type tasks.
8. Generate charts when the data has time dimensions, comparisons, or proportions.
9. **EFFICIENCY**: If a query_data call returns 0 items, do NOT retry with minor variations
   more than once. Instead, try a different date range (e.g., previous quarter/year) or
   ask the user. The test database may not have data for all periods.
10. **LIMIT QUERIES**: Aim for 2-4 query_data calls total. Plan your queries upfront
    and batch related data needs.

## HACKATHON USE CASES (reference patterns)

### Cash Flow Forecast (13 weeks)
- query_data: pending receivables (User_Invoice_Incomes, unreconciled, due_date next 91 days)
- query_data: pending payables (User_Expenses, unreconciled, due_date/charge_date next 91 days)
- query_data: recurring payroll (Payroll_Slips, last 3 months for pattern)
- query_data: bank balance/transactions (Bank_Reconciliations, recent MATCHED)
- compute: weekly cash inflows, outflows, net position, cumulative balance
- Alerts: weeks where projected balance goes negative

### Monthly Reporting Pack (P&L + KPIs)
- query_data: expenses by pnl_date for the month (User_Expenses)
- query_data: incomes by pnl_date for the month (User_Invoice_Incomes)
- query_data: payroll for the month (Payroll_Slips)
- compute: Revenue, COGS, Gross Margin, OpEx, EBITDA, Net Margin
- KPIs: receivables turnover, payables turnover, cash conversion cycle

### Modelo 303 (VAT Draft)
- query_data: expenses with VAT fields (pnl_date in quarter range, vatOperationType, ivas, vatDeductibleAmount)
- query_data: incomes with VAT fields (same quarter, vatTotalAmount)
- compute: Group by vatOperationType (NORMAL, INTRACOMUNITARIA, ISP, EXENTA)
- compute: IVA repercutido - IVA soportado deducible = resultado a liquidar

### Aging Analysis (Receivables + Payables)
- query_data: unreconciled receivables (User_Invoice_Incomes, not reconciled)
- query_data: unreconciled payables (User_Expenses, not reconciled)
- compute: Classify by aging buckets (0-30d, 31-60d, 61-90d, >90d from today)
- Identify top delinquent accounts by amount

### Client Profitability
- query_data: incomes per client (User_Invoice_Incomes)
- query_data: expenses per supplier (User_Expenses)
- Cross-reference: link supplier costs to client projects via subcategory/concept
- compute: Revenue - Direct Costs - Allocated Payroll = Margin per client

## AVAILABLE DATABASE TABLES

{wiki_text}

## SESSION CONTEXT
- userId/locationId: '{user_id}'
- Today: {today}

## DATA TIPS FOR query_data
- The data agent handles ALL DynamoDB specifics (table selection, GSIs, pagination).
  You describe WHAT data you need; it figures out HOW to query it.
- Provider/supplier names are stored in field **'nombre'** (not 'name') in the Providers table.
- The Suppliers table is legacy (empty) — the data agent uses the **Providers** table instead.
- If a period returns 0 items, try the previous quarter or year before giving up.
- When requesting supplier-specific data by name, the data agent will automatically
  look up the supplier's CIF in the Providers table first, then query expenses by CIF.
"""


class TaskAgent(ToolUseAgent):
    """Deep Agent for complex multi-step financial tasks.

    Uses Claude Opus as the strategic brain, delegating data retrieval
    to AWSAgent (Gemini) and computation to Gemini code execution.
    """

    def __init__(
        self,
        user_id: str,
        stage: str = "dev",
        region: str = "eu-west-3",
        model_id: str = "claude-opus-4.6",
        worker_model_id: str = "gemini-2.5-flash",
        subtask_model_id: str = "claude-sonnet-4.5",
        max_tool_calls: int = 30,
        completion_fn: Any | None = None,
        progress_callback: Callable[[str, dict], None] | None = None,
        ask_user_fn: Callable[[str, list[str] | None], str] | None = None,
        export_dir: str | None = None,
    ):
        if completion_fn is None:
            from hackathon_backend.services.lambdas.agent.core.config import completion
            completion_fn = completion

        super().__init__(
            completion_fn=completion_fn,
            model_id=model_id,
            max_tool_calls=max_tool_calls,
            temperature=0.2,
        )
        self.user_id = user_id
        self.stage = stage
        self.region = region
        self._worker_model_id = worker_model_id
        self._subtask_model_id = subtask_model_id
        self._progress_callback = progress_callback
        self._ask_user_fn = ask_user_fn
        self._export_dir = export_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "test_output",
            "task_exports",
        )

        self._accumulated_sources: list[dict] = []
        self._chart_htmls: list[str] = []
        self._export_paths: list[str] = []
        self._query_count: int = 0

        # Register all tools
        self._register_tool(QUERY_DATA_SCHEMA, self._handle_query_data)
        self._register_tool(COMPUTE_SCHEMA, self._handle_compute)
        self._register_tool(ANALYZE_SUBTASK_SCHEMA, self._handle_analyze_subtask)
        self._register_tool(GENERATE_CHART_SCHEMA, self._handle_generate_chart)
        self._register_tool(GENERATE_EXPORT_SCHEMA, self._handle_generate_export)
        self._register_tool(GET_CURRENT_DATE_SCHEMA, self._handle_get_current_date)

        # Only register ask_user if the callback is provided
        if self._ask_user_fn is not None:
            self._register_tool(ASK_USER_SCHEMA, self._handle_ask_user)

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        return TASK_AGENT_SYSTEM_PROMPT.format(
            wiki_text=get_wiki_text(),
            user_id=self.user_id,
            today=date.today().isoformat(),
        )

    # ------------------------------------------------------------------
    # Progress helper
    # ------------------------------------------------------------------

    def _emit_progress(self, event: str, data: dict | None = None) -> None:
        if self._progress_callback:
            try:
                self._progress_callback(event, data or {})
            except Exception as exc:
                logger.warning("Progress callback error: %s", exc)

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    def _handle_query_data(
        self,
        query_description: str,
        tables_hint: list[str] | None = None,
    ) -> dict:
        """Spawn a fresh AWSAgent to query DynamoDB."""
        self._query_count += 1
        self._emit_progress("querying", {
            "description": query_description[:100],
            "step": self._query_count,
            "tables_hint": tables_hint,
        })

        enhanced_query = query_description
        if tables_hint:
            enhanced_query += f"\n\nFocus on these tables: {', '.join(tables_hint)}"

        sub_agent = AWSAgent(
            user_id=self.user_id,
            stage=self.stage,
            region=self.region,
            model_id=self._worker_model_id,
            completion_fn=self._completion_fn,
        )

        try:
            result = sub_agent.run(enhanced_query)
        except Exception as exc:
            logger.error("query_data sub-agent failed: %s", exc)
            return {"success": False, "error": str(exc)}

        if not result.success:
            return {"success": False, "error": result.error or "Query failed"}

        data = result.data or {}
        sources = data.get("sources", [])
        self._accumulated_sources.extend(sources)

        return {
            "success": True,
            "answer": data.get("answer", ""),
            "data": data.get("data"),
            "metrics": data.get("metrics", {}),
            "sources_count": len(sources),
            "tool_calls_used": result.iterations_used,
        }

    def _handle_compute(
        self,
        data_json: str,
        computation_plan: str,
        output_format: str | None = None,
    ) -> dict:
        """Run Gemini code execution on gathered data."""
        self._emit_progress("computing", {"plan": computation_plan[:100]})

        prompt = COMPUTE_PROMPT.format(
            data_json=data_json,
            computation_plan=computation_plan,
            output_format=output_format or "JSON with 'result', 'summary', and 'metrics' keys",
        )

        try:
            response = self._completion_fn(
                model_id=self._worker_model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
        except Exception as exc:
            logger.error("compute failed: %s", exc)
            return {"success": False, "error": str(exc)}

        content = response.choices[0].message.content
        if not content:
            return {"success": False, "error": "Empty response from computation engine"}

        parsed = AWSAgent._extract_json(content)
        if parsed:
            return {"success": True, **parsed}
        return {"success": True, "raw_result": content}

    def _handle_analyze_subtask(
        self,
        task_description: str,
        context_data: str,
        expected_output: str | None = None,
    ) -> dict:
        """Delegate focused analysis to Claude Sonnet."""
        self._emit_progress("analyzing", {"task": task_description[:100]})

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a financial analyst assistant. Analyze the provided data "
                    "and produce the requested output. Be precise, quantitative, and "
                    "respond in the same language as the task description."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Task: {task_description}\n\n"
                    f"Data:\n```json\n{context_data}\n```\n\n"
                    f"Expected output: {expected_output or 'Structured analysis as JSON'}\n\n"
                    "Return your analysis as a JSON code block."
                ),
            },
        ]

        try:
            response = self._completion_fn(
                model_id=self._subtask_model_id,
                messages=messages,
                temperature=0.1,
            )
        except Exception as exc:
            logger.error("analyze_subtask failed: %s", exc)
            return {"success": False, "error": str(exc)}

        content = response.choices[0].message.content
        if not content:
            return {"success": False, "error": "Empty response from analysis model"}

        parsed = AWSAgent._extract_json(content)
        if parsed:
            return {"success": True, **parsed}
        return {"success": True, "analysis": content}

    def _handle_generate_chart(
        self,
        data_json: str,
        chart_description: str,
        chart_type: str | None = None,
    ) -> dict:
        """Generate a Chart.js chart."""
        self._emit_progress("charting", {"description": chart_description[:100]})

        try:
            data = json.loads(data_json) if isinstance(data_json, str) else data_json
        except (json.JSONDecodeError, TypeError) as exc:
            return {"success": False, "error": f"Invalid data JSON: {exc}"}

        if chart_type:
            chart_description = f"{chart_type} chart: {chart_description}"

        def _llm_caller(messages: list[dict]) -> str:
            resp = self._completion_fn(
                model_id=self._worker_model_id,
                messages=messages,
                temperature=0.0,
            )
            return resp.choices[0].message.content or ""

        try:
            html = generate_chart(
                data=data,
                chart_request=chart_description,
                model=self._worker_model_id,
                llm_caller=_llm_caller,
            )
            chart_id = len(self._chart_htmls)
            self._chart_htmls.append(html)
            return {"success": True, "chart_id": chart_id}
        except Exception as exc:
            logger.error("Chart generation failed: %s", exc)
            return {"success": False, "error": str(exc)}

    def _handle_generate_export(
        self,
        data_json: str,
        format: str = "xlsx",
        filename: str = "report",
        title: str = "",
    ) -> dict:
        """Generate a CSV or Excel export."""
        self._emit_progress("exporting", {"filename": filename, "format": format})

        result = generate_export(
            data_json=data_json,
            fmt=format,
            filename=filename,
            output_dir=self._export_dir,
            title=title,
        )

        if result.get("success") and result.get("file_path"):
            self._export_paths.append(result["file_path"])

        return result

    def _handle_ask_user(
        self,
        question: str,
        options: list[str] | None = None,
        context: str | None = None,
    ) -> dict:
        """Ask the user a clarifying question."""
        self._emit_progress("asking_user", {
            "question": question,
            "options": options,
        })

        if self._ask_user_fn is None:
            return {"user_response": "No user interaction available. Make your best judgment."}

        try:
            response = self._ask_user_fn(question, options)
            return {"user_response": response}
        except Exception as exc:
            logger.error("ask_user failed: %s", exc)
            return {"user_response": f"Error getting user response: {exc}. Proceed with best judgment."}

    def _handle_get_current_date(self) -> dict:
        """Return today's date and useful derived dates."""
        today = date.today()
        quarter = (today.month - 1) // 3 + 1
        quarter_start_month = (quarter - 1) * 3 + 1
        quarter_start = date(today.year, quarter_start_month, 1)
        if quarter == 4:
            quarter_end = date(today.year, 12, 31)
        else:
            quarter_end = date(today.year, quarter_start_month + 3, 1) - timedelta(days=1)

        prev_quarter = quarter - 1 if quarter > 1 else 4
        prev_quarter_year = today.year if quarter > 1 else today.year - 1
        prev_q_start_month = (prev_quarter - 1) * 3 + 1
        prev_quarter_start = date(prev_quarter_year, prev_q_start_month, 1)
        if prev_quarter == 4:
            prev_quarter_end = date(prev_quarter_year, 12, 31)
        else:
            prev_quarter_end = date(prev_quarter_year, prev_q_start_month + 3, 1) - timedelta(days=1)

        return {
            "today": today.isoformat(),
            "current_quarter": f"Q{quarter}-{today.year}",
            "quarter_start": quarter_start.isoformat(),
            "quarter_end": quarter_end.isoformat(),
            "previous_quarter": f"Q{prev_quarter}-{prev_quarter_year}",
            "previous_quarter_start": prev_quarter_start.isoformat(),
            "previous_quarter_end": prev_quarter_end.isoformat(),
            "fiscal_year_start": date(today.year, 1, 1).isoformat(),
            "month_start": date(today.year, today.month, 1).isoformat(),
            "days_from_now_91": (today + timedelta(days=91)).isoformat(),
            "days_ago_90": (today - timedelta(days=90)).isoformat(),
        }

    # ------------------------------------------------------------------
    # Main run method
    # ------------------------------------------------------------------

    def run(self, user_request: str, context_messages: list[dict] | None = None) -> AgentResult:
        """Run the full TaskAgent pipeline."""
        logger.info("TaskAgent.run | user=%s | request=%s", self.user_id, user_request[:120])
        self._trace = []
        self._accumulated_sources = []
        self._chart_htmls = []
        self._export_paths = []
        self._query_count = 0

        self._emit_progress("planning", {"step": "Analyzing task requirements..."})

        try:
            final_text = self._run_tool_loop(user_request, extra_messages=context_messages)
        except Exception as exc:
            logger.error("TaskAgent failed: %s", exc)
            return AgentResult(
                success=False,
                error=str(exc),
                trace=self._trace,
            )

        if not final_text:
            return AgentResult(
                success=False,
                error="No response produced",
                trace=self._trace,
            )

        # Parse structured output from Claude's final response
        structured = AWSAgent._extract_json(final_text)

        self._emit_progress("done", {"answer_preview": (final_text or "")[:200]})

        chart_html = "\n".join(self._chart_htmls) if self._chart_htmls else None

        return AgentResult(
            success=True,
            data={
                "answer": structured.get("answer", final_text) if structured else final_text,
                "report": structured.get("report") if structured else None,
                "data": structured.get("data") if structured else None,
                "sources": self._accumulated_sources,
                "metrics": structured.get("metrics", {}) if structured else {},
                "exports": self._export_paths,
            },
            iterations_used=self._tool_call_count,
            trace=self._trace,
            chart_html=chart_html,
        )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def query(self, user_request: str) -> str:
        """Run and return JSON string."""
        result = self.run(user_request)
        return result.to_json()
