"""Read only registered GCE instances with ADC/workload credentials."""

from datetime import datetime

import google.auth
import httpx
from google.auth.transport.requests import Request

from delta_platform.adapters.observability import finite, metric


class GcpInfrastructure:
    def __init__(self, *, credentials=None, transport=None):
        self.credentials = credentials
        self.transport = transport

    def read(self, env, start, end):
        if not env["binding"]["instances"]:
            return {"status": "no_data", "message": "연결된 VM이 없습니다.", "instances": []}
        if self.credentials is None:
            self.credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform.read-only"]
            )
        if not self.credentials.valid:
            self.credentials.refresh(Request())
        project = env["binding"]["gcp_project"]
        instances = []
        with httpx.Client(
            timeout=5,
            transport=self.transport,
            headers={"Authorization": f"Bearer {self.credentials.token}"},
        ) as client:
            for instance in env["binding"]["instances"]:
                url = f"https://compute.googleapis.com/compute/v1/projects/{project}/zones/{instance['zone']}/instances/{instance['name']}"
                response = client.get(url)
                response.raise_for_status()
                vm = response.json()
                if str(vm["id"]) != instance["id"]:
                    raise ValueError("Recreated GCP instance is not the registered target")
                row = {
                    "id": instance["id"],
                    "name": instance["name"],
                    "state": vm["status"].lower(),
                }
                for name, metric_type, scale in (
                    ("cpu_percent", "compute.googleapis.com/instance/cpu/utilization", 100),
                    ("memory_percent", "agent.googleapis.com/memory/percent_used", 1),
                ):
                    try:
                        expression = f'metric.type = "{metric_type}" AND resource.type = "gce_instance" AND resource.labels.instance_id = "{instance["id"]}"'
                        if name == "memory_percent":
                            expression += ' AND metric.labels.state = "used"'
                        response = client.get(
                            f"https://monitoring.googleapis.com/v3/projects/{project}/timeSeries",
                            params={
                                "filter": expression,
                                "interval.startTime": start.isoformat(),
                                "interval.endTime": end.isoformat(),
                                "aggregation.alignmentPeriod": "60s",
                                "aggregation.perSeriesAligner": "ALIGN_MEAN",
                                "view": "FULL",
                                "pageSize": 200,
                            },
                        )
                        response.raise_for_status()
                        body = response.json()
                        if body.get("nextPageToken"):
                            raise ValueError("Incomplete GCP metric response")
                        series = body.get("timeSeries", [])
                        if len(series) > 1:
                            raise ValueError("Ambiguous instance metric")
                        pairs = []
                        for point in series[0].get("points", []) if series else []:
                            at = datetime.fromisoformat(
                                point["interval"]["endTime"].replace("Z", "+00:00")
                            )
                            pairs.append((at, finite(point["value"]["doubleValue"]) * scale))
                        pairs.sort()
                        row[name] = metric(
                            pairs[-1][1] if pairs else None,
                            at=pairs[-1][0].timestamp() if pairs else None,
                            now=end,
                            unit="%",
                            series=[{"at": at.isoformat(), "value": value} for at, value in pairs],
                        )
                    except Exception:
                        row[name] = metric(
                            unit="%", status="error", message="GCP 지표를 조회하지 못했습니다."
                        )
                instances.append(row)
        return {"status": "ok", "message": None, "instances": instances}
