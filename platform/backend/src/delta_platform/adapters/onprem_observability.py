"""Read registered hostmetrics through the existing telemetry store, never SSH."""

from delta_platform.adapters.observability import finite, metric, stamp


class OnpremInfrastructure:
    def __init__(self, telemetry):
        self.telemetry = telemetry

    def read(self, env, start, end):
        instances = env["binding"]["instances"]
        if not instances:
            return {
                "status": "no_data",
                "message": "연결된 온프레미스 VM이 없습니다.",
                "instances": [],
            }
        rows = []
        for instance in instances:
            # These identifiers have already passed EnvironmentInput validation.
            labels = (
                f'"@resource.delta_service_id"="{env["service_id"]}",'
                f'"@resource.delta_environment_id"="{env["id"]}",'
                f'"@resource.host.id"="{instance["id"]}"'
            )
            cpu = '{"system.cpu.utilization",' + labels + ',"state"="idle"}'
            memory = '{"system.memory.utilization",' + labels + ',"state"="used"}'
            disk = '{"system.filesystem.utilization",' + labels + ',"mountpoint"="/"}'
            row = {"id": instance["id"], "name": instance["name"]}
            for name, raw, expression in (
                ("cpu_percent", cpu, f"(1 - avg({cpu})) * 100"),
                ("memory_percent", memory, f"max({memory}) * 100"),
                ("disk_percent", disk, f"max({disk}) * 100"),
            ):
                try:
                    observed = self.telemetry.query(f"min(timestamp({raw}))", end=end)
                    latest = self.telemetry.query(expression, end=end)
                    points = self.telemetry.query(expression, start=start, end=end)
                    if any(len(items) > 1 for items in (observed, latest, points)):
                        raise ValueError("Ambiguous host observation")
                    at = finite(observed[0]["value"][1]) if observed else None
                    if at is not None and at > end.timestamp() + 60:
                        raise ValueError("Host sample is in the future")

                    def percentage(value):
                        number = finite(value)
                        if not 0 <= number <= 100:
                            raise ValueError("Host utilization out of range")
                        return number

                    value = percentage(latest[0]["value"][1]) if latest else None
                    series = [
                        {"at": stamp(t), "value": percentage(v)}
                        for t, v in (points[0].get("values", [])[-121:] if points else [])
                    ]
                    row[name] = metric(
                        value,
                        at=at,
                        now=end,
                        unit="%",
                        series=series,
                        status="stale" if value is not None and at is None else None,
                    )
                except Exception:
                    row[name] = metric(
                        unit="%", status="error", message="온프레미스 지표를 조회하지 못했습니다."
                    )
            states = [row[k]["status"] for k in ("cpu_percent", "memory_percent", "disk_percent")]
            row["state"] = (
                "reporting" if "ok" in states else "stale" if "stale" in states else "unknown"
            )
            rows.append(row)
        states = [
            row[k]["status"]
            for row in rows
            for k in ("cpu_percent", "memory_percent", "disk_percent")
        ]
        status = next((s for s in ("error", "stale", "no_data") if s in states), "ok")
        return {
            "status": status,
            "message": "수신 상태는 Collector의 보고 여부이며 VM 전원 상태와 다릅니다.",
            "instances": rows,
        }
