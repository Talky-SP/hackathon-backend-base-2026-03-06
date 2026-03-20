# AI CFO Agent — Arquitectura del Orquestador

## Flujo General

```
Usuario envía pregunta
        │
        ▼
┌─────────────────┐
│   Classifier    │  (modelo rápido: gpt-5-mini)
│ fast_chat/task  │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
fast_chat   complex_task (TODO: async)
    │
    ▼
┌─────────────────┐
│  Orchestrator   │  (modelo potente: claude-sonnet-4.5)
│                 │
│  Decide:        │
│  A) Responde    │
│     directo     │
│  B) Necesita    │
│     datos → ──────────────┐
└────────┬────────┘          │
         │                   ▼
    direct_answer    ┌──────────────────┐
                     │  DB Query Agent  │  ← TU AGENTE
                     │  (externo)       │
                     └──────────────────┘
```

## Qué hace el Orquestador

1. Recibe la pregunta del usuario
2. Tiene contexto de las tablas disponibles y sus campos
3. Decide si puede responder solo (conocimiento general) o necesita datos
4. Si necesita datos, hace un tool_call `fetch_financial_data` que genera un JSON con:
   - La pregunta original del usuario
   - Lista de datasets que necesita (tabla, descripción, campos, fechas, filtros)
   - Sugerencia de chart opcional

## Formato de salida del Orquestador

### Caso A: Respuesta directa (no necesita datos)

```json
{
  "type": "direct_answer",
  "answer": "El Modelo 303 es la declaración trimestral del IVA...",
  "chart": null,
  "model_used": "claude-sonnet-4.5",
  "intent": "fast_chat"
}
```

### Caso B: Necesita datos (para el DB Query Agent)

```json
{
  "type": "needs_data",
  "user_question": "¿Cuánto facturé en marzo de 2026?",
  "data_requests": [
    {
      "table": "User_Invoice_Incomes",
      "description": "Todas las facturas de ingreso de marzo 2026",
      "fields_needed": ["total", "invoice_date", "client_name", "invoice_number"],
      "date_range": {
        "from": "2026-03-01",
        "to": "2026-03-31"
      },
      "filters": null
    }
  ],
  "chart_suggestion": {
    "type": "bar",
    "title": "Facturación marzo 2026"
  },
  "model_used": "claude-sonnet-4.5",
  "intent": "fast_chat"
}
```

## Estructura de cada `data_request`

| Campo            | Tipo     | Siempre presente | Descripción                                           |
|------------------|----------|------------------|-------------------------------------------------------|
| `table`          | string   | Sí               | Nombre de la tabla DynamoDB (sin prefijo de entorno)   |
| `description`    | string   | Sí               | Descripción en lenguaje natural de qué datos buscar    |
| `fields_needed`  | string[] | No               | Campos específicos que necesita de cada item           |
| `date_range`     | object   | No               | `{from: "YYYY-MM-DD", to: "YYYY-MM-DD"}`              |
| `filters`        | object   | No               | Filtros adicionales como key-value                     |

## Tablas disponibles

El orquestador puede pedir datos de estas tablas:

| Tabla                    | PK (siempre locationId) | Contenido                              |
|--------------------------|-------------------------|----------------------------------------|
| `User_Expenses`          | userId                  | Facturas de gasto                      |
| `User_Invoice_Incomes`   | userId                  | Facturas de ingreso                    |
| `Bank_Reconciliations`   | locationId              | Transacciones bancarias                |
| `Payroll_Slips`          | locationId              | Nóminas                                |
| `Delivery_Notes`         | userId                  | Albaranes                              |
| `Employees`              | locationId              | Maestro de empleados                   |
| `Providers`              | locationId              | Maestro de proveedores                 |
| `Customers`              | locationId              | Maestro de clientes                    |
| `Daily_Stats`            | locationId              | Estadísticas diarias pre-calculadas    |
| `Monthly_Stats`          | locationId              | Estadísticas mensuales pre-calculadas  |

**IMPORTANTE**: Todas las queries DEBEN estar acotadas por `locationId`. Es la clave de aislamiento multi-tenant. `userId = locationId` (naming legacy).

## Contrato: qué espera el Orquestador de vuelta

Una vez el DB Query Agent ejecute las queries, debe devolver los datos en este formato para que el orquestador genere la respuesta final:

```json
{
  "results": [
    {
      "table": "User_Invoice_Incomes",
      "description": "Todas las facturas de ingreso de marzo 2026",
      "items": [
        {
          "total": 1500.00,
          "invoice_date": "2026-03-05",
          "client_name": "Acme Corp",
          "invoice_number": "FAC-2026-042"
        },
        {
          "total": 3200.50,
          "invoice_date": "2026-03-12",
          "client_name": "Tech Solutions SL",
          "invoice_number": "FAC-2026-043"
        }
      ],
      "count": 2
    }
  ]
}
```

---

## Ejemplos de queries para probar

### 1. Query simple — Facturación de un mes

**Pregunta**: `"¿Cuánto facturé en marzo de 2026?"`

**data_requests generado**:
```json
[
  {
    "table": "User_Invoice_Incomes",
    "description": "Todas las facturas de ingreso emitidas en marzo de 2026 para calcular la facturación total del mes",
    "fields_needed": ["total", "invoice_date", "client_name", "invoice_number"],
    "date_range": {"from": "2026-03-01", "to": "2026-03-31"}
  }
]
```

**Query DynamoDB sugerida**:
```
Tabla: {env}_User_Invoice_Incomes
Index: UserIdInvoiceDateIndex
PK: userId = {locationId}
SK: invoice_date BETWEEN "2026-03-01" AND "2026-03-31"
```

---

### 2. Query simple — Proveedores

**Pregunta**: `"¿Cuántos proveedores tengo?"`

**data_requests generado**:
```json
[
  {
    "table": "Providers",
    "description": "Todos los proveedores registrados para contar el total",
    "fields_needed": ["cif", "name"]
  }
]
```

**Query DynamoDB sugerida**:
```
Tabla: {env}_Providers
PK: locationId = {locationId}
(Sin SK condition — scan de la partición completa)
```

---

### 3. Multi-query — Pack de reporting mensual

**Pregunta**: `"Genera el pack de reporting de febrero"`

**data_requests generado** (6 datasets):
```json
[
  {
    "table": "User_Expenses",
    "description": "Facturas de gasto de febrero 2025 con importes, IVA, proveedores y categorías",
    "fields_needed": ["total", "importe", "vatTotalAmount", "vatDeductibleAmount", "supplier_name", "supplier_cif", "invoice_date", "pnl_date", "category", "concept", "reconciled"],
    "date_range": {"from": "2025-02-01", "to": "2025-02-28"}
  },
  {
    "table": "User_Invoice_Incomes",
    "description": "Facturas de ingreso de febrero 2025",
    "fields_needed": ["total", "importe", "vatTotalAmount", "client_name", "client_cif", "invoice_date", "pnl_date", "category", "concept", "reconciled"],
    "date_range": {"from": "2025-02-01", "to": "2025-02-28"}
  },
  {
    "table": "Bank_Reconciliations",
    "description": "Transacciones bancarias de febrero 2025",
    "fields_needed": ["transactionId", "bookingDate", "amount", "merchant", "description", "status", "reconciled"],
    "date_range": {"from": "2025-02-01", "to": "2025-02-28"}
  },
  {
    "table": "Payroll_Slips",
    "description": "Nóminas de febrero 2025",
    "fields_needed": ["employee_nif", "payroll_info", "payroll_date"],
    "date_range": {"from": "2025-02-01", "to": "2025-02-28"}
  },
  {
    "table": "Monthly_Stats",
    "description": "Estadísticas mensuales de febrero 2025",
    "filters": {"monthKey": "2025-02"}
  },
  {
    "table": "Monthly_Stats",
    "description": "Estadísticas mensuales de enero 2025 para comparativa",
    "filters": {"monthKey": "2025-01"}
  }
]
```

**Queries DynamoDB sugeridas**:
```
1. {env}_User_Expenses     → UserIdPnlDateIndex    → PK={locationId}, SK BETWEEN "2025-02-01" AND "2025-02-28"
2. {env}_User_Invoice_Incomes → UserIdPnlDateIndex → PK={locationId}, SK BETWEEN "2025-02-01" AND "2025-02-28"
3. {env}_Bank_Reconciliations → LocationByStatusDate → PK={locationId}, SK BETWEEN "..." (filtrar por fecha)
4. {env}_Payroll_Slips     → OrgCifPeriodIndex      → PK={org_cif}, SK begins_with("PERIOD#2025-02")
5. {env}_Monthly_Stats     → PK={locationId}, SK="2025-02"
6. {env}_Monthly_Stats     → PK={locationId}, SK="2025-01"
```

---

### 4. Query con filtros — Facturas no conciliadas

**Pregunta**: `"¿Qué facturas de gasto tengo sin conciliar?"`

**data_requests generado**:
```json
[
  {
    "table": "User_Expenses",
    "description": "Facturas de gasto no conciliadas con el banco",
    "fields_needed": ["total", "supplier", "supplier_cif", "invoice_date", "due_date", "amount_due"],
    "filters": {"reconciled": false}
  }
]
```

**Query DynamoDB sugerida**:
```
Tabla: {env}_User_Expenses
Index: UserByReconStateDate
PK: userId = {locationId}
SK: begins_with("U#")       ← "U" = unreconciled
```

---

### 5. Query por proveedor/cliente — Aging de cobros

**Pregunta**: `"¿Cuánto me debe Acme Corp?"`

**data_requests generado**:
```json
[
  {
    "table": "User_Invoice_Incomes",
    "description": "Facturas de ingreso emitidas a Acme Corp pendientes de cobro",
    "fields_needed": ["total", "amount_due", "amount_paid", "due_date", "invoice_date", "invoice_number", "reconciled"],
    "filters": {"client_name": "Acme Corp", "reconciled": false}
  }
]
```

**Query DynamoDB sugerida**:
```
Tabla: {env}_User_Invoice_Incomes
Index: UserIdClientCifIndex (si se conoce el CIF)
   o: UserByReconStateDate PK={locationId}, SK begins_with("U#") + FilterExpression client_name="Acme Corp"
```

---

### 6. Query de nóminas — Coste salarial mensual

**Pregunta**: `"¿Cuánto gastamos en nóminas en enero?"`

**data_requests generado**:
```json
[
  {
    "table": "Payroll_Slips",
    "description": "Todas las nóminas de enero para calcular el coste salarial total",
    "fields_needed": ["employee_nif", "payroll_info.gross_amount", "payroll_info.net_amount", "payroll_info.company_ss_contribution", "payroll_info.irpf_amount"],
    "date_range": {"from": "2025-01-01", "to": "2025-01-31"}
  }
]
```

**Query DynamoDB sugerida**:
```
Tabla: {env}_Payroll_Slips
Index: OrgCifPeriodIndex
PK: org_cif = {org_cif}
SK: begins_with("PERIOD#2025-01")
```

---

## Cómo probar

```bash
# Instalar dependencias (desde el root del proyecto)
pip install litellm langfuse google-cloud-aiplatform

# Ejecutar preguntas individuales
python -m hackathon_backend.services.lambdas.agent.main -q "¿Cuánto facturé en marzo?" -m claude-sonnet-4.5
python -m hackathon_backend.services.lambdas.agent.main -q "¿Qué facturas tengo sin conciliar?" -m claude-sonnet-4.5
python -m hackathon_backend.services.lambdas.agent.main -q "Genera el pack de reporting de febrero" -m claude-sonnet-4.5

# Modo interactivo (escribir "model" para cambiar modelo, "models" para listar)
python -m hackathon_backend.services.lambdas.agent.main

# Cambiar modelo por defecto
python -m hackathon_backend.services.lambdas.agent.main -m gemini-3.0-flash
python -m hackathon_backend.services.lambdas.agent.main -m claude-opus-4.6

# Modelos disponibles
python -m hackathon_backend.services.lambdas.agent.main --list-models
```

## Modelos disponibles

| ID                  | Proveedor              | Uso recomendado                  |
|---------------------|------------------------|----------------------------------|
| `gemini-3.0-flash`  | Vertex AI (Google)     | Rápido, barato                   |
| `gemini-3.1-pro`    | Vertex AI (Google)     | Más potente que flash            |
| `gpt-5-mini`        | Azure OpenAI           | Clasificación, tareas simples    |
| `claude-sonnet-4.5` | Azure AI (Anthropic)   | Orquestación, tool calling       |
| `claude-opus-4.6`   | Azure AI (Anthropic)   | Máxima calidad, tareas complejas |

## Archivos clave

```
hackathon_backend/services/lambdas/agent/
├── main.py              # CLI local para probar
└── core/
    ├── config.py        # Secretos AWS + registro de modelos + Langfuse
    ├── prompts.py       # System prompts (editables en Langfuse)
    ├── schemas.py       # Esquemas de tablas DynamoDB para contexto LLM
    ├── classifier.py    # Clasificador de intención
    ├── orchestrator.py  # Orquestador principal
    └── db_tools.py      # Definición del tool fetch_financial_data
```
