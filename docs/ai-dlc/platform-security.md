# 플랫폼 SAST 결과 연결

- 기준일: 2026-11-03. Jenkins 전환에 이어 사용자의 다음 작업 구현 지시를 수행한다.
- 목적: 성공한 이미지 빌드의 고정 커밋을 기존 SAST 서비스에 검사시키고 근거·발견 사항을 플랫폼에서 확인한다.
- 범위: API/DB 작업 이력·Worker·SAST HTTP 어댑터·화면·계약/회귀 검증. DAST/AI 수정/운영 배포 승인은 포함하지 않는다.
- 상태: 구현·로컬 API/DB/브라우저·실제 SAST HTTP 연결 검증 완료. 새 스키마는 `0004`. PR #18 병합 후 Jenkins 운영 릴리스에서 DB `0004`와 공개 UI/API를 배포했다. 운영 SAST URL·토큰 연결과 공개 플랫폼의 실제 SAST 검증은 아직이다. [Jenkins AWS 기록](platform-jenkins-aws.md)을 따른다.

## 설계와 기준

1. 같은 서비스의 성공한 빌드만 참조한다. build ID·image digest·공개 저장소·40자리 commit을 snapshot에 고정한다.
2. `security_check` 실행 ID를 SAST의 `run_id`로 사용한다. 응답 유실/Worker 복구 시 같은 ID·입력으로 조회/재접수한다. SAST API의 중복 방지 계약을 따른다.
3. API는 지정한 검사 서버만 호출하며 인증 토큰·private snapshot을 사용자에게 반환하지 않는다. POST 결과·상태의 저장소/커밋/ID를 대조한다.
4. 완료 상태만으로 안전 판정을 내리지 않는다. SARIF 2.1.0을 검증하고 fail/review/기타 결과 수와 최대 100개 규칙·위치를 저장한다. 원문 비밀값이나 코드 조각은 화면에 보내지 않는다. 전체 SARIF의 해시를 근거로 기록한다.
5. 상태 `succeeded`는 검사 실행 완료다. 조치·검토 필요 여부는 별도이며 `deployment_approved=false`, `dast_check=not_run`이다. 빈 결과도 전체 안전 보장이 아니다.
6. SAST 미설정·서비스 오류·커밋 불일치·근거 누락은 실패/미연결로 표시한다. 다른 서비스의 빌드를 빌려 검사하거나 활성 작업과 충돌할 수 없다.

## 다음 연결 경계

현재 ECS 실행 확인은 검사 후 태스크를 종료한다. DAST에는 점검 동안 앱을 유지하는 실행 소유권, 검사 서비스만 접근하는 네트워크, 고정 digest/대상 ID와 종료 계약이 필요하다. 기존 로컬 게시판용 DAST에 고객 앱 주소만 보내 동일한 대상으로 검사했다고 표시하지 않는다.

## 운영과 복구

`PLATFORM_SAST_URL`, `PLATFORM_SAST_TOKEN`을 서버에만 설정한다. 운영은 HTTPS, 로컬 검증은 loopback HTTP를 허용한다. SAST 서버의 저장소 허용 목록도 별도로 맞춰야 한다. 미설정 상태에서도 기존 플랫폼 기능은 유지되고 새 검사 요청만 503으로 거부된다.

DB migration `0004`는 실행 종류를 추가한다. 보안 검사 기록이 있으면 자동 downgrade를 거부한다. 이전 앱으로 복구할 때는 배포 전 DB 백업 또는 스키마 호환 릴리스를 사용한다.

## 검증 기록

2026-11-03 검증했다.

- `cd platform/backend && uv run pytest -q`: 전체 122개 통과, live GitHub 1개 선택 제외. 고정 커밋·digest 연결, 응답 유실 후 같은 ID 회수, 다른 커밋/무효 SARIF 거부, 0건의 한계, 인증/CSRF, 이력 격리를 포함한다.
- `cd platform/web && npm run build && npm run test:e2e`: 빌드 성공, 전체 9개 통과. 실제 API/별도 Worker/PostgreSQL을 사용하고 외부 SAST 응답은 fixture다. 조치 대상·확인 필요·근거 표시와 커밋 불일치 실패를 확인했다. 데스크톱/390px 모바일 스크린샷을 검토했고 가로 넘침이 없다.
- Ruff·Prettier 검사 통과. `0004`는 실제 테스트 DB에 적용됐다. 운영 DB migration은 하지 않았다.
- 이후 migration 테스트 2개를 추가하고 `uv run pytest -q tests/test_security.py`로 SAST 관련 14개가 통과했다. `0003 → 0004`에서 기존 빌드 이력 보존과, 보안 검사 기록이 있을 때 `0003` downgrade 거부·현재 스키마 보존을 실제 PostgreSQL에서 확인했다.

### 실제 SAST 연결

기존 `services/delta-sast/Dockerfile`로 이미지를 만들고 loopback `127.0.0.1:18790`에 임시 컨테이너를 실행했다. 별도 임시 토큰, `SAST_LLM_BACKEND=off`, 공개 Docker 저장소 허용 범위를 사용했다. AWS 인증이나 호스트 디렉터리는 전달하지 않았다.

실제 `SecurityRunner` HTTP 어댑터가 `docker/welcome-to-docker`의 Jenkins 검증 커밋 `68c1b9f87c41fb3fef2667e27149865c2f42d1eb`을 요청했다. SAST 실행 ID `12cb4c9e-4505-4946-90ea-07279b8cccd7`의 fetch/scan/done과 SARIF 수신을 확인했다. 결과를 실제 빌드 `c11607b2-32ef-4246-985b-b3ed9ec5739e`의 digest와 연결했다.

이 검증은 제출 기록 저장소만 메모리 대역을 사용한 **실제 HTTP 어댑터↔SAST/공개 GitHub 연결 시험**이다. DB/Worker/UI 연결은 위 통합·브라우저 시험으로 검증했으며 공개 운영 플랫폼에서 실행한 것은 아니다. 이 저장소는 주로 JavaScript이고 현재 검사기의 지원 규칙 범위에서 발견 사항이 0건이었다. 앱 전체 보안 통과나 JavaScript 전체 분석 성공으로 해석하지 않는다. LLM 호출도 0건이다.

근거 해시는 `bc861b59b1e1165574f694c8961d7ffe0f9fb07c5d76552d9adde6710baa6e15`, 결과는 ignored `platform/.runtime/sast-live-result.json`에 보존했다. 검증 후 임시 컨테이너와 토큰 파일을 제거했다. 운영 SAST 서버/팀 도구의 설정은 수정하지 않았다.

API·결과 필드·설정·롤백은 [검사 계약](../../platform/contracts/security-checks.md)을 따른다.

## AWS 운영 연결 (2026-11-04 진행)

사용자가 main pull 후 다음 작업 진행을 승인했다. 기준은 main `cc1b1c3`, 작업 브랜치는 `feat/platform-sast-aws`다. 이전 Jenkins 권한 수정과 운영 기록은 작업 트리에 보존한다.

- 기존 도구 호스트 `172.31.25.180`의 SAST를 재사용한다. 플랫폼 `10.80.1.70`과 서로 다른 VPC이므로 전용 peering과 두 VM `/32` 왕복 경로만 추가한다. 플랫폼 SG에서 도구 호스트의 443만 수신하며, 기존 NAT/고객 TGW 경로는 바꾸지 않는다.
- 도구 호스트의 사설 IP에 HTTPS 프록시를 두고 SAST 접수·상태·SARIF 경로만 전달한다. SAST/DAST의 loopback 포트와 DAST 컨테이너는 유지한다. TLS 키는 호스트 밖으로 꺼내지 않는다.
- 플랫폼에 SAST CA 설정을 추가한다. 인증서 검증을 끄지 않고, 신뢰 CA가 바뀌면 기존 실행을 다른 대상으로 이어가지 않는다. 연결 토큰은 기존 로그인·DB·Jenkins 값을 보존하며 SSM SecureString에 저장한다.
- SAST 저장소 허용 목록은 기존 팀 조직을 유지하며 검증용 공개 Docker 조직을 추가한다. 기존 검사와 상태 파일을 보존하고 활성 검사 중에는 재시작을 거부한다. 모델 인증 설정은 유지한다.
- 성공 기준: TLS 미신뢰 거부/명시 신뢰 성공, 프로젝트 외 접근 거부, 기존 DAST 보존, 실제 공개 커밋의 SAST 검사와 SARIF 해시/이미지 연결, 공개 UI 결과 확인. 0건은 전체 안전 판정이 아니다.
- 플랫폼 코드 릴리스는 main 전용 GitHub 경계를 따른다. 구현·테스트·인프라 적용·서버 설정·공개 릴리스 상태를 따로 기록한다.

### 현재 구현·적용 결과

- 백엔드 전체 131개 통과/live 1개 제외. 실제 TLS 서버의 미신뢰 거부·명시 CA 성공, 무효 CA, 기존 실행의 신뢰 변경 거부를 추가했다. 배포 도구 29개, 네트워크 mock 3개 통과. Ruff·형식·shell 문법 검사 통과.
- Terraform 데이터 소스의 VPC 조회는 VM의 서브넷에서 확인하도록 수정했다. 최종 실제 Plan은 peering·왕복 `/32` Route·플랫폼 SG 참조 ingress, 총 4개 추가만 포함했다. SSO로 검토한 저장 Plan을 적용했고 변경/삭제는 0개다. peering은 `pcx-0521ac9dd5c734380`이다.
- 기존 도구 호스트에 사설 HTTPS 설치 완료. 인증서 검증한 `/healthz` 200, 토큰 없는 상태 조회 401, `/v1/tools`·DAST 경로 404를 확인했다. 외부 8790/8765/8766 포트는 계속 loopback이다.
- SAST는 같은 이미지·기존 상태/모델 설정으로 재시작했다. 모델 backend `api`와 기존 모델 값, DAST 컨테이너 ID/시작 시각은 그대로다. `stage`로 연결 정보를 SecureString에 저장했고 공개 플랫폼의 runtime은 아직 바꾸지 않았다.

### 실제 사설 경로 검증

- 새 `SecurityRunner`로 실제 AWS SAST에 접속했다. 임시 SSM 원격 포워딩을 **플랫폼 VM**에서 도구 VM `172.31.25.180:443`으로 연결하고, 원래 대상 IP의 인증서를 그대로 검증했다. 신뢰하지 않은 인증서는 거부됐고 등록 CA로는 정상 접속했다. 검증 후 임시 세션은 종료했다.
- 실제 검사 ID `fb0dddb8-6c5d-4b9e-8272-b94a1c2f7615`. 기존 공개 Jenkins 빌드 `0f95caa8-ae33-4159-84e9-20190f255311`의 `docker/welcome-to-docker` 고정 커밋과 ECR digest를 입력으로 검사했고, SARIF 근거 해시 `a5103e0c25d9f28e77cd8f5f2a06b675b0d05918d74a2b8ae48ed2d5cb792734`를 대조했다.
- 이 검증은 **실제 HTTP 어댑터·AWS 네트워크·SAST**를 사용하되 제출 저장소만 메모리 대역이다. 공개 플랫폼에서 실행한 검사로 표시하지 않는다. JavaScript 중심 저장소에서 현재 규칙의 발견 사항은 0건이었으며 전체 안전 판정이나 배포 승인으로 해석하지 않는다. DB/Worker/UI 연동은 기존 통합 검증과 이번 백엔드 회귀가 담당한다.
- 같은 플랫폼 VPC의 Jenkins 에이전트에서 SAST 443 접근은 실패했다. 허용한 플랫폼 경로와 구분됐다. 연결 root 최종 Plan은 No changes, 종료 코드 0이다.
- 활성화 설정의 dry run에서 기존 로그인·DB·Jenkins·샌드박스 필드가 모두 유지되는 것을 확인했다. `/delta-platform/runtime`은 버전 4로 유지되며 아직 SAST 값이 없다. 연결 토큰/CA는 별도 SecureString에만 준비됐다.
- ignored 근거: `platform/.runtime/sast-private-live-result.json`, `sast-host-verification.json`, `sast-denied-source.json`, `sast-network-plan.json`, `sast-network-final-plan.log`.

현재 구현·로컬 회귀·실제 네트워크/서버 설치·인증/검사 검증은 완료했다. **main 반영 → SAST 설정 activate → GitHub Platform release → 공개 웹 SAST 검증은 아직**이다. 기존 main 전용 OIDC와 사용자의 PR 병합 절차를 유지한다. 사용자가 `Go.`로 커밋·푸시·PR 생성을 승인했다. 기존 Jenkins 권한 보완은 `42bbe5b`로 분리했고 SAST 연결은 별도 기능 커밋으로 묶는다.

- 마무리 확인: 공개 플랫폼 readiness·기존 ID/PW 로그인·기존 Jenkins 성공 결과가 유지됐다. 임시 포워딩 세션은 AWS 이력에서도 `Terminated`를 확인했다. 사용자가 커밋·푸시·PR 생성을 승인했고 원격 게시를 진행한다. main 병합과 공개 SAST 활성화는 별도 단계다.

- CI 게시 전 재검증: 시스템 `python3`에는 boto3가 없어 병합 함수 단위 테스트가 실패하는 것을 재현했다. 해당 순수 함수 시험에서 SDK import만 대역으로 분리해 기존 배포 테스트 방식에 맞췄다. 새 SAST 배포 스크립트도 CI Ruff 검사에 포함했다.
