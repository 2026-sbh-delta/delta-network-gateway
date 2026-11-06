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
  `delta-deploy` 실행기, 결과 archive, 컨트롤러 이미지의 release·vm_release·배포기(`/opt/delta/release`) 포함을 검사한다.
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
  덮어쓰는 API를 의미하지 않는다. fixture는 vm_release 형식
  (`target_id`, `environment`, `cloud`, `region`, `profile_id`, `database{profile_id,mode}`)만 포함한다.
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

## 운영 배포 실행 경계 (2026-11-06 교체)

Codex `compose_host.py`·`release_transport.py`(기존 VM을 EIC/IAP로 갱신)와 그 호스트 시험은 삭제했다.
운영 배포는 `controller/vm_release.py`의 `execute(request) -> result`가 동규님 방식 배포기
(`deploy/platform/release/deploy.py`: 환경별 Terraform VM → DB → Compose → 관측)를 실행한다.
`test_vm_release.py`가 요청 변환(DELTA_DEPLOYMENT), 관측 기반 결과 계약, 실패 stage·code,
`release_deploy.main` 연결을 검사한다. 배포기 자체 시험은 `deploy/platform/release/tests`에 있다.
실 Docker·SSH·SSM·클라우드·Jenkins 시작은 시험 범위가 아니다.
