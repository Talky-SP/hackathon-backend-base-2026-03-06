# AI CFO Agent — Frontend Integration Guide

## Quick Start

```bash
# Start the server
python -m hackathon_backend.services.lambdas.agent.server --port 8000
```

Server endpoints:
- **WebSocket Chat**: `ws://localhost:8000/ws/chat` — real-time chat with streaming events
- **WebSocket Logs**: `ws://localhost:8000/ws/logs` — dev log panel (real-time)
- **REST Chat**: `POST /api/chat` — single question (no streaming)
- **Chat CRUD**: `GET/POST/DELETE /api/chats`, `GET /api/chats/{id}/messages`
- **Chat Context**: `GET /api/chats/{id}/context` — current LLM context window
- **Chat Costs**: `GET /api/chats/{id}/costs` — cost breakdown per chat
- **Chat Traces**: `GET /api/chats/{id}/traces` — all LLM call traces for a chat
- **User Costs**: `GET /api/costs?location_id=X` — cost summary per user
- **Model Pricing**: `GET /api/costs/models` — pricing table
- **Traces**: `GET /api/traces?location_id=X`, `GET /api/traces/{id}`
- **Cancel**: `POST /api/chats/{id}/cancel`, `POST /api/tasks/{id}/cancel`
- **Tasks**: `POST /api/tasks`, `GET /api/tasks`, `GET /api/tasks/{id}`
- **Task Traces**: `GET /api/tasks/{id}/traces`
- **Task Artifacts**: `GET /api/tasks/{id}/artifacts/{filename}` — download Excel/PDF
- **Task Types**: `GET /api/tasks/types`
- **Code Execution**: `POST /api/code-exec` — AI sandbox for Excel/chart generation
- **Dev Logs**: `GET /api/logs`, `ws://localhost:8000/ws/logs`
- **Models**: `GET /api/models`
- **Health**: `GET /api/health`
- **Swagger**: `http://localhost:8000/docs`

---

## Architecture Overview

The backend uses a **unified agent** — a single AI agent (Claude Sonnet) handles ALL queries. There is no classifier or orchestrator step. The agent decides on its own whether to:

1. **Answer directly** — general knowledge questions (no tools called)
2. **Query data + analyze** — fetches from DynamoDB, runs Python analysis, returns with chart
3. **Generate files** — creates Excel/PDF reports via AI code execution sandbox

For heavy tasks (cash flow forecast, Modelo 303, etc.), the system detects them by keyword and runs the agent as a **background task** with progress tracking and cost budgets.

```
User message → detect_heavy_task()
  ├── null → Run agent inline (WebSocket streaming)
  │          Agent tools: dynamo_query, run_analysis, generate_file
  │          Response: answer + chart + sources + artifacts
  │
  └── "cash_flow_forecast" → Create background task
                              Run same agent with task-specific guidance
                              Stream progress events via WebSocket
                              Response: answer + artifacts (Excel)
```

---

## 1. WebSocket API (Recommended)

### Connect
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/chat');
```

### Send a message
```javascript
ws.send(JSON.stringify({
    type: "message",                    // Required
    question: "Cuanto me he gastado en total?",  // Required
    location_id: "deloitte-84",         // Required: tenant ID
    chat_id: "uuid-of-chat",            // Optional: null = new chat
    model: "claude-sonnet-4.5",         // Optional: AI model
    request_id: "abc123"                // Optional: for tracking
}));
```

### Cancel an in-progress operation
```javascript
// Via WebSocket (preferred — instant)
ws.send(JSON.stringify({
    type: "cancel",
    chat_id: "uuid-of-chat"
}));

// Via REST (alternative)
await fetch(`/api/chats/${chatId}/cancel`, { method: 'POST' });
await fetch(`/api/tasks/${taskId}/cancel`, { method: 'POST' });
```

### Receive events

The server sends multiple messages during processing. Here's every event type you'll receive:

#### 1. `chat_id` — sent immediately after receiving a message
```json
{
    "type": "chat_id",
    "chat_id": "45b57715-a6d8-472b-854d-8e155ee29fd6",
    "request_id": "abc123"
}
```
Store this and send it back in subsequent messages to continue the conversation.

#### 2. `event` — progress feedback during agent processing
```json
{
    "type": "event",
    "event": "querying",
    "request_id": "abc123",
    "message": "Consultando User Expenses...",
    "table": "User_Expenses",
    "query_key": "query_1"
}
```

| Event | When | What to show |
|-------|------|-------------|
| `step` | Agent starts processing | Status: "Procesando..." |
| `thinking` | Agent planning next action | Spinner: "Pensando..." |
| `querying` | DynamoDB query executing | "Consultando [tabla]..." |
| `query_result` | Query returned data | "Encontrados N registros" |
| `analyzing` | Running Python analysis | "Ejecutando análisis..." |
| `generating` | Creating Excel/PDF file | "Generando archivo..." |
| `agent_done` | Agent finished | Hide spinner |

#### 3. `response` — final answer for inline queries
```json
{
    "type": "response",
    "request_id": "abc123",
    "chat_id": "45b57715-...",
    "message_id": 42,
    "answer": "Los 5 proveedores con mayor importe son...",
    "chart": { "type": "bar", "title": "Top 5 Proveedores", "labels": [...], "datasets": [...] },
    "sources": [ { "categoryDate": "COMPRAS#2025-01-15#uuid", "supplier": "Bio-Rad", "total": 695.30, ... } ],
    "artifacts": [ { "filename": "report.xlsx", "url": "/api/tasks/chat_abc/artifacts/report.xlsx" } ],
    "model_used": "claude-sonnet-4.5"
}
```

**This is the main response you render.** It contains:
- `answer` — markdown text to display
- `chart` — chart data for Chart.js (or null)
- `sources` — referenced invoices/documents (or [])
- `artifacts` — downloadable files like Excel (or [])

#### 4. `task_created` — heavy task started in background
```json
{
    "type": "task_created",
    "request_id": "abc123",
    "chat_id": "45b57715-...",
    "task_id": "e81b91e5-...",
    "task_type": "cash_flow_forecast",
    "task_type_name": "Previsión de Tesorería (13 semanas)"
}
```

#### 5. `task_progress` — background task progress
```json
{
    "type": "task_progress",
    "request_id": "abc123",
    "task_id": "e81b91e5-...",
    "progress": 45,
    "step": "Consultando Bank_Reconciliations..."
}
```

#### 6. `task_completed` — background task finished
```json
{
    "type": "task_completed",
    "request_id": "abc123",
    "task_id": "e81b91e5-...",
    "summary": "Previsión de tesorería generada...",
    "artifacts": [
        { "filename": "Prevision_Tesoreria_13_Semanas.xlsx", "url": "/api/tasks/e81b91e5-.../artifacts/..." }
    ],
    "cost_usd": 0.40
}
```

#### 7. `task_failed` / `task_cancelled` / `cancelled`
```json
{ "type": "task_failed", "task_id": "...", "error": "Budget exceeded" }
{ "type": "task_cancelled", "task_id": "..." }
{ "type": "cancelled", "id": "..." }
```

### Complete WebSocket handler
```javascript
ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    switch (msg.type) {
        // Store chat ID for follow-up messages
        case 'chat_id':
            currentChatId = msg.chat_id;
            break;

        // Progress events (show status indicators)
        case 'event':
            if (['querying', 'analyzing', 'generating', 'thinking'].includes(msg.event)) {
                showSpinner(msg.message);
            }
            if (msg.event === 'query_result') {
                showToast(msg.message);  // "Encontrados 230 registros"
            }
            if (msg.event === 'agent_done') {
                hideSpinner();
            }
            break;

        // Final response (inline queries)
        case 'response':
            hideSpinner();
            renderAnswer(msg.answer);          // Markdown text
            renderChart(msg.chart);             // Chart.js chart (if any)
            renderSources(msg.sources);         // Invoice references (if any)
            renderArtifacts(msg.artifacts);     // Download buttons (if any)
            break;

        // Background task lifecycle
        case 'task_created':
            showTaskCard(msg.task_id, msg.task_type_name);
            showCancelButton(msg.task_id);
            break;
        case 'task_progress':
            updateProgressBar(msg.task_id, msg.progress, msg.step);
            break;
        case 'task_completed':
            hideProgressBar(msg.task_id);
            renderAnswer(msg.summary);
            renderArtifacts(msg.artifacts);     // Download Excel
            showCostBadge(msg.cost_usd);
            break;
        case 'task_failed':
            showError(msg.error);
            break;

        // Final result for background tasks (sent after task_completed)
        case 'result':
            const d = msg.data;
            renderAnswer(d.answer);
            renderChart(d.chart);
            renderSources(d.sources);
            renderArtifacts(d.artifacts);
            break;

        // Cancellation confirmation
        case 'cancelled':
        case 'task_cancelled':
            hideSpinner();
            showStatus("Cancelado");
            break;
    }
};
```

---

## 2. REST API (Simple, no streaming)

```javascript
const response = await fetch('http://localhost:8000/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        question: "Cuantos proveedores tengo?",
        location_id: "deloitte-84",
        model: "claude-sonnet-4.5"
    })
});
const data = await response.json();
// data = { type, answer, chart, sources, artifacts, model_used, chat_id, ... }
```

> **Note:** For heavy tasks (cash flow, modelo 303, etc.), the REST API returns immediately with `type: "complex_task"`. The task runs in background — use `GET /api/tasks/{id}` to poll for results, or use WebSocket for real-time updates.

---

## 3. Response Fields Reference

Every response (WebSocket `response` or REST) contains these fields:

| Field | Type | Description |
|-------|------|-------------|
| `answer` | `string` | Markdown text response. Always present. |
| `chart` | `object \| null` | Chart data for Chart.js. See Section 4. |
| `sources` | `array` | Referenced invoices/bank transactions. See Section 5. |
| `artifacts` | `array` | Downloadable files (Excel, PDF). See Section 6. |
| `model_used` | `string` | AI model that generated the response. |

---

## 4. Charts

When the AI decides a visual would help, `chart` contains data compatible with [Chart.js](https://www.chartjs.org/):

```typescript
interface Chart {
    type: "bar" | "line" | "pie" | "table";
    title: string;
    labels: string[];
    datasets: Array<{
        label: string;
        data: number[] | any[][];  // numbers for charts, arrays for tables
    }>;
}
```

### Rendering with Chart.js

```javascript
import { Chart } from 'chart.js/auto';

function renderChart(chartData, canvasElement) {
    if (!chartData) return;

    // Special handling for table type
    if (chartData.type === 'table') {
        renderDataTable(chartData);
        return;
    }

    new Chart(canvasElement, {
        type: chartData.type,
        data: {
            labels: chartData.labels,
            datasets: chartData.datasets.map((ds, i) => ({
                label: ds.label,
                data: ds.data,
                backgroundColor: COLORS[i % COLORS.length],
                borderColor: BORDER_COLORS[i % BORDER_COLORS.length],
                borderWidth: 1,
            })),
        },
        options: {
            responsive: true,
            plugins: {
                title: { display: true, text: chartData.title },
                legend: { display: chartData.datasets.length > 1 },
            },
        },
    });
}

const COLORS = [
    'rgba(59, 130, 246, 0.5)',   // blue
    'rgba(16, 185, 129, 0.5)',   // green
    'rgba(245, 158, 11, 0.5)',   // yellow
    'rgba(239, 68, 68, 0.5)',    // red
    'rgba(139, 92, 246, 0.5)',   // purple
];
```

### Table chart type

When `chart.type === "table"`, render as HTML:
```json
{
    "type": "table",
    "title": "Facturas de Bio-Rad",
    "labels": ["Numero", "Fecha", "Concepto", "Total", "Estado"],
    "datasets": [{
        "label": "Facturas",
        "data": [
            ["FAC-001", "2025-01-15", "Material Lab", 695.30, "Pagada"],
            ["FAC-002", "2025-03-20", "Reactivos", 1200.00, "Pendiente"]
        ]
    }]
}
```

### When charts appear

| Query type | Chart type | Example |
|-----------|------------|---------|
| Gastos por categoría | `bar` or `pie` | "Dame un gráfico de gastos por categoría" |
| Top proveedores | `bar` | "Top 5 proveedores por gasto" |
| Evolución temporal | `line` | "Evolución de gastos mensuales" |
| Detalle facturas | `table` | "Facturas de Bio-Rad" |
| Comparativa | `bar` (multi-dataset) | "Ingresos vs gastos por mes" |

---

## 5. Sources (Document References)

Sources are invoices, bank transactions, or other documents the AI used to answer. Use them to let users click through to the original document.

```typescript
interface Source {
    // Document ID — use this to link to the invoice in your app
    categoryDate: string;  // "COMPRAS#2024-08-29#uuid" (invoices) or "MTXN#..." (bank)

    // Who
    supplier: string;       // Supplier/client/merchant name
    supplier_cif?: string;  // Tax ID (CIF/NIF) — invoices only

    // When
    invoice_date: string;   // "YYYY-MM-DD" — issue date or booking date

    // How much
    total: number;          // Amount in EUR (negative for bank outflows)
    importe?: number;       // Tax base — invoices only

    // Status
    reconciled: boolean;    // true = paid/matched, false = pending

    // Classification
    category: string;       // "COMPRAS", "SERVICIOS", "BANK", etc.
    concept?: string;       // Sub-category — invoices only

    // Visual reference (for highlighting on PDF)
    total_bounding_box?: { Height: number; Left: number; Top: number; Width: number };
}
```

### Two types of sources

1. **Invoice sources** (from `User_Expenses` / `User_Invoice_Incomes`):
   - `categoryDate` = `"COMPRAS#2024-08-29#uuid"` — this is the document PK
   - Has `supplier_cif`, `importe`, `concept`, `due_date`

2. **Bank transaction sources** (from `Bank_Reconciliations`):
   - `categoryDate` = `"MTXN#2025-01-15#txn_id"` — the bank transaction SK
   - `category` = `"BANK"`
   - `total` can be negative (outflow) or positive (inflow)

### Rendering sources
```javascript
function renderSources(sources) {
    if (!sources?.length) return;

    return sources.slice(0, 10).map(s => `
        <div class="source-card" onclick="openDocument('${s.categoryDate}')">
            <span class="source-name">${s.supplier}</span>
            <span class="source-amount ${s.total < 0 ? 'negative' : ''}">${formatEUR(s.total)}</span>
            <span class="source-date">${s.invoice_date}</span>
            <span class="source-status ${s.reconciled ? 'paid' : 'unpaid'}">
                ${s.reconciled ? 'Pagada' : 'Pendiente'}
            </span>
        </div>
    `).join('');
}

function formatEUR(amount) {
    return new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' }).format(amount);
}

function openDocument(categoryDate) {
    if (categoryDate.startsWith('MTXN#')) {
        router.push(`/bank/${encodeURIComponent(categoryDate)}`);
    } else {
        router.push(`/invoices/${encodeURIComponent(categoryDate)}`);
    }
}
```

---

## 6. Artifacts (Downloadable Files)

Artifacts are files generated by the AI — Excel reports, CSV exports, chart images, etc.

```typescript
interface Artifact {
    filename: string;  // "Prevision_Tesoreria_13_Semanas.xlsx"
    url: string;       // "/api/tasks/{task_id}/artifacts/{filename}"
}
```

### When artifacts appear

- **Inline responses**: When user asks "Exporta mis gastos a Excel" → agent calls `generate_file` → artifact in `response.artifacts`
- **Background tasks**: Cash flow forecast, Modelo 303, etc. → artifact in `task_completed.artifacts` and `result.data.artifacts`

### Downloading artifacts
```javascript
function renderArtifacts(artifacts) {
    if (!artifacts?.length) return;

    return artifacts.map(a => {
        const icon = a.filename.endsWith('.xlsx') ? '📊' :
                     a.filename.endsWith('.pdf') ? '📄' : '📁';
        return `
            <a href="${a.url}" download="${a.filename}" class="artifact-btn">
                ${icon} ${a.filename}
            </a>
        `;
    }).join('');
}
```

### Inline Excel preview (optional, with SheetJS)
```javascript
import * as XLSX from 'xlsx';

async function previewExcel(url) {
    const buffer = await fetch(url).then(r => r.arrayBuffer());
    const workbook = XLSX.read(buffer, { type: 'array' });

    // Sheet tabs
    const sheets = workbook.SheetNames;
    // e.g. ["Previsión 13 Semanas", "Detalle por Categoría", "Análisis Histórico", "Resumen Ejecutivo"]

    // Render first sheet as HTML table
    const html = XLSX.utils.sheet_to_html(workbook.Sheets[sheets[0]]);
    document.querySelector('.excel-preview').innerHTML = html;
}
```

### Artifact URL pattern
All artifacts are served at:
```
GET /api/tasks/{task_id}/artifacts/{filename}
```

For inline-generated files (from chat, not a background task), the `task_id` is auto-generated as `chat_{uuid}`.

---

## 7. Background Tasks (Heavy Reports)

Some queries trigger background processing with cost tracking and progress updates.

### What triggers a background task

These keywords in the user's message trigger a background task:

| Keywords | Task Type | Output |
|----------|-----------|--------|
| "previsión de tesorería", "cash flow", "flujo de caja" | `cash_flow_forecast` | 13-week Excel forecast |
| "pack reporting", "P&L mensual", "cuenta resultados" | `pack_reporting` | Multi-sheet P&L Excel |
| "modelo 303", "IVA trimestral" | `modelo_303` | VAT return draft Excel |
| "aging", "antigüedad", "cobros pendientes" | `aging_analysis` | Aging matrix Excel |
| "rentabilidad por cliente" | `client_profitability` | Client margin Excel |
| "modelo 347" | `modelo_347` | Third-party declaration Excel |

**Exception**: informational questions are NOT tasks. "¿Qué es el Modelo 303?" → direct answer (no task).

### WebSocket task flow
```
1. User sends "Genera previsión de tesorería"
2. ← task_created { task_id, task_type: "cash_flow_forecast" }
3. ← task_progress { progress: 5, step: "Iniciando agente..." }
4. ← task_progress { step: "Consultando Bank Reconciliations..." }
5. ← task_progress { step: "Ejecutando análisis..." }
6. ← task_progress { step: "Generando archivo..." }
7. ← task_completed { summary, artifacts: [{filename, url}], cost_usd }
8. ← result { data: { answer, chart, sources, artifacts } }
```

### Create a task via REST
```javascript
const task = await fetch('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        task_type: "cash_flow_forecast",
        description: "Previsión de tesorería 13 semanas",
        location_id: "deloitte-84",
        model: "claude-sonnet-4.5"
    })
}).then(r => r.json());
// Poll with GET /api/tasks/{task.task_id}
```

### Poll task status
```javascript
const task = await fetch(`/api/tasks/${taskId}`).then(r => r.json());
// task = { task_id, status, progress, result_summary, artifacts, cost_usd, ... }
// status: PENDING → RUNNING → COMPLETED | FAILED | CANCELLED
```

### Cost budgets per task type

| Task Type | Budget | Timeout |
|-----------|--------|---------|
| Cash Flow Forecast | $3.00 | 10 min |
| Pack Reporting | $3.00 | 10 min |
| Modelo 303 | $2.00 | 10 min |
| Aging Analysis | $1.50 | 5 min |
| Client Profitability | $2.50 | 10 min |
| Custom | $5.00 | 10 min |

If budget is exceeded, the task stops gracefully and returns partial results.

---

## 8. Chat Management

### Multi-turn conversations
```javascript
let chatId = null;

// Turn 1: new chat (no chat_id)
ws.send(JSON.stringify({ type: "message", question: "Cuantos proveedores tengo?", location_id: "deloitte-84" }));
// → Receives chat_id event → store it

// Turn 2: follow-up (with chat_id)
ws.send(JSON.stringify({ type: "message", question: "Y cual tiene más facturas?", location_id: "deloitte-84", chat_id: chatId }));
// → Agent uses conversation history for context
```

### Chat CRUD

| Endpoint | Description |
|----------|-------------|
| `POST /api/chats?location_id=X` | Create new chat |
| `GET /api/chats?location_id=X` | List chats |
| `GET /api/chats/{id}` | Get chat metadata |
| `GET /api/chats/{id}/messages` | Get message history |
| `PATCH /api/chats/{id}?title=X` | Update title |
| `DELETE /api/chats/{id}` | Delete chat |

### Context window
- Server keeps full history, builds a context window (max 20 messages, 30K chars)
- Older messages are summarized automatically
- Frontend just sends `chat_id` — no need to manage context

---

## 9. AI Cost Tracking

### Per-chat costs
```
GET /api/chats/{chat_id}/costs
```
```json
{
    "summary": { "total_calls": 3, "total_tokens": 15000, "total_cost_usd": 0.05 },
    "by_step": [
        { "step": "agent_iter_1", "total_tokens": 3000, "cost_usd": 0.01 },
        { "step": "agent_iter_2", "total_tokens": 8000, "cost_usd": 0.03 },
        { "step": "agent_iter_3", "total_tokens": 4000, "cost_usd": 0.01 }
    ]
}
```

### Per-user costs
```
GET /api/costs?location_id=deloitte-84&days=30
```

### Model pricing
```
GET /api/costs/models
```
```json
{
    "pricing_per_1m_tokens_usd": {
        "gemini-3.0-flash":  { "input": 0.10, "output": 0.40 },
        "gpt-5-mini":        { "input": 0.15, "output": 0.60 },
        "claude-sonnet-4.5": { "input": 3.00, "output": 15.00 },
        "claude-opus-4.6":   { "input": 15.00, "output": 75.00 }
    }
}
```

### Typical costs

| Query type | LLM calls | Cost |
|-----------|-----------|------|
| Knowledge question | 1 | ~$0.01 |
| Data query + chart | 2-3 | ~$0.03-0.08 |
| Cash flow forecast (task) | 5-8 | ~$0.30-0.50 |
| Modelo 303 (task) | 4-6 | ~$0.20-0.40 |

---

## 10. AI Traces (Debugging)

Every LLM call is traced. Use traces to debug issues, audit AI decisions, and optimize costs.

### Endpoints
```
GET /api/chats/{chatId}/traces     → traces for a chat
GET /api/tasks/{taskId}/traces     → traces for a task
GET /api/traces/{traceId}          → single trace detail
GET /api/traces?location_id=X      → aggregated stats
```

### Trace object
```json
{
    "trace_id": "uuid",
    "step": "agent_iter_1",
    "model": "claude-sonnet-4.5",
    "input_data": { "message_count": 3, "last_user_message": "Top 5 proveedores" },
    "output_data": { "text": "...", "finish_reason": "tool_calls" },
    "tool_calls": [
        { "id": "call_xxx", "name": "dynamo_query", "arguments": "{\"table_name\":\"User_Expenses\"}" }
    ],
    "prompt_tokens": 1200,
    "completion_tokens": 350,
    "total_tokens": 1550,
    "cost_usd": 0.008,
    "latency_ms": 2340,
    "status": "ok"
}
```

### Trace steps explained

| Step | Description |
|------|-------------|
| `agent_iter_1` | First agent iteration (planning + first tool call) |
| `agent_iter_2` | Second iteration (usually analysis after data fetch) |
| `agent_iter_N` | Subsequent iterations |

---

## 11. Code Execution API

Direct access to the AI code execution sandbox for custom file generation.

```javascript
const result = await fetch('/api/code-exec', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        prompt: "Create an Excel with monthly revenue breakdown",
        model: "claude-sonnet-4.5",
        data: JSON.stringify(myData),
        container_id: null  // or reuse from previous call
    })
}).then(r => r.json());

// result = {
//   success: true,
//   text: "Created the report...",
//   files: [{ filename: "report.xlsx", url: "/api/tasks/task_abc/artifacts/report.xlsx" }],
//   container_id: "container_011..."  // reuse for follow-up calls (~4.5 min TTL)
// }
```

---

## 12. Dev Logs Panel

Real-time log streaming for debugging.

### WebSocket (live)
```javascript
const logsWs = new WebSocket('ws://localhost:8000/ws/logs');
logsWs.onmessage = (event) => {
    const log = JSON.parse(event.data);
    // log = { ts, level, logger, message }
    appendToLogPanel(log);
};
```

### REST (polling)
```
GET /api/logs?limit=100
```

### Log panel CSS
```css
#log-panel {
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    background: #1e1e1e;
    color: #d4d4d4;
    padding: 8px;
    max-height: 400px;
    overflow-y: auto;
}
.log-INFO .log-level { color: #4fc1ff; }
.log-WARNING .log-level { color: #ffd700; }
.log-ERROR .log-level { color: #f44747; }
```

---

## 13. Available Models

```
GET /api/models
```
```json
{
    "models": [
        { "id": "gemini-3.0-flash",  "provider": "Google Vertex AI" },
        { "id": "gemini-3.1-pro",    "provider": "Google Vertex AI" },
        { "id": "gpt-5-mini",        "provider": "Azure OpenAI" },
        { "id": "claude-sonnet-4.5", "provider": "Azure AI (Anthropic)" },
        { "id": "claude-opus-4.6",   "provider": "Azure AI (Anthropic)" }
    ],
    "default_orchestrator": "claude-sonnet-4.5"
}
```

Recommended:
- **claude-sonnet-4.5** — best balance (default)
- **gemini-3.0-flash** — fastest and cheapest
- **claude-opus-4.6** — most capable, for complex analysis

---

## 14. Example Queries

| Query | What happens | Response includes |
|-------|-------------|-------------------|
| "¿Qué es el Modelo 303?" | Direct answer (no DB) | `answer` only |
| "Cuánto me he gastado en total?" | Query + analysis | `answer` + `chart` + `sources` |
| "Top 5 proveedores por importe" | Query + analysis + chart | `answer` + `chart` (bar) + `sources` |
| "Transacciones bancarias de enero" | Query + analysis | `answer` + `chart` (line) + `sources` (bank) |
| "Exporta mis gastos a Excel" | Query + generate_file | `answer` + `artifacts` (Excel) |
| "Genera previsión de tesorería" | Background task | Task events → `artifacts` (Excel 5 sheets) |
| "Borrador Modelo 303 Q1" | Background task | Task events → `artifacts` (Excel) |
| "Análisis de antigüedad" | Background task | Task events → `artifacts` (Excel) |

---

## 15. Error Handling

```javascript
// WebSocket reconnection
ws.onclose = () => setTimeout(() => connectWebSocket(), 3000);

// REST errors
try {
    const response = await fetch('/api/chat', { ... });
    if (!response.ok) {
        const error = await response.json();
        showError(error.detail || 'Error del servidor');
    }
} catch (e) {
    showError('No se pudo conectar al servidor');
}
```

---

## 16. Complete React Example

```tsx
import { useState, useEffect, useRef } from 'react';
import { Chart } from 'chart.js/auto';

function CFOChat({ locationId }) {
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [status, setStatus] = useState('');
    const [chatId, setChatId] = useState(null);
    const [taskProgress, setTaskProgress] = useState(null);
    const wsRef = useRef(null);

    useEffect(() => {
        const ws = new WebSocket(`ws://${window.location.host}/ws/chat`);

        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);

            switch (msg.type) {
                case 'chat_id':
                    setChatId(msg.chat_id);
                    break;

                case 'event':
                    if (['querying', 'analyzing', 'generating', 'thinking'].includes(msg.event))
                        setStatus(msg.message);
                    if (msg.event === 'agent_done')
                        setStatus('');
                    break;

                case 'response':
                    setStatus('');
                    setMessages(prev => [...prev, {
                        role: 'assistant',
                        answer: msg.answer,
                        chart: msg.chart,
                        sources: msg.sources,
                        artifacts: msg.artifacts,
                    }]);
                    break;

                case 'task_created':
                    setTaskProgress({ id: msg.task_id, name: msg.task_type_name, progress: 0, step: '' });
                    break;

                case 'task_progress':
                    setTaskProgress(prev => prev ? { ...prev, progress: msg.progress, step: msg.step } : null);
                    break;

                case 'task_completed':
                    setTaskProgress(null);
                    setMessages(prev => [...prev, {
                        role: 'assistant',
                        answer: msg.summary,
                        artifacts: msg.artifacts,
                        cost: msg.cost_usd,
                    }]);
                    break;

                case 'task_failed':
                    setTaskProgress(null);
                    setMessages(prev => [...prev, { role: 'error', answer: msg.error }]);
                    break;
            }
        };

        wsRef.current = ws;
        return () => ws.close();
    }, []);

    const send = () => {
        if (!input.trim()) return;
        setMessages(prev => [...prev, { role: 'user', answer: input }]);
        wsRef.current?.send(JSON.stringify({
            type: 'message',
            question: input,
            location_id: locationId,
            chat_id: chatId,
        }));
        setInput('');
    };

    return (
        <div className="cfo-chat">
            <div className="messages">
                {messages.map((msg, i) => (
                    <Message key={i} {...msg} />
                ))}
            </div>

            {status && <div className="status-bar">{status}</div>}

            {taskProgress && (
                <div className="task-progress">
                    <div className="task-name">{taskProgress.name}</div>
                    <progress value={taskProgress.progress} max={100} />
                    <div className="task-step">{taskProgress.step}</div>
                </div>
            )}

            <div className="input-bar">
                <input value={input} onChange={e => setInput(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && send()}
                    placeholder="Pregunta sobre tus finanzas..." />
                <button onClick={send}>Enviar</button>
            </div>
        </div>
    );
}

function Message({ role, answer, chart, sources, artifacts, cost }) {
    const chartRef = useRef(null);

    useEffect(() => {
        if (chart && chartRef.current && chart.type !== 'table') {
            new Chart(chartRef.current, {
                type: chart.type,
                data: { labels: chart.labels, datasets: chart.datasets },
                options: { plugins: { title: { display: true, text: chart.title } } },
            });
        }
    }, [chart]);

    return (
        <div className={`message ${role}`}>
            <div className="answer" dangerouslySetInnerHTML={{ __html: markdownToHtml(answer) }} />

            {chart && chart.type !== 'table' && <canvas ref={chartRef} />}

            {chart?.type === 'table' && (
                <table>
                    <thead><tr>{chart.labels.map(h => <th key={h}>{h}</th>)}</tr></thead>
                    <tbody>{chart.datasets[0].data.map((row, i) =>
                        <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>
                    )}</tbody>
                </table>
            )}

            {sources?.length > 0 && (
                <div className="sources">
                    <h4>Referencias ({sources.length})</h4>
                    {sources.slice(0, 5).map((s, i) => (
                        <div key={i} className="source-chip">
                            {s.supplier} — {formatEUR(s.total)}
                        </div>
                    ))}
                </div>
            )}

            {artifacts?.length > 0 && (
                <div className="artifacts">
                    {artifacts.map((a, i) => (
                        <a key={i} href={a.url} download={a.filename} className="download-btn">
                            📊 {a.filename}
                        </a>
                    ))}
                </div>
            )}

            {cost && <span className="cost-badge">${cost.toFixed(2)}</span>}
        </div>
    );
}
```
