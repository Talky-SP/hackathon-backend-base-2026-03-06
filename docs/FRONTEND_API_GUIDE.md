# AI CFO Agent — Guia de Integracion Frontend

## URLs Base

| Entorno | REST API | WebSocket Chat | WebSocket Logs |
|---------|----------|----------------|----------------|
| **Local** | `http://localhost:8000` | `ws://localhost:8000/ws/chat` | `ws://localhost:8000/ws/logs` |
| **AWS Dev** | `https://qjgx7zjsma.execute-api.eu-west-3.amazonaws.com` | `wss://buctm9ogkd.execute-api.eu-west-3.amazonaws.com/dev` | No disponible (solo local) |

> **Tip**: En el frontend, usa una variable de entorno para la URL base:
> ```ts
> const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
> const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws/chat'
> ```

---

## 1. WebSocket — Chat en Tiempo Real (RECOMENDADO)

El WebSocket es el metodo principal. Envia preguntas y recibe eventos en streaming (progreso, respuestas parciales, artifacts, etc.).

### Conexion

```ts
// LOCAL
const ws = new WebSocket('ws://localhost:8000/ws/chat')

// AWS — el location_id va como query param
const ws = new WebSocket('wss://buctm9ogkd.execute-api.eu-west-3.amazonaws.com/dev?location_id=deloitte-84')
```

> **Diferencia clave AWS**: En AWS, `location_id` se pasa como query parameter al conectar.
> En local se pasa en cada mensaje JSON.

### Enviar mensaje

```ts
ws.send(JSON.stringify({
  question: "¿Cual es el total de gastos del ultimo trimestre?",
  location_id: "deloitte-84",       // Identificador del tenant/empresa
  model: "claude-sonnet-4.5",       // Opcional, default: claude-sonnet-4.5
  chat_id: null,                     // null = chat nuevo, string = continuar chat
  request_id: "abc123",             // Opcional, para correlacionar respuesta
  attachments: []                    // Opcional, [{filename, mime_type, data}]
}))
```

### Modelos disponibles

| model_id | Proveedor | Uso recomendado |
|----------|-----------|-----------------|
| `claude-sonnet-4.5` | Azure (Anthropic) | Default — mejor calidad |
| `gpt-5-mini` | Azure OpenAI | Rapido y barato |
| `gemini-3.0-flash` | Google Vertex AI | Muy rapido |
| `gemini-3.1-pro` | Google Vertex AI | Alternativa potente |

### Eventos que recibe el frontend

Los mensajes llegan como JSON con un campo `type`:

#### `chat_id` — Confirmacion del chat
```json
{ "type": "chat_id", "chat_id": "uuid-del-chat", "request_id": "abc123" }
```
> Guardar este `chat_id` para continuar la conversacion.

#### `event` — Progreso del agente (streaming)

Todos los eventos del agente tienen `type: "event"` y un campo `event` con el nombre.
Ver [WEBSOCKET_EVENTS.md](WEBSOCKET_EVENTS.md) para el catalogo completo con ejemplos.

Eventos principales para el UI:
- `tool_call_detail`: Que herramienta llama el LLM y con que argumentos
- `querying`: Query DynamoDB con detalles (tabla, indice, filtro, limit)
- `query_result`: Resultado de la query (count, tabla)
- `analyzing`: Codigo Python ejecutado + datos disponibles
- `analysis_result`: Preview del resultado, chart, sources
- `generating`: Generacion de archivo (Excel/PDF)
- `thinking`: Paso del agente (iteracion)

```json
{
  "type": "event",
  "event": "tool_call_detail",
  "request_id": "abc123",
  "tool": "dynamo_query",
  "args": {"table_name": "Bank_Reconciliations", "filter_expression": {...}},
  "iteration": 1
}
```
```json
{
  "type": "event",
  "event": "query_result",
  "request_id": "abc123",
  "query_key": "query_1",
  "table": "Bank_Reconciliations",
  "count": 430,
  "message": "Encontrados 430 registros en Bank Reconciliations"
}
```
> Mostrar estos como indicadores de progreso. Para un panel de debug, usar `tool_call_detail` y `analyzing`.

#### `response` — Respuesta final (chat normal)
```json
{
  "type": "response",
  "request_id": "abc123",
  "chat_id": "uuid-del-chat",
  "message_id": 42,
  "answer": "El total de gastos del ultimo trimestre es 45.230,50 EUR...",
  "chart": null,
  "sources": [
    { "table": "User_Expenses", "query": "...", "rows_found": 156 }
  ],
  "artifacts": [
    { "filename": "gastos_q4.xlsx", "url": "/api/tasks/task-id/artifacts/gastos_q4.xlsx" }
  ],
  "model_used": "claude-sonnet-4.5"
}
```

#### `task_created` — Tarea compleja detectada
```json
{
  "type": "task_created",
  "request_id": "abc123",
  "chat_id": "uuid-del-chat",
  "task_id": "uuid-de-la-tarea",
  "task_type": "cash_flow_forecast",
  "task_type_name": "Prevision de Tesoreria (13 semanas)"
}
```
> Mostrar un panel de progreso de tarea.

#### `task_progress` — Progreso de tarea compleja
```json
{ "type": "task_progress", "task_id": "...", "progress": 45, "step": "Analizando flujo de caja..." }
```
> Actualizar barra de progreso.

#### `task_completed` — Tarea terminada
```json
{
  "type": "task_completed",
  "task_id": "...",
  "summary": "He generado la prevision de tesoreria...",
  "artifacts": [{ "filename": "prevision_tesoreria.xlsx", "url": "..." }]
}
```

#### `result` — Resultado final de tarea compleja
```json
{
  "type": "result",
  "request_id": "abc123",
  "chat_id": "...",
  "message_id": 43,
  "data": {
    "type": "complex_task",
    "answer": "He completado la prevision de tesoreria...",
    "chart": { "type": "line", "data": {...} },
    "sources": [...],
    "model_used": "claude-sonnet-4.5",
    "task_id": "...",
    "artifacts": [{ "filename": "prevision_tesoreria.xlsx", "url": "..." }]
  }
}
```

#### `cancelled` — Operacion cancelada
```json
{ "type": "cancelled", "request_id": "abc123", "chat_id": "..." }
```

#### `error` — Error
```json
{ "type": "error", "message": "Missing 'question' field" }
```

### Cancelar una operacion

```ts
ws.send(JSON.stringify({
  type: "cancel",
  chat_id: "uuid-del-chat"    // o task_id para cancelar tarea
}))
```

---

## 2. REST API — Endpoints

Todos los endpoints usan el prefijo `/api/`. Las URLs son identicas en local y AWS, solo cambia la base.

### Health Check
```
GET /api/health
→ { "status": "ok", "models_loaded": true }
```

### Modelos
```
GET /api/models
→ { "models": [{ "id": "claude-sonnet-4.5", "provider": "Azure AI (Anthropic)" }, ...] }
```

---

### Chat (REST — sin streaming)
```
POST /api/chat
Content-Type: application/json

{
  "question": "¿Cual es el total de gastos?",
  "location_id": "deloitte-84",
  "model": "claude-sonnet-4.5",
  "chat_id": null
}

→ {
    "type": "full_answer",
    "answer": "El total de gastos es...",
    "chart": null,
    "sources": [...],
    "intent": "fast_chat",
    "model_used": "claude-sonnet-4.5",
    "request_id": "abc123",
    "chat_id": "uuid",
    "message_id": 1
  }
```

---

### Gestion de Chats

| Metodo | Endpoint | Descripcion |
|--------|----------|-------------|
| `GET` | `/api/chats?location_id=deloitte-84` | Listar todos los chats |
| `POST` | `/api/chats?location_id=deloitte-84&model=claude-sonnet-4.5` | Crear chat vacio |
| `GET` | `/api/chats/{chat_id}` | Detalle de un chat |
| `GET` | `/api/chats/{chat_id}/messages` | Historial de mensajes |
| `PATCH` | `/api/chats/{chat_id}?title=Nuevo titulo` | Actualizar titulo/modelo |
| `DELETE` | `/api/chats/{chat_id}` | Eliminar chat |
| `POST` | `/api/chats/{chat_id}/cancel` | Cancelar operacion en curso |

#### Ejemplo: Listar chats
```
GET /api/chats?location_id=deloitte-84

→ {
    "chats": [
      {
        "chat_id": "uuid",
        "title": "Total de gastos del trimestre",
        "model": "claude-sonnet-4.5",
        "created_at": 1711100000.0,
        "updated_at": 1711100500.0,
        "message_count": 4
      }
    ]
  }
```

#### Ejemplo: Historial de mensajes
```
GET /api/chats/{chat_id}/messages

→ {
    "chat_id": "uuid",
    "messages": [
      { "id": 1, "role": "user", "content": "¿Total de gastos?", "timestamp": 1711100000.0, "metadata": {} },
      { "id": 2, "role": "assistant", "content": "El total es...", "timestamp": 1711100005.0, "metadata": { "type": "full_answer", "model": "claude-sonnet-4.5" } }
    ]
  }
```

---

### Tareas Complejas (Background)

Las tareas complejas (prevision de tesoreria, modelo 303, etc.) se ejecutan en background.

| Metodo | Endpoint | Descripcion |
|--------|----------|-------------|
| `GET` | `/api/tasks/types` | Tipos de tarea disponibles |
| `POST` | `/api/tasks` | Crear y lanzar tarea |
| `GET` | `/api/tasks?location_id=deloitte-84` | Listar tareas |
| `GET` | `/api/tasks/{task_id}` | Estado de tarea (polling) |
| `GET` | `/api/tasks/{task_id}/steps` | Pasos detallados |
| `GET` | `/api/tasks/{task_id}/artifacts` | Listar archivos generados |
| `GET` | `/api/tasks/{task_id}/artifacts/{filename}` | Descargar archivo |
| `DELETE` | `/api/tasks/{task_id}` | Cancelar tarea |
| `POST` | `/api/tasks/{task_id}/cancel` | Cancelar tarea en curso |

#### Ejemplo: Tipos de tarea
```
GET /api/tasks/types

→ {
    "types": [
      { "id": "cash_flow_forecast", "name": "Prevision de Tesoreria (13 semanas)", "cost_budget_usd": 3.0, "timeout_s": 600 },
      { "id": "pack_reporting", "name": "Pack Reporting Mensual", "cost_budget_usd": 3.0, "timeout_s": 600 },
      { "id": "modelo_303", "name": "Borrador Modelo 303 (IVA)", "cost_budget_usd": 2.0, "timeout_s": 600 },
      { "id": "aging_analysis", "name": "Analisis de Antiguedad (Aging)", "cost_budget_usd": 1.5, "timeout_s": 300 }
    ]
  }
```

#### Ejemplo: Estado de tarea
```
GET /api/tasks/{task_id}

→ {
    "task_id": "uuid",
    "status": "RUNNING",
    "progress": 45,
    "task_type": "cash_flow_forecast",
    "task_type_name": "Prevision de Tesoreria (13 semanas)",
    "result_summary": "",
    "cost_usd": 0.23,
    "cost_budget_usd": 3.0,
    "artifacts": [],
    "steps": [
      { "step_number": 1, "description": "Analizando datos", "status": "COMPLETED" },
      { "step_number": 2, "description": "Generando prevision", "status": "RUNNING" }
    ]
  }
```

---

### Descarga de Archivos (Artifacts)

```
GET /api/tasks/{task_id}/artifacts/{filename}
```

| Entorno | Comportamiento |
|---------|---------------|
| **Local** | Devuelve el archivo directamente (FileResponse) |
| **AWS** | Redirige (302) a URL prefirmada de S3 (valida 1 hora) |

> El frontend puede usar `<a href="...">` o `window.open()` — funciona igual en ambos entornos.

---

### Costes y Trazabilidad

| Metodo | Endpoint | Descripcion |
|--------|----------|-------------|
| `GET` | `/api/chats/{chat_id}/costs` | Coste IA de un chat |
| `GET` | `/api/costs?location_id=deloitte-84&days=30` | Costes agregados por empresa |
| `GET` | `/api/costs/models` | Tabla de precios por modelo |
| `GET` | `/api/chats/{chat_id}/traces` | Trazas LLM de un chat |
| `GET` | `/api/tasks/{task_id}/traces` | Trazas LLM de una tarea |
| `GET` | `/api/traces/{trace_id}` | Detalle de una traza |
| `GET` | `/api/traces?location_id=deloitte-84&days=7` | Trazas agregadas por empresa |

#### Ejemplo: Costes de un chat
```
GET /api/chats/{chat_id}/costs

→ {
    "chat_id": "uuid",
    "summary": {
      "total_calls": 3,
      "prompt_tokens": 12500,
      "completion_tokens": 3200,
      "total_tokens": 15700,
      "total_cost_usd": 0.085
    },
    "details": [...]
  }
```

---

### Ejecucion de Codigo
```
POST /api/code-exec
Content-Type: application/json

{
  "prompt": "Genera un Excel con las ventas mensuales",
  "model": "claude-sonnet-4.5",
  "data": "datos opcionales en texto...",
  "task_id": null
}

→ {
    "success": true,
    "text": "He generado el archivo Excel...",
    "files": [{ "filename": "ventas.xlsx", "url": "/api/tasks/exec_abc/artifacts/ventas.xlsx" }],
    "task_id": "exec_abc"
  }
```

---

### Logs en Tiempo Real (Solo Local)

```ts
const logsWs = new WebSocket('ws://localhost:8000/ws/logs')
logsWs.onmessage = (e) => {
  const log = JSON.parse(e.data)
  // { ts: 1711100000, level: "INFO", logger: "pipeline", message: "..." }
}
```

O por REST polling:
```
GET /api/logs?limit=100
```

> No disponible en AWS. Solo para desarrollo local.

---

## 3. Contexto de Chat (Context Window)

Para debug, puedes ver que contexto se envia al LLM:
```
GET /api/chats/{chat_id}/context

→ {
    "chat_id": "uuid",
    "context_messages": 8,
    "total_chars": 12500,
    "messages": [
      { "role": "user", "content": "..." },
      { "role": "assistant", "content": "..." }
    ]
  }
```

---

## 4. Ejemplo Completo de Integracion (TypeScript)

```ts
const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws/chat'

// --- WebSocket Chat ---
function connectChat(locationId: string) {
  const ws = new WebSocket(WS_URL.includes('amazonaws.com')
    ? `${WS_URL}?location_id=${locationId}`
    : WS_URL
  )

  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data)

    switch (msg.type) {
      case 'chat_id':
        // Guardar chat_id para siguiente mensaje
        currentChatId = msg.chat_id
        break

      case 'event':
        // Mostrar progreso: msg.event, msg.message
        showProgress(msg.message || msg.step)
        break

      case 'response':
        // Respuesta final del chat
        showAnswer(msg.answer)
        showSources(msg.sources)
        showArtifacts(msg.artifacts)
        break

      case 'task_created':
        // Tarea compleja creada
        showTaskProgress(msg.task_id, msg.task_type_name, 0)
        break

      case 'task_progress':
        // Actualizar progreso
        updateTaskProgress(msg.task_id, msg.progress, msg.step)
        break

      case 'result':
        // Resultado final de tarea compleja
        showAnswer(msg.data.answer)
        showArtifacts(msg.data.artifacts)
        break

      case 'error':
        showError(msg.message)
        break

      case 'cancelled':
        showCancelled()
        break
    }
  }

  return ws
}

// --- Enviar pregunta ---
function sendQuestion(ws: WebSocket, question: string, chatId?: string) {
  ws.send(JSON.stringify({
    question,
    location_id: 'deloitte-84',
    model: 'claude-sonnet-4.5',
    chat_id: chatId || null,
    request_id: crypto.randomUUID().slice(0, 8),
  }))
}

// --- Cancelar ---
function cancelOperation(ws: WebSocket, chatId: string) {
  ws.send(JSON.stringify({ type: 'cancel', chat_id: chatId }))
}

// --- REST: Listar chats ---
async function listChats(locationId: string) {
  const res = await fetch(`${API_BASE}/api/chats?location_id=${locationId}`)
  return res.json()
}

// --- REST: Historial ---
async function getMessages(chatId: string) {
  const res = await fetch(`${API_BASE}/api/chats/${chatId}/messages`)
  return res.json()
}

// --- REST: Descargar artifact ---
function downloadArtifact(taskId: string, filename: string) {
  window.open(`${API_BASE}/api/tasks/${taskId}/artifacts/${filename}`)
}
```

---

## 5. Variables de Entorno para el Frontend

```env
# Local
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws/chat

# AWS Dev
NEXT_PUBLIC_API_URL=https://qjgx7zjsma.execute-api.eu-west-3.amazonaws.com
NEXT_PUBLIC_WS_URL=wss://buctm9ogkd.execute-api.eu-west-3.amazonaws.com/dev
```

---

## 6. Notas Importantes

1. **location_id**: Siempre `deloitte-84` para la demo. Es el identificador del tenant/empresa.
2. **Artifacts en AWS**: Las URLs de descarga son presigned S3 URLs, validas 1 hora. Si expiran, volver a pedir via `/api/tasks/{id}/artifacts/{file}`.
3. **Tareas complejas**: Se detectan automaticamente por el contenido de la pregunta. Si pides "prevision de tesoreria" o "modelo 303", el agente crea una tarea en background.
4. **WebSocket AWS vs Local**: En AWS los eventos tienen la misma estructura JSON. La unica diferencia es la URL de conexion y que `location_id` va como query param.
5. **CORS**: Configurado para `*` en dev. En produccion se restringira.
6. **Timeout Lambda**: 15 minutos maximo por peticion en AWS.
