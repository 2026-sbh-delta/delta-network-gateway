# GitHub OIDC 권한 기반

GitHub OIDC provider와 Plan·Apply·Release IAM 역할을 관리한다. 일반 플랫폼 state와 분리한 `platform/identity.tfstate`를 사용한다. 이 디렉터리의 실제 Apply는 업무 계정의 관리자 인증으로 수행하며 GitHub 배포 역할에는 자기 권한을 수정할 권한을 주지 않는다.

신뢰 범위는 API에서 조회한 immutable repository subject의 `main`이다. Environment는 사용하지 않는다. GitHub Free/private 환경에서 수동 실행은 별도 승인자를 강제하지 않으므로 main의 코드 변경·워크플로 실행 권한을 운영 권한으로 취급한다.

## 권한 범위

| 역할 | 허용 범위 |
| --- | --- |
| Plan | 서울 EC2 메타데이터, 플랫폼 IAM·S3·ECR·CloudFront 설정 조회, 앱 state 읽기와 전용 잠금 |
| Apply | Plan 권한 + 플랫폼 태그의 EC2 변경, 지정 VPC·subnet에서 VM 실행, 기존 CloudFront·버킷·ECR 설정 변경, 앱 state 저장 |
| Release | 앱 state 읽기, 지정 ECR 이미지 게시, `releases/` 업로드, 플랫폼 앱/Jenkins controller/agent VM의 SSM 명령 실행 |

일반 Apply는 IAM 역할·정책 생성/변경, identity state 접근, 새로운 CloudFront 정책·S3/ECR 기반 생성을 허용하지 않는다. 기존 플랫폼의 변경과 VM 교체를 대상으로 한다. IAM 권한 변경이나 전체 플랫폼 기반 재구축은 관리자가 이 부트스트랩과 실제 Plan을 함께 검토해야 한다. EC2 조회와 SSM 명령 결과·파라미터 메타데이터 조회 중 리소스 제한을 지원하지 않는 API는 해당 리전에서 조회할 수 있다.

새 SG 규칙 생성은 별도 inline policy에서 `security-group-rule/*`와 `aws:RequestTag/Project`를 확인한다. 생성 전에는 리소스 태그가 없기 때문이다. 부모 SG에는 기존 `ec2:ResourceTag/Project` 제한이 계속 적용된다. 기존 관리형 정책의 크기 한도를 넘기지 않도록 생성 권한을 분리했다.

Release는 SSM Parameter의 비밀값을 직접 조회하지 않는다. 다만 지정 VM에서 실행하는 SSM 명령은 root 권한이므로 앱/Jenkins 운영 권한이다. 단순 이미지 업로드 역할로 취급하지 않는다. Apply의 PassRole은 app·nat·jenkins-controller·jenkins-agent 네 역할로 제한하고, VM 크기는 기존 ARM 소형과 t3.small/medium만 허용한다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `account_id` | string | 필수 | 업무 AWS 계정 |
| `region` | string | ap-northeast-2 | 플랫폼 리전 |
| `name` | string | delta-platform | 플랫폼 태그·리소스·역할 이름 |
| `github_subject_prefix` | string | 필수 | API로 확인한 `repo:OWNER@ID/REPO@ID` |
| `create_oidc_provider` | bool | false | provider가 없을 때 생성. 이미 이 state가 관리 중인 경우에도 true 유지 |
| `state_bucket` | string | 필수 | 기존 플랫폼 state 버킷 |
| `origin_request_policy_id` | string | 필수 | 변경을 허용할 기존 CloudFront 정책 |
| `cloudfront_prefix_list_id` | string | 필수 | 기존 플랫폼의 AWS 관리 CloudFront prefix list. 해당 목록의 항목 조회만 허용 |
| `platform_vpc_id` | string | 필수 | VM 실행·연결을 허용할 기존 VPC |
| `platform_subnet_ids` | set(string) | 필수 | 기존 public/private subnet ID |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `role_arns` | Plan·Apply·Release 역할 ARN |
| `oidc_provider_arn` | 생성 또는 재사용한 provider ARN |
| `trusted_subject` | 실제 허용한 main subject |

## 실행

1. 업무 계정 인증을 준비하고 `prepare-identity.py`로 STS·기존 provider·GitHub subject·기존 플랫폼 state를 확인한다. 실제 입력과 backend 설정은 Git에서 제외한다.
2. `terraform init -backend-config=backend.hcl -lockfile=readonly`, `validate`, `test`를 실행한다.
3. 저장 Plan의 역할·정책·provider 생성 범위를 검토하고 Access Analyzer 정책 검증을 수행한 뒤 적용한다. 기존 provider를 새로 생성하거나 다른 팀의 역할을 덮어쓰지 않는다.
4. 역할 상태와 IAM 허용/거부를 확인한다. GitHub 변수는 사용자가 [변수 안내](../GITHUB.md)에 따라 입력한다.

```bash
uv run --project platform/deploy python platform/deploy/scripts/prepare-identity.py --account-id 616723161368
terraform -chdir=platform/deploy/identity init -backend-config=backend.hcl -lockfile=readonly
terraform -chdir=platform/deploy/identity plan -out=identity.tfplan
```

GitHub 실제 인증과 배포는 코드가 main에 반영되고 변수가 등록된 뒤에 검증한다. IAM mock 시험·정책 시뮬레이션은 실제 OIDC 인증 성공과 구분한다.
