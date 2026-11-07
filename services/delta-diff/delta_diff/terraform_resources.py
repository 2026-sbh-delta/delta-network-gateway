"""Only non-secret LB/PostgreSQL attributes are copied from Terraform state."""


def resources_of(resources, kind):
    return [
        r["values"] for r in resources if r["type"] == kind and r.get("mode") != "data"
    ]


def extract_resources(resources, provider):
    lbs, databases = [], []
    if provider == "aws":
        listeners = resources_of(resources, "aws_lb_listener") + resources_of(
            resources, "aws_alb_listener"
        )
        for lb in resources_of(resources, "aws_lb") + resources_of(
            resources, "aws_alb"
        ):
            attached = [
                x
                for x in listeners
                if x.get("load_balancer_arn") == lb.get("arn") and lb.get("arn")
            ]
            protocols = sorted({x["protocol"] for x in attached if x.get("protocol")})
            lbs.append(
                {
                    "name": lb.get("name") or "unnamed",
                    "kind": lb.get("load_balancer_type", "unknown"),
                    "protocols": protocols,
                    "ports": [x["port"] for x in attached if x.get("port")],
                    "internal": lb.get("internal"),
                    "tls": bool(set(protocols) & {"HTTPS", "TLS"})
                    if protocols
                    else None,
                    "origin": "terraform",
                }
            )
        for lb in resources_of(resources, "aws_elb"):
            attached = lb.get("listener") or []
            protocols = sorted(
                {
                    (
                        "TLS"
                        if x["lb_protocol"].upper() == "SSL"
                        else x["lb_protocol"].upper()
                    )
                    for x in attached
                    if x.get("lb_protocol")
                }
            )
            lbs.append(
                {
                    "name": lb.get("name") or "unnamed",
                    "kind": "classic",
                    "protocols": protocols,
                    "ports": [x["lb_port"] for x in attached if x.get("lb_port")],
                    "internal": lb.get("internal"),
                    "tls": bool(set(protocols) & {"HTTPS", "TLS", "SSL"})
                    if protocols
                    else None,
                    "origin": "terraform",
                }
            )
        for db in resources_of(resources, "aws_db_instance") + resources_of(
            resources, "aws_rds_cluster"
        ):
            engine = db.get("engine")
            databases.append(
                {
                    "name": db.get("identifier")
                    or db.get("cluster_identifier")
                    or "unnamed",
                    "engine": "postgresql" if engine == "postgres" else engine,
                    "version": db.get("engine_version_actual")
                    or db.get("engine_version"),
                    "storage_gib": db.get("allocated_storage") or None,
                    "high_availability": db.get("multi_az"),
                    "origin": "terraform",
                }
            )
    else:
        for db in resources_of(resources, "google_sql_database_instance"):
            raw = db.get("database_version", "")
            settings = db.get("settings") or [{}]
            settings = settings[0] if isinstance(settings, list) else settings
            databases.append(
                {
                    "name": db.get("name") or "unnamed",
                    "engine": "postgresql"
                    if raw.startswith("POSTGRES_")
                    else raw or None,
                    "version": raw.removeprefix("POSTGRES_").replace("_", ".")
                    if raw.startswith("POSTGRES_")
                    else None,
                    "storage_gib": settings.get("disk_size"),
                    "high_availability": settings.get("availability_type") == "REGIONAL"
                    if settings.get("availability_type")
                    else None,
                    "origin": "terraform",
                }
            )
        proxies = {
            x.get("self_link"): (protocol, kind)
            for type_, protocol, kind in (
                ("google_compute_target_http_proxy", "HTTP", "application"),
                ("google_compute_target_https_proxy", "HTTPS", "application"),
                ("google_compute_region_target_http_proxy", "HTTP", "application"),
                ("google_compute_region_target_https_proxy", "HTTPS", "application"),
                ("google_compute_target_tcp_proxy", "TCP", "network"),
                ("google_compute_target_ssl_proxy", "TLS", "network"),
            )
            for x in resources_of(resources, type_)
        }
        for lb in resources_of(
            resources, "google_compute_forwarding_rule"
        ) + resources_of(resources, "google_compute_global_forwarding_rule"):
            protocol, kind = proxies.get(
                lb.get("target"), (lb.get("ip_protocol"), "network")
            )
            ports = lb.get("ports") or []
            port_range = str(lb.get("port_range") or "")
            if not ports and port_range.isdigit():
                ports = [int(port_range)]
            elif not ports and "-" in port_range:
                low, high = port_range.split("-", 1)
                if low.isdigit() and high.isdigit() and int(high) - int(low) < 100:
                    ports = list(range(int(low), int(high) + 1))
            scheme = lb.get("load_balancing_scheme")
            lbs.append(
                {
                    "name": lb.get("name") or "unnamed",
                    "kind": kind,
                    "protocols": [protocol] if protocol else [],
                    "ports": [int(p) for p in ports],
                    "internal": scheme.startswith("INTERNAL") if scheme else None,
                    "tls": protocol in ("HTTPS", "TLS") if protocol else None,
                    "origin": "terraform",
                }
            )
    return {"load_balancers": lbs, "databases": databases}


def extract_gcp(resources):
    subnets = [
        {
            "name": x.get("name") or "unnamed",
            "cidr": x["ip_cidr_range"],
            "tier": None,
            "origin": "terraform",
        }
        for x in resources_of(resources, "google_compute_subnetwork")
    ]
    servers = []
    for vm in resources_of(resources, "google_compute_instance"):
        accounts = vm.get("service_account")
        servers.append(
            {
                "name": vm.get("name") or "unnamed",
                "instance_type": vm.get("machine_type", "").split("/")[-1] or None,
                "service_account": None if accounts is None else bool(accounts),
                "origin": "terraform",
            }
        )
    return {
        "subnets": subnets,
        "servers": servers,
        "security": {"origin": "terraform"},
        **extract_resources(resources, "gcp"),
    }
