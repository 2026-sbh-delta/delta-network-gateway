# CloudFront 중앙 ANF 통신 시험

CloudFront의 작은 웹페이지에서 두 버튼으로 같은 사설 서버의 허용과 차단을 비교한다. 기존 Distribution과 서버를 재사용한다. 설계/승인/실제 검증은 [AI-DLC](../../docs/ai-dlc/cloudfront-anf-demo.md), 실행 절차는 [Runbook](RUNBOOK.md)을 따른다.

- 웹: `브라우저 → CloudFront HTTPS → private S3/OAC`.
- API: `CloudFront /api/* → Client VPC internal ALB → TGW Spoke → 중앙 ANF → TGW Inspected → Server EC2`.
- 허용: `/api/allow/delta-test-marker` → TCP 8080 → 실제 서버 JSON.
- 차단: `/api/deny` → TCP 8081 → ANF 최종 차단 → ALB 504. HTTP 실패만으로 원인을 확정하지 않고 실제 ANF SID/action/verdict와 대조한다.

ALB는 Client VPC의 전용 /24 두 개에 배치한다. 두 Target Group 모두 **Health Check는 8080**을 사용한다. Deny Target Group도 healthy인 상태에서 8081 요청을 전송하므로 unhealthy target을 차단으로 오인하지 않는다. ALB egress와 Server ingress는 두 포트를 모두 허용한다. 중앙 `allowed_flows`에는 ALB Subnet CIDR에서 서버 /32의 8080만 SID 1000002로 추가하고 기존 규칙을 보존한다.

서버 공인 IP와 인터넷 기본 Route는 생성하지 않는다. CloudFront 요건인 IGW는 Client VPC에 연결만 한다. 서비스 SG가 생성되기 전에는 CloudFront prefix list의 ALB TCP 80 ingress만 임시 사용하고 이후 제거한다. S3는 암호화/소유권 강제/Public Access Block을 적용하며 OAC는 해당 Distribution SourceArn의 GetObject만 허용한다.

2026-11-01 실제 배포: https://d228fpdue5s0by.cloudfront.net/ . 웹200/허용200/차단504(약10초), 허용 마커와 양방향 FLOW 및 차단 SID9000000 blocked/drop을 대조했다. 직접 Origin/Server IGW/임시 ingress 정리와 네 root No changes를 확인했다. 최종 관리 리소스는37개다. mock21개/중앙 회귀35개 통과. 브라우저 허용 버튼200은 확인했고 차단 버튼 완료 화면 재확인은 UI 도구 오류로 미검증이다. 구현/검증/운영 문서를 분할 서명 커밋하고 origin/main 게시를 확인했다. 게시 결과는 AI-DLC의 Git 게시 기록을 따른다.

## 준비와 실행

Terraform 1.16.4 / AWS Provider 6.71.0과 lock을 사용한다. 기존 Client/Server/TGW/중앙 검사 경로가 배포되어 있어야 한다. 계정 소유자, AZ, 사설 주소, SG 부착, TGW 기본 association/propagation 비활성화를 검증한다. ALB Subnet의 기존 CIDR 사용 여부와 RAM/Attachment/반환 Route는 실계정에서 사전 확인한다.

```bash
terraform/cloudfront-server/scripts/validate.sh
terraform/network-security/scripts/validate.sh
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/cloudfront-server init \
  -backend-config=backend.hcl -input=false -lockfile=readonly
```

기존 Workload S3 backend의 별도 key `delta/lab/cloudfront-server/terraform.tfstate`를 유지한다. Bucket owner/versioning/encryption/Public Access Block/use_lockfile를 확인한다. 기존 state를 자동 이전하지 않는다. 실제 tfvars/backend/state/Plan/검증 증거는 Git에서 제외하고 권한 0600으로 보관한다. 자격 증명은 파일/웹에 출력하지 않는다.

1. `terraform.tfvars.example`을 참고해 실제 입력을 작성한다. 처음에는 `bootstrap_origin_access=true`다.
2. 출력 `central_allowed_flow`와 동일한 8080 허용 항목을 기존 중앙 `allowed_flows`에 병합한다. 중앙 저장 Plan의 규칙을 AWS DryRun으로 검증한 뒤 적용한다.
3. Workload 저장 Plan에서 기존 EC2/VPC/Inspection 교체 부재, internal ALB/두 TG/SG/Route/S3/OAC와 기존 Distribution 갱신만 있는지 검토한다. 검토한 저장 Plan을 적용한다.
4. Target Group healthy, Origin/Distribution Deployed, 실제 HTTPS 허용/차단과 양방향 로그를 확인한다. 웹의 `verification.json`은 실제 증거를 확보한 후 갱신한다.
5. `bootstrap_origin_access=false`로 바꾸고 임시 prefix list rule만 제거한다. 직접 EC2 Origin에서 이전하는 경우 새 Distribution 전환을 먼저 확인한 뒤 이전 root 소유의 직접 Origin/Server SG rule/Server IGW를 정리한다. 서비스 소유 SG/ENI는 임의 삭제하지 않는다.
6. CloudFront/중앙/기존 Server root의 최종 Plan과 서버 주소/Route를 확인한다.

```bash
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/cloudfront-server plan \
  -input=false -out=.validation/reviewed.tfplan
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/cloudfront-server apply \
  -input=false .validation/reviewed.tfplan
```

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| name | string | `delta` | 이름 접두사. ALB/TG 길이 제한도 검증 |
| environment | string | `lab` | 환경 이름/공통 태그 |
| region | string | `ap-northeast-2` | 이번 시험의 서울 리전 |
| workload_account_id | string | 필수 | STS/기존 VPC/EC2와 대조하는 계정 |
| vpc_id | string | 필수 | 기존 private Server VPC |
| server_instance_id | string | 필수 | 재사용할 running EC2 |
| server_security_group_id | string | 필수 | EC2의 기존 Server SG. 새 standalone rule만 소유 |
| expected_server_private_ip | string | 필수 | EC2와 대조할 사설 RFC1918 IPv4 |
| ingress_vpc_id | string | 필수 | Server와 다른 기존 Client VPC. internal ALB 배치 |
| alb_subnets | map(string) | 필수 | 두 AZ ID별 전용 canonical /24. 중복/포함 관계 검증 |
| transit_gateway_id | string | 필수 | 기존 중앙 TGW |
| central_account_id | string | 필수 | TGW 소유자. Workload와 다른 12자리 계정 |
| bootstrap_origin_access | bool | `false` | 최초 생성만 true, 서비스 SG 확인 후 false |
| tags | map(string) | `{}` | 추가 공통 태그. 키는 태그 이름, 값은 태그 값 |

### alb_subnets 내부 속성

| 속성 | 타입 | 역할 |
| --- | --- | --- |
| map key | string | 서울의 물리 AZ ID. 예: apne2-az1, apne2-az2 |
| map value | string | 해당 AZ의 dedicated private /24 CIDR. 예: 10.10.4.0/24 |

공통 태그는 Team=Delta, Project=delta-multicloud, Environment=입력값, ManagedBy=Terraform이다. API와 오류 캐시는 비활성화하고 query를 전달한다. TLS는 CloudFront viewer에 적용하며 ALB/서버 HTTP를 ANF가 검사한다.

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| workload_account_id | 검증한 Workload 계정 |
| region | ALB와 Server 리전 |
| distribution_id | 기존 CloudFront Distribution ID |
| distribution_arn | Distribution ARN/OAC SourceArn |
| domain_name | CloudFront 기본 hostname |
| site_url | 두 버튼 페이지 HTTPS URL |
| health_url | ALB/ANF를 경유하는 /health URL |
| vpc_origin_id | internal ALB VPC Origin ID |
| internet_gateway_id | Client VPC에 Route 없이 연결한 필수 IGW |
| cloudfront_service_security_group_id | AWS 소유 Client VPC Origin 서비스 SG |
| alb_arn | internal ALB ARN |
| target_group_arns | allow/deny Target Group ARN 맵 |
| alb_subnet_ids | 물리 AZ ID별 ALB Subnet ID |
| site_bucket_name | private S3 정적 웹 bucket |
| central_allowed_flow | 중앙 기존 allowed_flows에 병합할 허용 항목 |
| server_instance_id | 교체하지 않는 기존 EC2 |
| bootstrap_origin_access | 최종 false여야 하는 초기 허용 여부 |

### central_allowed_flow 내부 속성

| 속성 | 역할 |
| --- | --- |
| cloudfront_alb_to_server | 중앙 map에 유지할 고정 키 |
| source_cidrs | ALB Subnet CIDR 두 개 |
| destination_cidrs | 기존 Server의 /32 |
| protocol | tcp |
| destination_ports | 8080만 허용 |
| sid | 1000002. 기존 SID와 중복 여부 확인 |

## 검증과 비용

격리 검증은 backend 없는 init/validate와 AWS mock Plan을 사용한다. 내부 ALB, TGW 경로, 두 포트/Health Check, SG 대조군, private S3/OAC, CloudFront 분기/오류 처리 및 잘못된 계정/AZ/CIDR을 검사한다. mock은 실제 통신 증거가 아니다. 실제 결과는 AI-DLC에 날짜/명령/결과를 별도로 기록한다.

CloudFront 요청/전송, S3 저장/요청, ALB 시간/LCU, TGW 처리, ANF Endpoint/처리와 CloudWatch 로그, 기존 EC2/디스크 비용이 발생한다. 단일 서버 시험이며 인증 API 운영/부하/장애 전환/TLS 복호화는 검증 범위 밖이다. 전체 해체는 요청하지 않았으며 기존 서버/중앙 기반/state/로그는 보존한다.

- [VPC Origin 요건](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html)
- [ALB Health Check 포트](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html)
- [CloudFront 오류 캐시](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistValuesErrorPages.html)
