# 운영 UI와 PR #75 파이프라인 통합

## 상태와 승인

- 날짜: 2026-11-07 KST.
- 사용자 지시: `운영의 UI를 가지고 가되, 최근에 정준우가 merge한 부분도 볼 수 있어야 해.`
- 이 지시를 Ideation의 방향과 범위 승인으로 기록한다. 운영 UI를 기준으로 PR #75의 기능을 통합한다.
- 사용자 `ㅇㅇ..`로 Inception 및 Construction U1/U2/U3의 설계와 구현 계획을 승인받았다.
- 현재 U1/U2 구현, 로컬 검증과 U3 실제 운영 반영/공개 HTTPS 검증을 완료했다. Git 게시와 원격 CI 상태는 Git 이력과 실제 실행 결과를 따른다.

## Ideation

- 문제: 운영에는 기존 웹 번들에 UI 패치를 추가한 화면이 있고, main에는 PR #75의 새 공통 테마 및 파이프라인 화면이 있다. main 전체 교체는 사용자가 원하는 기존 운영 UI 유지와 맞지 않는다.
- 사용자: 기존 플랫폼 화면을 사용하면서 새 파이프라인의 실행 결과와 로그도 확인할 팀원.
- 성공 기준: 기존 운영 화면을 유지하면서 같은 플랫폼 메뉴에서 PR #75의 파이프라인 화면과 실제 실행 기록을 볼 수 있다.
- Scope: 기존 운영 UI와 추가 모듈의 보존, 파이프라인 메뉴와 화면 통합, 필요한 API 및 설정 연결, 해당 배포 경로의 호환성 수정, 재현 가능한 빌드와 검증 기록.
- Non-goals: 새 공통 테마나 사이드바로 전체 교체, 검증 목적으로 고객 앱 운영 실행이나 DNS 전환, DB 데이터 초기화, 무관한 네트워크 또는 IAM 변경.

## 현재 확인한 사실

- PR #75의 작성자는 `JungJoonWoo`이고 main 병합 커밋은 `633994e74f6f46ac70ff07805e85c67b8e9ba9bb`다. 병합 시각은 2026-11-07 17:53:56 KST다.
- PR은 PipelinePanel, pipeline-model, pipeline.css와 API `/api/v1/pipeline/*`를 추가한다. 공통 테마와 AppShell 변경도 포함하므로 기능과 외형 변경을 구분해야 한다.
- 운영 정적 자산은 `index-ui-hidden-691f6b284aa530eb.js`를 사용한다. 기존 라우터에는 overview/build/migration/monitoring만 있고 pipeline 경로는 없다.
- 운영 `/health/ready`와 `/api/v1/auth/session`의 실제 GET은 각각 HTTP200, ready 및 presentation_mode=true였다.
- 같은 운영 세션의 `/api/v1/pipeline/runs` GET은 HTTP404였다. 새 화면만 추가해서 조회가 되는 상태는 아니다.
- 기존 운영 화면의 원본 TSX 전체가 저장소에 없다는 이전 기록이 있다. 운영 번들 전체 보존과 원본 소스 복원을 완료한 것으로 간주하지 않는다.
- main `b4a26e1e`의 Release 42363991423은 사전 검사20개와 이미지 빌드/게시 후 VM runtime 병합에서 실패했다. 서비스 교체와 migration 이전에 중단됐다.
- 설정 병합은 `/delta-platform/deployment/runtime`의 추가 키4개를 거부했다: PLATFORM_APPLICATION_PIPELINE, PLATFORM_ORCHESTRATOR_PASSWORD, PLATFORM_ORCHESTRATOR_TARGETS, PLATFORM_ORCHESTRATOR_URL. 해당 값은 출력하지 않았다.

## Inception 승인 완료

### Functional Requirements

1. 기존 운영의 개요, 배포 환경 카드, 이전 계획과 환경 차이 표, 모니터링, 탐색과 UI 숨김 정책을 유지한다.
2. 기존 메뉴에 파이프라인 진입점을 추가하고 PR #75의 5단계 흐름도, 단계 상세, 실행 기록과 로그 조회를 연결한다.
3. PR #75의 새 실행과 승인 동작을 이식할 때 기존 서버 권한과 사람 승인 계약을 유지한다. 테스트는 fixture에서 동작을 확인하고, 운영 검증에서는 실제 고객 실행이나 승인을 보내지 않는다.
4. 필요한 pipeline API와 오케스트레이터 연결 설정을 운영 백엔드에 통합한다. 설정 병합은 승인된 키만 허용하며 기존 값 충돌을 계속 거부한다.
5. 운영에만 있는 기능과 마운트를 사전 비교하고 보존한다. 실제 적용한 소스, 이미지 digest, 정적 자산 manifest를 함께 기록한다.

### Non-Functional Requirements

- 기존 운영 UI의 색상, 글꼴, 메뉴 형태와 마이그레이션 화면을 기준으로 한다.
- 새 pipeline CSS의 전역 테마가 기존 화면에 영향을 주지 않게 범위를 제한한다.
- 데스크톱1440px/모바일390px에서 기존 탐색과 새 화면의 접근 경로를 검증한다.
- 비밀값은 서버에만 유지하고 로그나 웹 번들에 포함하지 않는다.
- 운영 설정 확인과 백업을 서비스 중지 전에 마친다. 기존 DB와 세션, 서비스/환경 데이터를 보존한다.

### Architecture

- 기존 운영 UI를 기준으로 화면 구성을 보존하고, PR #75의 PipelinePanel과 모델을 독립된 화면으로 연결한다.
- AppShell/theme.css 전체 교체는 하지 않고 기존 탐색에 pipeline 경로를 추가한다.
- pipeline.css가 필요로 하는 테마 변수와 레이아웃은 새 화면 범위 안에서 제공한다.
- 운영 백엔드의 추가 기능과 PR #75의 OrchestratorRelay 및 라우트를 통합한다. 실제 운영 코드와 main의 차이는 구현 전에 확보해 검토한다.
- 기존 배포 도구의 설정 허용 목록과 마운트/설정 보존을 점검한 뒤 통합 릴리스를 준비한다.

### Unit of Work

- U1: 운영 화면/코드/설정의 기준 보관과 차이 확인, 기존 외형 안에 pipeline 화면 통합.
- U2: pipeline API/오케스트레이터 연결과 runtime 설정 호환성 수정, 관련 회귀 검사.
- U3: 통합 미리보기 Review, 배포/롤백 준비, 운영 Release 및 실제 읽기 전용 검증.

### Acceptance Criteria

- 기존 서비스와 환경의 ID/상태가 보존되고 기존 화면과 숨김 정책이 유지된다.
- 같은 메뉴에서 새 pipeline 화면을 열고 기존 화면으로 돌아올 수 있다.
- 실제 운영 실행 기록과 상세/로그를 조회할 수 있고 미연결/실패는 오류로 표시한다.
- 새 화면과 API의 의미 있는 회귀, 기존 브라우저 회귀, 빌드와 형식 검사를 실제 실행한다.
- 운영 readiness, 자산 hash와 source/image 식별을 확인한다. 실행하지 않은 고객 배포나 DNS 전환을 성공으로 기록하지 않는다.

## Construction 설계와 구현 계획 승인 완료

1. 최신 운영의 정적 파일, 현재 이미지/마운트/설정 hash, 읽기 전용 서비스/환경 응답을 확보하고 변경 기준을 고정한다.
2. 기존 운영 외형에 pipeline 진입점과 화면을 연결하고 스타일의 적용 범위를 제한한다. 실제 원본 소스의 부재가 확인되면 복원 범위와 한계를 먼저 기록한다.
3. 운영 백엔드와 main의 API 차이를 비교하고 pipeline 조회/실행/승인 계약을 보존 통합한다. runtime의 추가4개 설정을 검증 가능한 허용 목록으로 반영한다.
4. 설정 충돌, 비밀값 누출 방지, 기존/새 API, 메뉴 이동과 데스크톱/모바일 회귀를 실행한다. 실제 명령, 날짜와 결과를 기록한다.
5. 기존 UI와 새 pipeline의 통합 미리보기를 확인하고 배포할 정확한 차이, source/image/자산 manifest와 rollback을 준비한다.
6. 승인된 배포 범위에 따라 운영에 적용하고 readiness와 실제 기록/상세/로그 조회, 기존 서비스/환경 보존을 확인한다.

## Operation 계획

- Deployment: 통합 구현과 검증을 마친 뒤 실행한다. 기존 실패 Release를 성공으로 재분류하지 않는다.
- Observability: readiness, API 오류, 실제 실행 기록 조회와 브라우저 오류, 기존 데이터 보존을 확인한다.
- Rollback: 배포 전 확보한 이미지와 웹 파일, Compose/마운트/설정으로 복구한다. DB migration이 필요한 경우 별도 호환성을 먼저 검토한다.
- Runbook: 배포의 source/image/정적 파일 manifest와 사용 명령, 실제 결과를 이 문서에 갱신한다.

## 검증 기록

| 날짜 | 실제 실행 명령 | 결과 |
| --- | --- | --- |
| 2026-11-07 | gh pr list / gh pr view 75 | PR #75 작성자, main 병합 커밋과 변경 범위 확인 |
| 2026-11-07 | Python으로 보관된 운영 JS의 route와 pipeline 문자열 확인 | 기존 route4종, 새 pipeline route/API 사용 없음 확인 |
| 2026-11-07 | Python HTTPS 세션으로 운영 health/auth/pipeline GET | ready200, presentation_mode=true200, pipeline/runs404 |
| 2026-11-07 | 현재 main의 AppShell/PipelinePanel/pipeline.css/OrchestratorRelay 읽기 | 새 화면과 전역 스타일 및 backend 의존성 확인. 구현/테스트 완료 결과는 아님 |

## 승인 후 구현과 실제 로컬 검증

2026-11-07 KST:

- 소스 AppShell에서 기존 서랍과 완료 앱의 메뉴를 복구하고 pipeline 메뉴를 추가했다. main의 공통 theme.css와 최근 실행 카드 교체를 제거했다.
- 실제 운영 공개 정적 파일9개(index 포함)를 `platform/web/production-ui/baseline.tar.gz`에 보관하고 archive 및 파일별 SHA256을 고정했다. API 응답/쿠키/설정값은 archive에 포함하지 않았다.
- production-ui.mjs는 기존 JS의 route1개, 메뉴 배열2개, icon1개와 화면 분기1개만 AST로 검증해 삽입한다. 역변환으로 나머지 원본 바이트가 동일한지 검사한다. 기존 추가 모듈은 그대로 보존한다.
- 새 PipelinePanel은 소스에서 별도 빌드하고 모든 component CSS를 production-pipeline 범위로 제한했다. 실제 운영 기준 미리보기는 dev:production, 배포 빌드는 build:production이다. 일반 dev/build의 소스 회귀 경로는 유지한다.
- backend는 pipeline_relay.py, api/pipeline.py, api/production_pipeline.py의 독립 모듈3개를 추가했다. wrapper는 기존 factory, 세션과 CSRF middleware를 유지하고 기존 services API의 인증 의존성을 그대로 재사용한다. 기존 production orchestrator.py는 대체하지 않는다.
- read-runtime.py는 기존 필수5개와 확인한 선택4개만 허용한다. 필수 누락, 미등록 키, 빈값/타입/줄바꿈과 기존 설정 충돌은 계속 거부한다.
- 현재 운영 API 소스55개를 비밀 설정 없이 보관해 차이를 확인했다. 운영 전체를 main으로 교체하지 않고 검증한 모듈3개만 readonly bind하고 Uvicorn factory를 확장 factory로 바꾼다. 기존 API 이미지/환경과 Worker/DB를 보존한다.

| 날짜 | 실제 명령/검사 | 결과 |
| --- | --- | --- |
| 2026-11-07 | 소스 npm run build 및 Playwright fixture | 빌드 성공, 기존/신규49개 통과. 최종 pipeline CSS 후 탐색2개 추가 재검사도 통과 |
| 2026-11-07 | npm run build:production 및 npm run test:production | 운영 기준 통합 빌드 성공, 데스크톱/모바일 보존/로그/연결 오류와 lifecycle4개 통과 |
| 2026-11-07 | 실제 운영 응답 fixture를 이용한 migration-regression.spec.mjs | 1440px/390px2개 통과. 기존 단계/결과, 비교 계획, 대상 선택과 UI 숨김 정책 보존 및 쓰기 요청0개 확인 |
| 2026-11-07 | backend/.venv/bin/python -m pytest backend/tests/test_pipeline_integration.py -q | DB 없는 독립22개 통과. 기존 인증/CSRF 재사용과 wrapper 라우팅 포함 |
| 2026-11-07 | python3 -m unittest discover -s deploy/tests -p test_onprem_runtime_merge.py -q | 설정 병합8개 통과 |
| 2026-11-07 | dev:production 별도5186 smoke | 통과. 기존 compiled 자산을 Vite가 사전 변환하던 경고를 바로잡고 재검사 통과 |
| 2026-11-07 | npm run format:check, 추가 빌드 도구 Prettier, 대상 Ruff, git diff --check | 당시 실행 대상 통과. 최종 배포 도구는 작성/검토 후 별도 기록한다 |

초기 검사 실패와 한계:

- 초기 통합 브라우저2개 실패는 비동기 환경 조회가 끝나기 전 텍스트를 비교한 검사 문제와 모바일 상세 패널이 로그 버튼을 가리는 실제 UI 문제였다. 조회 완료를 기다리고 모바일 패널 범위를 조정한 후 재검사했다.
- 마이그레이션 비교 초기2개는 실제 진행 목록2개를 단일 locator로 기대한 선택자 오류였다. 목록2개와 응답 완료를 기준으로 수정한 후 위2개가 통과했다.
- BFCache 검증은 persisted=true의 pagehide/pageshow 이벤트를2회 재현한 lifecycle 검사다. 실제 외부 페이지 왕복에서 브라우저 캐시 적재를 실증한 것으로 확대하지 않는다.
- 전체 deploy unittest는 PyYAML 부재와 sandbox socket bind EPERM으로 일부 error였다. 대상 설정8개와 별도 후보 검증 결과를 전체 회귀 성공으로 표현하지 않는다.
- 고객 새 실행/승인 API는 browser fixture로 가로챘다. 실제 고객 배포나 DNS 전환은 실행하지 않았다.

## 구체 운영 반영 방식

- 기존 API image와 Env는 그대로 사용한다. 추가 Python3파일과 새 정적 웹 디렉터리만 readonly로 연결하고 실행 factory만 `delta_platform.api.production_pipeline:create_app`으로 바꾼다.
- 배포 도구는 현재 symlink, Compose 및 설정파일 hash, 실제 API/Worker/DB의 ID/image/환경/마운트를 확인한다. Docker의 mount 배열 순서는 정규화해 비교한다.
- stage는 실제 운영 API를 중지하지 않고 localhost8089 후보를 만들어 readiness, 기존 세션, 실제 runs/detail/log GET을 확인한다. 후보는 고유 network alias와 label을 사용하며 검사 후 제거한다.
- apply는 stage와 같은 파일 hash를 확인한 뒤 API만 교체한다. 기존 API는 정지 상태로 보존하고 실패하면 이전 컨테이너와 Compose로 자동 복구한다. Worker/DB 재시작이나 migration은 필요하지 않다.
- 실제 stage/apply와 공개 HTTPS/UI 검증 완료 상태는 아래 Operation 실제 완료 기록을 따른다.

## Operation 실제 완료 (2026-11-07 KST)

### 배포와 운영 식별

- 통합 릴리스: `pipeline-ui-22c435a8b910865544a0`.
- 고정 archive SHA256: `dd3ae234dedf1f7f80db681c535a9641f1c5203ab3c76ee832aede6ada1236b3`. 같은 입력으로 두 번 준비한 archive의 바이트 동일성을 확인했다.
- 비공개 artifact: `s3://delta-platform-artifacts-616723161368/releases/pipeline-ui-22c435a8b910865544a0/release.tar.gz`.
- stage SSM `32e01164-f9e2-41ae-a8fe-c5d5562a33ea`: 20:06:25~20:06:44 KST, Success/종료0. 별도 후보에서 readiness와 실제 실행20건, 상세200 및 build 로그200/12720자 조회를 확인하고 후보를 제거했다.
- apply SSM `a6101ac4-28a4-4e54-8757-aa8b580ac4e6`: 20:07:28~20:07:46 KST, Success/종료0. 이18초는 배포 명령의 실행 시간이며 실제 서비스 중단 시간을 측정한 값은 아니다.
- API 새 ID는 `87796838e8bc8abed87fc0425ed1d31a1f44a0d120514780837360cf9af77454`이며 healthy다. 이미지 `sha256:1cacdf34fa3d65f13f5ff6b307ed3ebc96a0a609c48c4a68a95ae8201ca2aba8` 및 기존 Env hash를 보존했다.
- Worker `40042b070149`와 DB `2c20a93d56b8`의 ID, 시작 시각, 이미지, 환경과 마운트 및 기존 env 파일 hash가 동일하다. API만1회 교체했고 Worker/DB 재시작, DB migration과 Terraform/IAM 변경은 실행하지 않았다.
- Compose SHA256은 `cef1be0be905fcc9966bda3202b810c592f9b510d6b3e3ac01a5b6a1203543ed`다. 원래 current symlink와 image pin을 유지하고 API command/필요한 mounts만 반영했다.
- 웹 경로는 `/opt/delta-platform/releases/pipeline-ui-22c435a8b910865544a0/web`이다. Python3파일은 같은 릴리스의 `python/delta_platform/`에서 각 기존 package 경로로 readonly 연결한다.
- 컨테이너 설정 전체 사본은 서버의 root 전용 `container-before.json`/`container-after.json`에만 보관했다. 설정값을 문서나 공개 자산에 복사하지 않았다.

### 공개 화면 확인 후 모바일 보완

- 첫 공개 브라우저 확인에서 조회 API는 정상이나390px의 새 실행 폼이 화면 밖으로 잘리는 문제를 확인했다. CSS의 모바일 폭/줄바꿈과 버튼 대비를 수정하고 운영 기준4개 브라우저 검사를 다시 실행해 전부 통과했다.
- 정적 보완 릴리스 `pipeline-static-c7a2feb59c8bd7d1fec7`, archive SHA256 `38ac31a06c63f6be5afa1946df2d82c19d92d01f525ad6c78a44d8d75e47b1ff`.
- SSM `a8fd1bba-e70e-45ae-a889-bb0a2470a729`: 20:13:05~20:13:06 KST, Success/종료0. 새 CSS를 준비하고 index만 원자적으로 교체했다. 기존 웹 파일19개, 이미지/환경/마운트를 보존하고 컨테이너 재시작은0회다.
- 최종 HTML SHA256은 `478f5340d8bc26555ffae3509d5010c6245b4bc438fca613c154e48d01c082e7`이며 새 CSS는 `assets/pipeline-extension-f6af704a9ef56ced.css`다.

### 최종 실제 검증

| 날짜 | 명령/확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-07 | capture-live.py public-final 및 manifest/응답 비교 | readiness ready, HTML/새 자산4개가 최종 build:production의 해시와 동일. 기존 서비스1개 및 AWS/온프레미스/GCP 환경3개의 전체 응답 동일 |
| 2026-11-07 | live-browser.mjs 실제 HTTPS1440px/390px | 기존 서랍, 서비스 화면, 마이그레이션/숨김 정책 유지. 새 메뉴로 실행20건, 단계 상세,12720자 빌드 로그, options200과 새 실행 폼 표시 확인 |
| 2026-11-07 | 최종 운영 브라우저 경계 | 두 폭 모두 새 실행 폼이 viewport 안에 표시됨, pageerror0개, 가로 넘침 없음, 실제 API 쓰기 요청0개 |
| 2026-11-07 | SSM host-after 및 전후 비교 | API image/Env 보존, Worker/DB의 전체 요약/시작 시각/환경/마운트 보존, env 파일 hash 동일 |
| 2026-11-07 | update-pipeline-static.py 임시파일 기반 검증 | apply, 명시 rollback, readiness 실패 자동복구에서 기존 index/CSS 및 참조되지 않은 JS 보존 확인. 실제 운영 rollback은 미실행 |

- 캡처와 증거는 ignored `platform/.runtime/production-pipeline-integration/`에 보관한다. 실제 로그 본문을 문서나 일반 출력으로 복사하지 않는다.
- 새 실행 폼의 표시와 defaults 조회까지만 검증했다. 이번 검증으로 실제 고객 새 실행/승인, 앱 배포, DNS 전환을 수행하지 않았다.
- 기존 GitHub main Release42363991423은 실패 상태다. 이번 성공은 위 SSM의 별도 보존 통합 배포이며 원격 CI 성공이나 main 커밋 배포로 바꿔 표현하지 않는다. Git 게시와 원격 CI 상태는 Git 이력과 실제 실행 결과를 따른다.

### Runbook과 Rollback

- 같은 운영 UI로 미리보기: `cd platform/web && npm run dev:production`. Vite5173을 이미 사용 중이면 `-- --port 5175`로 실행하고 backend의 local origin을 해당 포트에 맞춘다.
- 재현 빌드: `npm run build:production`, 운영 번들 회귀: `npm run test:production`. API/배포 코드 검증과 source fixture 검사는 위 기록을 따른다.
- 통합 artifact 준비: `python3 platform/deploy/scripts/prepare-pipeline-release.py --host-snapshot <검토한 최신 host JSON> --web-dist platform/web/dist --output <새 출력 경로>`. 기존 root에 다시 덮어쓰지 않으며 현재 운영 기준이 바뀌면 새 기준과 차이를 검토한다.
- 모바일 CSS 보완만 되돌리기: SSM에서 `python3 /opt/delta-platform/releases/pipeline-static-c7a2feb59c8bd7d1fec7/update.py /opt/delta-platform/releases/pipeline-static-c7a2feb59c8bd7d1fec7 rollback`. 현재 API/Compose/웹 해시가 일치해야 하며 이전 index만 복원한다.
- 통합 전체를 이전 UI/API로 되돌리기: SSM에서 `python3 /opt/delta-platform/releases/pipeline-ui-22c435a8b910865544a0/operate.py rollback /opt/delta-platform/releases/pipeline-ui-22c435a8b910865544a0`. 이전 API 컨테이너 `delta-platform-api-1-before-pipeline-ui-22c435a8b910865544a0`와 Compose를 복원한다. Worker/DB와 데이터를 되돌리지 않는다.
- 서버의 실제 rollback은 실행하지 않았다. 후보 사전 검사, 임시파일 기반 정적 rollback 검사와 운영 적용 성공을 구분한다.

- 최종 대상 Python10개 Ruff check/format, 웹 format:check 및 추가 빌드 도구 Prettier, git diff --check가 통과했다. 기본 배포 CLI의 config 위치를 수동 지정한 초기 lint 시도는 first-party 분류/줄 길이 설정 차이로 실패했으며, CI와 같은 파일별 설정 자동 탐색으로 최종 검증했다.
- 통합 operate 도구는 기존 factory에서 최초 확장하는 경로를 검증한다. 현재 확장 factory가 이미 적용된 운영의 다음 변경에는 기존 Python bind와 factory 기준을 먼저 검토한다. 정적 CSS 보완은 별도 atomic 도구를 사용한다.

## Git 게시와 원격 CI (2026-11-07 KST)

- 사용자의 커밋/푸시 지시로 `feat/platform-production-ui-pipeline`에 변경을 나누어 서명 커밋하고 푸시했다. 기존 PR #76은 사용자 지시로 병합하지 않고 닫았다.
- API와 설정 호환성 `9098cba1`, 기존 UI와 파이프라인 통합 `54870794`, 운영 도구와 검증 기록 `59376ed8`이다. 기존 UI 숨김 기반 커밋 `270f5b6a`도 이 브랜치에 포함한다.
- [Platform checks 42367680075](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/42367680075)는 `59376ed8757dac23afa320207f44dd405bde0e80`에서 수동 실행했고 20개 job이 모두 success다. AWS 운영 배포 워크플로가 아닌 검사 워크플로다.
- 이후 문서 전용 기록 커밋은 검사한 코드에 추가 변경을 넣지 않는다. 현재 원격 SHA와 PR/병합 상태는 Git 이력을 따른다.
