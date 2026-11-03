"""배포 후 실제 HTTPS 준비 상태를 확인한다. 로그인 정보는 CI에 전달하지 않는다."""

import json
import sys
import time
import urllib.request
from pathlib import Path

url = json.loads(Path(sys.argv[1]).read_text())["platform_url"]["value"]
for attempt in range(12):
    try:
        with urllib.request.urlopen(url + "/health/ready", timeout=10) as response:
            if response.status == 200:
                print("HTTPS readiness passed:", url)
                break
    except OSError:
        if attempt == 11:
            raise
    time.sleep(5)
else:
    raise SystemExit("HTTPS readiness failed")
