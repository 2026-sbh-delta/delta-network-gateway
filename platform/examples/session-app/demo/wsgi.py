import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo.settings")
os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")
os.environ.setdefault("OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", "DELTA")

if os.environ.get("OTEL_ENABLED") == "true":
    from demo.telemetry import setup
    setup()

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
