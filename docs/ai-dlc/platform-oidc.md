# 플랫폼 GitHub OIDC 연결

## 승인과 범위

2026-11-03 사용자 `Let's go`로 배포 자동화 마무리를 승인받았다. GitHub 변수는 사용자가 직접 입력하므로 에이전트는 값을 문서로 제공하고 GitHub 변수·secrets를 변경하지 않는다. 브랜치는 병합된 PR #7을 기준으로 만든 `feat/platform-oidc`다.

후속 `Go ahead`로 안내한 커밋·푸시·PR·CI 확인을 진행한다. 커밋 전에 Conventional Commit 메시지를 사용자에게 알렸다. main 반영 후 CI 활성화 변수는 사용자가 변경한다.

## 설계

- GitHub Free/private 저장소이므로 Environment를 제거하고 repository variables를 사용한다.
- 저장소 API에서 확인한 immutable subject `repo:2026-sbh-delta@350740329/delta-network-gateway@1441419092:ref:refs/heads/main`만 AWS 신뢰 정책에 허용한다.
- Plan·Apply·Release 역할을 분리한다. 같은 main 신뢰 범위이므로 main의 코드를 변경할 수 있는 사람은 배포 권한을 사용할 수 있다. 별도 승인자 강제 기능으로 표현하지 않는다.
- OIDC provider와 세 역할은 `platform/deploy/identity/`의 별도 Terraform state로 관리한다. 일반 GitHub Apply 역할은 IAM 역할·정책을 수정하거나 identity state에 접근하지 못한다.
- Plan은 조회와 state 잠금, Apply는 기존 플랫폼의 인프라 변경과 state 저장, Release는 ECR·릴리스 S3·플랫폼 앱 VM의 SSM 배포를 담당한다.
- OIDC 신뢰·IAM 역할, 플랫폼 VM 역할의 권한 변경은 관리자 부트스트랩 영역이다. 일반 Apply에서 IAM 변경이 필요한 경우 권한을 넓히지 않고 작업을 중단한다.

## 구현·검증 순서

1. 환경별 역할 변수 대신 `AWS_PLAN_ROLE_ARN`, `AWS_APPLY_ROLE_ARN`, `AWS_RELEASE_ROLE_ARN`을 선택한다. main·수동 실행·저장 Plan 검증을 유지한다.
2. 별도 IAM 기반 코드, 신뢰·권한의 허용/거부 검증, 실제 입력값과 사용자용 변수 안내를 작성한다.
3. 업무 계정 STS 확인 후 기존 OIDC provider를 조회한다. 존재하면 재사용하고 없을 때만 생성한다.
4. 저장 Plan을 검토해 IAM 기반을 적용하고 정책·역할 상태를 확인한다. GitHub 변수는 직접 설정하지 않는다.
5. workflow 문법·회귀 테스트를 수행한다. GitHub의 실제 OIDC 인증·배포는 변수 설정과 main 반영 후 별도 검증한다.

## 현재 상태

워크플로·IAM 코드와 사용자용 [변수 안내](../../platform/deploy/GITHUB.md), [복사용 값](../../platform/deploy/github-variables.json)을 준비했다. 변수 목록이 워크플로에서 실제 참조하는 9개와 일치하는지 자동 검증한다. 계정 확인과 기존 provider 소유권을 보존하는 입력 준비 도구도 구현했다.

AWS CLI v2.39.3을 설치하고 사용자 브라우저 승인으로 `delta-platform` SSO 프로필을 구성했다. SSO 리전은 `us-east-1`, 실제 서비스·Terraform 리전은 `ap-northeast-2`다. STS로 업무 계정 `616723161368`을 확인했다. 만료된 임시 키 전달 파일과 일회용 전달 스크립트는 제거했다.

실제 저장 Plan을 검토하고 IAM 기반 **11개 추가·변경 0·삭제 0**을 적용했다. GitHub OIDC provider 1개, 역할 3개, managed policy 3개, 연결 4개다. 기존 플랫폼 VM·네트워크 변경은 없다. 세 역할 ARN은 사용자가 등록한 변수와 일치한다. 적용 후 IAM 신뢰 확인·권한 시뮬레이션 19개·최종 Plan No changes를 확인했다. 구현을 `a0714e8`에 커밋·푸시했고 [PR #8](https://github.com/2026-sbh-delta/delta-network-gateway/pull/8)의 전체 CI 통과 후 main에 병합했다(`6b404fc`). 사용자가 CI를 활성화했고 실제 GitHub OIDC 인증과 Plan을 검증했다. 실제 GitHub Release와 배포 후 HTTPS·로그인·새 Worker 작업도 검증했다.

사용자가 Repository variables 9개를 등록했고 API 대조로 일치를 확인했다. 사용자가 `PLATFORM_CI_ENABLED=true`로 변경한 것을 API로 확인했다. 에이전트는 GitHub 변수·Secrets를 변경하지 않았다.

## 검증 기록

2026-11-03 로컬 검사와 업무 계정의 실제 AWS 검증을 다음과 같이 실행했다.

| 명령·확인 | 결과 |
| --- | --- |
| `python3 -m unittest discover -s platform/deploy/tests -q` | 16개 통과. 변수 일치, 잘못된 계정 거부, 기존 provider 재사용·소유권 유지, 비밀값을 CI로 읽지 않는 배포 경로 포함 |
| `ruff check`·`ruff format --check` (`platform/deploy/scripts`, `platform/deploy/tests`) | 통과 |
| `actionlint .github/workflows/platform-*.yml` | 통과 |
| identity root의 `terraform fmt -check -recursive`·`validate`·`test` | 통과, mock 6개. 실제 AWS 호출 검증은 아님 |
| 생성 정책 JSON 크기 | Apply 5,711자, Plan 2,656자, Release 1,617자. 각 managed policy 한도 6,144자 이내 |
| 두 root의 `.terraform.lock.hcl` 비교 | 동일. 기존 Linux CI에서 검증한 AWS provider 버전·체크섬 유지 |
| `gh pr view 7`, 저장소 OIDC API 조회 | PR #7 병합 및 전체 Checks 성공, immutable subject 확인 |
| GitHub Actions variables API 조회·값 비교 | 사용자가 등록한 9개 모두 일치, CI 활성화 값 false 확인 |
| 초기 임시 인증 확인 | 전달받은 복사본은 `ExpiredToken`. 이 인증으로 변경하지 않고 SSO 프로필로 전환 |
| `brew install awscli`, `aws --version` | v2.39.3 설치·실행 확인 |
| `aws configure sso --profile delta-platform`, STS 조회 | 사용자 브라우저 승인 후 업무 계정 616723161368 확인. 기본 배포 리전 서울 |
| `prepare-identity.py`, identity의 `init`·저장 `plan` | 기존 provider가 없어 새 provider 포함 11개 추가만 확인. 별도 S3 identity state |
| AWS Access Analyzer `ValidatePolicy` | managed policy 3개 finding 0. trust 3개는 오류·경고 0, audience 형식 확인 suggestion 각 1개. 이미 단일 값 `StringEquals`를 사용해 권고 충족 |
| `terraform apply identity.tfplan` | 11 added, 0 changed, 0 destroyed |
| IAM `GetRole`, `SimulatePrincipalPolicy` | 신뢰 정책 3개 일치, 허용·거부 19개 모두 예상과 일치. 실제 OIDC 실행 검증은 아님 |
| identity의 최종 `plan -detailed-exitcode` | 종료 0, No changes |
| 적용 후 `prepare-identity.py` 재실행 | 입력·backend 파일 동일, 이미 관리 중인 provider 소유권 유지 |
| `git diff --check` | 통과 |
| PR #8 원격 CI | test·Terraform 두 root 모두 성공, 실행 41553337823 |
| Git 게시·병합 | `a0714e8` 커밋·원격 브랜치 푸시·PR #8 생성 후 main `6b404fc` 병합 확인 |

애플리케이션 코드 변경은 없다. [PR CI 41553337823](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41553337823)에서 API·배포 도구·브라우저와 두 Terraform root 검사가 모두 통과했다. PR 이벤트 실행이 늦게 나타나 중복 요청한 수동 CI 41553375515는 취소했다. 이후 실제 GitHub Release로 운영 앱을 재배포하고 아래 기능 검증까지 완료했다.

## 실제 OIDC Plan과 권한 보완

[Platform infrastructure 41553866142](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41553866142)의 첫 실행에서 OIDC 역할 인증과 S3 state 초기화는 성공했다. Plan은 CloudFront data source의 `ec2:GetManagedPrefixListEntries` 권한 누락으로 실패했다. `ec2:Describe*`는 이 API를 포함하지 않는다.

- 로그가 거부된 action·resource·역할을 명시했고 동일 action/ARN의 IAM 시뮬레이션도 `implicitDeny`였다. 원인이 특정돼 다중 가설·추가 계측은 생략했다.
- 회귀 mock에서 해당 조회 권한 누락 실패를 확인한 뒤, 기존 플랫폼 state에서 얻은 서울 CloudFront prefix list `pl-22a6434b`의 정확한 ARN만 읽도록 추가했다. 다른 목록·다른 리전으로 범위를 넓히지 않았다.
- 로컬 배포 도구 테스트 17개, IAM mock 7개가 통과했다. mock의 provider 생성 기본값은 명시적으로 false로 고정해 로컬 실계정 tfvars가 시험 전제에 영향을 주지 않게 했다.
- 실제 저장 Plan은 `aws_iam_policy.plan` 1개 갱신뿐임을 확인했다. Access Analyzer finding 0을 확인한 뒤 적용했다. 해당 목록은 `allowed`, 다른 목록·리전은 `implicitDeny`였다. IAM root의 최종 Plan은 다시 No changes다.
- 같은 GitHub 실행의 2차 시도가 성공했다. 다운로드한 Plan의 commit·체크섬을 확인했고 실제 리소스 변경은 0이다. 플랫폼 인프라 Apply는 불필요해 실행하지 않았다. Apply 역할의 실제 GitHub 실행을 검증한 것으로 표현하지 않는다.

## 실제 Release와 기능 검증

[Platform release 41554586247](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41554586247)이 main `6b404fc`에서 성공했다. GitHub의 Release OIDC 역할로 ARM64 이미지를 ECR에 게시하고, 기존 서울 앱 VM에 SSM으로 배포했다. 모든 사전 검사·배포·HTTPS readiness 단계가 성공했다.

- 배포 artifact의 SSM 결과 `Success`, response code 0을 확인했다. PostgreSQL 이미지 digest는 기존 고정값과 같았다.
- 실제 HTTPS 웹·readiness 200, 기존 계정 로그인·Secure/HttpOnly/SameSite 쿠키·세션 로그아웃을 확인했다. 비밀값을 CI나 문서에 기록하지 않았다.
- 기존 서비스 ID `f886dfba-2dba-4026-aa38-8acc222a75d2`가 유지됐다. 새 소스 확인 `0b77568d-fab3-48ea-87e4-1341dfb81f31`을 접수해 Worker 성공과 이벤트 8개를 확인했다. GitHub 공개 예제의 고정 커밋 `68c1b9f87c41fb3fef2667e27149865c2f42d1eb`을 확인한 결과이며 고객 앱 이미지 빌드·배포 검증은 아니다.
- 미로그인 업무 API 401, 다른 Origin의 POST 403을 확인했다. 백업·복원·강제 재시작 시험은 이번 IAM/CI 변경 때문에 반복하지 않았다.
- 증거는 ignored `platform/.runtime/ci-plan-41553866142/`, `ci-release-41554586247/`, `https-release-smoke.json`에 보관한다. Terraform Plan·자격 증명·로그인 비밀번호는 커밋하지 않는다.

권한 보완 코드와 검증 기록은 `fix(ci): allow CloudFront managed prefix list reads` 후속 변경으로 게시한다. 배포 검증 이후의 변경은 IAM 권한·입력 준비·회귀 시험·문서이며 앱 런타임 코드는 바꾸지 않는다.

## 다음 작업

U2 공개 소스 격리 빌드를 구현한다. 현재 고객 서비스 기능은 소스 사전 확인까지이며, 고객 Dockerfile 실행과 이미지 게시·VM 배포는 다음 단위다.
