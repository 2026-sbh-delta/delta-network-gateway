# compose-delivery-20261106 — 독립 test-author 인계

2026-11-06. 승인된 명세에 대한 테스트만 작성했다. production 구현과 이 파일은 별개다.
메인 담당의 분할 요청에 따라 이 시험 범위를 동결한다.

## 실행 명령

저장소 `본선/delta-network-gateway`에서 실행한다. 기존 delta-staging 가상환경의
pytest와 cryptography를 사용하며 새 패키지를 설치하지 않는다.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 services/delta-staging/.venv/bin/python -m pytest -q -c /dev/null --confcutdir=deploy/platform/jenkins/tests deploy/platform/jenkins/tests --tb=line -p no:cacheprovider
```

`-c /dev/null`과 `--confcutdir`는 루트의 예선 harness pytest 설정과 conftest가
본선 검증에 개입하지 않도록 한다. 최초 환경 검증에서 이 충돌을 확인하고 수정했다.

최종 실행 결과: **11 FAILED / 1 PASSED / 15 SKIPPED / collection error 0**.
Python 8개 파일의 AST 구문 검사도 통과했다.
11 FAILED는 기존 Jenkins shell/JCasC/staging main의 행동 부재이고,
15 SKIPPED는 신규 release 모듈 구현 대기로 RED 증거에 합산하지 않는다.
GREEN 판정 시에는 이 skip 경로를 제거하고 15개 전부 실행해 **zero skip**을 확인해야 한다.

## 고정된 기존 경계

- `Jenkinsfile.build`의 실제 입력 검사·빌드 `sh` 본문을 임시 디렉터리에서 실행한다.
  Groovy triple-single-quoted 문자열의 역슬래시 escape를 복원한다.
  체크아웃 stage는 실행하지 않는다. `ar-token.py`는 fixture 문자열로 대체하고
  Docker CLI는 fixture가 명령만 기록하고 metadata JSON을 만든다.
- `frontend/`, `backend/` 각각 linux/amd64, 기존 provenance/SBOM 옵션 유지,
  두 manifest digest, schema2, canonical bundle hash, FE legacy alias를 검사한다.
  BE 실패 시험은 먼저 BE 빌드 명령 도달을 확인해 입력 오류를 성공적인 실패 주입으로 오인하지 않는다.
- `staging_deploy.main()`의 `urllib.request.Request`를 가로채 실제 직렬화된 HTTP body를 검사한다.
  `IMAGES_JSON`, `BUNDLE_SHA256` 전달, `IMAGE` FE legacy alias 호환,
  BE 누락/태그 입력의 배포 이전 거부, 관측 결과 필드·구조화 reset 보존을 검사한다.
- JCasC startup에 `delta-release`, 신뢰된 `/opt/delta/Jenkinsfile.release`,
  `delta-deploy` 실행기, 결과 archive, 컨트롤러 이미지의 release/common-host 파일 포함을 검사한다.
  release Jenkins 파라미터는 `DEPLOYMENT_ID` 또는 `RUN_ID` 별칭,
  `COMMIT`, `TARGET_ID`, `IMAGES_JSON`, `BUNDLE_SHA256`, `APPROVAL`이다.

## 신규 승인 API — 구현 담당이 연결할 계약

경로: `controller/release_deploy.py`

```python
verify_and_reserve(request: dict, token: dict, public_key_pem: bytes,
                   db_path: pathlib.Path, *, now: int) -> dict
```

입력 `request`:

- `deployment_id`: 운영 실행의 멱등 키.
- `commit_sha`: full40 SHA. `bundle.commit`과 같아야 한다.
- `bundle`: schema2 `result.json` 전체. `images`와 `image_digests`는 FE/BE 둘 다 필수.
  `bundle_sha256`는 `{schema:2, commit, platform:"linux/amd64", image_digests}`의
  키 정렬·공백 없는 UTF-8 JSON SHA256이다. registry 주소는 해시에서 제외한다.
  각 `images` suffix는 해당 서비스 `image_digests`와 같아야 한다.
- `target`: 서버의 신뢰된 단일 대상 인벤토리. 요청자가 호스트/DB/비밀 참조를
  덮어쓰는 API를 의미하지 않는다. fixture는 cloud/environment/target_id와
  비밀 없는 VM·Compose·로그·DB18 설정만 포함한다.
- `config_sha256`: 위 공개 `target` 설정의 canonical JSON SHA256.

`APPROVAL`은 다음 envelope의 JSON 문자열이다. API 함수에는 파싱한 dict를 준다.

```json
{"claims":{"deployment_id":"...","commit_sha":"...","bundle_sha256":"...","target_id":"...","environment":"production","config_sha256":"...","exp":1800000600,"nonce":"..."},"signature":"base64url-ed25519-no-padding"}
```

서명 대상은 claims의 키 정렬·공백 없는 UTF-8 JSON bytes다.
서명은 Ed25519이며 fixture는 매 시험 새 키를 메모리에서 생성한다.
공개 키는 PEM SubjectPublicKeyInfo bytes다. `exp <= now`는 거부한다.

최초 유효 접수는 `{"deployment_id": "...", "state": "reserved"}`를 반환한다.
동일 deployment_id·동일 요청 재접수는 기록을 추가하지 않고 기존 예약을 반환한다.
같은 nonce의 다른 deployment_id, 같은 deployment_id의 BE/묶음 변경,
대상·환경·commit·묶음·설정 해시 불일치, 서명 위조·만료는 `ValueError`로 거부한다.
SQLite 테이블명은 고정하지 않지만 실제 파일의 integrity와 예약 보존을 검사한다.
nonce와 deployment_id는 같은 transaction에서 예약해 동시 요청도 하나만 성공해야 한다.

신규 모듈 부재는 15개 시험을 명시적 SKIP으로 표시한다. 모듈이 생기면 동작 시험이
자동 활성화된다. 파일 없음/import 실패를 첫 RED 근거로 사용하지 않았다.

## 미작성·미검증 경계

common host의 subprocess fake 실행 시험은 작성하지 않았다. 따라서 `pull → up --no-build --wait`,
실제 두 RepoDigests/config ID 연결, DB18/앱 health, 직전 묶음·원래 중지 상태 복귀,
실제 flock·unknown 영속 상태의 다른 deployment_id 차단, gcplogs/awslogs 분리는
이번 시험의 검증 범위가 아니다. 실 Docker·SSH·클라우드·startup Jenkins 실행도 미수행이다.
기존에 제안한 `compose_host.deploy/query/render_compose`는 고정된 실행 시험 API가 아니며
구현 담당이 결정할 사항이다. fixture의 target 설정이 이 동작을 검증한다는 뜻은 아니다.

새 모듈의 승인 시험도 현재 모두 SKIP이므로 Ed25519/SQLite 동작을 검증 완료했다고 보고하지 않는다.
SAST·DAST·onboarding-aws·앱·production 코드는 수정하지 않았다.

호스트 실제 검증은 메인 담당이 별도 test-author에게 후속 배정한다.
외부 검토 보완 사항도 미검증으로 인계한다: SSH와 분리된 배포 세션의 flock 유지와
Jenkins polling(systemd-run/setsid), 실제 PG18 및 필수 스키마 preflight,
legacy local image ID 원상복구와 prune 금지, SQL Proxy와 BE 네트워크 네임스페이스 공유,
registry 단기 토큰의 password-stdin 전달, gcplogs non-blocking 설정,
ready 상태의 실제 영속 등록과 두 이미지 관측 JSON 증거.

## Host API — 독립 호스트 시험 인계 (2026-11-06)

이 절은 위의 common-host 미작성 기록 이후 추가한 계약이다. 기존 승인·빌드·staging
시험과 공용 conftest는 변경하지 않는다. trace=`compose-delivery-20261106`,
test-author, `tddmode=IMPLEMENTATION_ONLY`, `pre_gate=REVISED`.

```python
deploy(request: dict, state_dir: Path, *, runner, probe) -> dict
query(deployment_id: str, state_dir: Path) -> dict
render_compose(target: dict, bundle: dict) -> dict
main(argv: list[str], *, runner, probe) -> dict | int | None
```

request는 기존 release_request와 같은 키다. host_request만 별도로 확장하며
합성 `db_host/db_port/db_user/db_secret_file`, 기업 로그 `log_group/log_prefix`,
`sql_proxy_image/sql_connection_name`을 추가하고 config_sha256을 다시 계산한다.
db_secret_file은 tmp_path의 공개 합성 문자열만 담는다. 기존 비밀·환경 파일은 읽지 않는다.
이 입력은 승인 검증을 마친 신뢰된 요청이며 host가 승인 토큰을 재발급하지 않는다.

deploy는 최종 상태 dict를 반환하고 SQLite에 보존한다. 성공은 `state=ready`,
`deployment_id/target_id/commit_sha/bundle_sha256/image_digests`를 포함한다.
query는 같은 결과를 재구성하며 Docker·DB 변경 없이 호출할 수 있다.
동일 ID·동일 입력은 기존 결과 재사용, 같은 ID의 다른 입력은 변경 전에 ValueError다.
실패 후 원상복귀 성공/원래 중지 상태 보존은 `failed`, 복귀 실패는 `rollback_failed`,
명령 응답 유실(TimeoutExpired)은 `unknown`이다. unknown 상태를 임의로 failed로
바꾸거나 자동 복귀로 확정하지 않는다. 동일 대상의 unresolved running/unknown 중
다른 ID는 `{"state":"blocked","blocked_by":"<기존 deployment_id>"}`를 반환한다.
파일명/SQLite 스키마는 자유이며 잠금 파일 확장자는 `.lock`이다. 실제 flock를 사용한다.

render_compose는 Compose document dict다. 실제 명령에 전달하는 Compose 파일은
JSON으로 직렬화한다(JSON은 Compose가 받는 YAML의 부분집합). 앱 두 서비스 이름은
frontend/backend, environment는 dict, production build 없음/BE ports 없음이다.
APP_VERSION은 bundle.commit이다. GCP proxy가 있으면 앱 묶음의 세 번째 이미지로
취급하지 않고 인프라 보조 서비스로 둔다.

runner(argv, **kwargs)는 CompletedProcess의 stdout/stderr를 문자열로 반환한다.
시험은 docker compose config/ps/pull/up/stop/down, docker container inspect 또는
docker inspect, docker image inspect의 표준 JSON을 합성한다. inspect Go template은
쓰지 않는다. 실제 `.Image` config ID를 image inspect에 넣어 RepoDigests를 확인한다.
Docker 외 도구와 비밀 취득은 fake로 주입하거나 호스트 전용 fixture 경계에서 처리한다.
시험 fixture에 없는 실제 cloud/SSH/secret 명령을 우회 실행하면 안 된다.

probe(target)는 **신뢰된 호스트 진단 어댑터**가 만든 다음 dict다. 앱의 외부 JSON을
무검증으로 신뢰한다는 뜻이 아니다. 최초 probe의 preflight만 검사하여 원래 중지된
앱을 허용하고, up 이후 frontend/backend 진단을 모두 검사한다.

```json
{
  "target_id": "fixture-gcp",
  "environment": "production",
  "preflight": {
    "disk_ok": true, "docker_ok": true, "compose_ok": true, "registry_ok": true,
    "db": {
      "connected": true,
      "database": "fixture_db",
      "server_version_num": 180003,
      "schema": "delta_notes", "table": "notes",
      "columns": ["position", "id", "title", "content", "created_at"],
      "schema_ok": true
    }
  },
  "frontend": {
    "healthy": true, "commit": "<full40>", "version": "<full40>", "cloudProvider": "gcp"
  },
  "backend": {
    "healthy": true, "storage": "postgres",
    "db": {"connected": true, "database": "fixture_db", "server_version_num": 180003,
           "schema": "delta_notes", "table": "notes",
           "columns": ["position", "id", "title", "content", "created_at"], "schema_ok": true}
  }
}
```

실제 어댑터의 server_version_num은 동일 앱 연결에서 SHOW server_version_num으로 얻은
관측값이어야 한다. target.db_major 또는 하드코딩한 180000으로 대체하지 않는다.
DB 진단 누락·PG16·schema_ok=false는 pull/up 전에 실패한다. up 후 memory fallback,
wrong cloud/version, FE healthy+BE 다른 manifest digest는 전체 FE·BE 복귀 조건이다.
복귀한 이전 Compose/공개 설정과 두 config ID→manifest 관측을 다시 검사한다.

CLI argv는 다음과 같다. main의 반환 dict는 호출자가 JSON으로 출력해도 되고 main이
직접 JSON을 출력해도 된다. state/status 키는 위의 deploy/query와 동일하다.

```text
start --request <공개 요청 JSON 경로> --state-dir <상태 디렉터리>
run   --request <공개 요청 JSON 경로> --state-dir <상태 디렉터리>
query --deployment-id <ID> --state-dir <상태 디렉터리>
```

start는 systemd-run/setsid 또는 start_new_session을 사용해 run worker를 분리하고
즉시 반환한다. run worker가 SQLite/flock/교체/복귀를 소유한다. 시험은 실제 별도
Python 프로세스와 새 세션에서 worker를 실행하고 up 직후 gate로 멈춘 채 부모 종료,
query running, 다른 프로세스의 flock 경합, gate 해제 후 query ready를 확인한다.
fixture는 분리 명령의 run argv를 유지하며 Docker/probe만 주입한다. 테스트 종료 때
자신이 생성한 worker만 정리한다. 실제 SSH 단절/systemd 서비스/호스트 재부팅은 별도다.
분리 명령 runner는 systemd-run(--wait 없음), setsid(--fork/-f), 또는 명시적인
start_new_session=True를 확인한 뒤 합성 worker를 실제 새 세션에서 실행한다.
따라서 이 시험은 CLI의 분리 실행 요청·프로세스 간 상태/잠금 계약 검증이며 실제
systemd/SSH 도구 자체의 동작 증거는 아니다. query CLI도 별도 프로세스로 호출한다.

범위는 `test_compose_host.py` 18건이다. 실행 명령은 위 명령에서 대상을 해당 파일로
한정한다. 모듈·API 부재는 collection error/skip 대신 fixture의 명시적인
`missing capability` assertion으로 실패한다. 이 실패를 동작 RED·수용 coverage로
집계하지 않는다. 코드가 생긴 뒤 같은 동작 assertion으로 다시 실행해야 한다.
합성 PG18/앱 JSON은 controller 분기 유닛 시험이며 **실제 PostgreSQL18 관측 검증은
0건**이다. 실제 Docker·DB·SSH·클라우드·중앙 로그 수신은 메인의 별도 통합 시험이다.

작성 직후 실행 기록: 18 collected, collection error 0. 대상 파일 실행은
**18 setup errors / 0 passed / 0 skipped**였고 모두 compose_host.py 부재에 대한
missing capability assertion이다. 동작 RED 0건·호스트 동작 검증 0건이며,
Python 두 파일의 AST 구문 검사만 통과했다. 이후 구현 생성 시 같은 18건 재실행 필요.
