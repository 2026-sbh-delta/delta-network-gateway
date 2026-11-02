# GitHub에서 플랫폼 배포하기

인프라는 **GitHub Actions → OIDC 임시 인증 → Terraform → AWS**, 앱은 **테스트 → ARM64 이미지 → ECR → SSM → 사설 VM**으로 배포한다. 웹 서버가 자기 인프라의 Terraform을 실행하지 않는다.

현재 AWS 최초 배포는 로컬에서 수행했다. 워크플로를 [PR #7](https://github.com/2026-sbh-delta/delta-network-gateway/pull/7)에 게시하고 원격 테스트 실행을 시작했다. 최종 테스트 결과는 PR Checks를 따른다. **OIDC 역할·Environment는 아직 연결하지 않았으며 GitHub를 통한 AWS 배포 성공으로 표시하지 않는다.**

## 세 가지 실행

| 워크플로 | 시점 | 하는 일 |
| --- | --- | --- |
| `platform-check.yml` | PR·main/feat/platform push·수동 | 실제 시험 DB, API/Worker·브라우저 검사, Terraform mock 검사. AWS 인증 없음 |
| `platform-infra.yml` | main에서 수동 `plan` → 검토 후 수동 `apply` | 같은 커밋의 저장 Plan만 적용. S3 state·잠금 유지 |
| `platform-release.yml` | main에서 수동 | 같은 커밋의 테스트를 통과한 플랫폼을 배포. 인프라 Apply 없음 |

최초 state 버킷·OIDC 신뢰 설정은 관리자가 준비하는 부트스트랩이다. 이후에는 로컬 관리자 세션을 일상적인 배포 경로로 쓰지 않는다. 기존 `delta-platform-tf-616723161368/platform/terraform.tfstate`를 그대로 이어받으므로 리소스를 다시 만들거나 import할 필요가 없다.

## GitHub와 AWS의 연결 준비

1. AWS IAM에 `https://token.actions.githubusercontent.com` OIDC provider가 있는지 확인하고, 없으면 추가한다. Audience는 `sts.amazonaws.com`이다.
2. 아래 세 GitHub Environment를 **미리** 만든다. 배포 가능한 branch를 `main`으로 제한한다. `platform-infra`와 `platform-release`에는 지원되는 요금제에서 required reviewer를 설정한다. 이 private 저장소에서 해당 보호 기능을 쓸 수 있는지 먼저 확인한다.
3. 각 Environment마다 별도 AWS 역할을 연결한다. 신뢰 정책은 이 저장소·Environment에만 한정한다. `repo:*`와 관리자 전체 권한을 사용하지 않는다.
4. 아래 변수와 권한을 검토한 뒤 Environment 변수 `PLATFORM_CI_ENABLED=true`를 설정한다. 미설정이면 AWS 인증 전에 중단한다. 이 플래그 자체가 승인 기능을 대신하지는 않는다.
5. main 보호 규칙과 PR 검토를 적용하고, 승인받아 코드를 푸시·병합한 다음 `plan`부터 실행한다.

| Environment | 역할의 용도 | 필요한 권한 범위 |
| --- | --- | --- |
| `platform-plan` | 인프라 조회·Plan | 해당 state 읽기, 해당 `.tflock` 쓰기/삭제, 플랫폼 Terraform 리소스의 조회·태그 읽기. 리소스 변경 권한 없음 |
| `platform-infra` | 검토한 Plan 적용 | 플랫폼 VPC·EC2·CloudFront·ECR·S3·앱/NAT 역할 관리와 state 쓰기. `iam:PassRole`은 두 플랫폼 VM 역할 및 EC2 서비스만 허용 |
| `platform-release` | 이미지 게시·앱 배포 | state 읽기, `delta-platform` ECR push/read, 릴리스 S3 경로 쓰기, 특정 플랫폼 VM의 `AWS-RunShellScript` 실행·결과 조회, SSM 파라미터 메타데이터 조회 |

정책은 인프라 담당자가 실제 ARN과 생성 작업별 조건에 맞춰 설정한다. 이 문서는 IAM 정책 배포 완료를 뜻하지 않는다. 조회 API 중 Resource 제한을 지원하지 않는 작업과 생성 시 태그 조건이 필요한 작업을 분리한다. Release 역할에는 Terraform 변경·SSM 비밀값 조회·DB 백업 읽기 권한을 부여하지 않는다. SSM 명령은 VM의 운영 권한이므로 이 역할도 운영 배포 권한으로 취급한다.

신뢰 정책 예시(`platform-release` 역할):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "arn:aws:iam::616723161368:oidc-provider/token.actions.githubusercontent.com"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {"StringEquals": {
      "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub": "repo:2026-sbh-delta/delta-network-gateway:environment:platform-release"
    }}
  }]
}
```

Environment를 사용하면 OIDC의 `sub`에는 branch 대신 Environment 이름이 들어간다. 따라서 **Environment의 main 제한과 코드의 main 조건을 함께 유지**한다. 기존 터미널의 AWS 키·세션 토큰을 GitHub secrets에 복사하지 않는다. [GitHub OIDC 안내](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws), [Environment 보호와 요금제](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments).

## Environment 변수

세 Environment에 공통 값을 넣고 `AWS_ROLE_ARN`만 각 역할로 구분한다. 이 값들은 비밀번호가 아니다.

| 이름 | 현재 값·용도 |
| --- | --- |
| `AWS_ROLE_ARN` | 해당 Environment 전용 역할 ARN, 아직 생성·연결 전 |
| `AWS_ACCOUNT_ID` | `616723161368` |
| `AWS_REGION` | `ap-northeast-2` |
| `PLATFORM_STATE_BUCKET` | `delta-platform-tf-616723161368` |
| `PLATFORM_TF_INPUTS` | 아래 JSON, 세 환경에서 같은 값 사용 |
| `PLATFORM_CI_ENABLED` | 보호 규칙·권한 검토 완료 후 `true` |
| `PLATFORM_POSTGRES_IMAGE` | release에만 필요. 검증한 기존 PostgreSQL ECR digest |

```json
{
  "account_id": "616723161368",
  "region": "ap-northeast-2",
  "availability_zone": "ap-northeast-2a",
  "ami_id": "ami-029fa37c14d9a8917",
  "bootstrap_origin_access": false
}
```

현재 PostgreSQL digest는 다음과 같다. 앱 배포와 DB 이미지 업그레이드를 분리하기 위해 고정한다.

```text
616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform@sha256:a78d9af300151935bbc4dfaf00aae291d12abaea3037c636e43603cc1675684e
```

## Plan 검토와 적용

1. main의 **Platform infrastructure**를 `operation=plan`으로 실행한다.
2. 변경 목록과 `platform-plan` artifact의 `plan.txt`를 검토하고 실행의 run ID를 기록한다.
3. `operation=apply`, `plan_run_id=<검토한 실행 ID>`로 실행한다. Environment 승인 기능이 설정돼 있다면 담당자 승인 후 시작한다.
4. 워크플로·main·커밋·성공 상태·artifact 체크섬을 확인한 뒤 저장된 Plan을 적용한다. 새 Plan으로 몰래 바꿔 적용하지 않는다.

검토 이후 main 커밋이나 state가 바뀌면 새 Plan이 필요하다. Plan artifact는 3일 보관하며 저장소 권한이 있는 사람만 취급한다. Terraform Plan에는 민감한 인프라 정보가 들어갈 수 있으므로 공개 링크로 전달하지 않는다. 인프라와 앱 배포는 같은 concurrency group으로 직렬화한다.

## 앱 배포와 한계

`Platform release`는 테스트 후 이미지를 빌드해 ECR digest로 고정하고 SSM으로 설치한다. CI는 기존 `/delta-platform/runtime` SecureString의 존재만 확인한다. 실제 비밀값은 VM이 자기 역할로 읽는다. 파라미터가 없으면 실패하며 새 로그인이나 DB 비밀번호를 자동 생성하지 않는다.

CloudFront를 교체해 주소가 달라진 경우에는 운영자가 SSM의 `PLATFORM_PUBLIC_ORIGIN`도 새 주소로 갱신해야 한다. 로그인 정보는 CI artifact에 포함하지 않는다. 배포 후 HTTPS 준비 상태를 확인하지만, 이것만으로 로그인·Worker·고객 서비스 배포까지 검증했다고 판단하지 않는다.

현재 단일 VM의 Compose 재시작 배포이므로 짧은 중단이 발생할 수 있다. 무중단 배포·자동 롤백은 구현하지 않았다. 고객 공개 저장소의 코드는 이 관리자 권한 CI에서 실행하지 않으며, 후속 U2의 격리 빌드 경로를 사용한다.
