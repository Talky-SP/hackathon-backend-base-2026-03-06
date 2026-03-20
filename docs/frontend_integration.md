# AI CFO Agent — Frontend Integration Guide

## Quick Start

```bash
# Start the server
python -m hackathon_backend.services.lambdas.agent.server --port 8000
```

Server endpoints:
- **WebSocket**: `ws://localhost:8000/ws/chat` (real-time streaming)
- **REST**: `POST http://localhost:8000/api/chat` (single request)
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
    model: "claude-sonnet-4.5",        // Optional: orchestrator model
    classifier_model: "gpt-5-mini",    // Optional: classifier model
    request_id: "abc123"               // Optional: for tracking
}));
```

### Receive events (streaming feedback)

The server sends multiple messages during processing:

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

## 8. Example Queries to Test

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

## 9. Error Handling

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
