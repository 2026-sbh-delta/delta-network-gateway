# Jenkins 에이전트 IAM

기존 ECR `delta-platform-builds`에 이미지를 게시할 역할·정책·EC2 instance profile만 만든다. 컨트롤러에는 이 역할을 주지 않는다. AWS 계정·서울 리전을 확인한 뒤 별도 S3 state key `platform/jenkins-identity.tfstate`로 실행한다. backend 파일·실제 입력·state·Plan은 Git에 넣지 않는다.

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

```bash
terraform init -backend-config=backend.hcl
terraform validate
terraform test
terraform plan -out=identity.tfplan
# 리소스·대상 계정 확인 후
terraform apply identity.tfplan
```

`GetAuthorizationToken` 외 ECR 작업은 지정 저장소만 허용한다. IAM·EC2 생성·ECS 배포·플랫폼 DB/SSM 접근 권한은 없다. 이 역할은 VM의 인터넷 연결이나 Docker 내부의 IMDS 차단을 대신하지 않는다. [Jenkins 구성 안내](../README.md)를 함께 따른다.

이 모듈은 VM을 만들지 않으며, instance profile도 아직 VM에 연결하지 않는다. 실제 적용/검증/운영 전환 상태는 [작업 기록](../../../../docs/ai-dlc/platform-jenkins.md)을 따른다. 삭제는 에이전트가 역할을 사용하지 않는지 확인하고 destroy Plan 검토 후 별도로 수행한다.
