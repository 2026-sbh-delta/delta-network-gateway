# 플랫폼 AWS 실행 기반

`CloudFront HTTPS → VPC Origin → 사설 EC2의 API·Worker·PostgreSQL` 구성이다. 기존 고객 서비스 네트워크와 별도 VPC/state를 사용한다. 앱 EC2에는 공인 IP·SSH·DB 수신 규칙이 없다. 인터넷 조회는 공인 서브넷의 작은 NAT 인스턴스를 거친다.

## 구성과 한계

- 앱 `t4g.small`, NAT `t4g.micro`, AL2023 ARM64. 작은 팀의 U1 사용량을 기준으로 한 단일 AZ 구성이다. HA·자동 확장 구성은 아니다.
- Jenkins 컨트롤러 `t3.small`과 전용 에이전트 `t3.medium`은 서로 다른 전용 subnet의 `10.80.10.20`/`10.80.11.21`을 사용한다. 컨트롤러 이력은 별도 암호화 20 GiB EBS에 보존한다. 플랫폼/에이전트 SG만 컨트롤러 443에 접근한다. VM 역할은 [Jenkins IAM](../jenkins/identity/README.md)에서 먼저 준비한다.
- 기본값은 CloudFront HTTPS 주소이며 `custom_domain`과 동일 계정 us-east-1의 발급 완료된 ACM 인증서 ARN으로 관리 도메인을 지정할 수 있다. API·로그인 응답은 캐시하지 않으며 세션 쿠키·Origin·CSRF·요청 키를 전달한다. 도메인을 활성화할 때 SSM의 `PLATFORM_PUBLIC_ORIGIN`도 새 HTTPS origin으로 맞춘다.
- PostgreSQL은 별도 암호화 gp3 20 GiB에 저장한다. VM 교체와 데이터 디스크 수명을 분리하고 디스크에 `prevent_destroy`를 적용한다.
- S3 비공개·암호화·버전 관리, DB 백업 7일 보관. 백업 실행·복원 검증 여부는 [AWS 배포 기록](../../../docs/ai-dlc/platform-aws.md)을 따른다.
- SSM으로 관리한다. VM 역할은 ECR 읽기, 특정 설정 경로 읽기, 해당 백업·릴리스 경로 접근만 추가한다. API·Worker 컨테이너에는 AWS 키를 넣지 않는다.
- 로그인·DB 비밀값은 배포 도구가 SSM SecureString에 별도로 기록한다. Terraform 변수·state·user data에 비밀값을 넣지 않는다.
- 최초 Origin 생성 때만 `bootstrap_origin_access=true`로 CloudFront 관리 prefix list를 허용한다. 서비스 SG가 준비되면 false로 바꾸고 재적용한다.

역할별 전용 subnet은 Jenkins Controller `10.80.10.0/24`, Agent `10.80.11.0/24`, AI `10.80.20.0/24`, SAST `10.80.30.0/24`, DAST `10.80.40.0/24`이다. 실제 이전 상태는 [분리 기록](../../../docs/ai-dlc/platform-subnets.md)을 따른다. 기존 관리형 로그 수신 주소는 변경하지 않는다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `account_id` | string | 필수 | STS로 확인한 배포 계정, provider가 다른 계정을 거부 |
| `region` | string | ap-northeast-2 | AWS 리전 |
| `custom_domain` | string 또는 null | null | 관리 플랫폼의 DNS 호스트명. 미지정 시 CloudFront 기본 주소 |
| `custom_domain_certificate_arn` | string 또는 null | null | 도메인과 함께 지정하는 동일 계정 us-east-1 ACM 인증서 ARN |
| `availability_zone` | string | 필수 | 앱·NAT·DB 디스크의 AZ |
| `ami_id` | string | 필수 | AWS 조회로 확인한 AL2023 ARM64 AMI |
| `name` | string | delta-platform | 리소스·SSM 경로 접두어 |
| `bootstrap_origin_access` | bool | false | 최초 Origin 생성용 prefix list 수신 허용 |
| `instance_type` | string | t4g.small | 플랫폼 VM 크기, small/medium만 허용 |
| `data_volume_size` | number | 20 | DB 디스크 용량 GiB |
| `jenkins_ami_id` | string | ami-05becc39a92b78fa5 | 확인한 서울 AL2023 x86_64 AMI. 다른 리전은 해당 리전 이미지로 지정 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `platform_url` | 팀 접속용 HTTPS 주소 |
| `cloudfront_url` | DNS CNAME 대상 확인·복구용 기존 CloudFront HTTPS 주소 |
| `instance_id` | 플랫폼 VM의 SSM 배포 대상 |
| `nat_instance_id` | NAT VM 진단 대상 |
| `ecr_repository_url` | 플랫폼 이미지 저장소 |
| `artifact_bucket` | 비공개 릴리스·백업 버킷 |
| `data_volume_id` | 삭제 보호한 DB 디스크 |
| `runtime_parameter` | 별도로 채울 SSM SecureString 경로 |
| `distribution_id` | CloudFront 배포 ID |
| `region` | AWS 리전 |
| `account_id` | 배포 계정 |
| `jenkins_instances` | controller/agent별 SSM 대상 VM ID |
| `jenkins_url` | 사설 컨트롤러 HTTPS 주소 |
| `service_subnets` | 역할별 subnet ID, CIDR, route table ID |

## 검증과 적용

Terraform 1.16.4, AWS Provider 6.71.0을 고정한다. 실제 `terraform.tfvars.json`, `backend.hcl`, Plan·state·인증 파일은 Git에 넣지 않는다.

Mac 개발 환경(`darwin_arm64`)과 GitHub 실행 환경(`linux_amd64`)의 검증값을 lock 파일에 함께 기록한다. Provider 버전을 변경할 때는 다음 명령으로 공식 배포 파일의 검증값을 갱신한 뒤 커밋한다. CI는 `-lockfile=readonly`를 유지한다. [Terraform 공식 안내](https://developer.hashicorp.com/terraform/cli/commands/providers/lock)

```bash
terraform providers lock -platform=darwin_arm64 -platform=linux_amd64
```

```bash
terraform init -backend=false -lockfile=readonly
terraform validate
terraform test
```

위 명령의 시험은 mock provider만 사용한다. 실제 적용은 올바른 AWS 환경변수를 설정하고 S3 backend를 초기화한 뒤, 저장된 Plan의 추가·변경·삭제 대상을 검토해 수행한다. 자원 생성과 앱 배포 성공은 구분한다.

비용은 앱/NAT EC2, EBS, NAT 공인 IPv4, CloudFront 전송·요청, S3·ECR 저장에서 발생한다. NAT 장애 시 GitHub 검사와 관리 통신이 실패할 수 있으나 CloudFront 수신 경로는 NAT를 거치지 않는다. DB 디스크·백업 보호를 임의로 해제하거나 전체 destroy를 일반 롤백으로 사용하지 않는다.

[CloudFront VPC Origin 전제](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html)

## Worker의 AWS 인증

U2부터 API/Worker 관리 컨테이너가 VM 역할로 CodeBuild·ECR·빌드 로그를 호출한다. app VM만 IMDSv2 hop limit을 2로 설정해 Docker bridge에서 임시 자격 증명을 받는다. NAT VM은 1을 유지한다. 고객 Dockerfile은 이 VM에서 실행하지 않는다. 호출 권한은 [빌드 기반](../builds/README.md)의 전용 inline policy가 관리한다.
