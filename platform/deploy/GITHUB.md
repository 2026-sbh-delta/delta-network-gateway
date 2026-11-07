# GitHub에서 플랫폼 배포하기

인프라는 **GitHub Actions → OIDC 임시 인증 → Terraform → AWS**, 앱은 **테스트 → ARM64 이미지 → ECR → SSM → 사설 VM**으로 배포한다. 터미널의 AWS 키를 GitHub에 저장하지 않는다.

2026-11-07 현재 운영은 [기존 UI 보존과 PR #75 통합 릴리스](../../docs/ai-dlc/platform-production-ui-integration.md)다. main의 Release42363991423은 설정 병합에서 실패했고 이후 SSM 보존 배포로 반영했다. 현재 운영을 main 전체 이미지와 같은 버전으로 표시하지 않는다. 소스의 게시 상태는 Git 이력을 따른다. 운영 UI를 유지하는 빌드/적용/복구는 연결된 통합 기록을 따른다.

2026-11-03 PR #8 병합 후 실제 GitHub OIDC Plan·Release와 배포 후 기능 검증을 완료했다. 현재 저장소의 `PLATFORM_CI_ENABLED`는 사용자가 `true`로 설정했다. [실행·검증 기록](../../docs/ai-dlc/platform-oidc.md)을 따른다.

## 최초 설정에 필요한 변수

[저장소 Actions 변수 설정](https://github.com/2026-sbh-delta/delta-network-gateway/settings/variables/actions)에서 **Variables → Repository variables → New repository variable**로 아래 9개를 등록한다. Secrets가 아니다. 현재 Free/private 저장소에서는 Environment를 사용하지 않는다.

| Name | Value |
| --- | --- |
| `AWS_ACCOUNT_ID` | `616723161368` |
| `AWS_REGION` | `ap-northeast-2` |
| `AWS_PLAN_ROLE_ARN` | `arn:aws:iam::616723161368:role/delta-platform-github-plan` |
| `AWS_APPLY_ROLE_ARN` | `arn:aws:iam::616723161368:role/delta-platform-github-apply` |
| `AWS_RELEASE_ROLE_ARN` | `arn:aws:iam::616723161368:role/delta-platform-github-release` |
| `PLATFORM_STATE_BUCKET` | `delta-platform-tf-616723161368` |
| `PLATFORM_TF_INPUTS` | 아래 JSON 전체 |
| `PLATFORM_POSTGRES_IMAGE` | 아래 이미지 주소 전체 |
| `PLATFORM_CI_ENABLED` | 먼저 `false`, AWS 역할 생성과 새 워크플로의 main 반영을 확인한 뒤 `true` |

2026-11-03 위 역할 3개를 AWS에 생성하고 신뢰 정책·권한 시뮬레이션을 확인했다. GitHub의 실제 Plan·Release 인증과 배포도 성공했다. 위 표의 CI 플래그 false는 최초 설정값이며, 현재 활성화된 저장소에서 다시 입력할 필요는 없다. 모든 값의 복사용 원본은 [github-variables.json](github-variables.json)에 있다.

`PLATFORM_TF_INPUTS`:

```json
{
  "account_id": "616723161368",
  "region": "ap-northeast-2",
  "availability_zone": "ap-northeast-2a",
  "ami_id": "ami-029fa37c14d9a8917",
  "bootstrap_origin_access": false
}
```

`PLATFORM_POSTGRES_IMAGE`:

```text
616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform@sha256:a78d9af300151935bbc4dfaf00aae291d12abaea3037c636e43603cc1675684e
```

PostgreSQL 이미지는 기존 검증 버전으로 고정한다. 앱 배포 때 DB 이미지까지 함께 업그레이드하지 않는다. `AWS_ROLE_ARN`이라는 별도 변수나 AWS access key·secret key는 등록할 필요가 없다.

## 세 가지 실행

| 워크플로 | 시점 | 하는 일 |
| --- | --- | --- |
| `platform-check.yml` | PR·main/feat/platform push·수동 | API/Worker·브라우저·Terraform mock 검사. AWS 인증 없음 |
| `platform-infra.yml` | main에서 수동 `plan` → 검토 후 수동 `apply` | Plan 역할로 조회하고 Apply 역할로 같은 커밋의 저장 Plan 적용 |
| `platform-release.yml` | main에서 수동 | 테스트를 통과한 플랫폼 이미지를 Release 역할로 배포. 인프라 Apply 없음 |

기존 `delta-platform-tf-616723161368/platform/terraform.tfstate`를 이어받는다. 기존 리소스를 다시 만들거나 import하지 않는다. 인프라와 앱 배포는 같은 concurrency group으로 직렬화한다.

## AWS 연결과 권한

관리자가 [identity Terraform](identity/README.md)으로 OIDC provider와 세 역할을 준비한다. 별도 `platform/identity.tfstate`를 사용하며, 일반 GitHub Apply 역할은 IAM 역할·정책이나 이 state 내용을 수정할 수 없다.

최초 설정·복구 등 로컬 작업에는 **AWS CLI v2의 SSO 프로필**을 사용한다. Access Portal에서 복사한 임시 키는 자동 갱신되지 않으므로 반복해서 전달하지 않는다.

```bash
# 최초 한 번: Access Portal의 SSO 주소 또는 Issuer URL과 SSO 리전을 입력
aws configure sso --profile delta-platform

# 로그인 세션이 만료됐을 때
aws sso login --profile delta-platform

# 업무 계정 616723161368인지 확인
aws sts get-caller-identity --profile delta-platform
```

SSO 리전과 서비스 배포 리전은 별개다. 이 프로젝트의 배포 리전은 `ap-northeast-2`다. 로컬 Python·Terraform은 `AWS_PROFILE=delta-platform`으로 실행하고, 이전에 export한 `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`은 먼저 해제한다. SSO 설정은 로컬 `~/.aws/config`에 저장하며 로그인 캐시를 저장소에 넣지 않는다. 로그인 세션이 유효한 동안 SDK가 임시 인증을 갱신한다. GitHub 배포는 이 개인 SSO 세션 대신 위 OIDC 역할을 사용한다. [AWS SSO 설정 안내](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html).

| 역할 | 용도 |
| --- | --- |
| Plan | 기존 플랫폼 설정 조회, 앱 state 읽기와 잠금 |
| Apply | 기존 플랫폼 인프라 변경, 앱 state 저장. IAM 기반 변경은 관리자 작업 |
| Release | 지정 ECR 이미지 게시, 릴리스 S3 업로드, 플랫폼 앱 VM에 SSM 배포 |

신뢰 정책의 audience는 `sts.amazonaws.com`이다. 저장소 API로 확인한 아래 subject와 **정확히 일치하는 main 실행**만 허용한다.

```text
repo:2026-sbh-delta@350740329/delta-network-gateway@1441419092:ref:refs/heads/main
```

세 역할은 같은 main을 신뢰한다. 역할을 나눴다고 별도 승인자가 강제되는 것은 아니다. main 코드를 변경할 수 있는 사람의 권한을 배포 권한으로 취급한다. `PLATFORM_CI_ENABLED`도 배포 활성화 스위치이며 승인 기능은 아니다. [GitHub OIDC 안내](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws) · [immutable subject](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims) · [Environment 지원 범위](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

## 설정 후 실행 순서

1. AWS 역할 생성과 이 변경의 main 반영을 확인한 뒤 `PLATFORM_CI_ENABLED=true`로 바꾼다.
2. main의 **Platform infrastructure**를 `operation=plan`으로 실행한다.
3. 변경 목록과 `platform-plan` artifact의 `plan.txt`를 검토한다. 변경이 없다면 Apply는 필요 없다.
4. 변경이 필요하면 `operation=apply`, `plan_run_id=<검토한 실행 ID>`로 실행한다. 워크플로·main·커밋·성공 상태·artifact 체크섬을 확인한 뒤 저장 Plan을 적용한다.
5. 앱을 갱신할 때 **Platform release**를 main에서 실행한다. 완료 후 HTTPS 화면과 readiness를 확인한다.

검토 이후 main 커밋이나 state가 바뀌면 새 Plan이 필요하다. Plan artifact는 3일 보관한다. 저장 Plan에는 민감한 인프라 정보가 포함될 수 있으므로 공개 링크로 전달하지 않는다.

## 앱 배포의 범위

CI는 기존 `/delta-platform/runtime` SecureString의 존재만 확인한다. 실제 로그인·DB 비밀값은 VM이 자기 역할로 읽는다. 파라미터가 없으면 실패하며 새 비밀번호를 생성하지 않는다. Release 역할의 SSM 명령은 VM의 root 권한이므로 앱 운영 권한으로 취급한다.

CloudFront 주소를 바꾸면 SSM의 `PLATFORM_PUBLIC_ORIGIN`도 갱신해야 한다. 현재 단일 VM의 Compose 재시작 배포이므로 짧은 중단이 발생할 수 있다. 무중단 배포·자동 롤백은 아직 구현하지 않았다. 고객 공개 저장소의 코드는 이 관리자 CI에서 실행하지 않으며 후속 U2의 격리 빌드 경로를 사용한다.
