"""
Native Code Execution — uses Claude and Gemini's built-in sandboxed code
execution tools for dynamic analysis and artifact generation.

Claude: code_execution_20250825 (Bash + file ops, via Azure AI Foundry)
Gemini: code_execution tool (Python, via Vertex AI)

Both sandboxes include: openpyxl, pandas, numpy, matplotlib, scipy, etc.
This means the LLM can write and run code to generate Excel files, charts,
statistical analysis — anything, dynamically.

Architecture:
- run_code_execution() — main entry point, routes to provider
- _claude_code_exec() — LiteLLM call with code_execution_20250825 tool
- _gemini_code_exec() — google.genai SDK call with code_execution tool
- _extract_b64_files() — extracts base64-encoded files from Claude stdout
- Container reuse for multi-step within a task
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import Any

import litellm
from langfuse import observe

# ---------------------------------------------------------------------------
# Lazy SDK clients (initialized on first use with credentials from config)
# ---------------------------------------------------------------------------
_gemini_client = None


def _get_gemini_client():
    """Get or create Gemini client configured for Vertex AI."""
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client

    from google import genai
    from google.genai import types as genai_types

    # GOOGLE_APPLICATION_CREDENTIALS already set by config.init_models()
    from hackathon_backend.services.lambdas.agent.core.config import get_secret
    sec = get_secret("vertex_ai")

    _gemini_client = genai.Client(
        vertexai=True,
        project=sec["project_id"],
        location="global",
    )
    return _gemini_client


# ---------------------------------------------------------------------------
# Artifacts directory
# ---------------------------------------------------------------------------
ARTIFACTS_DIR = os.path.join(os.environ.get("TEMP", "/tmp"), "cfo_artifacts")


def _ensure_artifacts_dir(task_id: str) -> str:
    path = os.path.join(ARTIFACTS_DIR, task_id)
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
@observe(name="code_execution")
def run_code_execution(
    prompt: str,
    model_id: str = "claude-sonnet-4.5",
    data_context: str = "",
    task_id: str = "",
    container_id: str | None = None,
    max_tokens: int = 16384,
    system_prompt: str | None = None,
) -> dict:
    """
    Run code execution using the model's native sandbox.

    Args:
        prompt: What to do (e.g., "Generate an Excel cash flow forecast with this data: ...")
        model_id: Which model to use (claude-sonnet-4.5, gemini-3.0-flash, etc.)
        data_context: Serialized data to include in the prompt (JSON string of query results)
        task_id: For saving generated files
        container_id: Reuse a Claude container for multi-step work
        max_tokens: Max output tokens
        system_prompt: Optional system prompt

    Returns:
        {
            "success": bool,
            "text": str,           # LLM's text response
            "code_blocks": list,   # Code that was executed
            "files": list[dict],   # Generated files [{filename, path, size_bytes}]
            "container_id": str,   # For container reuse (Claude)
            "usage": dict,         # Token usage
        }
    """
    full_prompt = prompt
    if data_context:
        full_prompt = f"{prompt}\n\nDATA:\n{data_context}"

    if model_id.startswith("claude") or model_id.startswith("azure"):
        result = _claude_code_exec(
            full_prompt, model_id, task_id, container_id, max_tokens, system_prompt,
        )
        # If Azure AI Foundry doesn't support code execution, fall back to Gemini
        if not result.get("success") and "not supported" in result.get("text", "").lower():
            print(f"Claude code execution not available, falling back to Gemini...")
            return _gemini_code_exec(
                full_prompt, "gemini-3.0-flash", task_id, max_tokens, system_prompt,
            )
        return result
    elif model_id.startswith("gemini"):
        return _gemini_code_exec(
            full_prompt, model_id, task_id, max_tokens, system_prompt,
        )
    elif model_id.startswith("gpt"):
        # GPT models don't have native code execution, use Gemini
        return _gemini_code_exec(
            full_prompt, "gemini-3.0-flash", task_id, max_tokens, system_prompt,
        )
    else:
        # Default: try Gemini Flash (cheapest, fastest code execution)
        return _gemini_code_exec(
            full_prompt, "gemini-3.0-flash", task_id, max_tokens, system_prompt,
        )


# ---------------------------------------------------------------------------
# Claude Code Execution (via LiteLLM → Azure AI Foundry)
# ---------------------------------------------------------------------------

# Instruct Claude to output file contents as base64 so we can capture them
_FILE_CAPTURE_INSTRUCTION = """
IMPORTANT: After saving any file, you MUST print its base64 encoding so the file
can be captured. Use this exact pattern for EACH file:
```
python3 -c "import base64; data=open('<filepath>','rb').read(); print('FILE_B64_START:<filename>'); print(base64.b64encode(data).decode()); print('FILE_B64_END')"
```
Replace <filepath> with the full path and <filename> with just the filename.
"""


def _claude_code_exec(
    prompt: str,
    model_id: str,
    task_id: str,
    container_id: str | None,
    max_tokens: int,
    system_prompt: str | None,
) -> dict:
    """Execute code using Claude's native code_execution_20250825 tool via LiteLLM."""
    from hackathon_backend.services.lambdas.agent.core.config import AVAILABLE_MODELS, init_models

    # Ensure models are registered
    if not AVAILABLE_MODELS:
        init_models()

    # Resolve model config — use claude-sonnet-4.5 by default
    cfg_key = "claude-sonnet-4.5"
    if model_id in AVAILABLE_MODELS:
        cfg_key = model_id
    elif "opus" in model_id:
        cfg_key = "claude-opus-4.6" if "claude-opus-4.6" in AVAILABLE_MODELS else "claude-sonnet-4.5"

    cfg = AVAILABLE_MODELS[cfg_key]

    # Build messages — inject file capture instruction into prompt
    full_prompt = prompt + "\n" + _FILE_CAPTURE_INSTRUCTION
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": full_prompt})

    litellm_kwargs: dict[str, Any] = {
        "model": cfg["model"],
        "api_key": cfg.get("api_key"),
        "api_base": cfg.get("api_base"),
        "messages": messages,
        "tools": [{"type": "code_execution_20250825", "name": "code_execution"}],
        "max_tokens": max_tokens,
    }

    # Container reuse
    if container_id:
        litellm_kwargs["container"] = container_id

    try:
        response = litellm.completion(**litellm_kwargs)
    except Exception as e:
        return {
            "success": False,
            "text": f"Claude code execution error: {e}",
            "code_blocks": [],
            "files": [],
            "container_id": container_id,
            "usage": {},
        }

    # Parse LiteLLM response (OpenAI-compatible format)
    msg = response.choices[0].message
    text = msg.content or ""
    code_blocks = []
    files = []
    new_container_id = container_id

    # Extract provider-specific fields
    psf = msg.provider_specific_fields or {}

    # Container ID for reuse
    container_info = psf.get("container", {})
    if container_info and container_info.get("id"):
        new_container_id = container_info["id"]

    # Collect code blocks and stdout from tool results
    all_stdout = ""
    for tr in psf.get("tool_results", []):
        tr_type = tr.get("type", "")
        content = tr.get("content", {})
        stdout = content.get("stdout", "") or ""
        all_stdout += stdout

        if "bash" in tr_type:
            code_blocks.append({"type": "bash", "stdout": stdout})
        elif "text_editor" in tr_type:
            code_blocks.append({"type": "file_op"})

    # Extract tool call code
    for tc in (msg.tool_calls or []):
        fn = tc.function
        if fn.name == "bash_code_execution":
            args = json.loads(fn.arguments) if fn.arguments else {}
            code_blocks.append({"type": "bash", "code": args.get("command", "")})
        elif fn.name == "text_editor_code_execution":
            args = json.loads(fn.arguments) if fn.arguments else {}
            code_blocks.append({
                "type": "file_op",
                "command": args.get("command", ""),
                "path": args.get("path", ""),
            })

    # Extract base64-encoded files from stdout
    if task_id and "FILE_B64_START:" in all_stdout:
        files = _extract_b64_files(all_stdout, task_id)

    # Usage
    usage = {}
    if response.usage:
        u = response.usage
        usage = {
            "model": model_id,
            "step": "code_execution",
            "prompt_tokens": u.prompt_tokens or 0,
            "completion_tokens": u.completion_tokens or 0,
            "total_tokens": u.total_tokens or 0,
        }

    return {
        "success": True,
        "text": text,
        "code_blocks": code_blocks,
        "files": files,
        "container_id": new_container_id,
        "usage": usage,
    }


def _extract_b64_files(stdout: str, task_id: str) -> list[dict]:
    """Extract base64-encoded files from Claude's stdout output."""
    files = []
    pattern = r"FILE_B64_START:(.+?)\n(.+?)\nFILE_B64_END"
    for match in re.finditer(pattern, stdout, re.DOTALL):
        filename = match.group(1).strip()
        b64_data = match.group(2).strip()
        try:
            data = base64.b64decode(b64_data)
            dir_path = _ensure_artifacts_dir(task_id)
            filepath = os.path.join(dir_path, filename)
            with open(filepath, "wb") as f:
                f.write(data)
            files.append({
                "filename": filename,
                "path": filepath,
                "size_bytes": len(data),
                "type": _detect_file_type(filename),
            })
        except Exception as e:
            print(f"Warning: Could not decode file {filename}: {e}")
    return files


# ---------------------------------------------------------------------------
# Gemini Code Execution (via google.genai SDK → Vertex AI)
# ---------------------------------------------------------------------------
def _gemini_code_exec(
    prompt: str,
    model_id: str,
    task_id: str,
    max_tokens: int,
    system_prompt: str | None,
) -> dict:
    """Execute code using Gemini's native code execution tool."""
    client = _get_gemini_client()
    from google.genai import types as genai_types

    # Map our model IDs to Gemini model names
    model_map = {
        "gemini-3.0-flash": "gemini-3-flash-preview",
        "gemini-3.1-pro": "gemini-3.1-pro-preview",
    }
    gemini_model = model_map.get(model_id, "gemini-3-flash-preview")

    config = genai_types.GenerateContentConfig(
        tools=[genai_types.Tool(code_execution=genai_types.ToolCodeExecution)],
        max_output_tokens=max_tokens,
        temperature=0.1,
    )
    if system_prompt:
        config.system_instruction = system_prompt

    try:
        response = client.models.generate_content(
            model=gemini_model,
            contents=prompt,
            config=config,
        )
    except Exception as e:
        # Try fallback location
        try:
            client2 = _create_gemini_client_location("us-central1")
            response = client2.models.generate_content(
                model=gemini_model,
                contents=prompt,
                config=config,
            )
        except Exception as e2:
            return {
                "success": False,
                "text": f"Gemini code execution error: {e2}",
                "code_blocks": [],
                "files": [],
                "container_id": None,
                "usage": {},
            }

    # Parse response parts
    text_parts = []
    code_blocks = []
    files = []

    if response.candidates and response.candidates[0].content:
        for part in response.candidates[0].content.parts:
            if hasattr(part, "text") and part.text:
                text_parts.append(part.text)
            if hasattr(part, "executable_code") and part.executable_code:
                code_blocks.append({
                    "type": "python",
                    "code": part.executable_code.code,
                })
            if hasattr(part, "code_execution_result") and part.code_execution_result:
                output = part.code_execution_result.output
                if output:
                    text_parts.append(f"[Code output: {output[:500]}]")
            # Inline image/file data from Gemini
            if hasattr(part, "inline_data") and part.inline_data:
                saved = _save_gemini_inline_data(part.inline_data, task_id)
                if saved:
                    files.append(saved)

    # Extract token usage
    usage = {}
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        um = response.usage_metadata
        prompt_tokens = getattr(um, "prompt_token_count", 0) or 0
        completion_tokens = getattr(um, "candidates_token_count", 0) or 0
        usage = {
            "model": model_id,
            "step": "code_execution",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    # Gemini code execution runs in its sandbox and files stay there.
    # Re-execute the generated code locally to capture the files.
    if code_blocks and not files and task_id:
        local_files = _execute_code_locally(code_blocks, task_id)
        files.extend(local_files)

    return {
        "success": True,
        "text": "\n".join(text_parts),
        "code_blocks": code_blocks,
        "files": files,
        "container_id": None,  # Gemini doesn't have container reuse
        "usage": usage,
    }


def _create_gemini_client_location(location: str):
    """Create a Gemini client for a specific Vertex AI location."""
    from google import genai
    from hackathon_backend.services.lambdas.agent.core.config import get_secret
    sec = get_secret("vertex_ai")
    return genai.Client(
        vertexai=True,
        project=sec["project_id"],
        location=location,
    )


def _save_gemini_inline_data(inline_data, task_id: str) -> dict | None:
    """Save inline data from Gemini response (images, generated files)."""
    try:
        mime_type = inline_data.mime_type or "application/octet-stream"
        data = inline_data.data

        # Determine filename based on mime type
        ext_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "application/pdf": ".pdf",
            "text/csv": ".csv",
        }
        ext = ext_map.get(mime_type, ".bin")
        filename = f"gemini_output_{int(time.time())}{ext}"

        dir_path = _ensure_artifacts_dir(task_id)
        filepath = os.path.join(dir_path, filename)

        if isinstance(data, str):
            data = base64.b64decode(data)
        with open(filepath, "wb") as f:
            f.write(data)

        return {
            "filename": filename,
            "path": filepath,
            "size_bytes": os.path.getsize(filepath),
            "type": _detect_file_type(filename),
        }
    except Exception as e:
        print(f"Warning: Could not save Gemini inline data: {e}")
        return None


# ---------------------------------------------------------------------------
# Local code execution — re-runs Gemini/Claude generated code locally
# to capture generated files (since Gemini sandbox files aren't downloadable)
# ---------------------------------------------------------------------------
def _execute_code_locally(code_blocks: list[dict], task_id: str) -> list[dict]:
    """
    Re-execute Python code blocks locally to capture generated files.

    Rewrites /tmp/ paths to our artifacts directory so files are saved
    in the right place for download.
    """
    dir_path = _ensure_artifacts_dir(task_id)
    files = []

    for block in code_blocks:
        if block.get("type") != "python":
            continue

        code = block.get("code", "")
        if not code:
            continue

        # Rewrite /tmp/ paths to our artifacts dir
        code = code.replace("/tmp/", dir_path.replace("\\", "/") + "/")

        # Create a safe execution environment with common libraries
        try:
            exec_globals = {"__builtins__": __builtins__}
            exec(code, exec_globals)
        except Exception as e:
            print(f"Warning: Local code execution failed: {e}")
            continue

    # Collect any files that were created in the artifacts dir
    if os.path.isdir(dir_path):
        for f in os.listdir(dir_path):
            fp = os.path.join(dir_path, f)
            if os.path.isfile(fp):
                files.append({
                    "filename": f,
                    "path": fp,
                    "size_bytes": os.path.getsize(fp),
                    "type": _detect_file_type(f),
                })

    return files


# ---------------------------------------------------------------------------
# Multi-step code execution (for complex tasks with iterative steps)
# ---------------------------------------------------------------------------
@observe(name="multi_step_code_execution")
def run_multi_step(
    steps: list[dict],
    model_id: str = "claude-sonnet-4.5",
    task_id: str = "",
    system_prompt: str | None = None,
) -> dict:
    """
    Run multiple code execution steps with container reuse (Claude) or
    accumulated context (Gemini).

    Each step: {"prompt": str, "data": str}

    Returns aggregated results with all generated files.
    """
    all_files = []
    all_text = []
    all_code = []
    all_usage = []
    container_id = None

    for i, step in enumerate(steps):
        result = run_code_execution(
            prompt=step["prompt"],
            model_id=model_id,
            data_context=step.get("data", ""),
            task_id=task_id,
            container_id=container_id,
            system_prompt=system_prompt,
        )

        all_text.append(result.get("text", ""))
        all_code.extend(result.get("code_blocks", []))
        all_files.extend(result.get("files", []))
        if result.get("usage"):
            all_usage.append(result["usage"])

        # Reuse container for next step
        if result.get("container_id"):
            container_id = result["container_id"]

        if not result.get("success"):
            break

    return {
        "success": True,
        "text": "\n---\n".join(all_text),
        "code_blocks": all_code,
        "files": all_files,
        "container_id": container_id,
        "usage": all_usage,
    }


# ---------------------------------------------------------------------------
# Prompt builders for financial reports
# ---------------------------------------------------------------------------
CODE_EXEC_SYSTEM = """\
You are a financial data analyst with code execution capabilities.
You write and execute Python code to analyze financial data and generate
professional Excel reports using openpyxl.

IMPORTANT RULES:
1. Always use openpyxl for Excel generation (pre-installed in sandbox)
2. Save files to /tmp/ directory
3. Use professional formatting: headers with blue fill, borders, currency format
4. Include charts (using openpyxl.chart) where appropriate
5. Use Spanish labels for financial reports (Ingresos, Gastos, IVA, etc.)
6. Currency format: #,##0.00 € (EUR)
7. Return structured JSON results alongside Excel files
8. Handle edge cases (empty data, missing fields) gracefully
"""


def build_excel_prompt(task_type: str, synthesis_data: dict, description: str = "") -> str:
    """Build a prompt for the LLM to generate an Excel report via code execution."""
    data_json = json.dumps(synthesis_data, ensure_ascii=False, default=str)

    prompts = {
        "cash_flow_forecast": f"""Generate a professional 13-week Cash Flow Forecast Excel file.

Save it as /tmp/cash_flow_forecast_13w.xlsx

The Excel must have:
1. Main sheet "Cash Flow Forecast" with:
   - Row headers: Saldo Inicial, Cobros (Entradas), Pagos (Salidas), Flujo Neto, Saldo Acumulado
   - Column headers: Week dates
   - Currency formatting (EUR), negative numbers in red
   - Bold headers with blue background (hex #2B579A, white text)
   - Borders on all cells
   - Green background for total rows

2. A Line Chart showing Cobros, Pagos, and Saldo Acumulado over weeks

3. If detailed data is available, create additional sheets for:
   - "Detalle Cobros" (receivables breakdown)
   - "Detalle Pagos" (payables breakdown)

DATA:
{data_json}
""",
        "modelo_303": f"""Generate a professional Modelo 303 (IVA trimestral) draft Excel file.

Save it as /tmp/modelo_303_borrador.xlsx

The Excel must have:
1. Title: "BORRADOR MODELO 303 — [period]"
2. Section "IVA REPERCUTIDO (Ventas)" with columns: Tipo IVA, Base Imponible, Cuota, Num. Facturas
3. Section "IVA SOPORTADO (Compras)" with same columns
4. RESULTADO = Total Cuota Repercutido - Total Cuota Soportado
5. If resultado > 0: "A INGRESAR", else: "A COMPENSAR/DEVOLVER"
6. Professional formatting with borders, currency format, colored totals

DATA:
{data_json}
""",
        "aging_analysis": f"""Generate a professional Aging Analysis Excel file.

Save it as /tmp/aging_analysis.xlsx

The Excel must have:
1. Sheet "Cobros Pendientes" (Accounts Receivable):
   - Columns: Cliente, CIF, Factura, Fecha, Vencimiento, Importe, 0-30d, 31-60d, 61-90d, >90d
   - Each amount in the correct aging bucket
   - Totals row at bottom
   - Bar chart showing aging distribution

2. Sheet "Pagos Pendientes" (Accounts Payable): same structure

3. Sheet "Resumen" with summary KPIs:
   - Total AR, Total AP, Net position
   - Average days overdue
   - Top 5 debtors

DATA:
{data_json}
""",
        "pack_reporting": f"""Generate a comprehensive Monthly Reporting Pack Excel file.

Save it as /tmp/pack_reporting.xlsx

The Excel must have:
1. "P&L" sheet: Profit & Loss with categories, subtotals, EBITDA
2. "KPIs" sheet: Key metrics (revenue, margin, growth, cash position)
3. "Ingresos" sheet: Revenue breakdown by client
4. "Gastos" sheet: Expense breakdown by category
5. "Personal" sheet: Personnel costs breakdown
6. Charts on each sheet where relevant
7. Professional styling throughout

DATA:
{data_json}
""",
        "client_profitability": f"""Generate a Client Profitability Analysis Excel file.

Save it as /tmp/client_profitability.xlsx

The Excel must have:
1. "Ranking Clientes" sheet: ranked by revenue with margin %
2. "Detalle Ingresos" sheet: per-client revenue breakdown
3. "Análisis Margen" sheet: margin analysis with cost allocation
4. Pie chart of revenue distribution
5. Bar chart comparing margins

DATA:
{data_json}
""",
    }

    base_prompt = prompts.get(task_type)
    if not base_prompt:
        base_prompt = f"""Generate a professional Excel report for this financial analysis task.

Task type: {task_type}
Description: {description}

Save it as /tmp/{task_type}_report.xlsx

Create appropriate sheets with the data, including formatting, totals, and charts.

DATA:
{data_json}
"""

    return base_prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _detect_file_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    type_map = {
        ".xlsx": "excel",
        ".xls": "excel",
        ".csv": "csv",
        ".pdf": "pdf",
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
    }
    return type_map.get(ext, "other")


def collect_sandbox_files(task_id: str) -> list[dict]:
    """List all files in the artifacts directory for a task."""
    dir_path = os.path.join(ARTIFACTS_DIR, task_id)
    if not os.path.isdir(dir_path):
        return []
    artifacts = []
    for f in os.listdir(dir_path):
        fp = os.path.join(dir_path, f)
        if os.path.isfile(fp):
            artifacts.append({
                "filename": f,
                "path": fp,
                "size_bytes": os.path.getsize(fp),
                "type": _detect_file_type(f),
            })
    return artifacts
