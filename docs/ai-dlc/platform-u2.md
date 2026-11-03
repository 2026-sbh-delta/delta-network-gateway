# 플랫폼 U2: 공개 저장소 이미지 빌드

- 기준일: 2026-11-03. 브랜치 `feat/platform-builds`.
- 승인: 사용자의 `Let's do next jobs.`에 따라 U2를 시작했고, CodeBuild 비교 후 `Then I will follow your decision.`으로 실행기 선택을 승인받았다. 구현 범위를 사전에 공유했다.
- 상태: 구현·로컬 검증·AWS 빌드 기반 Apply·실제 이미지 빌드 완료. 백엔드·브라우저 최종 재검증 완료. 구현 커밋 `d0d3154`를 게시하고 [PR #14](https://github.com/2026-sbh-delta/delta-network-gateway/pull/14)를 생성했다. GitHub CI 실행 중이며 병합은 사용자가 진행한다. 앱 운영 배포는 아직 하지 않았다.

## Ideation / Inception

개발자가 공개 GitHub 저장소를 등록한 뒤 이미지 빌드 결과를 플랫폼에서 확인한다. 성공한 소스 검사의 커밋을 그대로 사용하고, ECS 검사에 전달할 `image@sha256:…`을 저장한다. 고객 Dockerfile을 플랫폼 VM이나 Terraform CI에서 실행하지 않는다.

범위는 빌드 접수·이력·상태·결과와 CodeBuild/ECR 연결이다. ECS 실행 검사, Jenkins 전체 파이프라인 교체, 고객 VM 배포, 모니터링은 후속 단위다.

## Design / Implementation Plan

```text
웹 → API → PostgreSQL 작업/이벤트
                ↕ Worker (시작·조회만)
           AWS CodeBuild (일회성 x86_64 실행 환경)
                ├─ 공개 GitHub의 고정 커밋
                ├─ 플랫폼이 관리하는 빌드 명령 + 고객 Dockerfile
                └─ 전용 ECR → 이미지 digest → DB → 웹
                                      └─ U3 ECS 검사 입력
```

1. 기존 `runs`에 `image_build` 종류와 외부 실행 정보를 추가한다. 기존 소스 검사 API와 이력은 유지한다.
2. 성공한 `source_check_id`를 명시해 빌드를 요청한다. 커밋·빌드 경로를 복사하고 브랜치를 다시 해석하지 않는다.
3. 요청 키로 중복 접수를 막는다. CodeBuild 시작 의도를 먼저 저장하고 실행 ID를 받아 기록한다. 복구 시 같은 실행을 조회한다. 5분짜리 AWS 중복 방지 기간을 넘긴 불명확한 시작은 자동 재실행하지 않는다.
4. 빌드 명령은 플랫폼 Terraform에 고정한다. 고객 저장소의 buildspec/Jenkinsfile은 실행하지 않는다. Dockerfile·context 경로와 실제 체크아웃 커밋을 다시 검사한다.
5. `linux/amd64`, 단일 이미지 manifest로 만들고 ECR의 실제 digest를 조회한다. ECS가 받은 이미지와 비교할 수 있게 한다.
6. 웹에 빌드 버튼·상태·단계·실패 원인·이미지 주소를 표시한다. 성공은 실행 검사나 배포 성공을 뜻하지 않는다.
7. PostgreSQL 통합, 재시작/중복 접수/실패 검증, AWS SDK 계약, Terraform mock, 브라우저 검증 후 실제 빌드를 확인한다.

## 권한·비용·운영 경계

- 빌드 역할은 전용 ECR push와 전용 CloudWatch 빌드 로그만 허용한다. Terraform, IAM 변경, 플랫폼 SSM 설정, DB, 고객 운영망 권한은 없다.
- CodeBuild는 Docker 빌드를 위한 privileged 환경이다. 관리 권한과 실행 호스트를 분리하는 것이며, 적대적 다중 고객에 대한 완전한 격리를 검증했다고 주장하지 않는다. 공개 서비스 전에는 고객별 registry 권한·네트워크·자원 격리 강화가 필요하다.
- 고객 build args/secret/SSH 전달은 지원하지 않는다. 소스는 공개 저장소만 허용한다.
- 최소 on-demand 빌드 환경, 동시 빌드 1개, 실행 15분·AWS 대기 10분 제한. 상시 빌드 VM은 만들지 않는다. Worker는 한 번에 한 작업을 처리하므로 장시간 빌드 동안 다른 작업은 DB에서 대기한다.
- 빌드 기반은 별도 Terraform root/state로 관리하고 플랫폼 관리 CI 역할을 고객 빌드에 사용하지 않는다. 기존 IAM 역할에 제한된 호출 권한을 연결하는 초기 작업은 SSO 관리자 실행이다.
- 기존 AWS 플랫폼은 ARM64지만 고객 이미지/ECS 스테이징은 x86_64다. 두 아키텍처를 혼동하지 않는다.
- 로그는 빌드 전용이다. 고객 서비스의 중앙 로깅/모니터링 구현은 이 단위에 포함하지 않는다.

## 완료 기준과 검증

- 동일 요청 재전송은 같은 실행을 반환한다.
- 실패한/다른 서비스의 소스 검사로는 빌드할 수 없다.
- Worker 중단 후 기존 외부 실행을 이어 확인하며 새 빌드를 만들지 않는다.
- 실패/시간 초과/미설정은 성공으로 표시하지 않는다.
- 성공 결과에 커밋, 빌드 실행 ID, `linux/amd64`, 전용 ECR 이미지 digest를 저장한다.
- 실제 실행한 검증 결과는 이후 이 문서에 기록한다.

## 참고

- [CodeBuild StartBuild: 중복 방지 토큰은 5분 유효](https://docs.aws.amazon.com/codebuild/latest/APIReference/API_StartBuild.html)
- [CodeBuild 개요](https://docs.aws.amazon.com/codebuild/latest/userguide/welcome.html)
- [Jenkins 빌드 격리](https://www.jenkins.io/doc/book/security/securing-builds/)

## 작업 중 main 갱신

사용자가 main을 pull해 현재 브랜치가 `1def073`에서 `23aa0b1`(PR #12)로 fast-forward됐다. 미커밋 U2 변경은 보존됐고 충돌은 없었다. 변경 파일은 `deploy/tooling/README.md`, `remote.sh`, `services/delta-dast/dast_agent/server.py` 세 개다. SAST·DAST의 Anthropic 인증에 AWS OIDC/WIF를 추가했으며 U2 빌드 API와 직접 겹치지 않는다. U3에서는 설정 존재 표시(`credential_present`)만으로 인증 성공을 판단하지 않고 실제 도구 호출을 확인한다. 이 WIF, GitHub OIDC, 로컬 관리자 SSO, 플랫폼 고정 로그인은 서로 다른 인증 경로다.

## 검증 기록 (2026-11-03)

| 구분 | 명령·확인 | 결과 |
| --- | --- | --- |
| 초기 로컬 환경 | `uv run pytest -q` | Docker가 꺼져 있어 DB 연결 실패. Docker·시험용 PostgreSQL 시작 후 아래 검증 수행 |
| PostgreSQL 통합 | `uv run pytest -q --tb=short --maxfail=3` | 최종 71개 통과, 실제 GitHub 별도 테스트 1개 제외. 실제 소스·빌드는 아래 AWS 검증으로 별도 확인 |
| 브라우저 | `npm run build`, `npm run test:e2e` | 빌드 성공, 5개 통과. 실제 DB/API/Worker, GitHub·AWS 응답은 fixture. 데스크톱/모바일 캡처 확인 |
| 배포 도구 | `python3 -m unittest discover -s platform/deploy/tests -q` | 19개 통과 |
| Terraform mock | builds `validate`·`test`, 기존 platform `test` | validate 성공, 빌드 mock 1개·플랫폼 mock 3개 통과 |
| AWS 계정 | `aws sts get-caller-identity --profile delta-platform` | 616723161368. 작업 리전 ap-northeast-2. SSO 재로그인, 신규 계정/장기 키 등록 없음 |
| AWS Plan/Apply | builds root 별도 S3 state | 6개 추가. 기존 네트워크/VM/DB 교체 없음 |
| 권한 시뮬레이션 | 빌드 역할 `simulate_principal_policy` | 자기 ECR push 허용, 플랫폼 ECR push·SSM runtime·Terraform state·IAM 생성·EC2 생성 거절, 6개 확인 |
| 첫 실제 빌드 | CodeBuild `630fb33a-0a9b-4856-82ef-78d6e2b6ab8e` | 패키지 설치 단계 실패. DB가 실패를 기록하고 이미지 결과를 저장하지 않음 |
| 네트워크 보완 | 빌드 명령의 DOCKER-USER 규칙 | private 목적지 차단이 정상 응답도 차단하므로 established 응답과 호스트 resolver의 DNS를 먼저 허용. CodeBuild 프로젝트 1개만 업데이트 |
| 실제 재빌드 | CodeBuild `67782e3f-a50e-4000-a96a-d8e30e77c14a` | 성공. 아래 커밋·digest를 ECR과 대조하고 DB에 저장 |
| 실제 결과 API | 로그인한 로컬 API TestClient → 실제 AWS | 결과 succeeded·같은 digest 확인, CloudWatch 로그 4,277자 조회 성공 |
| runtime 설정 | `configure-runtime.py` | 기존 SSM 설정에 공개 빌드 값 세 개만 병합, 버전 2. 기존 ID/비밀번호·DB 설정 유지 |
| 최종 실제 Plan | builds `plan -detailed-exitcode` | 종료 코드 0, No changes |
| 기존 플랫폼 Plan | platform root 실제 Plan | app VM metadata hop limit 1→2의 in-place 변경 1개만 확인. 추가·삭제 없음, Apply 미실행 |
| 최종 재검증 | pytest·브라우저·ruff·prettier·actionlint·diff | 백엔드 71개·브라우저 5개·배포 도구 19개 통과, 형식·워크플로 검사 성공. Release 스크립트는 bash 문법 검사만 수행했으며 새 순서의 실제 배포는 미실행 |

- 실제 소스: `https://github.com/docker/welcome-to-docker`, 커밋 `68c1b9f87c41fb3fef2667e27149865c2f42d1eb`.
- 실제 결과: `616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds@sha256:f443317a8cf63d5ad1efcba56700d2619aa4b139befa2003f7fcab56973cf576` (`linux/amd64`).
- 로컬 Worker·DB와 실제 AWS를 연결한 검증이다. 공개 플랫폼에서 U2를 실행했다고 표현하지 않는다.
- 고객 소스 취약점 검사나 ECS 실행 검사는 아직 하지 않았다. 스캔 승인 상태가 아니다.

## Operation / 남은 게시·배포

- 기존 플랫폼 URL은 U1을 계속 제공한다. 빌드 기반만 실제 AWS에 구성했다.
- `platform/deploy/terraform`의 app VM IMDSv2 hop limit 2는 코드 변경이며 아직 Apply하지 않았다. 게시 후 기존 GitHub Infra Plan/Apply로 적용하고 Release로 U2를 배포한다.
- 스키마 `0002`는 빌드 종류·외부 실행 정보를 추가한다. Release는 기존 API/Worker를 멈추고 S3 DB 백업 후 migration을 실행한다. 구버전 Worker와 새 작업 종류가 섞이지 않게 한다. 단일 VM이므로 이 배포 구간에는 중단이 있다.
- U1 바이너리는 schema `0001`을 요구한다. U2를 쓴 뒤 단순히 U1 이미지로 돌리면 readiness가 실패한다. 수정 릴리스나 DB 백업 복구가 필요하며 빌드 기록이 있는 상태의 downgrade는 거절한다.
- AWS 자원 추가와 앱 운영 배포를 구분한다. Conventional Commit 메시지와 검증 결과를 공유했고 사용자의 `Commit.` 지시로 로컬 커밋을 승인받았다. 구현 커밋을 푸시하고 [PR #14](https://github.com/2026-sbh-delta/delta-network-gateway/pull/14)를 열었다. main 병합은 사용자가 진행하며 앱 Release는 아직 하지 않았다.

## Git 기록

- 사용자 `Commit.` 지시에 따른 로컬 커밋: `feat(platform): add public repository image builds with CodeBuild`.
- 대상 브랜치: `feat/platform-builds`, 기준 main: `23aa0b1` (PR #12).
- 실제 설정·state·Plan·실행 로그·자격 증명 파일은 커밋에서 제외한다. 원격 게시·배포 승인은 이 로컬 커밋과 구분한다.

- 2026-11-03 사용자 `Open PR. I'll merge it.` 지시로 `git push -u origin feat/platform-builds`와 PR 생성을 완료했다. [PR #14](https://github.com/2026-sbh-delta/delta-network-gateway/pull/14)는 OPEN·MERGEABLE이며 GitHub Platform checks가 시작됐다. CI 최종 결과는 PR Checks를 따른다. 병합·배포는 실행하지 않았다.
