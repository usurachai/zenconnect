import pytest
from unittest.mock import MagicMock, patch
import opentelemetry.trace as _trace_module
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import StatusCode


@pytest.fixture(autouse=True)
def reset_tracer_provider():
    """Reset OTEL global state before and after each test."""
    _trace_module._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    _trace_module._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    trace.set_tracer_provider(provider)
    yield
    _trace_module._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    _trace_module._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]


def test_inject_trace_context_adds_trace_and_span_id():
    from app.telemetry import inject_trace_context

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("test_span"):
        event_dict: dict = {}
        result = inject_trace_context(None, "info", event_dict)

    assert "trace_id" in result
    assert "span_id" in result
    assert len(result["trace_id"]) == 32
    assert len(result["span_id"]) == 16


def test_inject_trace_context_without_active_span():
    """Outside any span context, trace_id/span_id must not be injected."""
    from app.telemetry import inject_trace_context

    event_dict: dict = {}
    result = inject_trace_context(None, "info", event_dict)

    assert "trace_id" not in result
    assert "span_id" not in result


def test_get_current_trace_id_with_active_span():
    from app.telemetry import get_current_trace_id

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("test_span"):
        trace_id = get_current_trace_id()

    assert trace_id is not None
    assert len(trace_id) == 32
    int(trace_id, 16)  # valid hex


def test_get_current_trace_id_without_active_span():
    """Outside any span context, get_current_trace_id returns None."""
    from app.telemetry import get_current_trace_id

    assert get_current_trace_id() is None


def test_handle_exception_records_on_span_and_sets_error_status():
    from app.telemetry import handle_exception

    span = MagicMock()
    exc = ValueError("something broke")

    with patch("structlog.get_logger") as mock_get_logger:
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger
        handle_exception(span, exc)

    span.record_exception.assert_called_once_with(exc)
    span.set_status.assert_called_once()
    assert span.set_status.call_args[0][0] == StatusCode.ERROR

    mock_logger.error.assert_called_once()
    log_kwargs = mock_logger.error.call_args[1]
    assert log_kwargs["error_type"] == "ValueError"
    assert log_kwargs["error_message"] == "something broke"
    assert "error_stack" in log_kwargs


def test_setup_tracing_skips_when_no_endpoint():
    from app.telemetry import setup_tracing
    import os

    os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    # Should not raise
    setup_tracing()
