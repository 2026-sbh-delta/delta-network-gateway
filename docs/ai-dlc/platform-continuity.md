# 플랫폼 관측과 서비스 이전

- 기준일: 2026-11-04.
- 승인: 사용자가 TODO 검토 후 OTel·한 페이지 대시보드·프론트/백엔드/DB·로그인 유지 범위를 합의하고 `Okay. Let's go.`로 구현을 지시했다. 후속으로 커밋과 PR 병합도 위임했다. 커밋 메시지를 사전에 알리고, 테스트·CI 확인 후 병합한다.
- 현재: PR #21 DAST 연결은 CI 통과 후 main `de1f5ff`로 병합했다. 관측 API·대시보드·Collector·Django 예제 구현과 로컬/실제 AWS 수집 검증을 완료했다. PR #22 관측 기능은 CI 통과 후 main `3fefcb9`로 병합했다. 관측 조회 IAM Apply와 최종 No changes를 확인했다. 플랫폼 Release와 AWS→GCP/온프레미스 실제 이전은 아직 하지 않았다.

## 목적과 성공 기준

개발자가 한 화면에서 고객 앱의 실행 위치·상태·핵심 관측값을 확인하고, 같은 도메인·로그인 상태로 다른 환경으로 이전한 결과를 검증한다. 작은 자원으로 실행하지만 소유 관계, 승인, 실패·복구 경계는 생략하지 않는다. 대규모 처리 성능을 입증했다고 주장하지 않는다.

## 범위와 구현 단위

1. 준비된 DAST 연결을 PR #21로 검증·병합한다. 운영 연결은 기존 검사와 상태를 보존하고 별도 검증한다.
2. 관측: OTel 앱 계측과 Collector, AWS 관측 저장소, AWS/GCP 인프라 조회, 한 페이지 UI. 앱 요청 수·5xx 비율·p95, VM 상태·CPU·메모리, 최근 오류 5건과 대표 trace 1건만 우선 제공한다. 없음·실패·오래된 값을 구분하며 예제값을 운영 결과로 사용하지 않는다.
3. 식별·승인: project/service/environment를 안정된 대상으로 저장하고 workflow에 개별 run/assessment와 승인한 입력 해시를 연결한다. 기존 기록은 보존한다. 같은 입력 재시도는 같은 실행, 변경 입력은 새 실행이다. 현재 로그인은 공통 작업 공간이며 고객별 RBAC가 완성된 것으로 표시하지 않는다.
4. 앱·DB 이전: 첫 지원 사례는 PostgreSQL과 DB 세션을 사용하는 작은 풀스택 예제로 정한다. AWS → GCP 및 온프레미스 경로에 동일한 검증 계약을 사용한다. 초기 복사·CDC·대상 실행 검사·전환 승인·최종 동기화·단일 쓰기 전환·로그인/데이터 보존을 검증한다. 실제 제공된 환경부터 검증하고 미연결 환경은 명시한다.
5. 운영: 필요한 IAM·수집 설정·VM 실행 계약을 IaC와 스크립트로 제공하고 GitHub CI/Release를 사용한다. 실제 Plan과 Apply, 코드·로컬 통합·클라우드 검증을 구분해 기록한다. 별도 대규모 관측 클러스터, DB 엔진 변환, 양쪽 DB 동시 쓰기는 범위 밖이다.

## 설계

- 플랫폼 업무 DB에는 대상·실행·승인·요약만 저장한다. 로그/metrics/trace 원본은 관측 저장소에 둔다. S3는 보고서와 필요한 백업 파일에 사용한다.
- 앱 → 제한된 OTel Collector → 관측 저장소 → 플랫폼 조회 API → 한 페이지 대시보드. 수집/조회 권한과 대상 식별자를 고정하며 클라이언트가 임의 계정·로그 그룹·쿼리를 지정하지 못한다.
- 서버가 저장한 서비스·환경 연결로 AWS/GCP 자원을 조회한다. 한 공급자 실패로 다른 데이터까지 숨기지 않는다. 최대 2시간 조회, 응답·시간 제한과 짧은 캐시를 둔다.
- p95는 분포에서 계산한다. 여러 서버의 p95를 평균 내거나 누락값을 0으로 바꾸지 않는다. OTel HTTP 요청 수·5xx·지연의 같은 범위와 단위를 사용한다.
- 환경 목록과 실행 바인딩은 플랫폼이 발급한 UUID로 관리한다. 임의 UUID만으로 권한이 성립하지 않으며 서비스/프로젝트 소유 관계와 커밋·이미지·승인 해시를 대조한다.
- 앱 이전은 검증한 image digest, 설정·인증 키, DB 접근·세션을 포함한다. 기존 앱과 새 앱이 서로 다른 DB에 동시 쓰지 않게 전환한다. DAST는 별도 테스트 DB를 사용한다.
- 최종 쓰기 전환은 명시적 승인과 준비 검사 후 실행한다. 새 DB 쓰기 이후에는 단순 DNS 원복으로 복구됐다고 표시하지 않는다. 로그아웃·세션 갱신·배치도 쓰기 통제에 포함한다.
- 고객 도메인을 유지하고 기존 요청의 완료를 기다린다. 진행 중 TCP 연결이나 미저장 브라우저 입력을 자동 이전한다고 보장하지 않는다.
- 자체 관측 원본 보관 목표 2시간과 AWS 관리형 저장소의 실제 정책을 구분한다. 백업·승인·DB 복제 로그는 필요한 복구 기간에 따라 보존한다.

## 검증 계획

- 실제 PostgreSQL migration과 기존 데이터 보존, 대상/입력 바꿔치기 거부, 중복/재시도/승인 불일치.
- AWS/GCP SDK·HTTP 경계의 정상/누락/오류/시간 초과, p95·오류율 계산, 서비스·환경 격리와 조회 제한.
- 실제 OTel SDK → Collector → 저장소의 요청·오류·trace와 조회 결과 비교. 테스트 대역과 실제 클라우드 증거를 분리한다.
- 브라우저 한 페이지, 환경 전환·새로고침·데이터 없음·모바일, 기존 빌드/검사 회귀.
- DB 초기 복사·변경 동기화·전환 후 같은 세션과 읽기/쓰기, 전환 전 실패·승인 불일치·재시작 복구. 실환경 접근이 없는 경로는 완료라고 표시하지 않는다.

## 운영·복구

기존 Terraform root와 팀 네트워크를 재생성하지 않는다. 관측 조회 실패는 배포 상태를 덮어쓰지 않는다. 기능별 설정으로 비활성화할 수 있게 하고 기존 플랫폼 DB/로그인/빌드 설정을 유지한다. 변경별 테스트와 PR/Release 결과를 이 문서에 추가한다.

## 관측 구현·검증 (2026-11-04)

- `platform/backend`: `uv run pytest -q` → **153 passed**, GitHub 실통신 검사 1 deselected. 실제 PostgreSQL `0006` 환경 연결 보존·다른 서비스의 환경 거부·인증·2시간 상한, 부분 장애, 누락/오래된 값, OTel DELTA histogram 쿼리와 GCP 단위 변환을 검증했다. 이 테스트의 AWS/GCP API 경계는 대역이다. Ruff 검사/포맷 통과.
- `platform/web`: `npm run format:check`, `npm run build` 통과. 관측 브라우저 1개와 기존 12개를 순서대로 실행해 모두 통과했다. 대시보드 API는 테스트 전용 fixture다. 데스크톱·모바일 캡처를 시각 확인했고 환경 변경 시 이전 값 노출·자동 새로고침 깜박임을 수정했다. 로컬 Node 20.18의 Vite 권고 버전 경고가 있으며 CI는 Node24다.
- `platform/examples/session-app`: Django와 실제 별도 PostgreSQL 테스트 DB에서 `manage.py test demo.notes --noinput` → **4 passed**. 새 앱 클라이언트의 세션 유지, 데이터 소유 관계, CSRF, 메모 쓰기 차단과 로그아웃 전파를 검증했다. DB 자체의 이전 시험은 아니다.
- OTel Collector **0.164.0** 이미지 digest를 고정하고 `validate --config`를 실행해 통과했다. 처음에는 X-Ray exporter가 지원하지 않는 `sending_queue`가 있어 제거하고 실제 지원하는 `num_workers`를 사용했다. 구성 검증용 가짜 인증과 실제 AWS 인증 검증은 구분한다.
- 실제 로컬 Docker의 Django·PostgreSQL → Collector → AWS 서울 CloudWatch/X-Ray를 연결했다. 실제 브라우저 세션을 HTTP 클라이언트로 유지하며 로그인·메모 저장·조회, 앱 컨테이너 교체 후 로그인 유지, 제어된 쓰기 차단 503을 확인했다. AWS 조회 결과 **요청 14건, 5xx 1건에 해당하는 오류율 7.142857%, p95 약 215.76ms, 앱/DB 상태 1**, 경고 로그 1건과 HTTP/DB trace 3개 span을 확인했다. 이 숫자는 검증 당시의 실제 값이며 고정 화면 데이터가 아니다. 증거는 ignored `platform/.runtime/otel-live-result.json`에 저장했다.
- SSO 토큰 갱신은 만료됐지만 Collector에 전달한 기존 단기 AWS 세션은 STS가 유효하게 인정해 위 최종 조회를 완료했다. 임시 키를 코드·문서·CI에 저장하지 않는다. 검증 후 앱·Collector는 중지하고 DB 볼륨은 보존했다.
- 관측 조회 정책은 일반 GitHub Apply에 IAM 권한이 없어 별도 관리자 root `platform/deploy/observability/identity`로 분리했다. 해당 root의 `terraform validate`와 mock `terraform test` 1개를 통과했다. 실제 Plan/Apply는 후속이다.
- 기존 GCP ADC로 `term2-delta` Compute API 조회 HTTP200을 확인했다. 현재 고객 VM은 없으므로 GCP CPU/메모리의 실제 값까지 확인한 것은 아니다.

연결 계약은 [관측 안내](../../platform/observability/README.md), 첫 풀스택 예제는 [세션 앱](../../platform/examples/session-app/README.md), 사용자 설명은 [한국어 HTML](../../platform/docs/migration.ko.html)을 따른다. 현재 환경 연결은 고정 공통 프로젝트 ID를 사용하며 workflow/AI의 전체 식별 계약은 다음 단위다.

## 식별·승인 구현과 검증 (2026-11-04)

- DB `0007`: 기존 서비스·환경·실행을 유지하며 공통 project, immutable workflow, 도구 실행 기록을 추가했다. 서비스/프로젝트 FK와 환경 소유 관계를 검증한다. UUID seed를 PostgreSQL UUID로 명시 변환하고, 기존 source-check 요청 해시에 새 project 필드가 끼지 않도록 수정했다.
- 계획 API·계획 승인 화면, 동일 입력 재시도, 대상 변경 거부, 승인한 action 입력 해시 검증, 결과 바꿔치기/덮어쓰기 거부를 구현했다. 웹 승인은 계획 기록이며 실제 변경 실행을 시작하지 않는다.
- 팀 `delta-ai` 계약에 선택적 execution_context를 추가했다. 기존 호출을 유지하며 신규 호출은 ID·커밋·공급자·본문 해시를 대조하고 단일 프로세스 영속 ledger로 재시도 결과를 보존한다. 플랫폼 어댑터는 결과 context를 다시 확인한다.
- `platform/backend`: Ruff 검사/포맷 통과. `uv run pytest -q -x` → **158 passed**, 실통신 1 deselected. `uv run --with-editable ../../services/delta-ai pytest -q tests/integration_assessments.py` → **1 passed**. 실제 팀 AI API를 호출하되 외부 LLM은 사용하지 않는 통합 검사다.
- `services/delta-ai`: `uv run --extra dev pytest -q` → **245 passed**. API 재시작 후 같은 결과 재사용·변경 입력 거부·다른 서비스/커밋/공급자 거부 포함.
- `platform/web`: format/build 통과, 격리된 새 계획 화면 테스트 1개와 전체 브라우저 **14개 통과**. 데스크톱/모바일 시각 확인.
- 관측 IAM 저장 Plan을 검토해 읽기 정책 **1 추가, 변경/삭제 0**을 Apply했다. 이후 `terraform plan -detailed-exitcode` → **No changes, 0**. 일반 GitHub Apply에 IAM 변경 권한을 추가하지 않았다.
- DAST 사설 연결은 기존 ALB·VM 교체 없이 Route 3개·SG 규칙 1개 추가 Plan을 검토·Apply했다. 실제 DAST 이미지 갱신/활성화·플랫폼 Release 검증은 이어서 진행한다.

[식별 계약과 현재 경계](../../platform/docs/workflows.ko.md). 이 단위의 실제 AI 서버 연결·변경 실행기·앱/DB 이전 검증은 후속이며 계획 승인만으로 완료라고 표시하지 않는다.

## DB 이전 실행기·전체 앱 로컬 검증 (2026-11-04)

- 설계·계획: 같은 PostgreSQL17·전용 DB/앱 계정·같은 스키마, 초기 복사/CDC, 모든 앱 DB 연결 차단/정리, 복제 마커·전체 데이터 대조·sequence 동기화, 단일 쓰기 승격을 구현한다. source/target은 별도 클러스터로 제한한다. 기존 앱·DB와 팀 인프라를 변경하지 않는 별도 로컬 예제로 먼저 검증한다. 기존 TODO 구현 승인 범위다.
- 구현: `delta_platform.migration`에 비밀값 없는 저장 계획, 프로세스 간 journal 잠금·원자 저장, 스키마·DB 신원·쓰기 권한 검증, 복제 슬롯 상한 확인, 재개·승격 전 중단·승격 후 자동 원복 거부를 추가했다. CLI와 한국어 운영 문서, Docker 통합 검사를 작성했다. 현재 웹 Worker 자동 연결은 하지 않았다.
- 실제 DB 검사: `docker compose -f platform/migration/compose.test.yaml build test` 후 `run --rm test` → **6 passed**. PostgreSQL 두 개를 사용하며 플랫폼 테스트 DB와 분리된다.
- 실제 전체 앱: `uv run --project platform/backend python platform/migration/demo.py` → 같은 로컬 Docker 이미지 ID로 프론트/백엔드·DB 이전, Chromium 로그인 1회·같은 URL/쿠키·이전 전/CDC 중 메모·대상 새 메모 저장 확인. 증거는 ignored `platform/.runtime/migration-demo/proof.json`과 before/after PNG에 저장했다. 마지막 제어된 대기 **4.332초**, 헬스 **503 17건 / 200 12건**.
- 검증 중 수정: 새 target 스키마를 복원할 때 public 스키마가 이미 존재하므로 데모의 빈 스키마만 비CASCADE 삭제하고 복원했다. 실제 이전기는 대상 데이터를 지우지 않는다. Nginx reload 직후의 503을 발견해 새 진입 경로에서 target 정상 응답을 연속 확인한 뒤 완료를 알리도록 했다. 이미지도 로컬 content ID로 고정하고 양쪽 컨테이너의 실제 ID를 대조한다.
- 범위: 로컬 Nginx는 주소 유지용 시험 도구다. 운영에 HAProxy나 추가 LB를 넣었다는 뜻이 아니다. DB 중단 시간이 있으며 무중단·대규모 DB 성능·AWS→GCP 실제 이전은 미검증이다.
- GCP 실조회: term2-delta, 고객 서브넷 `delta-customer-vms-seoul` `10.30.1.0/24` 존재, VM0·asia-northeast3 VPN 터널0. 실제 고객 VM/DB 준비 주체를 사용자에게 확인하면서 독립 구현을 계속한다.

## 플랫폼 Release·DAST 운영 확인 (2026-11-04)

- PR #23은 전체 CI 통과 후 main `4c138ee`로 병합했다. [Release 41691494886](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41691494886) 성공. DB0007·관측·계획 화면·DAST 연결이 AWS 플랫폼에 배포됐다.
- 공개 웹 재로그인·기존 서비스/빌드/SAST 보존, 데스크톱/모바일·콘솔 오류·가로 넘침을 확인했다. 환경 미등록은 실제 값 없음으로 표시하며 예제 지표를 채우지 않는다. 실제 AI 서버/고객 환경 등록은 별도 후속이다.
- DAST Route3/SG1 추가 후 실제 Plan **No changes**. 기존 SAST 컨테이너는 재시작하지 않았다. DAST만 main 기반 새 ARM64 이미지로 교체했고 기존 환경/마운트/모델 인증을 유지했다. 중지된 이전 DAST 컨테이너는 복구용으로 보존했다. 사설 HTTPS·고정 샌드박스 등록을 확인하고 SSM runtime의 DAST 필드만 활성화했다.
- 공개 API의 실제 DAST 실행 `7841eef7-34cd-4ae5-8531-ec387b300f62`: 기존 성공한 이미지/커밋/SAST로 ECS를 실행해 헬스·이미지를 확인하고 DAST API를 호출했다. 지원 XSS 후보가 0개여서 `dast_check=not_run`, `requires_review=true`, `deployment_approved=false`로 기록했다. 이것은 취약점 재현 성공이 아니다. 실행 종료 후 ECS running/pending 0개를 확인했다. 증거는 ignored `platform/.runtime/dast-public-result.json`.
- 원본 관측 IAM과 DAST 사설 연결 Apply는 완료했지만, 실제 취약 앱의 XSS 재현·GCP/온프레미스 샌드박스는 아직 별도 검증이 필요하다.

- DB 이전 단위 커밋 전 회귀: `platform/backend`에서 `uv run pytest -q` → **158 passed, 1 deselected**. backend와 migration 도구 Ruff 검사·포맷, `git diff --check` 통과. 새 DB/전체 앱 CI 단계는 추가했으며 원격 실행 결과는 PR에서 확인한다.

- PR #24 최초 CI: 기존 앱·브라우저·Terraform 7개 root와 PostgreSQL 통합 6개는 통과했지만 Linux 전체 앱 데모는 root 소유 0600 계획 파일을 runner 사용자가 읽지 못해 실패했다. Linux 컨테이너에서 같은 PermissionError를 재현했고, 동일 사용자 UID/GID로 journal 생성·읽기 및 0600 유지가 통과하는 것을 확인했다. 데모 실행기를 호출 사용자로 실행하고 미리 설치한 CLI를 사용한다. 실패 정리 중 브라우저 종료 대기가 원래 오류를 덮지 않도록 kill 후 정리한다. 전체 CI 재검증은 후속 실행 결과를 따른다.
