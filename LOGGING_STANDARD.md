# Logging & Tracing Standard

This document defines the logging and distributed tracing conventions for all FastAPI-based services in this stack. Give this file to an LLM to implement the standard in any new project.

## Stack

| Layer | Tool |
|---|---|
| Structured logs | `structlog` (JSON to stdout) |
| Log shipping | Promtail → Loki → Grafana |
| Distributed tracing | OpenTelemetry → Jaeger → Grafana |
| Trace propagation | W3C `traceparent` header |

---

## Dependencies

Add to `pyproject.toml`:

```toml
opentelemetry-sdk = "*"
opentelemetry-api = "*"
opentelemetry-instrumentation-fastapi = "*"
opentelemetry-instrumentation-httpx = "*"
opentelemetry-exporter-otlp-proto-grpc = "*"
structlog = "*"
```

---

## Configuration

Set these env vars per environment:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317   # gRPC endpoint
OTEL_SERVICE_NAME=zenconnect                       # optional override
```

`SERVICE_NAME` is hardcoded per project (see setup below). `OTEL_EXPORTER_OTLP_ENDPOINT` must always be set via env var.

---

## Setup (`app/main.py`)

### 1. OTEL tracer provider

```python
import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource

SERVICE_NAME = "zenconnect"  # hardcode per project

def setup_tracing() -> None:
    resource = Resource.create({"service.name": SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"],
        insecure=True,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
```

### 2. structlog with trace_id injection

```python
import structlog
from opentelemetry import trace as otel_trace
from typing import Any

def inject_trace_context(
    logger: Any, method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Inject OTEL trace_id and span_id into every log line."""
    span = otel_trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        inject_trace_context,                     # inject OTEL context
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)
```

### 3. FastAPI app setup

```python
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

def create_app() -> FastAPI:
    setup_tracing()

    app = FastAPI(title=SERVICE_NAME)

    # Auto-instrument: incoming HTTP requests get spans + W3C traceparent on outgoing httpx calls
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()

    return app
```

### Resulting baseline fields on every log line

```json
{
  "timestamp": "2026-03-17T10:00:00.000Z",
  "level": "info",
  "event": "Processing valid message event",
  "service": "zenconnect",
  "environment": "production",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7"
}
```

Add `service` and `environment` as structlog bound vars at app startup:

```python
import structlog

# Bind once at startup — all subsequent loggers inherit these
structlog.contextvars.bind_contextvars(
    service=SERVICE_NAME,
    environment=os.getenv("ENV", "development"),
)
```

---

## Error Handling Pattern

**Always** do all three steps on exception: log structured fields, record on span, set span status ERROR.

```python
import traceback
from opentelemetry import trace
from opentelemetry.trace import StatusCode

def handle_exception(span: trace.Span, exc: Exception) -> None:
    """Call this in every except block. Never catch silently."""
    span.record_exception(exc)
    span.set_status(StatusCode.ERROR, str(exc))
    structlog.get_logger().error(
        "Exception occurred",
        error_type=type(exc).__name__,
        error_message=str(exc),
        error_stack=traceback.format_exc(),
    )
```

Usage:

```python
tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("rag.ask") as span:
    try:
        answer = await rag.ask(buffer_text, history, settings)
    except Exception as e:
        handle_exception(span, e)
        raise
```

---

## Manual Spans Pattern

Use this to wrap any business-critical function with its own span and custom attributes.

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

async def flush_buffer(conversation_id: str) -> None:
    with tracer.start_as_current_span("worker.flush_buffer") as span:
        span.set_attribute("conversation_id", conversation_id)

        # ... do work ...

        span.set_attribute("buffer_size", len(rows))   # add attributes as they become known
        span.set_attribute("agent_mode", conv["agent_mode"])
```

**Rules:**
- Name spans as `{component}.{operation}` e.g. `worker.flush_buffer`, `rag.ask`, `zendesk.send_reply`
- Set attributes with known values immediately; add more as they become available
- Never swallow exceptions inside a span — always re-raise after recording

---

## ARQ Worker Pattern

Workers run after a debounce delay — they must **not** be child spans of the original request. Instead:

1. **At enqueue time** — capture the current `trace_id` and pass it as a job argument
2. **In the worker** — start a new root span and attach `parent_trace_id` as an attribute

### At enqueue time (in the webhook/request handler):

```python
from opentelemetry import trace

def get_current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None

# Pass parent_trace_id when enqueuing
await redis.enqueue_job(
    "flush_buffer",
    conversation_id,
    parent_trace_id=get_current_trace_id(),
    _job_id=f"flush:{conversation_id}",
    _defer_by=debounce_seconds,
)
```

### In the worker function:

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

async def flush_buffer(
    ctx: dict[str, Any],
    conversation_id: str,
    parent_trace_id: str | None = None,
) -> None:
    with tracer.start_as_current_span("worker.flush_buffer") as span:
        span.set_attribute("conversation_id", conversation_id)
        if parent_trace_id:
            # Link back to the originating request trace — visible in Jaeger as an attribute
            span.set_attribute("parent_trace_id", parent_trace_id)

        log = structlog.get_logger().bind(
            conversation_id=conversation_id,
            parent_trace_id=parent_trace_id,
        )

        # ... rest of worker logic ...
```

---

## Outgoing HTTP (httpx)

`HTTPXClientInstrumentor().instrument()` at startup handles this automatically. Every `httpx.AsyncClient` request will:
- Create a child span
- Inject `traceparent` header so the downstream service continues the trace

No additional code needed per-call. Ensure you use `httpx.AsyncClient` (not `requests`).

---

## What gets traced automatically vs manually

| Event | How |
|---|---|
| Incoming HTTP request | Auto — `FastAPIInstrumentor` |
| Outgoing HTTP call (`httpx`) | Auto — `HTTPXClientInstrumentor` |
| Errors & exceptions | Manual — `handle_exception(span, exc)` |
| ARQ worker jobs | Manual — `tracer.start_as_current_span(...)` |
| Business logic functions | Manual — `tracer.start_as_current_span(...)` |
| DB queries | Optional — manual span wrapping asyncpg calls |

---

## Log Pipeline

Services write JSON to **stdout only**. Never write to files.

```
stdout → Promtail (infra base) → Loki → Grafana
```

Promtail scraping is configured in the infra base project. No log-shipping code lives in the service.

---

## Checklist for a new project

- [ ] Add OTEL + structlog dependencies
- [ ] Set `OTEL_EXPORTER_OTLP_ENDPOINT` in env
- [ ] Hardcode `SERVICE_NAME` in `app/main.py`
- [ ] Call `setup_tracing()` before app creation
- [ ] Configure structlog with `inject_trace_context` processor
- [ ] Bind `service` and `environment` at startup with `structlog.contextvars.bind_contextvars`
- [ ] Instrument app with `FastAPIInstrumentor` and `HTTPXClientInstrumentor`
- [ ] Use `handle_exception(span, exc)` in all except blocks
- [ ] Wrap worker entry points in a manual span
- [ ] Pass `parent_trace_id` when enqueuing ARQ jobs
