# tests/test_logging.py
"""Tests for structlog configuration and OTEL trace processor."""
from unittest.mock import MagicMock, patch

import structlog


def test_otel_processor_injects_trace_id_when_span_active():
    """_otel_trace_processor adds trace_id and span_id to event dict when a valid span exists."""
    from siphon.main import _otel_trace_processor

    mock_span = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.is_valid = True
    mock_ctx.trace_id = 0x4BF92F3577B34DA6A3CE929D0E0E4736
    mock_ctx.span_id = 0x00F067AA0BA902B7
    mock_span.get_span_context.return_value = mock_ctx

    event_dict = {"event": "hello"}
    with patch("opentelemetry.trace.get_current_span", return_value=mock_span):
        result = _otel_trace_processor(None, None, event_dict)

    assert result["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert result["span_id"] == "00f067aa0ba902b7"


def test_otel_processor_noop_when_no_active_span():
    """_otel_trace_processor leaves event dict unchanged when no valid span is active."""
    from siphon.main import _otel_trace_processor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry import trace

    # Ensure no span is active
    trace.set_tracer_provider(TracerProvider())
    event_dict = {"event": "hello"}
    result = _otel_trace_processor(None, None, event_dict)

    assert "trace_id" not in result
    assert "span_id" not in result


def test_structlog_contextvars_bind_and_clear():
    """Bound contextvars appear in structlog event dicts and are cleared independently."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(job_id="abc-123", pipeline_id="pipe-456")

    bound = structlog.contextvars.get_contextvars()
    assert bound["job_id"] == "abc-123"
    assert bound["pipeline_id"] == "pipe-456"

    structlog.contextvars.clear_contextvars()
    assert structlog.contextvars.get_contextvars() == {}
