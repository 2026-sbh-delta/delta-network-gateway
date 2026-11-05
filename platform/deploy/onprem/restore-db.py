"""Restore a checksum-approved dump into the dedicated EMPTY notes database only."""

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main():
    p = argparse.ArgumentParser(
        description="전용 notes DB의 최초 복원; 기존 데이터 덮어쓰기 금지"
    )
    p.add_argument("--host", required=True)
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--dump", type=Path, required=True)
    p.add_argument("--approved-sha256", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]+", a.host):
        raise ValueError("Invalid host")
    data = a.dump.read_bytes()
    if len(data) > 100_000_000 or not data.startswith(b"PGDMP"):
        raise ValueError("Small custom-format PostgreSQL dump required")
    digest = hashlib.sha256(data).hexdigest()
    if digest != a.approved_sha256:
        raise ValueError("Approved dump changed")
    ssh = [
        "ssh",
        "-i",
        str(a.key.expanduser()),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        a.host,
    ]

    def remote(command, body=None):
        r = subprocess.run(
            ssh + ["sudo -n k3s kubectl " + command],
            input=body,
            check=False,
            capture_output=True,
            timeout=180,
        )
        if r.returncode:
            raise RuntimeError("Database operation failed; inspect dedicated database")
        return r.stdout

    prefix = "exec -i -n delta-notes database-0 -- "

    def sql(text):
        return (
            remote(
                prefix + "psql -U postgres -d notes -At -v ON_ERROR_STOP=1",
                text.encode(),
            )
            .decode()
            .strip()
        )

    if sql("SELECT count(*) FROM pg_tables WHERE schemaname='public';") != "0":
        raise ValueError("Target is not empty; existing data preserved")
    deployments = json.loads(
        remote("get deployment backend -n delta-notes --ignore-not-found -o json")
        or b"{}"
    )
    if deployments.get("spec", {}).get("replicas", 0) != 0:
        raise ValueError("Stop target backend writes before restore")
    if not sql("SHOW server_version;").startswith("16."):
        raise ValueError("Expected PostgreSQL 16 target")
    remote(
        prefix
        + "pg_restore --exit-on-error --single-transaction --no-owner --no-acl -U postgres -d notes",
        data,
    )
    sql("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO notes;")
    count = int(sql("SELECT count(*) FROM notes;"))
    a.output.write_text(
        json.dumps(
            {
                "dump_sha256": digest,
                "restored_at": datetime.now(UTC).isoformat(),
                "target": "delta-notes/database-0/notes",
                "rows": count,
                "status": "restored",
                "application_read_write_check": "pending",
                "source_write_freeze": "must_be_recorded_by_source_operator",
            },
            indent=2,
        )
    )
    print("전용 빈 DB 복원 완료. 앱 조회·새 쓰기는 별도 확인해야 합니다.")


if __name__ == "__main__":
    main()
