import os
import traceback
from collections.abc import MutableMapping, Mapping
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import StatusCode

SERVICE_NAME = "zenconnect"


def _add_service_context(
    logger: Any, method: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    """Structlog processor — stamps every log line with service and environment."""
    event_dict["service"] = SERVICE_NAME
    event_dict["environment"] = os.getenv("ENV", "development")
    return event_dict


def configure_logging() -> None:
    """Configure structlog with JSON output, trace injection, and service/environment fields.

    Call this once at process startup — from both app/main.py and app/worker.py.
    """
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            inject_trace_context,
            _add_service_context,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def setup_tracing() -> None:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        structlog.get_logger().warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT not set — tracing disabled"
        )
        return
    resource = Resource.create({"service.name": SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def inject_trace_context(
    logger: Any, method: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    """Structlog processor — injects trace_id and span_id from the active OTEL span."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def handle_exception(span: trace.Span, exc: Exception) -> None:
    """Record an exception on the span and emit a structured error log.

    Call this in every except block. Never catch silently.
    """
    span.record_exception(exc)
    span.set_status(StatusCode.ERROR, str(exc))
    structlog.get_logger().error(
        "Exception occurred",
        error_type=type(exc).__name__,
        error_message=str(exc),
        error_stack=traceback.format_exc(),
    )


def get_current_trace_id() -> str | None:
    """Return the active trace_id as a 32-char hex string, or None if no span is active."""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
