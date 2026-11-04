"""Platform-owned runtime injection. No customer source or image is rewritten."""

import copy
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit

NODE_IMAGE = (
    "ghcr.io/open-telemetry/opentelemetry-operator/autoinstrumentation-nodejs"
    "@sha256:576e2b00bdf9a6040a7e3a497dd5eea4e624f60dcfd28871d9190752a0f18955"
)
MOUNT = "/opt/delta/otel/nodejs"
VOLUME = "delta-otel-nodejs"
INIT = "delta-otel-init"
AGENT = MOUNT + "/node_modules/@opentelemetry"
NODE_OPTIONS = (
    f"--require={AGENT}/auto-instrumentations-node/build/src/register.js "
    f"--experimental-loader={AGENT}/instrumentation/hook.mjs"
)


def endpoint(value):
    url = urlsplit(value)
    if (
        url.scheme not in ("http", "https")
        or url.username
        or url.password
        or url.path not in ("", "/")
        or url.query
        or url.fragment
        or not url.hostname
        or url.port not in (4318,)
    ):
        raise ValueError("Registered private OTLP origin on port 4318 required")
    try:
        address = ipaddress.ip_address(url.hostname)
    except ValueError:
        if not re.fullmatch(
            r"[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9-]*\.svc\.cluster\.local",
            url.hostname,
        ):
            raise ValueError(
                "Private collector IP or Kubernetes Service required"
            ) from None
    else:
        if (
            not (address.is_private or address.is_loopback)
            or address.is_unspecified
            or address.is_link_local
        ):
            raise ValueError("Private collector address required")
    return value.rstrip("/")


def environment(target, name, version, *, existing=None, metrics=True, root=MOUNT):
    """A trace-producing library is loaded inside the app, not in the Collector."""
    endpoint(target)
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", name) or not re.fullmatch(
        r"[a-f0-9]{40}", version
    ):
        raise ValueError("Registered service name and full commit required")
    existing = existing or {}
    if not isinstance(existing.get("NODE_OPTIONS", ""), str):
        raise TypeError("NODE_OPTIONS must be an explicit string")
    if (
        any(k.startswith("OTEL_") for k in existing)
        or "opentelemetry" in existing.get("NODE_OPTIONS", "")
        or MOUNT in existing.get("NODE_OPTIONS", "")
    ):
        raise ValueError(
            "Existing instrumentation must be disabled before automatic injection"
        )
    return {
        "NODE_OPTIONS": " ".join(
            filter(
                None, [existing.get("NODE_OPTIONS"), NODE_OPTIONS.replace(MOUNT, root)]
            )
        ),
        "OTEL_SERVICE_NAME": name,
        "OTEL_RESOURCE_ATTRIBUTES": "service.version=" + version,
        "OTEL_NODE_RESOURCE_DETECTORS": "env",
        "OTEL_NODE_ENABLED_INSTRUMENTATIONS": "http",
        "OTEL_SEMCONV_STABILITY_OPT_IN": "http",
        "OTEL_EXPORTER_OTLP_ENDPOINT": target,
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_TIMEOUT": "2000",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_TRACES_SAMPLER": "parentbased_always_on",
        "OTEL_PROPAGATORS": "tracecontext,baggage",
        "OTEL_BSP_MAX_QUEUE_SIZE": "256",
        "OTEL_BSP_MAX_EXPORT_BATCH_SIZE": "64",
        "OTEL_BSP_SCHEDULE_DELAY": "1000",
        "OTEL_BSP_EXPORT_TIMEOUT": "2000",
        "OTEL_METRICS_EXPORTER": "otlp" if metrics else "none",
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "DELTA",
        "OTEL_METRIC_EXPORT_INTERVAL": "60000",
        "OTEL_LOGS_EXPORTER": "none",
        "OTEL_LOG_LEVEL": "error",
    }


def inject(spec, target, name, version, *, metrics=True):
    """Inject one trusted Node.js profile into a single application Pod template."""
    result = copy.deepcopy(spec)
    if len(result.get("containers", [])) != 1:
        raise ValueError("Explicit single application container required")
    app = result["containers"][0]
    if app.get("envFrom"):
        raise ValueError(
            "Explicit environment required to detect duplicate instrumentation"
        )
    if any(v.get("mountPath") == MOUNT for v in app.get("volumeMounts", [])):
        raise ValueError("Instrumentation mount already exists")
    if any(v.get("name") == VOLUME for v in result.get("volumes", [])) or any(
        c.get("name") == INIT for c in result.get("initContainers", [])
    ):
        raise ValueError(
            "Instrumentation already present; render from the base template"
        )
    values = {}
    for item in app.get("env", []):
        if item["name"] == "NODE_OPTIONS" or item["name"].startswith("OTEL_"):
            if "value" not in item:
                raise ValueError("Hidden instrumentation options cannot be combined")
            values[item["name"]] = item["value"]
    updates = environment(target, name, version, existing=values, metrics=metrics)
    app["env"] = [e for e in app.get("env", []) if e["name"] not in updates] + [
        {"name": k, "value": v} for k, v in updates.items()
    ]
    result.setdefault("volumes", []).append(
        {"name": VOLUME, "emptyDir": {"sizeLimit": "192Mi"}}
    )
    app.setdefault("volumeMounts", []).append(
        {"name": VOLUME, "mountPath": MOUNT, "readOnly": True}
    )
    result.setdefault("initContainers", []).append(
        {
            "name": INIT,
            "image": NODE_IMAGE,
            "imagePullPolicy": "IfNotPresent",
            "command": ["cp", "-R", "/autoinstrumentation/.", MOUNT + "/"],
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 10001,
                "runAsGroup": 10001,
                "allowPrivilegeEscalation": False,
                "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]},
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "resources": {
                "requests": {
                    "cpu": "20m",
                    "memory": "32Mi",
                    "ephemeral-storage": "192Mi",
                },
                "limits": {
                    "cpu": "200m",
                    "memory": "128Mi",
                    "ephemeral-storage": "256Mi",
                },
            },
            "volumeMounts": [{"name": VOLUME, "mountPath": MOUNT}],
        }
    )
    result.setdefault("securityContext", {}).setdefault("fsGroup", 10001)
    return result


def fingerprint(spec):
    """Bind the injection configuration separately from the immutable app digest."""
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
