"""Bounded read-only cloud queries; raw telemetry never enters the business DB."""

import copy
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config

from delta_platform.core.environments import FIELDS, STORED_METADATA_FIELDS, EnvironmentInput

SDK_CONFIG = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1})


def finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite observation")
    return result


def stamp(value):
    return datetime.fromtimestamp(float(value), UTC).isoformat()


def metric(value=None, *, at=None, now=None, unit="", series=None, status=None, message=None):
    state = status or ("no_data" if value is None else "ok")
    if state == "ok" and at and now and now.timestamp() - float(at) > 300:
        state = "stale"
    return {
        "status": state,
        "value": value,
        "unit": unit,
        "collected_at": stamp(at) if at else None,
        "message": message,
        "series": series or [],
    }


def safe_text(value):
    text = str(value)[:2000]
    text = re.sub(r"(?i)(?:Bearer|Basic)\s+\S+", "[redacted]", text)
    text = re.sub(
        r"(?i)(password|passwd|token|secret|authorization|api[_-]?key)\s*[=:]\s*[^\s,;]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[redacted]", text)
    text = re.sub(r"(?:AKIA|ASIA)[A-Z0-9]{16}", "[redacted]", text)
    return text[:400]


class CloudWatchTelemetry:
    def __init__(self, region, *, session=None, transport=None):
        if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d", region):
            raise ValueError("Unsupported telemetry region")
        self.region = region
        self.session = session or boto3.Session(region_name=region)
        self.transport = transport

    def client(self, name, region=None):
        return self.session.client(name, region_name=region or self.region, config=SDK_CONFIG)

    def query(self, expression, *, start=None, end=None):
        operation = "query_range" if start else "query"
        payload = {"query": expression, "limit": "3"}
        if start:
            payload.update(start=start.timestamp(), end=end.timestamp(), step="60s")
        else:
            payload["time"] = end.timestamp()
        body = urlencode(payload)
        url = f"https://monitoring.{self.region}.amazonaws.com/api/v1/{operation}"
        request = AWSRequest(
            method="POST",
            url=url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        credentials = self.session.get_credentials()
        if not credentials:
            raise ValueError("AWS authentication unavailable")
        SigV4Auth(credentials.get_frozen_credentials(), "monitoring", self.region).add_auth(request)
        with httpx.Client(timeout=5, transport=self.transport) as client:
            with client.stream(
                "POST", url, content=body, headers=dict(request.headers)
            ) as response:
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1_000_000:
                        raise ValueError("Telemetry response too large")
        result = json.loads(raw)
        if result.get("status") != "success" or result.get("warnings"):
            raise ValueError("Incomplete telemetry query")
        return result["data"]["result"]

    def metrics(self, env, start, end):
        # UUIDs are database-owned; no caller-supplied PromQL/labels are accepted.
        labels = f'"@resource.delta_service_id"="{env["service_id"]}","@resource.delta_environment_id"="{env["id"]}"'
        histogram = '{"http.server.request.duration",' + labels + "}"
        error_histogram = (
            '{"http.server.request.duration",' + labels + ',"http.response.status_code"=~"5.."}'
        )
        window = f"{int((end - start).total_seconds())}s"
        total = f"sum(histogram_count(sum_over_time({histogram}[{window}])))"
        errors = f"sum(histogram_count(sum_over_time({error_histogram}[{window}])))"
        # The collector contract is DELTA; rate() on a delta histogram is wrong.
        queries = {
            "requests": (total, "requests"),
            "error_rate": (f"(({errors}) or vector(0)) / ({total}) * 100", "%"),
            "p95_ms": (
                f"histogram_quantile(0.95, sum(sum_over_time({histogram}[{window}]))) * 1000",
                "ms",
            ),
            "app_up": ('min({"delta.app.up",' + labels + "})", "state"),
            "db_up": ('min({"delta.db.up",' + labels + "})", "state"),
        }
        observed = self.query('max(timestamp({"delta.app.up",' + labels + "}))", end=end)
        observed_at = finite(observed[0]["value"][1]) if observed else None

        def one(item):
            name, (query, unit) = item
            try:
                points = self.query(query, start=start, end=end)
                values = points[0].get("values", []) if points else []
                series = []
                for at, value in values[-121:]:
                    try:
                        series.append({"at": stamp(at), "value": finite(value)})
                    except ValueError:
                        continue  # 0/0 means no requests, not a zero error rate.
                latest = self.query(query, end=end)
                try:
                    value = finite(latest[0]["value"][1]) if latest else None
                except ValueError:
                    value = None
                state = "stale" if value is not None and observed_at is None else None
                return name, metric(
                    value, at=observed_at, now=end, unit=unit, series=series, status=state
                )
            except Exception:
                return name, metric(
                    unit=unit, status="error", message="앱 지표를 조회하지 못했습니다."
                )

        with ThreadPoolExecutor(max_workers=5) as pool:
            return dict(pool.map(one, queries.items()))

    def logs(self, env, start, end):
        result = self.client("logs").get_log_events(
            logGroupName=f"/delta-platform/apps/{env['service_id']}/{env['id']}",
            logStreamName="application",
            startTime=int(start.timestamp() * 1000),
            endTime=int(end.timestamp() * 1000),
            limit=20,
            startFromHead=False,
        )
        items = []
        for event in result.get("events", []):
            try:
                data = json.loads(event["message"])
                severity = str(data.get("severity_text", data.get("SeverityText", ""))).upper()
                body = data.get("body", data.get("Body", ""))
                if severity not in ("WARN", "WARNING", "ERROR", "FATAL", "CRITICAL"):
                    continue
                if not isinstance(body, str):
                    continue
                trace_id = data.get("trace_id", data.get("TraceId"))
                if not isinstance(trace_id, str) or not re.fullmatch(r"[a-f0-9]{32}", trace_id):
                    trace_id = None
                items.append(
                    {
                        "timestamp": stamp(event["timestamp"] / 1000),
                        "severity": severity,
                        "message": safe_text(body),
                        "trace_id": trace_id,
                    }
                )
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        return {
            "status": "ok" if items else "no_data",
            "message": None,
            "items": sorted(items, key=lambda item: item["timestamp"], reverse=True)[:5],
        }

    def trace(self, env, start, end):
        client = self.client("xray")
        expr = f'annotation.delta_service_id = "{env["service_id"]}" AND annotation.delta_environment_id = "{env["id"]}"'
        summaries = client.get_trace_summaries(
            StartTime=start, EndTime=end, FilterExpression=expr, Sampling=False
        )
        candidates = summaries.get("TraceSummaries", [])
        if not candidates:
            return {
                "status": "no_data",
                "message": None,
                "id": None,
                "duration_ms": None,
                "spans": [],
            }
        summary = max(candidates, key=lambda value: value.get("StartTime", start))
        traces = client.batch_get_traces(TraceIds=[summary["Id"]]).get("Traces", [])
        spans = []

        def visit(segment, service, parent=None):
            if len(spans) >= 20:
                return
            begun, ended = finite(segment["start_time"]), finite(segment["end_time"])
            spans.append(
                {
                    "span_id": str(segment["id"])[:64],
                    "parent_id": parent,
                    "name": safe_text(segment.get("name", "request")),
                    "service": safe_text(service),
                    "started_at": stamp(begun),
                    "duration_ms": max(0, ended - begun) * 1000,
                }
            )
            for sub in segment.get("subsegments", []):
                visit(sub, service, segment["id"])

        for trace in traces[:1]:
            for segment in trace.get("Segments", [])[:20]:
                doc = json.loads(segment["Document"])
                annotations = doc.get("annotations", {})
                if annotations.get("delta_service_id") != str(env["service_id"]) or annotations.get(
                    "delta_environment_id"
                ) != str(env["id"]):
                    continue
                if not doc.get("end_time"):
                    continue
                visit(doc, doc.get("name", "app"), doc.get("parent_id"))
        return {
            "status": "ok" if spans else "no_data",
            "message": None,
            "id": summary["Id"] if spans else None,
            "duration_ms": finite(summary["Duration"]) * 1000 if spans else None,
            "spans": sorted(spans, key=lambda row: row["started_at"]),
        }

    def aws_infra(self, env, start, end):
        binding = env["binding"]
        ids = [i["id"] for i in binding["instances"]]
        if not ids:
            return {"status": "no_data", "message": "연결된 VM이 없습니다.", "instances": []}
        # Do not accidentally query an identically named resource in another account.
        account = self.client("sts").get_caller_identity()["Account"]
        if account != binding["account_id"]:
            raise ValueError("AWS resource account mismatch")
        instances = self.client("ec2", env["region"]).describe_instances(InstanceIds=ids)
        states = {
            i["InstanceId"]: i["State"]["Name"]
            for r in instances["Reservations"]
            for i in r["Instances"]
        }
        query, names = [], {}
        for index, instance_id in enumerate(ids):
            for name, namespace, metric_name in (
                ("cpu_percent", "AWS/EC2", "CPUUtilization"),
                ("memory_percent", "CWAgent", "mem_used_percent"),
            ):
                identifier = f"m{index}{'c' if name == 'cpu_percent' else 'm'}"
                names[identifier] = (instance_id, name)
                query.append(
                    {
                        "Id": identifier,
                        "MetricStat": {
                            "Metric": {
                                "Namespace": namespace,
                                "MetricName": metric_name,
                                "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                            },
                            "Period": 60,
                            "Stat": "Average",
                        },
                    }
                )
        result = self.client("cloudwatch", env["region"]).get_metric_data(
            MetricDataQueries=query,
            StartTime=start,
            EndTime=end,
            ScanBy="TimestampDescending",
            MaxDatapoints=800,
        )
        mapped = {}
        for row in result.get("MetricDataResults", []):
            key = names[row["Id"]]
            if row.get("StatusCode") != "Complete" or result.get("NextToken"):
                mapped[key] = metric(
                    unit="%", status="error", message="인프라 지표 응답이 불완전합니다."
                )
                continue
            pairs = sorted(zip(row.get("Timestamps", []), row.get("Values", []), strict=True))
            mapped[key] = metric(
                finite(pairs[-1][1]) if pairs else None,
                at=pairs[-1][0].timestamp() if pairs else None,
                now=end,
                unit="%",
                series=[{"at": t.isoformat(), "value": finite(v)} for t, v in pairs],
            )
        return {
            "status": "ok",
            "message": None,
            "instances": [
                {
                    "id": i["id"],
                    "name": i["name"],
                    "state": states.get(i["id"], "unknown"),
                    **{
                        name: mapped.get((i["id"], name), metric(unit="%"))
                        for name in ("cpu_percent", "memory_percent")
                    },
                }
                for i in binding["instances"]
            ],
        }


class DashboardReader:
    def __init__(self, region, *, telemetry=None, gcp=None, clock=None):
        self.telemetry = telemetry or CloudWatchTelemetry(region)
        self.gcp = gcp
        self.clock = clock or (lambda: datetime.now(UTC))
        self.cache, self.cache_lock = {}, threading.Lock()
        self.locks = [threading.Lock() for _ in range(16)]

    def read(self, env, minutes):
        EnvironmentInput.model_validate(
            {
                **{k: env[k] for k in ("name", "cloud", "region", "version")},
                # Stored lifecycle evidence is not part of the resource identity input.
                **{k: v for k, v in env["binding"].items() if k not in STORED_METADATA_FIELDS},
            }
        )
        key = (str(env["service_id"]), str(env["id"]), str(env["updated_at"]), minutes)
        # One request per binding at a time. Small read cache bounds provider costs.
        with self.locks[hash(key) % len(self.locks)]:
            with self.cache_lock:
                cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 20:
                return copy.deepcopy(cached[1])
            end = self.clock()
            start = end - timedelta(minutes=minutes)
            empty_metrics = {
                name: metric(
                    unit=unit, status="error", message="관측 데이터를 조회하지 못했습니다."
                )
                for name, unit in (
                    ("requests", "requests"),
                    ("error_rate", "%"),
                    ("p95_ms", "ms"),
                    ("app_up", "state"),
                    ("db_up", "state"),
                )
            }

            def infrastructure():
                if env["cloud"] == "aws":
                    return self.telemetry.aws_infra(env, start, end)
                if env["cloud"] == "gcp":
                    if self.gcp is None:
                        from delta_platform.adapters.gcp_observability import GcpInfrastructure

                        self.gcp = GcpInfrastructure()
                    return self.gcp.read(env, start, end)
                from delta_platform.adapters.onprem_observability import OnpremInfrastructure

                return OnpremInfrastructure(self.telemetry).read(env, start, end)

            jobs = {
                "metrics": (lambda: self.telemetry.metrics(env, start, end), empty_metrics),
                "infra": (
                    infrastructure,
                    {
                        "status": "error",
                        "message": "VM 지표를 조회하지 못했습니다.",
                        "instances": [],
                    },
                ),
                "logs": (
                    lambda: self.telemetry.logs(env, start, end),
                    {"status": "error", "message": "로그를 조회하지 못했습니다.", "items": []},
                ),
                "trace": (
                    lambda: self.telemetry.trace(env, start, end),
                    {
                        "status": "error",
                        "message": "Trace를 조회하지 못했습니다.",
                        "id": None,
                        "duration_ms": None,
                        "spans": [],
                    },
                ),
            }
            result = {
                "service_id": str(env["service_id"]),
                "environment": {k: str(env[k]) if k == "id" else env[k] for k in FIELDS},
                "observed_at": end.isoformat(),
                "window": {"minutes": minutes, "start": start.isoformat(), "end": end.isoformat()},
                "migration": None,
            }
            with ThreadPoolExecutor(max_workers=4) as pool:
                pending = {name: pool.submit(job) for name, (job, _) in jobs.items()}
                for name, future in pending.items():
                    try:
                        result[name] = future.result()
                    except Exception:
                        result[name] = jobs[name][1]
            with self.cache_lock:
                if len(self.cache) >= 64:
                    self.cache.clear()
                self.cache[key] = (time.monotonic(), result)
            return copy.deepcopy(result)
