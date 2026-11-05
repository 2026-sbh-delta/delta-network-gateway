"""Jenkins shell 시험 전용 Docker 대역. 네트워크/daemon에 연결하지 않는다."""

import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["FIXTURE_DOCKER_CALLS"], "a") as output:
    output.write(json.dumps(args) + "\n")
if args[:2] == ["buildx", "build"]:
    service = Path(args[-1]).name
    if os.environ.get("FIXTURE_FAIL_SERVICE") == service:
        sys.exit(23)
    manifest = "sha256:" + {"frontend": "a", "backend": "b"}.get(service, "9") * 64
    metadata = args[args.index("--metadata-file") + 1]
    Path(metadata).write_text(json.dumps({"containerimage.digest": manifest,
                                        "containerimage.config.digest": "sha256:" + "e" * 64}))
elif args[:2] == ["buildx", "imagetools"] or args[0] in ("manifest", "tag", "push", "pull", "login", "logout"):
    if args[0] == "login":
        sys.stdin.read()
else:
    raise SystemExit("허용하지 않은 빌드 fixture 명령: " + repr(args))
