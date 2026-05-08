"""
OpenTelemetry tracing: optional, no-op when OTEL_EXPORTER_OTLP_ENDPOINT not set.
Lightweight: lazy init, minimal overhead when disabled.
"""
import os
import threading

_otlp_configured = False
_otlp_lock = threading.Lock()


def get_tracer(name: str, version: str = "1.0.0") -> "Tracer":
    """Return OTel tracer. No-op tracer when OTel not configured or not installed."""
    global _otlp_configured
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return _noop_tracer(name, version)

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError:
        return _noop_tracer(name, version)

    with _otlp_lock:
        if not _otlp_configured:
            provider = TracerProvider()
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            trace.set_tracer_provider(provider)
            _otlp_configured = True

    return trace.get_tracer(name, version)


def _noop_tracer(name: str, version: str) -> "Tracer":
    """No-op tracer when OTel disabled or not installed."""
    try:
        from opentelemetry import trace
        return trace.get_tracer(name or "noop", version)
    except ImportError:
        return _fallback_noop()


def _fallback_noop():
    """Fallback when opentelemetry not installed."""
    from types import SimpleNamespace

    def _noop_span(*args, **kwargs):
        return SimpleNamespace(__enter__=lambda s: s, __exit__=lambda s, *a: None)

    return SimpleNamespace(start_as_current_span=_noop_span)
