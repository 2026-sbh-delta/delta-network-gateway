"""SSM에서 읽은 설정을 VM의 root 전용 파일로 저장한다. 값은 출력하지 않는다."""

import json
import os
import sys
from pathlib import Path

runtime = json.load(sys.stdin)
required = {
    "PLATFORM_DATABASE_URL",
    "PLATFORM_LOGIN_ID",
    "PLATFORM_PASSWORD_HASH",
    "PLATFORM_PUBLIC_ORIGIN",
    "PLATFORM_LOCAL_MODE",
}
assert required <= runtime["app_env"].keys()
assert runtime["app_env"]["PLATFORM_LOCAL_MODE"] == "false"
assert runtime["app_env"]["PLATFORM_PUBLIC_ORIGIN"].startswith("https://")
config_dir = Path("/etc/delta-platform")
config_dir.mkdir(mode=0o700, exist_ok=True)


def write(name, data):
    if any("\n" in str(v) or "\r" in str(v) for v in data.values()):
        raise ValueError("설정 값에 줄바꿈이 있습니다.")
    path = config_dir / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        for key, value in data.items():
            handle.write(f"{key}={value}\n")


write("app.env", runtime["app_env"])
write(
    "deploy.env",
    {
        "PLATFORM_DB_PASSWORD": runtime["db_password"],
        "PLATFORM_IMAGE": sys.argv[1],
        "POSTGRES_IMAGE": sys.argv[2],
    },
)
write(
    "backup.env",
    {"PLATFORM_BACKUP_BUCKET": sys.argv[3], "AWS_DEFAULT_REGION": sys.argv[4]},
)
