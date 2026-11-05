# delta-pipeline

빌드 → SAST → 스테이징 → DAST를 잇는 고정 전이 그래프다. 오케스트레이터(`services/delta-orchestrator`)가 이 그래프를 띄우고, 실행은 각 도구가 맡는다. 여기서는 순서, 게이트 판정, 사람 확인, 재개만 다룬다.

연재님의 `feat/pipeline-graph`(dab7b38, [ADR-AI-40](../delta-ai/docs/adr/ADR-AI-40-pipeline-graph.md))를 바탕으로 했다. 재개 규칙, 시작·대기 노드 분리, 사람 확인(`interrupt`), Jenkins 호출은 그대로 쓴다. 바꾼 부분은 아래와 같다.

- **흐름:** 웹 갈래(명세 v1.1 6-2) 최소 연결로 바꿨다. 온프렘 증거, 카나리, 이전(대역) 노드는 이 흐름에 없어서 가져오지 않았다. 원본 브랜치에 그대로 있다.
- **게이트:** delta-ai 호출이 아니라 이 코드가 판정한다(명세 v1.1 7장, 11/4 결정). delta-ai는 장애 원인 파악에 남긴다(`clients.DeltaAi`).
- **DAST:** 화면용 CSRF 토큰과 내장 샘플 입력 대신 DAST 플랫폼 API(`PUT /api/platform/runs/{id}`, 플랫폼 토큰)에 SAST가 만든 SARIF와 빌드 digest·스테이징 주소를 묶어 보낸다. 점검 ID는 실행에서 정해 재개해도 같은 점검을 받는다.
- **스테이징 점유:** 스테이징 배포 때 이 실행 ID로 점유를 잡는다(Jenkins `HOLDER`). 점검이 끝나거나 멈추면 조회 토큰으로 풀고, 못 풀면 1시간 뒤 만료된다. 점유 중에는 다른 실행이 배포·DB 초기화를 하지 못한다.
- **Jenkins:** 작업 두 개(`delta-build` 빌드 에이전트, `delta-deploy` 배포 전용 실행기)를 쓴다. 진행 상태는 플러그인 없이 기본 `api/json`으로 읽는다.

2026-11-06 승인 명세 `본선/자료/배포도구_명세_v1.md`에 따라 schema2 FE·BE 묶음과 선택적 운영 배포를 추가했다. `Options.staging_cloud`는 `aws|gcp`이며 기본값은 `gcp`다. 배포 요청·DAST binding·점유 해제가 같은 cloud를 사용한다. 2차 수정 회차는 새 커밋·묶음·작업 키와 기존 HOLDER·reverify 연결을 유지한다.

- schema2 빌드는 full40 commit, linux/amd64, 두 digest 고정 ref, `image_digests`, frontend 별칭, `{schema,commit,platform,image_digests}`의 정규 JSON SHA-256을 모두 검증한다. 잘못된 v2 입력을 단일 이미지로 후퇴시키지 않는다.
- stage에는 기존 `IMAGE`와 `IMAGES_JSON`·`BUNDLE_SHA256`을 함께 보낸다. 실행 결과의 commit·묶음·두 ref/digest가 빌드와 같고, `data_reset_remaining`이 **정수 0**, `database_version_num`이 **PostgreSQL18 정수 버전 번호**여야 한다. 기존 `data_reset` 단계의 남은 행 0 기록도 확인한다.
- DAST에는 기존 `image`·`image_digest`·`commit_sha`와 함께 `binding.images`를 보낸다. DAST 서비스 코드는 변경하지 않았다.
- 기존 단일 이미지 입력은 스테이징 호출자 호환으로 유지한다. 운영은 검증된 schema2 묶음만 허용한다. 사람의 SAST·DAST 계속 진행은 stage 연결 확인을 위한 미해결 상태이며 운영 승인 자격이 아니다. 후보·재검증 대상이 있는데 DAST가 `skipped`이면 자동 통과시키지 않는다.

```text
prepare → (build_start → build_wait ∥ sast_start → sast_wait) → join ─┬→ ask_sast ─┐
                                                                     └────────────┴→ stage_start → stage_wait
  → dast_start ─┬→ dast_wait ─┬→ ask_dast ─┐
                │             └────────────┴→ done
                └→ done (점검 대상 0건)
  (어느 노드든 멈추면 → halted)
```

## 구조

| 파일 | 역할 |
|---|---|
| `graph.py` | 전이 표. 다음 단계는 코드로만 정한다. LLM 은 전이를 고르지 않는다 |
| `steps.py` | 노드 본문과 게이트 판정(최소판), 단계 직전 게이트 재확인 |
| `run.py` | 실행 구동, 사람 답 대기, 강제 종료 뒤 재개 |
| `model.py` | 실행 입력(`Options`), 화면용 노드 상태(`Board`) |
| `clients.py` | SAST·DAST·Jenkins·delta-ai HTTP 호출 |
| `delivery.py` | schema2 묶음·DB18·공개 인벤토리 검증, Ed25519 서명 |

## 게이트(최소판, 1-A에서 명세 7장 전체로 채운다)

- **SAST:** 점검 실패나 점검한 커밋이 다르면 멈춘다. 비밀값이 있으면 사람 확인을 받는다. 그 밖은 통과이고, DAST 대상이 0건이면 DAST를 건너뛴다.
- **스테이징:** Jenkins 실패, 다른 실행의 점유(409), 준비 안 됨, digest 불일치, 데이터 초기화 기록 없음이나 남은 행이 0이 아니면 멈춘다.
- **DAST:** 모든 항목이 `NOT_REPRODUCED`이고 누락이 없을 때만 자동 통과다. 그 밖(재현, 판정 불가, 결과 누락)은 사람 확인이다(D3, 자동 통과 금지). `interrupted`·`cancelled`·`error`는 멈춘다. `skipped`(후보 없음)는 통과다.

## 운영 승인과 연결 계약

`Options.release_target`은 등록된 단일 target ID다. 미설정이고 `cutover=False`이면 기존 스테이징 검증으로 끝난다. **미설정인데 `cutover=True`이면 prepare에서 중단한다.** 운영 verify 없이 DNS를 바꾸는 호환 경로는 없다.

```text
DAST 통과 → ask_release(사람 승인·서명) → release_start → release_wait → release_verify
  → [cutover=True: ask_cutover → check → switch → verify] → done
```

오케스트레이터 담당은 `Run` 또는 `Deps`에 아래 세 입력을 명시적으로 주입한다. 이 저장소 변경 범위에는 오케스트레이터 UI/API·키 파일 주입 구현이 포함되지 않는다.

| 입력 | 계약 |
|---|---|
| `release_jenkins: JenkinsClient \| None` | 기존 Jenkins 인터페이스의 운영 전용 job. `find_build/trigger/status/file/console_url` 재사용 |
| `release_signing_key: bytes \| None` | 호출자가 승인된 파일/비밀 참조에서 주입한 Ed25519 private PEM bytes 또는 raw seed 32 bytes. pipeline은 키 파일·환경을 자동 탐색하거나 키를 생성하지 않음 |
| `release_config: dict \| None` | 해당 target의 신뢰된 **비밀 없는 공개 인벤토리 전체**. 최소 `target_id`, `cloud`, `environment=production`을 확인하고 전체 객체를 해시. DB 주소·접속·로그·비밀 참조 등 실제 필수 속성 검증은 Jenkins controller 담당 |

설정은 prepare에서 JSON 복사해 고정하고 승인·실행·verify 때 현재 인벤토리와 대조한다. `config_sha256`은 키 정렬·공백 없는 JSON의 UTF-8 SHA-256이다. **controller가 target ID로 조회한 인벤토리와 pipeline에 주입한 객체가 정확히 같아야 한다.** 환경별 기본값도 해시 전에 같은 방식으로 확정한다. 요청자가 호스트·DB·비밀 참조를 덮어쓰는 입력은 추가하지 않았다.

사람 승인 뒤 pipeline이 발급하는 `APPROVAL` JSON은 `{"claims": {...}, "signature": "<base64url padding 생략>"}`다. 서명 대상은 정규 JSON claims이며 claims는 `deployment_id`, `commit_sha`, `bundle_sha256`, `target_id`, `environment=production`, `config_sha256`, `exp=승인 시각+600초`, `nonce`의 8개다. 키·설정 누락, 대상 불일치, 미해결/알 수 없는 SAST·DAST 게이트에서는 발급·운영 호출을 막는다. 임의 키·unsigned 대체 경로는 없다.

Jenkins에는 `TARGET_ID`, `COMMIT`, `BUNDLE_SHA256`, `IMAGES_JSON`, `APPROVAL`을 전달한다. 기존 `Jenkins.trigger`가 `RUN_ID`를 더하며 이는 claims의 deployment_id와 같은 회차별 작업 키다(`run-release` 또는 `run-r2-release`). **controller 팀의 `verify_and_reserve`가 공개 키로 서명·만료·입력·인벤토리 해시를 검증하고 nonce/deployment_id를 SQLite에 예약해야 한다.** pipeline은 nonce 접수 저장소나 새 배포 서비스를 만들지 않는다.

토큰은 승인 후 체크포인트에 보존하며 Board/API 화면·이벤트 로그에는 넣지 않는다. 체크포인트 저장 경로는 승인 토큰을 포함하므로 서비스 계정만 접근하게 운영 담당이 권한을 설정해야 한다. 재개 때는 같은 작업 키를 먼저 조회한다. 기존 작업이 있으면 토큰이 만료돼도 그 작업 결과를 조회하고, 없는데 만료됐으면 새 토큰으로 자동 재시도하지 않는다. Jenkins 조회 범위는 기존 최근 50개 계약을 유지한다.

운영 `result.json`은 `state=verified`, `target_id`, `environment=production`, commit·묶음·두 이미지/digest·frontend digest, 실제 `database_version_num`을 제공해야 한다. `ready`, Jenkins 실패, `unknown`, digest·DB 불일치는 cutover를 차단한다. 반환한 `deployment_id`·`config_sha256`도 있으면 대조한다. 실행 이미지 관측과 rollback/대상 잠금의 진실성은 controller·호스트 실행기 책임이며 이 pipeline의 fake 성공으로 실배포를 증명하지 않는다.

통합 선행 조건: 오케스트레이터 운영 승인 UI/API와 세 Deps 주입, pipeline/Jenkins 동일 인벤토리, private/public 키 분리 주입, Jenkins `APPROVAL` 로그 비노출·`verify_and_reserve` 연결, DAST 담당의 `binding.images` 수용이 필요하다. `cryptography` 의존성을 pyproject에 추가했으며 lock 갱신·상위 서비스 의존성 반영은 통합 담당이 수행한다. 이 조건들을 실환경에서 확인하기 전에는 운영 배포 완료로 처리하지 않는다.

이번 범위에서 플랫폼 실행 API 연결은 사용자 후속 결정으로 제외했다. 새 CLI는 만들지 않았다. 기존 호출자가 신뢰된 설정·키·클라이언트를 이미 확보한 뒤 사용하는 `Run` 생성 예시는 다음과 같다(실행된 배포 명령이 아니다).

```python
from delta_pipeline.model import Options
from delta_pipeline.run import Run

run = Run(
    Options(repo=repo_url, commit=full40_commit, staging_cloud="gcp",
            release_target=trusted_target["target_id"], cutover=False),
    sast=sast, dast=dast, build_jenkins=build_jenkins,
    deploy_jenkins=deploy_jenkins, staging=staging,
    image_repo=image_repo, store_dir=store_dir,
    release_jenkins=release_jenkins,
    release_signing_key=private_key_bytes,  # 호출자가 승인된 참조에서 주입
    release_config=trusted_target,         # controller와 동일한 공개 인벤토리
)
```

호스트 프로그램은 기존처럼 별도 작업에서 `run.execute()`를 구동하고 `run.snapshot()`으로 사람 승인 대기를 표시한 뒤 실제 사람이 선택한 값을 `run.answer("approve")` 또는 `run.answer("stop")`으로 전달한다. 자동 승인 코드는 제공하지 않는다. 재개 시 같은 `run_id`·`store_dir`와 신뢰된 Deps를 다시 주입한다. 이 예시의 플랫폼 UI/API 구현과 실운영 실행은 후속 범위다.

## 서비스 도메인 전환(선택, 명세 v1.1 6-3)

실행에 `release_target`과 `cutover: true`를 지정하면 운영 verify 뒤 별도 사람 승인을 거친다. 호원님 Cloudflare 스크립트(11/5)의 검증을 옮겼고, 외부 CLI 대신 Cloudflare API를 직접 부른다. 레코드는 DNS 전용 CNAME이다. 각 공개 cutover 단계와 done은 운영 verify와 묶음 근거를 다시 확인한다.

```text
ask_cutover(사람 승인) → cutover_check(blue 원본에 서비스 도메인 Host로 요청: 200 + X-Served-By)
  → cutover_switch(레코드가 green일 때만 blue로, 다시 읽어 확인. 이미 blue면 재개로 보고 넘어감)
  → cutover_verify(서비스 도메인으로 실제 요청을 3번 연속 blue 응답 확인. 실패하면 green으로 되돌리고 멈춤) → done
```

- 설정: `DELTA_CUTOVER`(JSON: `zone_id`, `record`, `green`, `blue`, `blue_origin`, `verify_path`, `expect_served_by`)와 `CLOUDFLARE_API_TOKEN`(해당 zone DNS 편집만). 오케스트레이터는 SSM `orchestrator/cutover_config`·`orchestrator/cloudflare_token`에서 읽는다.
- green은 전환 뒤에도 내리지 않는다.

## 재개 규칙

- 체크포인트는 `durability="sync"` 로 저장한다. 기본값으로 두면 강제 종료 때 끝난 노드가 다시 돌아 배포가 두 번 시작될 수 있다.
- 재개하면 끝나지 못한 노드만 처음부터 다시 돈다. 사람 확인 노드는 `interrupt` 앞에 부수 효과를 두지 않는다.
- Jenkins 빌드에는 `RUN_ID` 를 넣고, 시작 전에 같은 값의 빌드가 큐·이력에 있는지 찾는다. 이력은 최근 50개까지만 본다.
- SAST는 같은 `run_id`로, DAST는 같은 점검 ID·같은 입력으로 다시 부르면 기존 점검을 돌려준다. DAST 서버가 재시작돼 사라진 점검은 `interrupted`이고 자동으로 다시 돌리지 않는다(실행은 멈춘다).
- 표시 상태는 `<상태 디렉터리>/runs/<run_id>.json`, 체크포인트는 `<상태 디렉터리>/checkpoints.sqlite` 에 있다.

## 테스트

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

LangSmith 추적은 켜지 않는다. `LANGSMITH_TRACING` 을 설정하면 실행 내용이 외부로 나갈 수 있다.

2026-11-06 · `compose-delivery-20261106` · IMPLEMENTATION_ONLY 검증 기록:

| 명령/검증 | 실제 결과 |
|---|---|
| 구현 전 신규 bundle/release 시험 | 37 failed, 7 passed (RED) |
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_delivery_bundle.py tests/test_delivery_release.py tests/test_delivery_approval.py --tb=line` | 50 passed |
| 전체 시험 중간 결과(기존 cutover fixture 갱신 전) | 105 passed, 5 failed, 수집 오류 없음 |
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests --tb=line` (독립 작성자의 cutover fixture 갱신 후) | **111 passed in 9.14s**, 수집 오류 없음 |
| 합성 키 서명·정확한 8 claims·키/설정 누락·토큰 Board 비노출·후보 있는 skipped 반례 | 통과. 기존 키 파일/환경·실계정 접근 없음 |
| 검증된 release fixture로 DNS/TTL60·자동 TTL300·사전검사 실패·DNS rollback·already-blue 확인 | 합성 검증 5개 통과 |
| `git diff --check -- services/delta-pipeline` | 통과 |

중간 5개 실패는 기존 `tests/test_cutover.py`가 `Options(cutover=True)`만 사용해 운영 대상·verify 없이 DNS 변경을 기대했기 때문이다. 독립 테스트 작성자가 기존 DNS·TTL·rollback assertion을 유지하면서 schema2 묶음·운영 승인/verify fixture·별도 cutover 승인을 추가했다. target 없는 cutover의 명시적 차단 시험도 추가됐고, 최종 111개가 모두 통과했다. production guard를 약화하지 않았으며 이 구현 담당은 테스트를 수정하지 않았다.

기존 runtime/interrupt/r2 회귀와 신규 bundle/release/approval 시험을 포함한 로컬 검증 결과다. 다른 서비스·Jenkins·Terraform·플랫폼 API 수정, 실제 클라우드/운영 배포, commit/push/PR은 실행하지 않았다. 실제 controller/DAST 연결과 클라우드 실행은 위 인계 조건 및 사용자 후속 결정에 따른다.
