# GCP 서버와 중앙 ANF 왕복 시험

현재는 코드와 mock/로컬 앱 검증만 완료했다. GCP VM/Gateway/Tunnel과 AWS VPN은 미생성이다. 아래 명령은 실제 배포 후의 실행 절차이며 성공 기록이 아니다. 현재 Billing `open=false`/`billingEnabled=false`를 먼저 해결해야 한다. 크레딧 계정을 변경하거나 유료 업그레이드하는 절차는 포함하지 않는다.

## 1. 프로젝트 확인과 GCP 기반

[README의 준비/상태 bucket/첫 Plan](README.md)을 따른다. preflight로 Project ID/Number, Billing과 API 상태를 재확인한다. Compute API 활성 후 기존 Subnet 전체와 이미지/쿼터를 확인하고 현재 AWS 대역과 비교한다. CIDR 중복이나 조직 정책 거부가 있으면 리소스를 생성하기 전에 원인을 해결한다.

빈 터널 맵의 저장 Plan을 검토/적용한 뒤 아래 실제 결과를 확인한다.

```bash
terraform -chdir=terraform/gcp-server output -json server
terraform -chdir=terraform/gcp-server output -json gateway_ips_by_interface
gcloud compute instances describe delta-lab-server --zone=asia-northeast3-a \
  --project=project-5bb650b3-9c21-4c8b-a16 \
  --format='yaml(name,status,networkInterfaces,serviceAccounts,metadata,shieldedInstanceConfig)'
gcloud compute routes list --project=project-5bb650b3-9c21-4c8b-a16 \
  --filter='network:delta-lab-server' --format=json
gcloud compute ssh delta-lab-server --zone=asia-northeast3-a \
  --project=project-5bb650b3-9c21-4c8b-a16 --tunnel-through-iap
```

VM에 accessConfigs/IPv6/serviceAccounts가 없고, 새 VPC에 인터넷 `0.0.0.0/0` Route/NAT/LB가 없어야 한다. IAP에는 운영자의 IAP tunnel 접속 및 OS Login 권한이 필요하다. 조직의 계층형 방화벽 정책도 별도로 확인한다. SSH 안에서 다음을 실행한다.

```bash
sudo systemctl is-active delta-test
sudo journalctl -u delta-test --no-pager -n 20
ss -lnt
curl --noproxy '*' http://127.0.0.1:8080/health
curl --noproxy '*' http://127.0.0.1:8081/health
```

두 포트 모두 JSON의 cloud=gcp, service=delta-private-test와 해당 port를 반환해야 한다. 서비스가 실패하면 startup 로그에서 `/usr/bin/python3`와 이미지 종류를 확인한다. 인터넷 apt/pip 다운로드를 추가해 해결하지 않는다. IAP/localhost 성공은 중앙 ANF 통과 증거가 아니다.

## 2. AWS VPN 두 연결

AWS 중앙 계정 `482840608926`, Region `ap-northeast-2`, TGW `tgw-0dafbd07013d574ed`를 실제 조회해 확인한다. 기존 TGW VPN들의 Inside CIDR을 조회하고 아래 후보 4개와 중복이 없는지 확인한다. 기존 tfvars의 계정/Inspection/공유/Spoke 계약은 유지하고 VPN 입력만 병합한다.

```bash
aws sts get-caller-identity --profile sbh-delta-security
aws ec2 describe-vpn-connections --profile sbh-delta-security --region ap-northeast-2 \
  --query 'VpnConnections[].{id:VpnConnectionId,tgw:TransitGatewayId,inside:Options.TunnelOptions[].TunnelInsideCidr}'
```

다음 두 `public_ip`는 GCP `gateway_ips_by_interface`의 실제 ID별 주소로 교체한다. 예시에는 실행 가능한 공인 주소를 넣지 않는다.

```hcl
vpn_networks = { gcp = { cidrs = ["10.30.1.0/24"], kind = "gcp" } }
vpn_peers = {
  gcp_if0 = {
    network_key = "gcp", public_ip = "<GCP interface 0 실제 주소>", bgp_asn = 65001
    tunnel1_inside_cidr = "169.254.10.0/30", tunnel2_inside_cidr = "169.254.10.4/30"
    gcp_interface = 0
  }
  gcp_if1 = {
    network_key = "gcp", public_ip = "<GCP interface 1 실제 주소>", bgp_asn = 65001
    tunnel1_inside_cidr = "169.254.10.8/30", tunnel2_inside_cidr = "169.254.10.12/30"
    gcp_interface = 1
  }
}
```

두 peer가 같은 `vpn_networks.gcp`를 참조해 서버 prefix의 중복 정적 Route를 만들지 않는다. 중앙의 새 Plan에서 CGW/VPN 각각 2개, VPN의 Spoke Association과 Inspected에만 BGP Propagation, 새 prefix의 Inspection 경로를 검토한다. 기존 리소스 삭제/직결 우회 Route가 없어야 한다. 실제 변경 목록은 Plan으로 다시 판정한다.

```bash
umask 077
AWS_PROFILE=sbh-delta-security terraform -chdir=terraform/network-security plan \
  -input=false -out=.validation/gcp-vpn.tfplan
AWS_PROFILE=sbh-delta-security terraform -chdir=terraform/network-security show \
  -no-color .validation/gcp-vpn.tfplan
AWS_PROFILE=sbh-delta-security terraform -chdir=terraform/network-security apply \
  -input=false .validation/gcp-vpn.tfplan
```

중앙 폴더의 ignored `.validation`이 있어야 한다. 기본 Association/Propagation/자동 수락은 기존처럼 disable을 유지한다. 이 단계만으로 터널 UP이 되지는 않는다.

## 3. GCP 네 터널과 BGP

[README의 전달 도구](README.md)를 사용한다. 일반 handoff의 inside/outside/ASN을 그대로 사용하고 PSK는 채팅/일반 JSON/출력으로 옮기지 않는다. 4터널과 BGP/Router Interface 각각 4개, External Gateway 1개를 추가하는 GCP 저장 Plan을 검토/적용한다. 위에서 입력한 `vpn_tunnels={}`가 실제 tfvars에 있으면 제거하고 자동 입력 파일이 유일한 정의가 되게 한다.

```bash
umask 077
terraform -chdir=terraform/gcp-server plan -input=false -out=.validation/vpn.tfplan
terraform -chdir=terraform/gcp-server show -no-color .validation/vpn.tfplan
terraform -chdir=terraform/gcp-server apply -input=false .validation/vpn.tfplan
gcloud compute routers get-status delta-lab-aws --region=asia-northeast3 \
  --project=project-5bb650b3-9c21-4c8b-a16 --format=json
gcloud compute vpn-tunnels list --regions=asia-northeast3 \
  --project=project-5bb650b3-9c21-4c8b-a16 \
  --format='table(name,status,peerIp,vpnGatewayInterface)'
```

4개 Tunnel ESTABLISHED 및 4개 BGP session UP을 확인한다. GCP는 서버 `10.30.1.0/24`만 광고하고 AWS `10.10.0.0/16`을 학습해야 한다. AWS에서는 두 VPN의 터널 4개 telemetry UP, Inspected RT의 서버 prefix가 VPN에서 전파됐는지 확인한다. 전체 VPN JSON 대신 `VgwTelemetry` 등 필요한 상태 필드만 출력한다. `customer_gateway_configuration`과 raw state는 출력하지 않는다.

## 4. Route와 허용 정책

| 위치 | 목적지 | 다음 홉/동작 |
| --- | --- | --- |
| Workload Client Subnet RT 두 개 | `10.30.1.0/24` | 중앙 TGW |
| TGW Spoke RT | `10.30.1.0/24`, `10.10.0.0/16` | Inspection Attachment |
| Inspection TGW Subnet RT 각 AZ | 두 검사 prefix | 같은 AZ Primary ANF Endpoint |
| Inspection Firewall Subnet RT 각 AZ | 두 검사 prefix | 중앙 TGW |
| TGW Inspected RT | `10.30.1.0/24` | VPN BGP 학습 경로 |
| TGW Inspected RT | `10.10.0.0/16` | Workload Attachment |
| GCP VPC | `10.10.0.0/16` | Cloud Router가 VPN BGP로 학습한 경로 |
| GCP 서버 ingress | AWS Client `/32`, TCP 8080/8081 | 둘 다 허용 |
| AWS Client SG egress | GCP 서버 `/24`, TCP 8080/8081 | 둘 다 허용 |
| 중앙 ANF | Client `/32` → Server `/32`, TCP 8080 | 허용 연결 추적 |
| 중앙 ANF | 같은 주소, TCP 8081 | 마지막 drop SID 9000000 |

Workload `remote_cidrs=["10.30.1.0/24"]`를 등록하고 실제 Workload Plan/Apply를 수행한다. 중앙 `allowed_flows`에는 기존 키를 보존하고 아래 하나를 병합한다. SID가 기존 규칙과 겹치지 않는지 확인한다.

```hcl
allowed_flows = {
  aws_client_to_gcp = {
    source_cidrs      = ["10.10.1.143/32"]
    destination_cidrs = ["10.30.1.10/32"]
    protocol         = "tcp"
    destination_ports = [8080]
    sid               = 1000001
  }
}
# 별도 metadata. 실제 ANF 원본 로그에 CSP/service_id가 생기는 것은 아니다.
cidr_service_map = {
  aws_client = { cidr = "10.10.1.143/32", csp = "aws", service_id = "delta-client" }
  gcp_server = { cidr = "10.30.1.10/32", csp = "gcp", service_id = "delta-server" }
}
```

등록한 주소는 현재 AWS Client IP와 GCP 실제 output을 다시 대조한다. 중앙 Plan의 방화벽/HOME_NET 변경을 검토한 후 적용한다. `pass` 또는 모든 포트 허용 규칙을 추가하지 않는다.

## 5. 실제 ANF 통과와 차단 판정

AWS의 기존 EIC Endpoint로 EC2 `i-077f4b16dd163998a`에 접속한다. [Workload Runbook](../workload-client/RUNBOOK.md)의 사설 접속 절차를 사용한다. Client의 사설 IP를 다시 확인한 뒤 실행한다.

```bash
curl --noproxy '*' --connect-timeout 5 --max-time 10 http://10.30.1.10:8080/health
curl --noproxy '*' --connect-timeout 5 --max-time 10 http://10.30.1.10:8081/health
```

첫 요청은 실제 GCP JSON 응답이어야 한다. 두 번째 요청은 차단돼야 하지만 timeout만으로 ANF 원인이라고 단정하지 않는다. GCP localhost에서 양쪽 리스너가 여전히 응답하는지, GCP ingress와 AWS egress가 양쪽 포트를 허용하는지 대조한다.

중앙 FLOW와 ALERT 로그에서 실제 source/destination IP, port, protocol, 시각과 flow_id를 대조한다. [중앙 로그 조회](../network-security/RUNBOOK.md) 및 [ALERT query](../network-security/queries/alerts.cwli)를 사용한다. 8080은 양방향 FLOW와 실제 HTTP 응답을 함께 증거로 삼는다. 허용 flowbit 규칙은 `noalert`여서 해당 허용 SID ALERT가 없어도 정상이다.

8081은 SID `9000000`의 실제 blocked action 또는 존재하는 verdict와 통신 실패를 확인한다. ALERT 발생만으로 차단을 판정하지 않는다. 로그 전달 지연이 있으면 시각 범위를 늘리고 로그 목적지/권한과 실제 트래픽 유입을 확인한다. 관리 SSH/IAP와 localhost 응답은 ANF 통과 판정에서 제외한다.

검증표에는 날짜, 명령, 실제 결과와 증거 위치를 남긴다. 4터널 UP, 양방향 Route, 허용/차단/로그를 각각 구분한다. 장애 전환, MTU/대용량, 장시간 rekey, 서버 HA 및 TLS 복호화는 이 1회 HTTP 시험으로 검증되지 않는다.

## 6. 실패 시 확인과 해체

- Billing/API 오류: 현재 프로젝트의 무료 크레딧 연결과 활성 상태를 확인한다. 계정 유형과 결제 연결을 자동 변경하지 않는다.
- IAP 실패: IAP/OS Login 권한, source `35.235.240.0/20`, target tag와 계층형 방화벽을 확인한다. VM 외부 IP나 인터넷 기본 Route를 추가하지 않는다.
- Tunnel 실패: 네 개의 outside/inside 대응, PSK version, IKEv2, AES256/SHA256/Group14 단일 transform을 확인한다. PSK는 로그에 출력하지 않는다.
- BGP UP/HTTP 실패: GCP 학습 경로와 prefix 광고, Spoke/Inspected Association, Inspected에만 VPN Propagation, appliance mode, 같은 AZ의 ANF Endpoint 및 실제 정책을 확인한다.
- 8081 성공: 중앙 정책과 우회 Route를 점검하고 실패로 기록한다. 허용 정책을 더 넓히지 않는다.

Destroy는 별도 작업으로 취급한다. 먼저 Client 원격 Route/송신 규칙과 중앙 허용 정책/metadata/해당 원격 경로를 정리하고, GCP BGP Peer/Interface/Tunnel과 AWS VPN 연결/CGW를 정리한다. 서로 연결된 state의 참조를 제거하는 Plan을 각각 확인한다. GCP Router/Gateway/VM/내부 주소/방화벽/Subnet/VPC 순서로 의존 관계를 따른다.

VM은 `server.tf`의 deletion_protection을 false로 바꾸는 저장 Plan/Apply가 먼저 필요하다. API는 disable_on_destroy=false이므로 남는다. 기존 AWS Inspection/Workload와 S3/GCS state bucket/버전 및 중앙 로그는 기본 보존한다. 공유되는 기존 리소스를 일괄 삭제하거나 전체 환경 Destroy를 자동 실행하지 않는다.
