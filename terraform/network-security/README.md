# 중앙 Network / Security 기반

GuardDuty는 이 계정/리전의 기본 탐지를 관리하며 기본 활성 상태는 true다. ANF의 inspection_mode와 독립적으로 동작한다. 신규 detector는 먼저 guardduty_enabled=false로 생성하고 선택 기능 OFF를 확인한 뒤 true로 활성화한다. [GuardDuty 설계와 실제 검증 상태](../../docs/ai-dlc/aws-guardduty.md), [등록과 운영 절차](RUNBOOK.md#guardduty-등록과-운영)를 따른다.

ANF 비용 중단과 원복은 [전환 Runbook](RUNBOOK.md#anf-비용-중단과-원복)과 [AI-DLC](../../docs/ai-dlc/anf-cost-mode.md)를 따른다. 아래의 검사 경로 설명은 기본 `inspection_mode=enabled` 기준이다.

| `inspection_mode` | 트래픽 경로 | ANF 엔드포인트 | 용도 |
| --- | --- | --- | --- |
| `enabled` | 중앙 ANF 검사 | 유지, 시간 요금 발생 | 기본 검사 상태 |
| `bypass` | TGW에서 목적지 VPC로 직접 전달 | 유지, 시간 요금 발생 | 제거 전 통신 확인 또는 원복 준비 |
| `disabled` | TGW에서 목적지 VPC로 직접 전달 | 제거, 해당 엔드포인트 과금 중단 | 비용 절감 상태 |

모드는 `enabled -> bypass -> disabled`, 원복은 `disabled -> bypass -> enabled` 순서로 바꾼다. bypass는 삭제 보호를 해제한다. enabled는 `firewall_protection` 입력을 다시 적용한다. disabled에서도 정책, 규칙 집합, 기존 로그 그룹, VPC/서브넷, TGW/Attachment와 RAM Share/Principal은 보존한다. 기존 로그는 설정한 보존 기간을 따른다. 우회 중에는 ANF 검사와 신규 검사 로그가 없고, 기존 8081 차단 시험도 통과할 수 있다.

2026-11-04 사용자 지시로 GCP 연결에서는 ANF disabled를 유지한다. bypass/disabled의 VPC 목적지는 소유 attachment로 직접 전달하고, VPN은 두 연결 모두 Spoke RT에 BGP를 전파한다. VPN 목적지의 정적 검사 경로를 제거해 ECMP/경로 철회를 보존한다. enabled에서는 기존 검사 경로와 Inspected RT 전파만 사용한다. 이번 추가는 로컬 mock 검증이며 실제 VPN 배포/통신은 아직 확인하지 않았다. [승인/현재 상태](../../docs/ai-dlc/gcp-central-network.md)

하나의 중앙 AWS 계정에 TGW 1개, Inspection VPC 1개, VPC 기반 Network Firewall 1개를 만든다. 기본 2 AZ에서는 TGW 전용 Subnet 2개와 Firewall 전용 Subnet 2개를 사용한다. 소비자 연결과 VPN 입력이 비어 있어도 중앙 기반을 생성할 수 있다.

기본 이름은 `delta`, 환경은 `lab`, Region은 `ap-northeast-2`다. Terraform `1.16.4`와 AWS Provider `6.71.0`을 고정했다. Provider 패키지는 최초 init에서 HashiCorp 서명을 확인했고 `.terraform.lock.hcl`을 함께 보관한다. 로컬 파일 mirror를 사용하는 재검증 init은 `unauthenticated`로 표시될 수 있으나 기존 lock checksum을 검증한다.

업무 계정의 VPC/ALB/Backend/보조 Endpoint, GCP/온프레미스 내부 장비, NAT/IGW/중앙 인터넷 출구, Organizations/OU/SCP/Identity Center 변경은 생성하지 않는다. 동일 VPC 내부 통신과 원격 네트워크 내부 통신이 자동 검사되는 구성도 아니다.

동일 Workload 계정의 두 VPC 간 HTTP 허용/차단과 FLOW/ALERT 검증은 [과거 Server 기록](../../docs/ai-dlc/aws-workload-server.md)에 보존한다. 현재는 `10.10.0.0/16`의 [공통 네트워크 root](../workload/README.md)로 통합한다. 폐기 대역의 연결과 정책을 정리한 실제 결과는 [통합 기록](../../docs/ai-dlc/aws-workload-consolidation.md)을 따른다.

## 준비할 값

1. 중앙 계정에서 사용할 SSO/Profile 또는 임시 자격 증명. 셸에서 `export AWS_PROFILE=sbh-delta-security`를 지정한다. Provider에는 Profile이나 인증 키를 하드코딩하지 않는다.
2. 검증할 중앙 Account ID, 실제 Inspection VPC CIDR, 선택한 물리적 AZ IDs와 AZ별 TGW/Firewall Subnet CIDR.
3. 원격 backend용 기존 S3 bucket과 key. 처음 준비하는 경우 아래 절차를 별도로 수행한다.
4. 공유 시 실제 소비자 Account IDs. 기본적으로 같은 Organization의 계정만 허용하며 RAM의 Organizations 통합이 사전 활성화돼 있어야 한다. 조직 관리 계정의 AWS RAM Settings에서 Enable sharing with AWS Organizations를 켜거나 RAM의 `enable-sharing-with-aws-organization`을 실행한다. 이 코드는 Organizations 설정을 변경하지 않는다.
5. 연결 단계에서 업무 계정의 Attachment 계약, 원격 Gateway/BGP 정보, 실제 허용할 서비스 목록.

```bash
export AWS_PROFILE=sbh-delta-security
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output json
aws ec2 describe-availability-zones --region ap-northeast-2 \
  --query 'AvailabilityZones[?ZoneType==`availability-zone`].{Id:ZoneId,Name:ZoneName,State:State}'
```

계정별 AZ 이름은 물리적 위치를 보장하지 않는다. 중앙과 소비자는 반드시 AZ ID를 교환한다. 실제 Account ID는 `central_account_id`로 입력하며 Provider의 `allowed_account_ids`와 별도 identity precondition으로 검사한다. 실제값을 추정하거나 예시 CIDR로 AWS 리소스를 생성하지 않는다.

RAM 조직 공유 활성화는 조직 관리 계정에서 수행하며 `AWSServiceRoleForResourceAccessManager` 서비스 연결 역할을 생성한다. Organizations 화면에서 trusted access만 켠 경우 이 역할이 생성되지 않을 수 있으므로 AWS RAM 화면이나 RAM CLI를 사용한다. 현재 구성은 공유 대상 입력이 비어 있어도 TGW/Firewall을 Share에 연결하므로 첫 중앙 Apply 전 이 사전 설정이 필요하다. 관련 오류의 확인 및 재시도는 [Runbook](RUNBOOK.md#ram-resource-association이-실패할-때)을 따른다. [AWS 활성화 절차](https://docs.aws.amazon.com/ram/latest/userguide/getting-started-sharing.html)

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `name` | `string` | `delta` | 리소스 이름 접두사 |
| `environment` | `string` | `lab` | 이름과 환경 태그 |
| `region` | `string` | `ap-northeast-2` | 중앙 리소스 Region |
| `central_account_id` | `string` | 필수 | 명시적으로 기대하는 중앙 12자리 Account ID |
| `inspection_vpc_cidr` | `string` | 필수 | 정규화된 IPv4 VPC CIDR, /16~ /28 |
| `inspection_azs` | `map(object)` | 필수 | AZ ID별 전용 Subnet CIDR, 최소 2 AZ |
| `tgw_asn` | `number` | `64512` | TGW의 private BGP ASN |
| `tags` | `map(string)` | `{}` | Team/Project/Environment/ManagedBy 기본 태그 재정의 |
| guardduty_enabled | bool | `true` | 계정/리전 기본 탐지. false는 detector와 findings를 보존한 일시 중지 |
| guardduty_finding_publishing_frequency | string | `SIX_HOURS` | 후속 finding 알림 주기. FIFTEEN_MINUTES/ONE_HOUR/SIX_HOURS |
| guardduty_features | map(object) | 기능별 기본값 표 참고 | 필수 7개 선택 기능과 추가 설정의 명시적 상태 |
| `guardduty_findings_forwarding_enabled` | `bool` | `true` | 플랫폼 구독만 제어. false여도 원본 Finding 저장과 탐지 유지 |
| `guardduty_findings_destination_arn` | `string` | `arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs` | 같은 리전의 플랫폼 계정 논리 Destination |
| `shared_account_ids` | `set(string)` | `[]` | TGW와 Firewall을 공유할 소비자 계정 |
| `allow_external_principals` | `bool` | `false` | Organization 밖 RAM 공유 허용 |
| `spoke_attachments` | `map(object)` | `{}` | 소비자 VPC 연결 및 수락 관리 계약 |
| `vpn_networks` | `map(object)` | `{}` | 중복 없이 정의하는 논리적 원격 네트워크 |
| `vpn_peers` | `map(object)` | `{}` | 입력된 AWS Customer Gateway/VPN 연결 |
| `allowed_flows` | `map(object)` | `{}` | 명시적 TCP/UDP/ICMP 허용 목록 |
| `suricata_rules` | `map(object)` | `{}` | 허용 추적보다 먼저 평가할 선택적 추가 규칙 |
| `stateful_rule_capacity` | `number` | `1000` | Rule Group capacity, 변경 시 교체에 주의 |
| `inspection_mode` | `string` | `enabled` | `enabled`: 검사, `bypass`: ANF를 유지한 직접 경로, `disabled`: 직접 경로와 엔드포인트 제거. 직접 모드의 VPN은 Spoke RT에 BGP 전파 |
| `preserve_vpn_direct_routes` | `bool` | `false` | enabled에서도 Spoke BGP 전파 유지. 광고 필터가 아니며 대상 분리는 아래 `isolated_inspection_spokes`와 실제 RT/광고로 확인 |
| `isolated_inspection_spokes` | `set(string)` | `[]` | 대상 spoke key별 송신 검사 RT 분리. VPN RT에 원격 정적 대역을 넣어 BGP로 되광고하지 않도록 함 |
| `firewall_protection` | `object` | 내부 속성 모두 `true` | 삭제/Subnet/정책 연결 변경 보호 |
| `log_retention_days` | `number` | `30` | 지원되는 유한 CloudWatch Logs 보존 일수 |
| `retain_logs_on_destroy` | `bool` | `true` | 해체 시 로그 그룹 보존, 재관리 시 import 필요 |
| `network_firewall_logs_forwarding_enabled` | `bool` | `true` | 기업 NFW FLOW와 ALERT 그룹의 플랫폼 구독 제어. false여도 원본 그룹 유지 |
| `network_firewall_logs_destination_arn` | `string` | `arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs` | 같은 리전의 플랫폼 계정 논리 Destination |
| `cidr_service_map` | `map(object)` | `{}` | 나중에 로그에 보강할 CSP/service_id 매핑 |

`Name`은 각 리소스에 설정한다. 나머지 공통 태그의 기본값은 `Team=Delta`, `Project=delta-multicloud`, `Environment=var.environment`, `ManagedBy=Terraform`이며 `tags`로 조정한다.

### `inspection_azs` 내부 속성

맵 키는 계정별 AZ 이름이 아닌 물리적 AZ ID다. Subnet은 /28 이상 크기로 잡고 VPC 안에 들어가며 서로 겹치지 않아야 한다.

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `tgw_subnet_cidr` | `string` | 필수 | 해당 AZ의 TGW Attachment 전용 Subnet |
| `firewall_subnet_cidr` | `string` | 필수 | 해당 AZ의 Primary Firewall Endpoint 전용 Subnet |

### `spoke_attachments` 내부 속성

맵 키는 수명 동안 유지하는 연결 이름이다. 동일 Attachment ID를 여러 키에 넣지 않는다.

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `account_id` | `string` | 필수 | `shared_account_ids`에 있는 소비자 계정 |
| `role` | `string` | 필수 | `ALB`, `Backend` 또는 사설 시험 클라이언트 `Client` |
| `vpc_cidrs` | `set(string)` | 필수 | 해당 VPC의 실제 CIDR 목록 |
| `attachment_id` | `string` | 필수 | 소비자가 만든 TGW VPC Attachment ID |
| `acceptance_mode` | `string` | 필수 | `accept`: 중앙 accepter 소유, `existing`: 이미 수락된 연결 참조 |
| `reported_state` | `string` | 필수 | `pendingAcceptance` 또는 `available`; existing은 available만 허용 |
| `az_ids` | `set(string)` | 필수 | 실제 Attachment가 사용하는 중앙 지원 AZ IDs |

`acceptance_mode=accept`는 수락 후에도 유지하고 `reported_state`만 available로 갱신한다. API의 일시적 상태로 for_each 키를 바꾸지 않는다. existing에는 accepter를 만들지 않는다. 현재 API 조회 시 기존 연결은 available 상태로 제한하고 TGW/소유 계정도 확인한다. 같은 accepter를 다른 state에서 중복 소유하지 않는다.

Provider 6.71.0의 [accepter 삭제 구현](https://github.com/hashicorp/terraform-provider-aws/blob/v6.71.0/internal/service/ec2/transitgateway_vpc_attachment_accepter.go)은 실제 소비자 Attachment도 삭제한다. 잘못된 mode/key 변경을 막기 위해 `prevent_destroy=true`를 적용했다. 소비자 소유 연결의 실제 삭제는 소비자와 조율하고, 단순 수락 상태 갱신 때문에 accepter를 제거하지 않는다.

### `vpn_networks`와 `vpn_peers` 내부 속성

논리적 네트워크의 CIDR은 한 번만 정의한다. GCP HA의 두 AWS 연결은 같은 `network_key`를 참조한다. 서로 다른 네트워크 및 VPC의 CIDR은 겹칠 수 없다.

| 입력 | 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- | --- |
| `vpn_networks` | `cidrs` | `set(string)` | 필수 | 원격 측에서 광고하기로 계약한 CIDRs |
| `vpn_networks` | `kind` | `string` | 필수 | `gcp` 또는 `onprem` |
| `vpn_peers` | `network_key` | `string` | 필수 | 정의된 논리적 원격 네트워크 키 |
| `vpn_peers` | `public_ip` | `string` | 필수 | 실제 공인 Gateway IPv4, private/reserved/TEST-NET 거부 |
| `vpn_peers` | `bgp_asn` | `number` | 필수 | private BGP ASN, TGW ASN과 달라야 함 |
| `vpn_peers` | `tunnel1_inside_cidr` | `string` | 필수 | 첫 터널의 비예약 link-local /30 |
| `vpn_peers` | `tunnel2_inside_cidr` | `string` | 필수 | 둘째 터널의 비예약 link-local /30 |
| `vpn_peers` | `gcp_interface` | `number` | `null` | GCP gateway interface 0 또는 1 |

GCP 네트워크는 interface 0/1의 서로 다른 공인 IP와 동일 Cloud Router ASN을 사용하는 Peer 2개가 필요하다. 온프레미스는 장비의 실제 HA 구조에 맞게 Peer를 입력한다. 같은 Gateway IP/ASN 조합을 여러 Peer 키로 중복 관리할 수 없다. Tunnel Inside CIDR은 전체 TGW에서 중복할 수 없으며 AWS 예약 /30을 거부한다. 32비트 ASN은 Provider의 `bgp_asn_extended`를 사용한다.

### `allowed_flows`와 `suricata_rules` 내부 속성

| 입력 | 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- | --- |
| `allowed_flows` | `source_cidrs` | `set(string)` | 필수 | 연결을 시작할 출발지 CIDRs |
| `allowed_flows` | `destination_cidrs` | `set(string)` | 필수 | 도착지 CIDRs |
| `allowed_flows` | `protocol` | `string` | 필수 | `tcp`, `udp`, `icmp` |
| `allowed_flows` | `destination_ports` | `set(number)` | `[]` | TCP/UDP는 1~65535 포트 필수, ICMP는 비움 |
| `allowed_flows` | `icmp_type` | `number` | `null` | ICMP 요청 type 필수, 예시는 echo request |
| `allowed_flows` | `icmp_code` | `number` | `null` | ICMP code 선택, TCP/UDP에는 지정하지 않음 |
| `allowed_flows` | `sid` | `number` | 필수 | 고유 정수 SID, 1000000 이상 2000000 미만 |
| `suricata_rules` | `sid` | `number` | 필수 | 고유 정수 SID, 2000000 이상 9000000 미만 |
| `suricata_rules` | `rule` | `string` | 필수 | 선언한 SID가 들어간 단일 Suricata drop/alert/reject 규칙 |

### `firewall_protection`과 `cidr_service_map` 내부 속성

| 입력 | 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- | --- |
| `firewall_protection` | `delete` | `bool` | `true` | Firewall 삭제 보호 |
| `firewall_protection` | `subnet` | `bool` | `true` | Firewall Subnet 변경 보호 |
| `firewall_protection` | `policy` | `bool` | `true` | Firewall Policy 연결 변경 보호 |
| `cidr_service_map` | `cidr` | `string` | 필수 | 보강할 서비스/네트워크 CIDR |
| `cidr_service_map` | `csp` | `string` | 필수 | 운영자가 정의한 CSP 메타데이터 |
| `cidr_service_map` | `service_id` | `string` | 필수 | 운영자가 정의한 서비스 식별자 |

### GuardDuty 기능별 기본값

| 기능명 | 기본 상태 | 추가 설정 |
| --- | --- | --- |
| S3_DATA_EVENTS | DISABLED | 없음 |
| EKS_AUDIT_LOGS | DISABLED | 없음 |
| EBS_MALWARE_PROTECTION | DISABLED | 없음 |
| RDS_LOGIN_EVENTS | DISABLED | RDS_DATA_RISK=DISABLED |
| LAMBDA_NETWORK_LOGS | DISABLED | 없음 |
| RUNTIME_MONITORING | DISABLED | EKS/ECS Fargate/EC2 에이전트 자동 관리 모두 DISABLED |
| AI_PROTECTION | DISABLED | 없음 |

### guardduty_features 내부 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| map key | string | 위 기능별 기본값 | 필수 7개 키를 유지한다. 해당 리전에서 지원을 확인한 AI_ANALYST만 선택적으로 추가할 수 있다 |
| status | string | 필수 | ENABLED 또는 DISABLED |
| additional_configuration | map(string) | `{}` | 기능별 추가 설정. 값은 ENABLED 또는 DISABLED. 아래 RDS/Runtime 키는 모두 명시해야 한다 |
| RDS_LOGIN_EVENTS.RDS_DATA_RISK | string | 위 기능별 기본값 | RDS 로그인 탐지의 추가 데이터 위험 탐지 설정 |
| RUNTIME_MONITORING.EKS_ADDON_MANAGEMENT | string | DISABLED | EKS 보안 에이전트 자동 관리 |
| RUNTIME_MONITORING.ECS_FARGATE_AGENT_MANAGEMENT | string | DISABLED | ECS Fargate 보안 에이전트 자동 관리 |
| RUNTIME_MONITORING.EC2_AGENT_MANAGEMENT | string | DISABLED | EC2 보안 에이전트 자동 관리 |

`guardduty_features` 입력은 전체 맵을 교체한다. 기능 하나만 바꾸더라도 필수 7개 키와 RDS/Runtime 추가 설정을 모두 유지한다. 꺼진 기능의 추가 설정도 DISABLED여야 하며 다른 기능에는 추가 설정을 넣지 않는다. 과거 EKS_RUNTIME_MONITORING은 RUNTIME_MONITORING과 함께 구성하지 않는다.

### guardduty 출력 내부 속성

| 속성명 | 역할 |
| --- | --- |
| account_id / region | 관리하는 실제 계정과 리전 |
| detector_id / arn | detector 식별자 |
| enabled | 기본 탐지 활성 상태. false는 detector 보존과 일시 중지를 뜻한다 |
| finding_publishing_frequency | 기존 finding의 후속 발생 알림 주기 |
| features | 기능명별 실제 Provider 상태와 additional_configuration의 이름/상태 맵. Findings 본문은 포함하지 않는다 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `central_account_id` | 인증된 중앙 계정 ID |
| guardduty | 계정/리전 detector ID/ARN, 활성 상태, 게시 주기와 관리하는 기능/추가 설정 |
| `guardduty_findings` | 기업 원본 Finding 로그 그룹, EventBridge 규칙과 플랫폼 구독 구성 |
| `network_firewall_logs` | 기업 NFW FLOW와 ALERT 원본 그룹 및 플랫폼 구독 구성 |
| `region` | 중앙 Region |
| `az_ids` | 지원하는 물리적 AZ IDs |
| `tgw_id` | 공유 TGW ID |
| `tgw_arn` | 공유 TGW ARN |
| `spoke_route_table_id` | 검사 전 TGW RT ID |
| `inspected_route_table_id` | 검사 후 TGW RT ID |
| `inspection_attachment_id` | appliance mode Inspection Attachment ID |
| `firewall_arn` | 공유할 Firewall 자체 ARN, disabled에서는 null |
| `inspection_mode` | 적용할 검사/우회/비용 중단 모드 |
| `isolated_inspection_route_table_ids` | 대상 spoke key별 준비한 전용 송신 검사 RT ID. VPN에는 연결하지 않음 |
| `inspection_route_targets` | 정적 Spoke Route의 기대 다음 홉. 직접 모드의 VPN 목적지는 BGP 경로이므로 맵에서 제외 |
| `primary_endpoint_ids_by_az_id` | Inspection VPC의 AZ ID별 Primary Endpoint IDs, disabled에서는 빈 맵 |
| `tgw_ram_share_arn` | TGW RAM Share ARN |
| `firewall_ram_share_arn` | Firewall RAM Share ARN |
| `log_groups` | 과거 로그 보존용 기존 Network Firewall FLOW와 ALERT 그룹 이름 |
| `inspection_route_table_ids` | AZ ID별 TGW/Firewall Subnet RT IDs |
| `vpn_handoff` | 원격 담당자용 비밀값 없는 Gateway/Tunnel/BGP 계약 |
| `cidr_service_map` | 로그 보강용 입력 메타데이터 |

`vpn_handoff`는 network_key, remote_cidrs, aws_cidrs_to_advertise, gcp_interface, Customer Gateway/VPN/Attachment ID, Gateway IP, 고객 ASN, IKE/암호화/무결성/DH 설정과 두 터널의 outside/inside IP 및 AWS ASN을 제공한다. `aws_cidrs_to_advertise`는 AWS에서 원격 측으로 광고할 업무 VPC CIDR 목록이다. PSK와 `customer_gateway_configuration`은 출력하지 않는다.

### `guardduty_findings` 출력 내부 속성

| 속성명 | 역할 |
| --- | --- |
| `log_group_name` | 기업 원본 그룹 `/delta/security/guardduty` |
| `log_group_arn` | 원본 그룹 ARN |
| `event_rule_name` | 기본 버스의 GuardDuty Finding 규칙 이름 |
| `event_rule_arn` | 규칙 ARN |
| `destination_arn` | 기존 플랫폼 논리 Destination ARN |
| `forwarding_enabled` | 구독 필터 생성 구성. 실제 전송 성공 여부는 별도 검증 |
| `subscription_filter_name` | `delta-platform-central`. 전달 토글 false이면 null |

### `network_firewall_logs` 출력 내부 속성

| 속성명 | 역할 |
| --- | --- |
| `log_groups` | `FLOW=/delta/security/nfw-flow`, `ALERT=/delta/security/nfw-alert` |
| `destination_arn` | 기존 플랫폼 논리 Destination ARN |
| `forwarding_enabled` | 두 기업 원본 그룹의 구독 생성 구성. 실제 전송 성공 여부는 별도 검증 |
| `subscription_filter_names` | 로그 유형별 `delta-platform-central`. 전달 토글 false이면 각 값은 null |

## S3 backend를 준비하고 실행하기

다음 bucket 준비 명령은 운영자가 실제값을 정해 별도로 실행하는 절차다. 이번 코드 작성 및 로컬 검증에서 실행하지 않았다. `DELTA_STATE_BUCKET`, `DELTA_REGION`에는 실제 bucket 이름과 Region을 지정한다.

```bash
aws s3api create-bucket --bucket "$DELTA_STATE_BUCKET" --region "$DELTA_REGION" \
  --create-bucket-configuration "LocationConstraint=$DELTA_REGION"
aws s3api put-bucket-versioning --bucket "$DELTA_STATE_BUCKET" \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket "$DELTA_STATE_BUCKET" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-public-access-block --bucket "$DELTA_STATE_BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

`us-east-1`에서 bucket을 생성할 때는 create-bucket의 LocationConstraint 인수를 생략한다. 기존 bucket이면 생성 명령을 반복하지 말고 버전 관리, 암호화, Public Access Block, 소유 계정과 접근 권한을 확인한다. bucket에 state와 lockfile을 관리할 최소 권한을 부여한다. lockfile에는 GetObject/PutObject/DeleteObject가 필요하지만 state 파일 자체의 DeleteObject는 필수가 아니다. DynamoDB locking은 추가하지 않는다.

이미 실제 `terraform.tfvars`와 `backend.hcl`을 작성했다면 아래 예시 복사 단계는 건너뛴다.

```bash
cd terraform/network-security
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# 두 파일을 실제값으로 수정한다. 예시 Account ID/CIDR/AZ를 배포에 사용하지 않는다.
terraform init -backend-config=backend.hcl
terraform fmt -check -recursive
terraform validate
umask 077
terraform plan -input=false -out=central.tfplan
```

`backend.hcl`은 자동으로 읽는 파일이 아니다. 현재 root의 `backend "s3" {}`는 부분 설정이므로 `terraform init -backend-config=backend.hcl`로 파일을 명시해야 한다. 파일을 지정하지 않고 `terraform init`만 실행하면 bucket 등 필수 값을 대화형으로 다시 요구한다. `terraform.tfvars`는 리소스 입력용이며 backend 설정을 대신하지 않는다. [HashiCorp 부분 backend 설정](https://developer.hashicorp.com/terraform/language/backend#partial-configuration)

backend는 부분 설정이며 `encrypt=true`, `use_lockfile=true`, 기대하는 계정을 설정한다. 인증정보는 backend 파일에 넣지 않는다. 기존 backend나 state가 발견되면 이 절차로 자동 migrate하지 않는다. Plan 파일과 state에는 민감값이 포함될 수 있어 커밋하지 않는다. Apply는 검토한 Plan과 별도 실행 지시가 있을 때만 수행한다.

## 중앙 생성과 계정 간 연결 순서

1. 실제 중앙 입력을 정하고 공유 대상과 VPN/업무 연결은 필요에 따라 빈 상태로 중앙 기반을 준비한다.
2. 별도 승인된 Apply 후 중앙 출력 중 TGW/Firewall ARN, AZ IDs, RAM Share ARNs를 소비자에게 전달한다.
3. 소비자는 RAM 공유를 확인하고 자기 Provider/state에서 VPC Attachment와 보조 Endpoint를 요청한다.
4. 소비자의 Account/역할/CIDR/Attachment/수락 상태/AZ 계약을 받아 `spoke_attachments`에 등록한다. 허용 정책도 실제 요구에 맞게 입력한다.
5. 중앙 수락, Association과 Route 등록의 Plan을 검토하고 별도 승인된 Apply 후 양방향 통신과 SID 로그를 확인한다.

구체적인 소비자 Route와 Endpoint 예시는 [CONNECTIONS.md](CONNECTIONS.md)에 있다. GCP와 온프레미스 장비는 이 저장소에서 생성하거나 설정하지 않는다.

## Route Table 표

| 위치 | 목적지 | 다음 홉 |
| --- | --- | --- |
| TGW Spoke | 등록한 모든 원격/업무 CIDR | Inspection VPC Attachment |
| Inspection TGW Subnet, AZ별 | 같은 등록 CIDR | 해당 AZ의 Primary Firewall Endpoint |
| Inspection Firewall Subnet, AZ별 | 같은 등록 CIDR | TGW |
| TGW Inspected | 업무 VPC CIDR | 해당 업무 VPC Attachment |
| TGW Inspected | 원격 VPN 광고 Prefix | 해당 VPN에서 BGP로 학습한 Attachment |

위 표는 기본 전체 검사 모드다. 이 모드의 업무 VPC/VPN Attachment는 Spoke, Inspection Attachment는 Inspected에 연결한다. Spoke에는 직접 Route/Propagation을 두지 않고 AWS VPC는 정적 Route, VPN Propagation은 Inspected에 둔다. 중앙 VPC에는 IGW/NAT나 인터넷 기본 경로가 없다. `EXTERNAL_NET`의 전체 IPv4 값은 규칙 변수이며 인터넷 Route가 아니다.

현재 `delta-board-vpc`는 `preserve_vpn_direct_routes=true`, `isolated_inspection_spokes=["aws_client"]`를 사용한다. board만 전용 송신 RT에 연결해 GCP 정적4경로를 Inspection으로 보낸다. 두 VPN은 기존 공유 Spoke RT에 남고 GCP BGP 전파를 유지하며 AWS board 반환 정적 경로만 Inspection으로 보낸다. Inspected RT는 두 VPN의 BGP/ECMP를 유지한다. VPN 연결 RT의 경로가 원격 광고에 영향을 주므로 GCP 정적 prefix를 그 RT에 넣어 되광고하지 않는다. 플래그만으로 광고가 필터링되는 것은 아니다. [대상 한정 전환과 복귀](RUNBOOK.md#delta-board-vpc-인터넷과-gcp-검사-재개), [실제 검증](../../docs/ai-dlc/anf-board-reactivation.md)을 따른다.

GCP에서 Account B Backend로 가는 실제 요청/응답의 단계별 설명과 ALB 경로는 [연결 문서](CONNECTIONS.md#왕복-경로를-추적하기)에 있다. appliance mode 설정만으로 소비자 VPC Route나 원격 BGP가 자동 완성되지는 않는다.

## 기본 차단과 검사 범위

- Stateless 일반/Fragment 기본 Action은 `aws:forward_to_sfe`다. Stateful Policy와 Rule Group 모두 `STRICT_ORDER`, stream exception은 `DROP`이다.
- 추가 Suricata 규칙을 먼저 평가한 뒤 허용 목록에 맞는 시작 방향의 패킷을 `alert`와 `flowbits:set,delta_allowed`로 표시한다. `flowbits:noalert`로 허용 표시 자체의 ALERT는 억제한다. 같은 연결의 응답은 같은 flowbit를 사용한다.
- 마지막 SID `9000000`은 해당 bit가 없는 트래픽을 명시적으로 drop하고 차단 ALERT를 남긴다. 허용 목록이 비어 있으면 전체 허용 규칙 없이 이 차단 규칙만 남는다.
- 이 방식의 허용 표시는 종료형 pass가 아니다. 패킷은 이후에도 추가 시그니처 평가 대상이 된다. Policy에 `aws:drop_strict`를 추가하면 허용 표시된 패킷도 차단되므로 기본 전체 drop을 중복 설정하지 않는다. 마지막 차단 규칙을 제거하거나 비활성화해서도 안 된다.
- TCP 연결 성립과 응답, UDP 응답, ICMP echo 요청/응답은 배포 후 실제 패킷으로 검증한다. ALB Health Check도 별도 TCP 허용 입력이 필요하다. ICMP 오류/PMTUD 등 추가 type을 필요로 하면 별도로 설계하고 검증한다.
- HOME_NET은 Inspection, 등록된 ALB/Backend/Client VPC, GCP/온프레미스 CIDR의 합집합이다. Rule Group의 EXTERNAL_NET을 전체 IPv4로 명시해 HOME_NET 간 통신이 음수 조건으로 빠지지 않게 한다.
- SID 범위를 분리하고 중복을 거부한다. 추가 규칙은 한 줄의 drop/alert/reject만 받으며 pass, flowbit 변경과 STRICT_ORDER의 priority keyword는 거부한다. 추가 규칙 내부와 허용 목록 내부는 각각 맵 키의 사전순으로 평가하므로 순서가 필요하면 키에 순서 접두사를 붙인다. reject는 TCP에만 사용한다. 기본 제공 IPS 서명 집합은 없고 포트 허용/차단을 완전한 IPS라고 부르지 않는다.
- 계정 간 보조 Endpoint 공유 때문에 TLS 복호화를 활성화하지 않는다. 암호화된 HTTPS 본문 검사나 기존 연결의 허용 취소 즉시 적용을 보장하지 않는다. 정책을 바꾼 뒤 새 연결과 기존 연결을 구분해 확인한다.

## GuardDuty Finding의 플랫폼 전달

서울 중앙 계정의 `GuardDuty Finding` 이벤트를 기본 EventBridge 버스에서 선택해 `/delta/security/guardduty`에 전체 JSON으로 저장한다. 규칙은 송신 계정과 리전도 제한한다. GuardDuty가 분석하는 CloudTrail/Flow/DNS 원본 전체가 아니라 탐지 결과의 저장 경로다.

원본 그룹 하나의 빈 구독 필터가 새 로그를 `guardduty_findings_destination_arn`으로 보낸다. 기존 플랫폼 Destination/Kinesis/Lambda를 거쳐 실제 `/delta-cp/central/aws`에 원본 계정/그룹/시각/본문과 수집 정보를 저장한다. 플랫폼의 tracked 코드와 실제 그룹 이름 차이는 [연결 안내](../../platform/deploy/logging/README.md)를 따른다. 이 root는 플랫폼 수신 리소스를 관리하지 않는다.

원본은 기존 `log_retention_days`(기본 30일), `retain_logs_on_destroy`(기본 true)를 재사용하고 플랫폼 중앙은 현재 3일 보관한다. `guardduty_findings_forwarding_enabled=false`는 구독만 제거하며 EventBridge의 원본 저장과 GuardDuty 활성 상태는 유지한다. 재개 이후 새 이벤트부터 전달하므로 중단 구간이나 기존 Findings가 자동 소급 전송되지는 않는다.

로그 쓰기 정책은 EventBridge 전달 서비스 두 개에 원본 그룹의 스트림 생성/쓰기만 허용한다. EventBridge 타깃과 논리 Destination 구독에 송신 역할을 지정하지 않는다. 전송 중복은 가능하며 Finding ID, EventBridge 이벤트 ID와 플랫폼의 안정적인 `event_id`를 구분한다. [설계와 실제 검증 상태](../../docs/ai-dlc/aws-guardduty-logging.md), [조회/샘플/원복 절차](RUNBOOK.md#guardduty-finding-로그-전달)를 따른다.

## 로그, 로컬 검증과 운영

기존 `/aws/vendedlogs/network-firewall/delta-lab/flow`와 `/aws/vendedlogs/network-firewall/delta-lab/alert`는 과거 로그와 Terraform 소유권을 보존한다. 새 Network Firewall 대상은 `/delta/security/nfw-flow`와 `/delta/security/nfw-alert`다. 로그 그룹 이름은 직접 바꿀 수 없으므로 기존 그룹을 교체하거나 삭제하지 않는다. 구독은 새 이벤트만 플랫폼으로 보내며 과거 로그를 자동으로 다시 보내지 않는다.

두 기업 원본 그룹의 빈 구독 필터가 `network_firewall_logs_destination_arn`으로 전송한다. 필터 이름은 각 그룹에서 `delta-platform-central`이고 배포 방식은 `Random`이다. 기존 플랫폼 Destination, Kinesis와 Lambda를 거쳐 실제 `/delta-cp/central/aws`에 원본 계정, 그룹, 시각과 본문을 저장한다. `network_firewall_logs_forwarding_enabled=false`는 두 구독만 제거하고 네 개의 원본 그룹과 향후 Network Firewall 로그 대상 정의를 유지한다.

중앙 계정의 기존 및 기업 FLOW/ALERT Log Group과 로그 전달 Resource Policy를 관리한다. `delivery.logs.amazonaws.com`에 네 그룹의 log-stream 생성/쓰기만 허용하고 SourceAccount와 SourceArn을 제한한다. 실행 역할에는 CloudWatch Logs의 Create/Get/Update/Delete/ListLogDelivery와 Put/DescribeResourcePolicies, DescribeLogGroups 권한이 필요하다. 정책 설명 및 목록 API에는 공식 문서에 맞는 wildcard 권한 범위를 사용한다. IAM User와 Access Key는 만들지 않는다.

로그 설정은 Firewall ARN 기준이다. `inspection_mode=disabled`에서는 Firewall과 logging configuration이 없지만 기업 원본 그룹과 구독은 유지한다. 이후 ANF를 복구하면 FLOW와 ALERT 모두 새 기업 그룹을 사용한다. 보조 Endpoint에는 별도 destination 설정이 없으므로 공유 Firewall의 중앙 로그 구성을 사용한다. 소비자 계정에 자동 Log Group이나 CloudWatch OAM을 생성하지 않는다. 소비자별 트래픽 발생 시 중앙 그룹 유입과 원시 스키마를 확인하는 것이 운영 수용 조건이다. [설계와 검증 상태](../../docs/ai-dlc/aws-traffic-log-forwarding.md), [조회와 전달 제어](RUNBOOK.md#network-firewall-로그-전달)를 따른다.

```bash
# AWS 인증과 실제 입력 없이 실행하는 독립 검증
TERRAFORM_BIN=terraform ./scripts/validate.sh
```

검증 스크립트는 원본 fmt check 후 별도 임시 디렉터리에서 `init -backend=false`, `validate`, `terraform test -var-file=terraform.tfvars.example`을 실행한다. 모든 test run은 `command=plan`과 mock AWS Provider만 사용한다. 실제 배포 Plan이나 AWS 패킷 처리 검증이 아니다. mock fixture의 가짜 ID/IP는 테스트에만 사용하며 root 설정의 실제값으로 사용하지 않는다.

검증 결과, 실행하지 않은 검사와 실계정 Plan의 미입력 값은 [AI-DLC 기록](../../docs/ai-dlc/central-network.md)을 확인한다. `DELTA_KEEP_VALIDATION=1`로 실행하면 임시 디렉터리와 테스트 JSON 이벤트를 보관한다. `DELTA_VERBOSE_PLAN=1`도 지정하면 mock Plan의 resource_changes를 보관하며 반복되는 대용량 Provider schema는 제거한다.

2026-11-01 최초 AWS Rule Group DryRun은 SCP의 `network-firewall:CreateRuleGroup` explicit deny로 거부됐다. 사용자 SCP 수정 안내 후 같은 중앙 프로필과 계정을 확인해 재시도한 결과, 기본 차단 및 TCP/UDP/ICMP와 추가 서명 예제의 두 규칙 집합 모두 AWS DryRun을 통과했다. 소비 용량은 각각 1과 5였고 리소스는 생성하지 않았다. 이는 AWS 규칙 요청 검증이며 전체 배포 권한과 실제 패킷 처리는 별도로 검증해야 한다. 이 작업에서 SCP/Organizations를 변경하지 않았다.

배포 후 확인은 [RUNBOOK.md](RUNBOOK.md)를 따른다. Firewall/Endpoint READY, RAM/Attachment, VPN/BGP/실제 Route 확인 후 GCP→Backend, ALB→Workload, Account A→B의 허용/차단 통신과 SID를 대조한다. traceroute만으로 Firewall 경유를 판정하지 않는다.

해체 순서는 소비자 Route와 보조 Endpoint 정리, 소비자 TGW 연결 정리, 중앙 Route 및 VPN 정리, 보호 설정 해제 Apply, 로그 설정 해제와 Firewall 및 나머지 중앙 리소스 삭제다. 로그 그룹과 state는 기본 보존하며 재관리 시 import와 backend 접근을 별도로 다룬다. 공유만 해제해도 기존 보조 Endpoint가 사라지지 않으므로 모든 Endpoint 제거를 확인해야 한다.

비용에는 AZ별 Primary Endpoint, 나중에 추가되는 Secondary Endpoint, 데이터 처리, TGW Attachment/처리, 각 AWS VPN 연결, CloudWatch Logs 저장/수집/조회, S3 state 보관을 포함한다. GCP HA는 AWS VPN 연결 2개다. Endpoint 비용은 공유된 보조 Endpoint를 포함해 Firewall 소유 계정에 청구된다. 현재 가격은 공식 가격표에서 사용 Region과 실제 트래픽으로 확인하고 근거 없는 금액을 제시하지 않는다.

## 공식 근거

- [Firewall 자체 공유와 AZ IDs](https://docs.aws.amazon.com/network-firewall/latest/developerguide/sharing.html)
- [보조 Endpoint 조건](https://docs.aws.amazon.com/network-firewall/latest/developerguide/creating-vpc-endpoint-association.html)
- [Endpoint와 TLS 제약](https://docs.aws.amazon.com/network-firewall/latest/developerguide/firewall-and-firewall-endpoints-considerations.html)
- [Seoul을 포함한 지원 Region](https://docs.aws.amazon.com/general/latest/gr/network-firewall.html), [여러 Endpoint의 지원 Region](https://aws.amazon.com/about-aws/whats-new/2025/05/aws-network-firewall-multiple-vpc-endpoints/)
- [TGW Route Table](https://docs.aws.amazon.com/vpc/latest/tgw/tgw-route-tables.html), [Network Firewall Route](https://docs.aws.amazon.com/network-firewall/latest/developerguide/vpc-config-route-tables.html)
- [STRICT_ORDER와 pass 동작](https://docs.aws.amazon.com/network-firewall/latest/developerguide/suricata-rule-evaluation-order.html), [Suricata 지원 제약](https://docs.aws.amazon.com/network-firewall/latest/developerguide/suricata-limitations-caveats.html)
- [Provider 6.71.0 Firewall 스키마](https://github.com/hashicorp/terraform-provider-aws/blob/v6.71.0/website/docs/r/networkfirewall_firewall.html.markdown), [보조 Endpoint 스키마](https://github.com/hashicorp/terraform-provider-aws/blob/v6.71.0/website/docs/r/networkfirewall_vpc_endpoint_association.html.markdown)
- [CloudWatch Logs 설정](https://docs.aws.amazon.com/network-firewall/latest/developerguide/logging-cw-logs.html), [로그 전달 권한](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/AWS-logs-infrastructure-CWL.html), [로그 스키마](https://docs.aws.amazon.com/network-firewall/latest/developerguide/firewall-logging-contents.html)
- [GCP HA VPN→AWS](https://docs.cloud.google.com/network-connectivity/docs/vpn/how-to/connect-ha-vpn-aws-peer-gateway)
- [S3 backend와 lockfile](https://developer.hashicorp.com/terraform/language/backend/s3), [Terraform mock Provider](https://developer.hashicorp.com/terraform/language/tests/mocking)
- [Network Firewall 가격](https://aws.amazon.com/network-firewall/pricing/), [TGW 가격](https://aws.amazon.com/transit-gateway/pricing/), [VPN 가격](https://aws.amazon.com/vpn/pricing/), [CloudWatch 가격](https://aws.amazon.com/cloudwatch/pricing/)
