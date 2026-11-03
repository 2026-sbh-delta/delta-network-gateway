# 플랫폼과 기존 SAST의 사설 연결

서로 다른 VPC의 기존 플랫폼 VM과 도구 VM을 연결한다. VM·LB를 새로 만들지 않는다. VPC peering 1개, 두 VM의 `/32` 왕복 Route 2개, 플랫폼 SG만 허용하는 HTTPS ingress 1개를 관리한다. NAT/TGW 기본 경로는 수정하지 않는다.

이 root는 두 기존 VPC의 연결 설정을 소유한다. 각 VPC/VM 자체의 Terraform state와 분리해 `platform/sast-network.tfstate`에 저장한다. 기존 플랫폼 CI의 IAM은 다른 팀 VPC 변경을 허용하지 않으므로, 최초 연결은 업무 계정 SSO로 저장 Plan을 검토한 뒤 적용한다. 테스트는 GitHub CI에도 포함한다. 이후 VM 교체로 사설 IP가 달라지면 새 Plan, TLS 인증서, 플랫폼 URL을 함께 갱신한다.

```bash
AWS_PROFILE=delta-platform terraform init -backend-config=backend.hcl
terraform test
AWS_PROFILE=delta-platform terraform plan -out=reviewed.tfplan
AWS_PROFILE=delta-platform terraform show reviewed.tfplan
AWS_PROFILE=delta-platform terraform apply reviewed.tfplan
```

[동일 리전 peering의 SG 참조](https://docs.aws.amazon.com/vpc/latest/peering/vpc-peering-security-groups.html)와 [양방향 Route](https://docs.aws.amazon.com/vpc/latest/peering/vpc-peering-routing.html)를 사용한다. 애플리케이션 토큰과 인증서는 Terraform에 넣지 않는다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `account_id` | string | 필수 | 두 VM이 있는 업무 계정 |
| `region` | string | `ap-northeast-2` | 같은 리전에서 연결 |
| `platform_instance_id` | string | 필수 | 플랫폼 VM |
| `tooling_instance_id` | string | 필수 | 기존 SAST/DAST 호스트 |
| `platform_route_table_id` | string | 필수 | 플랫폼 VM 서브넷에 적용된 Route Table |
| `tooling_route_table_id` | string | 필수 | 도구 VM 서브넷에 적용된 Route Table |
| `platform_security_group_id` | string | 필수 | 플랫폼 VM에 연결된 SG |
| `tooling_security_group_id` | string | 필수 | 도구 VM에 연결된 SG |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `sast_url` | 도구 VM 사설 IP의 HTTPS 주소 |
| `tooling_instance_id` | SSM으로 HTTPS를 설치할 기존 호스트 |
| `platform_instance_id` | 연결을 검증할 플랫폼 VM |
| `region` | 운영 스크립트용 리전 |
| `account_id` | 운영 스크립트의 계정 확인 |
| `peering_id` | 전용 VPC peering ID |
