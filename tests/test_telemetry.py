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


def test_setup_tracing_registers_provider_when_endpoint_set(reset_tracer_provider):
    from app.telemetry import setup_tracing
    from opentelemetry.sdk.trace import TracerProvider
    import os

    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
    try:
        setup_tracing()
        provider = trace.get_tracer_provider()
        assert isinstance(provider, TracerProvider)
    finally:
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)


def test_add_service_context_injects_service_and_environment():
    from app.telemetry import _add_service_context
    import os

    os.environ["ENV"] = "production"
    try:
        event_dict: dict = {}
        result = _add_service_context(None, "info", event_dict)
        assert result["service"] == "zenconnect"
        assert result["environment"] == "production"
    finally:
        os.environ.pop("ENV", None)


def test_add_service_context_defaults_to_development():
    from app.telemetry import _add_service_context
    import os

    os.environ.pop("ENV", None)
    event_dict: dict = {}
    result = _add_service_context(None, "info", event_dict)
    assert result["environment"] == "development"


def test_configure_logging_configures_structlog():
    import structlog
    from app.telemetry import configure_logging

    configure_logging()

    config = structlog.get_config()
    processor_names = [type(p).__name__ for p in config["processors"]]
    assert "JSONRenderer" in processor_names
    assert "TimeStamper" in processor_names


def test_handle_exception_logs_structured_error_fields():
    from app.telemetry import handle_exception

    span = MagicMock()
    log_calls: list[dict] = []

    class CapturingLogger:
        def error(self, event, **kw):
            log_calls.append({"event": event, **kw})

    try:
        raise RuntimeError("disk full")
    except RuntimeError as exc:
        with patch("structlog.get_logger", return_value=CapturingLogger()):
            handle_exception(span, exc)

    assert len(log_calls) == 1
    call = log_calls[0]
    assert call["error_type"] == "RuntimeError"
    assert call["error_message"] == "disk full"
    assert "error_stack" in call
    assert "RuntimeError" in call["error_stack"]
