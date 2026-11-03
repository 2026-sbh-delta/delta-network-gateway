# Jenkins 에이전트 IAM

컨트롤러·전용 빌드 에이전트의 IAM 역할과 instance profile을 만든다. 기존 ECR `delta-platform-builds` 게시 권한은 에이전트에만 준다. 두 VM은 자신의 Jenkins 구성, 플랫폼 도구 이미지, Jenkins 릴리스 파일만 읽으며 SSM 관리 권한을 갖는다. AWS 계정·서울 리전을 확인한 뒤 별도 S3 state key `platform/jenkins-identity.tfstate`로 실행한다. backend 파일·실제 입력·state·Plan은 Git에 넣지 않는다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `account_id` | `string` | 필수 | 대상 AWS 계정, provider 오배포 방지 |
| `region` | `string` | `ap-northeast-2` | 기존 ECR 리전 |
| `repository_name` | `string` | `delta-platform-builds` | 읽기·게시할 단일 기존 ECR |
| `verification_principal_arn` | `string` 또는 `null` | `null` | 실제 로컬 검증 중만 역할을 맡을 같은 계정 IAM 역할. 검증 후 제거 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `agent_role_arn` | 빌드 전용 역할 ARN |
| `agent_instance_profile` | 후속 EC2에 연결할 프로필 이름 |
| `repository_url` | 기존 ECR URI |
| `controller_instance_profile` | 컨트롤러 전용 EC2 프로필 이름 |

```bash
terraform init -backend-config=backend.hcl
terraform validate
terraform test
terraform plan -out=identity.tfplan
# 리소스·대상 계정 확인 후
terraform apply identity.tfplan
```

ECR은 고객 이미지 게시와 도구 이미지 읽기를 구분한다. IAM·EC2 생성·ECS 배포·플랫폼 DB/runtime SecureString 접근 권한은 없다. 각자 `/delta-platform/jenkins/controller` 또는 `/agent`만 읽는다. 이 역할은 VM의 인터넷 연결이나 Docker 내부의 IMDS 차단을 대신하지 않는다. [운영 구성](../aws/README.md)을 함께 따른다.

이 모듈은 VM을 만들지 않는다. 플랫폼 Terraform root가 VM에 이 프로필을 연결한다. 실제 적용/검증/운영 전환 상태는 [작업 기록](../../../../docs/ai-dlc/platform-jenkins-aws.md)을 따른다. 삭제는 역할을 사용하는 VM이 없는지 확인하고 destroy Plan 검토 후 별도로 수행한다.
