# AI CFO Agent — Frontend Integration Guide

## Quick Start

```bash
# Start the server
python -m hackathon_backend.services.lambdas.agent.server --port 8000
```

Server endpoints:
- **WebSocket**: `ws://localhost:8000/ws/chat` (real-time chat with streaming)
- **REST Chat**: `POST http://localhost:8000/api/chat` (send message)
- **Chat CRUD**: `GET/POST/DELETE /api/chats`, `GET /api/chats/{id}/messages`
- **Chat Context**: `GET /api/chats/{id}/context` (current LLM context window)
- **Chat Costs**: `GET /api/chats/{id}/costs` (AI cost breakdown per chat)
- **User Costs**: `GET /api/costs?location_id=X` (AI cost summary per user)
- **Model Pricing**: `GET /api/costs/models` (pricing table)
- **Tasks**: `POST /api/tasks`, `GET /api/tasks`, `GET /api/tasks/{id}`, `DELETE /api/tasks/{id}`
- **Task Artifacts**: `GET /api/tasks/{id}/artifacts/{filename}` (download Excel/PDF)
- **Task Types**: `GET /api/tasks/types` (available task types with budgets)
- **Code Execution**: `POST /api/code-exec` (AI sandbox for Excel/chart generation)
- **Models**: `GET http://localhost:8000/api/models`
- **Health**: `GET http://localhost:8000/api/health`
- **Swagger**: `http://localhost:8000/docs`

---

## 1. WebSocket API (Recommended)

### Connect
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/chat');
```

### Send a message
```javascript
ws.send(JSON.stringify({
    question: "Cuanto me he gastado en total?",
    location_id: "deloitte-84",       // Required: tenant ID
    chat_id: "uuid-of-chat",          // Optional: null = new chat, string = continue chat
    model: "claude-sonnet-4.5",        // Optional: orchestrator model
    classifier_model: "gpt-5-mini",    // Optional: classifier model
    request_id: "abc123"               // Optional: for tracking
}));
```

### Receive events (streaming feedback)

The server sends multiple messages during processing:

#### Chat ID message (sent immediately)
```json
{
    "type": "chat_id",
    "chat_id": "45b57715-a6d8-472b-854d-8e155ee29fd6",
    "request_id": "abc123"
}
```
Store this `chat_id` and send it back in subsequent messages to continue the conversation.

#### Event messages (progress feedback)
```json
{
    "type": "event",
    "event": "step",
    "request_id": "abc123",
    "message": "Clasificando intencion..."
}
```

**Event types and their meaning:**

| Event | Description | Show to user |
|-------|-------------|--------------|
| `step` | Pipeline step change (classify, orchestrate, query_agent, done) | Yes - as status |
| `intent` | Intent classification result | Optional |
| `agent_start` | Query agent started | Optional |
| `thinking` | Agent is planning next step | Yes - as "thinking..." |
| `querying` | Executing a DynamoDB query | Yes - show table being queried |
| `query_result` | Query completed | Yes - show count |
| `query_error` | Query failed (agent will retry) | Optional |
| `analyzing` | Running code analysis on data | Yes - as "computing..." |
| `agent_done` | Agent finished processing | Yes - hide spinner |

**Suggested UX for events:**
```javascript
ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    if (msg.type === 'event') {
        switch (msg.event) {
            case 'step':
                showStatus(msg.message);  // "Clasificando intencion..."
                break;
            case 'thinking':
                showSpinner("Pensando...");
                break;
            case 'querying':
                showSpinner(msg.message); // "Consultando User Expenses..."
                break;
            case 'query_result':
                showToast(msg.message);   // "Encontrados 23 registros"
                break;
            case 'analyzing':
                showSpinner("Calculando metricas...");
                break;
            case 'agent_done':
                hideSpinner();
                break;
        }
    }

    if (msg.type === 'result') {
        hideSpinner();
        renderResult(msg.data);
    }
};
```

#### Result message (final response)
```json
{
    "type": "result",
    "request_id": "abc123",
    "data": {
        "type": "direct_answer | full_answer | complex_task",
        "answer": "Texto de la respuesta...",
        "chart": { ... } | null,
        "sources": [ ... ],
        "intent": "fast_chat",
        "model_used": "claude-sonnet-4.5"
    }
}
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
        model: "claude-sonnet-4.5",
        classifier_model: "gpt-5-mini"
    })
});
const data = await response.json();
```

Response format is the same as the WebSocket `result.data` object.

---

## 3. Response Types

### `direct_answer`
The AI answered without needing database data (general knowledge).
- `answer`: Text response
- `chart`: Always `null`
- `sources`: Always `[]`

### `full_answer`
The AI queried DynamoDB and computed an answer.
- `answer`: Text response with financial data
- `chart`: Chart configuration (see below) or `null`
- `sources`: List of referenced documents (see below)

### `complex_task`
The question requires background processing (not yet implemented).

---

## 4. Chart Format

When the AI suggests a chart, the `chart` field contains:

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

### Chart.js Integration (bar, line, pie)

```javascript
import { Chart } from 'chart.js/auto';

function renderChart(chartData, canvasElement) {
    if (!chartData) return;

    if (chartData.type === 'table') {
        renderTable(chartData);
        return;
    }

    new Chart(canvasElement, {
        type: chartData.type,  // "bar", "line", "pie"
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
    'rgba(236, 72, 153, 0.5)',   // pink
    'rgba(20, 184, 166, 0.5)',   // teal
    'rgba(249, 115, 22, 0.5)',   // orange
];
```

### Table Chart Type

When `chart.type === "table"`, the data structure is:
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

Render as an HTML table:
```javascript
function renderTable(chartData) {
    const headers = chartData.labels;
    const rows = chartData.datasets[0].data;

    return `
        <table>
            <thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
            <tbody>${rows.map(row =>
                `<tr>${row.map(cell => `<td>${cell}</td>`).join('')}</tr>`
            ).join('')}</tbody>
        </table>
    `;
}
```

### Chart Types by Query

| Query type | Chart type | Example |
|-----------|------------|---------|
| Gastos por categoria | `bar` or `pie` | "Dame un grafico de gastos por categoria" |
| Top proveedores | `bar` | "Top 5 proveedores por gasto" |
| Evolucion temporal | `line` | "Evolucion de gastos mensuales" |
| Detalle facturas | `table` | "Facturas de Bio-Rad" |
| Prevision | `line` | "Prevision de gastos" |
| Comparativa | `bar` (multi-dataset) | "Ingresos vs gastos por mes" |

---

## 5. Sources Format (Document References)

Each source is a "paper reference" — a document the AI used to generate the answer.

```typescript
interface Source {
    // Document ID — use this to link to the invoice in the frontend
    categoryDate: string;  // e.g. "COMPRAS#2024-08-29#b7aedbcb-33b5-4de4-93ec-dbe84b7938c4"

    // Who
    supplier: string;       // Supplier or client name
    supplier_cif: string;   // Tax ID (CIF/NIF)

    // When
    invoice_date: string;   // "YYYY-MM-DD"
    due_date: string;       // "YYYY-MM-DD" (payment deadline)

    // How much
    total: number;          // Total amount (EUR)
    importe: number;        // Tax base amount (EUR)

    // Status
    reconciled: boolean;    // true = paid, false = unpaid

    // Classification
    category: string;       // "COMPRAS", "I+D", "SERVICIOS PROFESIONALES", etc.
    concept: string;        // Sub-category

    // Visual reference (for highlighting on PDF)
    total_bounding_box?: {
        Height: number;
        Left: number;
        Top: number;
        Width: number;
    };
}
```

### Rendering sources as clickable references
```javascript
function renderSources(sources) {
    return sources.map(s => `
        <div class="source-card" onclick="openInvoice('${s.categoryDate}')">
            <div class="source-supplier">${s.supplier}</div>
            <div class="source-amount">${formatEUR(s.total)}</div>
            <div class="source-date">${s.invoice_date}</div>
            <div class="source-status ${s.reconciled ? 'paid' : 'unpaid'}">
                ${s.reconciled ? 'Pagada' : 'Pendiente'}
            </div>
        </div>
    `).join('');
}

// The categoryDate is the document PK — use it to navigate to the invoice detail
function openInvoice(categoryDate) {
    // Navigate to invoice detail page
    router.push(`/invoices/${encodeURIComponent(categoryDate)}`);
}
```

---

## 6. Available Models

```
GET /api/models
```

Response:
```json
{
    "models": [
        { "id": "gemini-3.0-flash",  "provider": "Google Vertex AI" },
        { "id": "gemini-3.1-pro",    "provider": "Google Vertex AI" },
        { "id": "gpt-5-mini",        "provider": "Azure OpenAI" },
        { "id": "claude-sonnet-4.5", "provider": "Azure AI (Anthropic)" },
        { "id": "claude-opus-4.6",   "provider": "Azure AI (Anthropic)" }
    ],
    "default_orchestrator": "claude-sonnet-4.5",
    "default_classifier": "gpt-5-mini"
}
```

### Model selection UI suggestion
- Let users pick the orchestrator model from a dropdown
- Default to `claude-sonnet-4.5` (best balance of speed/quality)
- `claude-opus-4.6` for complex analysis
- `gemini-3.0-flash` for fast/cheap queries
- Classifier always uses `gpt-5-mini` (fast, cheap, good enough)

---

## 7. Full React Example

```tsx
import { useState, useEffect, useRef } from 'react';
import { Chart } from 'chart.js/auto';

function CFOChat({ locationId }) {
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [status, setStatus] = useState('');
    const [model, setModel] = useState('claude-sonnet-4.5');
    const [models, setModels] = useState([]);
    const wsRef = useRef(null);
    const chartRef = useRef(null);

    useEffect(() => {
        // Load models
        fetch('/api/models').then(r => r.json()).then(d => setModels(d.models));

        // Connect WebSocket
        const ws = new WebSocket(`ws://${window.location.host}/ws/chat`);
        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);

            if (msg.type === 'event') {
                setStatus(msg.message || msg.event);
            }

            if (msg.type === 'result') {
                setStatus('');
                setMessages(prev => [...prev, {
                    role: 'assistant',
                    ...msg.data
                }]);

                // Render chart if present
                if (msg.data.chart && chartRef.current) {
                    renderChart(msg.data.chart, chartRef.current);
                }
            }
        };
        wsRef.current = ws;
        return () => ws.close();
    }, []);

    const send = () => {
        if (!input.trim()) return;
        setMessages(prev => [...prev, { role: 'user', answer: input }]);
        wsRef.current?.send(JSON.stringify({
            question: input,
            location_id: locationId,
            model: model,
        }));
        setInput('');
    };

    return (
        <div className="cfo-chat">
            <select value={model} onChange={e => setModel(e.target.value)}>
                {models.map(m => (
                    <option key={m.id} value={m.id}>{m.id} ({m.provider})</option>
                ))}
            </select>

            <div className="messages">
                {messages.map((msg, i) => (
                    <div key={i} className={`message ${msg.role}`}>
                        <div className="answer">{msg.answer}</div>
                        {msg.sources?.length > 0 && (
                            <div className="sources">
                                {msg.sources.map((s, j) => (
                                    <SourceCard key={j} source={s} />
                                ))}
                            </div>
                        )}
                    </div>
                ))}
            </div>

            {status && <div className="status-bar">{status}</div>}

            <canvas ref={chartRef} />

            <div className="input-bar">
                <input value={input} onChange={e => setInput(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && send()}
                    placeholder="Pregunta sobre tus finanzas..." />
                <button onClick={send}>Enviar</button>
            </div>
        </div>
    );
}
```

---

## 8. Chat Management API

The server supports persistent multi-turn conversations. Each chat has a unique `chat_id` and stores full message history.

### Create a new chat
```
POST /api/chats?location_id=deloitte-84&model=claude-sonnet-4.5
```
Response:
```json
{
    "chat_id": "45b57715-a6d8-472b-854d-8e155ee29fd6",
    "location_id": "deloitte-84",
    "title": "",
    "model": "claude-sonnet-4.5",
    "created_at": 1711036800.0,
    "updated_at": 1711036800.0,
    "message_count": 0
}
```

### List chats
```
GET /api/chats?location_id=deloitte-84&limit=50
```
Response:
```json
{
    "chats": [
        {
            "chat_id": "45b57715-...",
            "location_id": "deloitte-84",
            "title": "Cuanto me he gastado en total?",
            "model": "claude-sonnet-4.5",
            "created_at": 1711036800.0,
            "updated_at": 1711036900.0,
            "message_count": 4
        }
    ]
}
```

### Get chat metadata
```
GET /api/chats/{chat_id}
```

### Get chat messages (full history)
```
GET /api/chats/{chat_id}/messages?limit=200
```
Response:
```json
{
    "chat_id": "45b57715-...",
    "messages": [
        {
            "id": 1,
            "chat_id": "45b57715-...",
            "role": "user",
            "content": "Cuantos proveedores tengo?",
            "timestamp": 1711036800.0,
            "metadata": {}
        },
        {
            "id": 2,
            "chat_id": "45b57715-...",
            "role": "assistant",
            "content": "Tienes 49 proveedores registrados...",
            "timestamp": 1711036810.0,
            "metadata": {
                "type": "full_answer",
                "chart": true,
                "sources_count": 49,
                "model": "claude-sonnet-4.5"
            }
        }
    ]
}
```

### Update chat (title or model)
```
PATCH /api/chats/{chat_id}?title=Mi+conversacion&model=claude-opus-4.6
```

### Delete chat
```
DELETE /api/chats/{chat_id}
```
Response: `{"deleted": true}`

---

## 9. Multi-Turn Conversation Flow

The agent supports follow-up questions with automatic context. Here's the recommended frontend flow:

### Flow diagram
```
1. User opens app → no chat_id yet
2. User sends first message → send with chat_id: null
3. Server returns chat_id event → store it
4. User sends follow-up → send with stored chat_id
5. Server uses conversation history for context
```

### Example multi-turn conversation
```javascript
let currentChatId = null;

// Turn 1: "Cuantos proveedores tengo?"
ws.send(JSON.stringify({
    question: "Cuantos proveedores tengo?",
    location_id: "deloitte-84",
    chat_id: null  // new chat
}));
// → Receives chat_id event, store it
// → Result: "Tienes 49 proveedores" + bar chart

// Turn 2: "Y cual es el que mas facturas tiene?"
ws.send(JSON.stringify({
    question: "Y cual es el que mas facturas tiene?",
    location_id: "deloitte-84",
    chat_id: currentChatId  // continue same chat
}));
// → Agent understands context, answers from previous data

// Turn 3: "Cuanto le debo a ese proveedor?"
ws.send(JSON.stringify({
    question: "Cuanto le debo a ese proveedor?",
    location_id: "deloitte-84",
    chat_id: currentChatId
}));
// → Agent resolves "ese proveedor" from history, queries DB for unpaid invoices
```

### Context window behavior
- The server keeps full message history in storage
- When calling the LLM, it builds a **context window** (max 20 messages, 30K chars)
- Older messages are automatically **summarized** to save tokens
- Recent messages are sent verbatim for accuracy
- The frontend does NOT need to manage context — just send `chat_id`

### Sidebar: loading previous chats
```javascript
// Load chat list for sidebar
const { chats } = await fetch('/api/chats?location_id=deloitte-84').then(r => r.json());

// When user clicks a chat, load its messages
const { messages } = await fetch(`/api/chats/${chatId}/messages`).then(r => r.json());

// Resume conversation by sending chat_id with new messages
ws.send(JSON.stringify({
    question: "Nueva pregunta...",
    location_id: "deloitte-84",
    chat_id: chatId
}));
```

---

## 10. Example Queries to Test

| Query | What it tests |
|-------|--------------|
| "Cuanto me he gastado en total?" | Basic aggregation, pie chart |
| "Facturas sin pagar" | Reconciliation filter (unpaid) |
| "Cuanto gasto en Bio-Rad?" | Multi-step: find supplier CIF -> filter expenses |
| "Top 5 proveedores por gasto" | Bar chart with ranking |
| "Movimientos bancarios de enero" | Bank transactions, line chart |
| "Prevision gastos mes que viene" | Trend analysis, forecast |
| "Cuanto IVA he pagado en febrero?" | VAT calculation from ivas field |
| "Cuentas contables de Bio-Rad" | AccountingEntries analysis |
| "Productos de laboratorio" | all_products field search |
| "Que es el modelo 303?" | Direct answer (no DB) |

---

## 11. AI Cost Tracking.

The server tracks token usage and estimated costs for every LLM call. Use these endpoints to build cost dashboards and monitor AI spend.

### Get costs for a chat
```
GET /api/chats/{chat_id}/costs
```
Response:
```json
{
    "chat_id": "e3b26017-...",
    "summary": {
        "total_calls": 5,
        "prompt_tokens": 29761,
        "completion_tokens": 1725,
        "total_tokens": 31486,
        "total_cost_usd": 0.0958
    },
    "by_model": [
        {"model": "claude-sonnet-4.5", "calls": 4, "prompt_tokens": 27336, "completion_tokens": 1675, "total_tokens": 29011, "cost_usd": 0.0957},
        {"model": "gpt-5-mini", "calls": 1, "prompt_tokens": 243, "completion_tokens": 50, "total_tokens": 293, "cost_usd": 0.0001}
    ],
    "by_step": [
        {"step": "classifier", "calls": 1, "total_tokens": 293, "cost_usd": 0.0001},
        {"step": "orchestrator", "calls": 1, "total_tokens": 3217, "cost_usd": 0.0113},
        {"step": "query_agent_iter_1", "calls": 1, "total_tokens": 3152, "cost_usd": 0.0104},
        {"step": "query_agent_iter_2", "calls": 1, "total_tokens": 3391, "cost_usd": 0.0122},
        {"step": "query_agent_iter_3", "calls": 1, "total_tokens": 17576, "cost_usd": 0.0617}
    ],
    "details": [...]
}
```

### Get costs for a user (location)
```
GET /api/costs?location_id=deloitte-84
GET /api/costs?location_id=deloitte-84&days=30   // last 30 days only
```
Response:
```json
{
    "location_id": "deloitte-84",
    "summary": {
        "total_calls": 7,
        "prompt_tokens": 29761,
        "completion_tokens": 1725,
        "total_tokens": 31486,
        "total_cost_usd": 0.1123
    },
    "by_model": [...],
    "by_chat": [
        {"chat_id": "e3b26017-...", "title": "Cuantos proveedores tengo?", "calls": 5, "total_tokens": 27628, "cost_usd": 0.0958},
        {"chat_id": "bc024721-...", "title": "Que es el margen bruto?", "calls": 2, "total_tokens": 3858, "cost_usd": 0.0166}
    ]
}
```

### Get model pricing table
```
GET /api/costs/models
```
Response:
```json
{
    "pricing_per_1m_tokens_usd": {
        "gemini-3.0-flash":  {"input": 0.10, "output": 0.40},
        "gemini-3.1-pro":    {"input": 1.25, "output": 5.00},
        "gpt-5-mini":        {"input": 0.15, "output": 0.60},
        "claude-sonnet-4.5": {"input": 3.00, "output": 15.00},
        "claude-opus-4.6":   {"input": 15.00, "output": 75.00}
    }
}
```

### Get context window for a chat
```
GET /api/chats/{chat_id}/context
```
Response:
```json
{
    "chat_id": "e3b26017-...",
    "context_messages": 4,
    "total_chars": 2150,
    "messages": [
        {"role": "user", "content": "Cuantos proveedores tengo?"},
        {"role": "assistant", "content": "Tienes 49 proveedores..."},
        {"role": "user", "content": "Y cual es el que mas facturas tiene?"},
        {"role": "assistant", "content": "HOFFMANN EITLE..."}
    ]
}
```

### Cost dashboard example
```javascript
// Show cost per chat in sidebar
async function loadChatCosts(locationId) {
    const { by_chat, summary } = await fetch(
        `/api/costs?location_id=${locationId}`
    ).then(r => r.json());

    return {
        totalSpend: summary.total_cost_usd,
        totalTokens: summary.total_tokens,
        chats: by_chat.map(c => ({
            id: c.chat_id,
            title: c.title,
            cost: c.cost_usd,
            tokens: c.total_tokens,
        })),
    };
}

// Show detailed breakdown for a specific chat
async function loadChatCostDetail(chatId) {
    const data = await fetch(`/api/chats/${chatId}/costs`).then(r => r.json());
    // data.by_step shows: classifier, orchestrator, query_agent_iter_N
    // data.by_model shows cost per model used
    return data;
}
```

### Understanding the cost steps

| Step | Description | Typical cost |
|------|-------------|-------------|
| `classifier` | Intent classification (fast_chat vs complex_task) | Very low (~$0.0001) |
| `orchestrator` | Main brain decides if data needed + what to fetch | Low (~$0.01) |
| `query_agent_iter_N` | Each query agent iteration (plan, query, analyze) | Medium (~$0.01-0.06) |

- **Direct answers** (no DB): ~$0.02 total (classifier + orchestrator only)
- **Data queries**: ~$0.05-0.15 total depending on complexity
- **Using `gemini-3.0-flash`** instead of `claude-sonnet-4.5` reduces costs ~30x

---

## 12. Complex Tasks (Deep Agent)

For heavy tasks (Cash Flow Forecast, Modelo 303, reporting, audits), the system uses async background tasks with sub-agents.

### How it works

```
1. User sends question → Classifier detects "complex_task"
2. Server creates a Task → runs sub-agents in parallel
3. Sub-agents query DynamoDB + analyze data
4. Synthesizer produces final output
5. Excel artifacts generated and downloadable
```

### Task types

```
GET /api/tasks/types
```
Response:
```json
{
    "types": [
        {"id": "cash_flow_forecast", "name": "Previsión de Tesorería (13 semanas)", "cost_budget_usd": 1.0, "max_agents": 4, "timeout_s": 300},
        {"id": "pack_reporting", "name": "Pack Reporting Mensual", "cost_budget_usd": 1.5, "max_agents": 5, "timeout_s": 600},
        {"id": "modelo_303", "name": "Borrador Modelo 303 (IVA)", "cost_budget_usd": 0.8, "max_agents": 3, "timeout_s": 300},
        {"id": "aging_analysis", "name": "Análisis de Antigüedad (Aging)", "cost_budget_usd": 0.5, "max_agents": 2, "timeout_s": 180},
        {"id": "client_profitability", "name": "Rentabilidad por Cliente", "cost_budget_usd": 1.0, "max_agents": 4, "timeout_s": 300},
        {"id": "custom", "name": "Tarea Personalizada", "cost_budget_usd": 2.0, "max_agents": 5, "timeout_s": 600}
    ]
}
```

### Create a task (REST)

```javascript
const response = await fetch('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        task_type: "cash_flow_forecast",
        description: "Genera la previsión de tesorería de las próximas 13 semanas",
        location_id: "deloitte-84",
        chat_id: "optional-chat-id",   // Link task to a chat
        model: "claude-sonnet-4.5"
    })
});
const task = await response.json();
// task.task_id → use for polling
```

### Poll task status

```javascript
// Poll every 3-5 seconds while task is running
const task = await fetch(`/api/tasks/${taskId}`).then(r => r.json());
```

Response:
```json
{
    "task_id": "d6b9426b-...",
    "task_type": "cash_flow_forecast",
    "task_type_name": "Previsión de Tesorería (13 semanas)",
    "status": "COMPLETED",
    "progress": 100,
    "cost_usd": 0.2378,
    "cost_budget_usd": 1.0,
    "total_tokens": 47639,
    "result_summary": "Previsión de tesorería generada para las próximas 13 semanas...",
    "artifacts": [
        {"filename": "cash_flow_forecast_13w.xlsx", "type": "excel", "size_bytes": 7255}
    ],
    "steps": [
        {"step_number": 1, "status": "COMPLETED", "description": "Consultar facturas pendientes de pago"},
        {"step_number": 2, "status": "COMPLETED", "description": "Consultar facturas pendientes de cobro"},
        {"step_number": 3, "status": "COMPLETED", "description": "Consultar nóminas recientes"},
        {"step_number": 4, "status": "COMPLETED", "description": "Consultar transacciones bancarias"}
    ]
}
```

Task statuses: `PENDING` → `RUNNING` → `COMPLETED` | `FAILED` | `CANCELLED`

### Download artifacts

```javascript
// List artifacts
const { artifacts } = await fetch(`/api/tasks/${taskId}/artifacts`).then(r => r.json());

// Download Excel
const blob = await fetch(`/api/tasks/${taskId}/artifacts/${artifacts[0].filename}`).then(r => r.blob());
const url = URL.createObjectURL(blob);
const a = document.createElement('a');
a.href = url;
a.download = artifacts[0].filename;
a.click();
```

### WebSocket task events

When a complex task is triggered via WebSocket (user asks a question that's classified as `complex_task`), the server sends these events:

```javascript
ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    // Task was created
    if (msg.type === 'task_created') {
        showTaskBanner(msg.task_id, msg.task_type_name);
    }

    // Progress updates (during task execution)
    if (msg.type === 'task_progress') {
        updateProgress(msg.task_id, msg.progress, msg.step);
    }

    // Task completed — result includes artifacts
    if (msg.type === 'result' && msg.data.type === 'complex_task') {
        hideProgress();
        showAnswer(msg.data.answer);
        // Show download buttons for artifacts
        msg.data.artifacts.forEach(a => {
            showDownloadButton(a.filename, a.url);
        });
        showCost(msg.data.cost_usd);
    }

    // Task failed
    if (msg.type === 'task_failed') {
        showError(msg.error);
    }
};
```

### List tasks for sidebar

```javascript
const { tasks } = await fetch('/api/tasks?location_id=deloitte-84').then(r => r.json());
// tasks: [{task_id, task_type, task_type_name, status, progress, cost_usd, artifacts, ...}]
```

### Cancel a running task

```javascript
await fetch(`/api/tasks/${taskId}`, { method: 'DELETE' });
```

### Get step-by-step trace

```
GET /api/tasks/{task_id}/steps
```
Returns detailed execution trace for each sub-agent step (useful for debugging/auditing).

### Cost guardrails

Each task type has a cost budget. If the budget is exceeded during execution, the task stops gracefully and returns partial results. The budgets are:

| Task Type | Budget | Max Agents | Timeout |
|-----------|--------|------------|---------|
| Cash Flow Forecast | $1.00 | 4 | 5 min |
| Pack Reporting | $1.50 | 5 | 10 min |
| Modelo 303 | $0.80 | 3 | 5 min |
| Aging Analysis | $0.50 | 2 | 3 min |
| Client Profitability | $1.00 | 4 | 5 min |
| Custom | $2.00 | 5 | 10 min |

### Example queries that trigger complex tasks

| Query | Task Type | Output |
|-------|-----------|--------|
| "Genera la previsión de tesorería" | cash_flow_forecast | Excel 13-week forecast |
| "Prepara el pack reporting mensual" | pack_reporting | Multi-sheet Excel (P&L, KPIs) |
| "Genera el borrador del Modelo 303" | modelo_303 | Excel matching official form |
| "Análisis de antigüedad de cobros" | aging_analysis | Excel aging matrix |
| "Rentabilidad por cliente" | client_profitability | Excel margin analysis |

---

## 13. Code Execution & AI-Powered File Generation

The backend uses **native AI code execution** (Claude's `code_execution_20250825` sandbox and Gemini's code execution) to dynamically generate Excel reports, charts, and analysis files. This replaces hardcoded templates — the LLM writes and runs Python code (openpyxl, pandas, matplotlib) in a sandboxed container.

### How it works

1. **Fast-chat**: The query agent has a `generate_file` tool. When the user asks for a report/export, the LLM calls this tool which triggers code execution.
2. **Deep-agent tasks**: After sub-agents gather data and the synthesizer produces results, code execution generates the final Excel artifact.
3. **Direct API**: `POST /api/code-exec` for custom code execution requests.

### Direct Code Execution: `POST /api/code-exec`

```javascript
const response = await fetch('/api/code-exec', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        prompt: "Create an Excel with monthly revenue breakdown",
        model: "claude-sonnet-4.5",   // or "gemini-3.0-flash"
        data: JSON.stringify(myData), // optional: data context
        system_prompt: null,          // optional
        task_id: null,                // optional: auto-generated if null
        container_id: null            // optional: reuse Claude container
    })
});

const result = await response.json();
// {
//   success: true,
//   text: "I've created the Excel report with...",
//   files: [
//     { filename: "revenue_report.xlsx", url: "/api/tasks/task_abc/artifacts/revenue_report.xlsx" }
//   ],
//   container_id: "container_011...",  // for follow-up requests
//   usage: { prompt_tokens: 5000, completion_tokens: 800, total_tokens: 5800 }
// }
```

### Downloading generated files

```javascript
// Files from code execution (direct API)
const fileUrl = result.files[0].url;  // "/api/tasks/{task_id}/artifacts/{filename}"
window.open(fileUrl);

// Files from deep-agent tasks (same endpoint)
const taskArtifact = `/api/tasks/${taskId}/artifacts/${filename}`;
window.open(taskArtifact);
```

### Provider fallback chain

The system automatically falls back across providers:
1. **Claude** (Azure AI Foundry) — preferred, supports container reuse
2. **Gemini** (Vertex AI) — fallback, code re-executed locally for file capture
3. **Template fallback** — last resort, uses predefined openpyxl templates

### Prompt caching (automatic)

For Claude models, the backend automatically applies **prompt caching** (`cache_control: {"type": "ephemeral"}`) to system messages and large conversation history. This reduces API costs by up to 90% on cache hits. No frontend action needed.

### Multi-step container reuse

For complex tasks, pass `container_id` from a previous response to reuse the same Claude sandbox (files and state persist ~4.5 minutes):

```javascript
// Step 1: Generate data
const step1 = await fetch('/api/code-exec', {
    method: 'POST',
    body: JSON.stringify({ prompt: "Load and clean this data...", data: rawData })
}).then(r => r.json());

// Step 2: Reuse container to generate chart from the same data
const step2 = await fetch('/api/code-exec', {
    method: 'POST',
    body: JSON.stringify({
        prompt: "Now create a chart from the cleaned data",
        container_id: step1.container_id  // reuse sandbox
    })
}).then(r => r.json());
```

### Files in WebSocket chat (fast-chat flow)

When the user asks for a file in the chat (e.g., "Exporta mis gastos a Excel"), the response arrives via WebSocket with `files` in the final message:

```javascript
ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    if (msg.type === 'final') {
        // msg.data.files is an array of generated files (may be empty)
        const files = msg.data.files || [];
        files.forEach(file => {
            // file = { filename: "gastos_export.xlsx", url: "/api/tasks/chat_abc/artifacts/gastos_export.xlsx", type: "excel" }..
            renderDownloadButton(file);
        });

        // msg.data.answer is the AI's text response
        renderMarkdown(msg.data.answer);
    }
};
```

### Rendering file downloads in the UI

```javascript
function renderDownloadButton(file) {
    const icon = {
        excel: 'table-cells',       // .xlsx files
        csv: 'file-csv',            // .csv files
        image: 'chart-bar',         // .png charts
        pdf: 'file-pdf',            // .pdf files
    }[file.type] || 'file';

    const btn = document.createElement('a');
    btn.href = file.url;
    btn.download = file.filename;
    btn.className = 'download-btn';
    btn.innerHTML = `<i class="fa fa-${icon}"></i> ${file.filename}`;
    document.querySelector('.chat-files').appendChild(btn);
}
```

### Files in deep-agent tasks

Task artifacts appear in the task completion event and in the task detail API:

```javascript
// Via WebSocket (real-time)
ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'task_complete') {
        // msg.data.artifacts = [{ filename, type, size_bytes }]
        msg.data.artifacts.forEach(artifact => {
            const url = `/api/tasks/${msg.data.task_id}/artifacts/${artifact.filename}`;
            renderDownloadButton({ ...artifact, url });
        });
    }
};

// Via REST API (polling)
const task = await fetch(`/api/tasks/${taskId}`).then(r => r.json());
if (task.status === 'completed' && task.artifacts?.length) {
    task.artifacts.forEach(artifact => {
        const url = `/api/tasks/${taskId}/artifacts/${artifact.filename}`;
        // Trigger download or show preview
        window.open(url, '_blank');
    });
}
```

### Excel preview with SheetJS (optional)

For inline Excel preview without downloading:

```javascript
import * as XLSX from 'xlsx';

async function previewExcel(url) {
    const response = await fetch(url);
    const buffer = await response.arrayBuffer();
    const workbook = XLSX.read(buffer, { type: 'array' });

    // Get all sheet names
    const sheets = workbook.SheetNames; // ["Cash Flow Forecast", "Detalle Cobros", ...]

    // Convert first sheet to HTML table
    const html = XLSX.utils.sheet_to_html(workbook.Sheets[sheets[0]]);
    document.querySelector('.excel-preview').innerHTML = html;

    // Or convert to JSON for custom rendering
    const data = XLSX.utils.sheet_to_json(workbook.Sheets[sheets[0]]);
    renderTable(data);
}
```

### Chart images in chat

When the AI generates chart images (.png), they appear in the `files` array:

```javascript
files.forEach(file => {
    if (file.type === 'image') {
        const img = document.createElement('img');
        img.src = file.url;
        img.alt = file.filename;
        img.className = 'chart-image';
        document.querySelector('.chat-charts').appendChild(img);
    }
});
```

### Available models for code execution

| Model | Provider | Speed | Best for |
|-------|----------|-------|----------|
| `claude-sonnet-4.5` | Azure AI Foundry | ~30-60s | Complex multi-sheet Excel, container reuse |
| `gemini-3.0-flash` | Vertex AI | ~15-20s | Fast generation, charts, simple reports |

GPT models automatically fall back to Gemini for code execution.

---

## 14. Error Handling

```javascript
ws.onerror = (error) => {
    console.error('WebSocket error:', error);
    showToast('Error de conexion');
};

ws.onclose = () => {
    // Reconnect after 3 seconds
    setTimeout(() => connectWebSocket(), 3000);
};
```

For REST API errors:
```javascript
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
