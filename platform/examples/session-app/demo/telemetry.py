import logging
import os

import psycopg
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.django import DjangoInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.metrics import Observation
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup():
    resource = Resource.create({"service.name": "delta-session-demo", "service.version": os.environ.get("APP_VERSION", "dev")})
    tracer = TracerProvider(resource=resource)
    tracer.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(timeout=3)))
    trace.set_tracer_provider(tracer)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter(timeout=3), export_interval_millis=15000)]))
    logs = LoggerProvider(resource=resource)
    logs.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(timeout=3)))
    logging.getLogger("demo").addHandler(LoggingHandler(level=logging.WARNING, logger_provider=logs))
    meter = metrics.get_meter("delta-session-demo")
    meter.create_observable_gauge("delta.app.up", callbacks=[lambda _: [Observation(1)]])

    def db_up(_):
        try:
            with suppress_instrumentation(), psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=2) as conn:
                conn.execute("SELECT 1")
            return [Observation(1)]
        except Exception:
            return [Observation(0)]

    meter.create_observable_gauge("delta.db.up", callbacks=[db_up])
    DjangoInstrumentor().instrument()
    PsycopgInstrumentor().instrument()
