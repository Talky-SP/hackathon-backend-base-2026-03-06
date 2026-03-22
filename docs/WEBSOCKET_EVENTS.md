# WebSocket Events — Agent Debug Panel

## Connection

```javascript
const ws = new WebSocket("ws://localhost:8000/ws/chat");
// Production: wss://<api-gw-url>/ws/chat
```

## Send a message

```javascript
ws.send(JSON.stringify({
  question: "Dime las transacciones bancarias sin conciliar",
  location_id: "deloitte-84",
  model: "claude-sonnet-4.5",
  chat_id: "optional-existing-chat-id",  // null = new chat
  request_id: "optional-correlation-id",
}));
```

## Receive events

Every message from the server is JSON with `type` field:

```javascript
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  switch (msg.type) {
    case "chat_id":    // Chat resolved/created
    case "event":      // Agent progress events (the interesting ones)
    case "result":     // Final response
    case "cancelled":  // User cancelled
    case "error":      // Error
  }
};
```

## Agent Events (`type: "event"`)

All agent events have this shape:

```json
{
  "type": "event",
  "event": "<event_name>",
  "request_id": "abc123",
  ...event-specific fields
}
```

### Event catalog

#### 1. `tool_call_detail` — What the LLM decided to call
Emitted for EACH tool call the agent makes. Shows the full arguments.

```json
{
  "type": "event",
  "event": "tool_call_detail",
  "tool": "dynamo_query",
  "args": {
    "table_name": "Bank_Reconciliations",
    "filter_expression": {"field": "reconciled", "op": "eq", "value": false}
  },
  "iteration": 1
}
```

#### 2. `querying` — DynamoDB query starting
Full query details for debugging.

```json
{
  "type": "event",
  "event": "querying",
  "message": "Consultando Bank Reconciliations...",
  "table": "Bank_Reconciliations",
  "query_key": "query_1",
  "index": null,
  "pk_field": "userId",
  "sk_field": "categoryDate",
  "sk_condition": {"op": "begins_with", "value": "MTXN#2025"},
  "filter": [{"field": "reconciled", "op": "eq", "value": false}],
  "limit": null
}
```

#### 3. `query_result` — DynamoDB query completed
Shows how many items were returned + same query details.

```json
{
  "type": "event",
  "event": "query_result",
  "query_key": "query_1",
  "table": "Bank_Reconciliations",
  "count": 251,
  "message": "Encontrados 251 registros en Bank Reconciliations",
  "index": null,
  "filter": [{"field": "reconciled", "op": "eq", "value": false}],
  "limit": null
}
```

#### 4. `tool_calls` — Summary of all tools called in one iteration

```json
{
  "type": "event",
  "event": "tool_calls",
  "message": "Ejecutando: dynamo_query, run_analysis",
  "tools": ["dynamo_query", "run_analysis"],
  "iteration": 1
}
```

#### 5. `analyzing` — Python code execution starting
Shows the actual code and available data.

```json
{
  "type": "event",
  "event": "analyzing",
  "message": "Ejecutando analisis de datos...",
  "detail": "txns = data['query_1']['items']",
  "code": "txns = data['query_1']['items']\ntotal = len(txns)\n...",
  "available_queries": ["query_1"],
  "query_counts": {"query_1": 430}
}
```

#### 6. `analysis_result` — Code execution completed

```json
{
  "type": "event",
  "event": "analysis_result",
  "message": "Analisis completado",
  "answer_preview": "Total de transacciones: 430\nSin conciliar: 251...",
  "has_chart": true,
  "sources_count": 430
}
```

#### 7. `generating` — File generation starting (Excel/PDF)

```json
{
  "type": "event",
  "event": "generating",
  "message": "Generando archivo (code execution)...",
  "detail": "Create an Excel file with all bank transactions...",
  "model": "claude-sonnet-4.5"
}
```

#### 8. `code_exec_start` — AI sandbox code execution starting

```json
{
  "type": "event",
  "event": "code_exec_start",
  "message": "Escribiendo y ejecutando codigo Python (claude-sonnet-4.5)...",
  "task_id": "chat_c610325a"
}
```

#### 9. `thinking` — Agent iteration progress

```json
{
  "type": "event",
  "event": "thinking",
  "step": 1,
  "message": "Analizando tu pregunta..."
}
```

#### 10. `agent_start` — Agent loop started

```json
{
  "type": "event",
  "event": "agent_start",
  "question": "Dime las transacciones sin conciliar",
  "model": "claude-sonnet-4.5"
}
```

#### 11. `agent_done` — Agent finished

```json
{
  "type": "event",
  "event": "agent_done",
  "message": "Analisis completado"
}
```

#### 12. Task events (heavy/background tasks)

```json
{"type": "event", "event": "task_created",   "task_id": "...", "task_type": "bank_reconciliation"}
{"type": "event", "event": "task_progress",  "task_id": "...", "progress": 25, "step": "Consultando..."}
{"type": "event", "event": "task_completed", "task_id": "...", "summary": "...", "artifacts": [...]}
{"type": "event", "event": "task_failed",    "task_id": "...", "error": "..."}
{"type": "event", "event": "task_cancelled", "task_id": "...",}
```

## Final result (`type: "result"`)

```json
{
  "type": "result",
  "request_id": "abc123",
  "chat_id": "741ea5f1-...",
  "message_id": 3,
  "data": {
    "type": "full_answer",
    "answer": "markdown text...",
    "chart": {"type": "bar", "title": "...", "labels": [...], "datasets": [...]},
    "sources": [{"categoryDate": "...", "supplier": "...", "total": 1234.56}],
    "model_used": "claude-sonnet-4.5",
    "artifacts": [{"filename": "report.xlsx", "url": "/api/tasks/xxx/artifacts/report.xlsx"}]
  }
}
```

## Frontend implementation example

```javascript
// Minimal debug panel
const debugLog = [];

ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);

  if (msg.type === "event") {
    debugLog.push({
      timestamp: new Date().toISOString(),
      event: msg.event,
      data: msg,
    });

    // Update debug panel UI
    switch (msg.event) {
      case "tool_call_detail":
        addToPanel(`[Tool] ${msg.tool}(${JSON.stringify(msg.args).slice(0, 200)})`);
        break;
      case "querying":
        addToPanel(`[Query] ${msg.table} | index=${msg.index} | filter=${JSON.stringify(msg.filter)}`);
        break;
      case "query_result":
        addToPanel(`[Result] ${msg.table}: ${msg.count} items`);
        break;
      case "analyzing":
        addToPanel(`[Code] ${msg.detail}\n  queries: ${JSON.stringify(msg.query_counts)}`);
        break;
      case "analysis_result":
        addToPanel(`[Analysis] ${msg.answer_preview}`);
        break;
      case "generating":
        addToPanel(`[File] ${msg.detail}`);
        break;
      case "thinking":
        addToPanel(`[Think] Step ${msg.step}: ${msg.message}`);
        break;
    }
  }

  if (msg.type === "result") {
    // Show final answer
    showAnswer(msg.data);
  }
};
```

## REST API for traces (after the fact)

```
GET /api/chats/{chat_id}/traces     — All traces for a chat
GET /api/traces/{trace_id}          — Single trace detail
GET /api/tasks/{task_id}/steps      — Task execution steps
GET /api/costs?location_id=X        — Cost breakdown by model
GET /api/chats/{chat_id}/costs      — Cost breakdown for one chat
```
