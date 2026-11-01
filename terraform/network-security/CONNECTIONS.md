# 소비자와 원격 네트워크 연결 계약

## 중앙 계정과 소비자 계정의 소유 경계

중앙 계정은 TGW, Inspection VPC의 Primary Firewall Endpoints, Firewall 정책과 공유를 소유한다. 업무 계정은 각각 분리된 ALB VPC와 Backend VPC, 자기 계정의 TGW VPC Attachment, ALB VPC의 보조 Endpoint를 소유한다. 소비자 Provider와 state는 중앙과 분리한다.

TGW RAM Share와 Firewall RAM Share는 별도다. Firewall Policy만 공유받아서는 보조 Endpoint를 생성할 수 없다. Organization 통합이 활성화된 같은 조직에서는 공유 접근이 자동 부여될 수 있다. 조직 밖 공유를 명시적으로 허용한 경우 두 Share의 RAM 초대 수락 절차도 필요하다. 이 저장소는 그 사전 설정을 변경하지 않는다.

중앙에서 전달할 값:

- 중앙 Account ID/Region/AZ IDs, TGW ID/ARN, Firewall ARN.
- TGW와 Firewall RAM Share ARNs.
- Spoke/Inspected RT IDs 및 Inspection Attachment ID: 중앙 운영 확인용.
- Primary Endpoint IDs: 중앙 경로 감사용이며 업무 VPC Route에 재사용하지 않는다.

소비자가 돌려줄 값:

- Account ID, VPC 역할(`ALB`/`Backend`), 실제 VPC CIDRs.
- TGW VPC Attachment ID와 현재 수락 상태.
- 실제 Attachment 및 보조 Endpoint가 사용하는 AZ IDs.
- ALB listener/Backend/Health Check의 실제 프로토콜과 포트.

## 소비자 보조 Endpoint 예시

아래는 소비자의 별도 Terraform에서 사용할 안내다. 중앙 root module에서 실행하지 않는다. 실제 VPC/전용 Firewall Subnet은 소비자가 먼저 준비한다. Region은 중앙과 같고 `firewall_subnets` 키는 중앙이 제공한 AZ ID여야 한다.

```hcl
variable "consumer_account_id" { type = string }
variable "region" { type = string }
variable "firewall_arn" { type = string }
variable "primary_az_ids" { type = set(string) }
variable "firewall_subnets" { type = map(string) }

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.consumer_account_id]
}

data "aws_subnet" "firewall" {
  for_each = var.firewall_subnets
  id       = each.value
}

resource "aws_networkfirewall_vpc_endpoint_association" "secondary" {
  for_each     = var.firewall_subnets
  firewall_arn = var.firewall_arn
  vpc_id       = data.aws_subnet.firewall[each.key].vpc_id
  subnet_mapping {
    subnet_id       = each.value
    ip_address_type = "IPV4"
  }
  lifecycle {
    precondition {
      condition = contains(var.primary_az_ids, each.key) && (
        data.aws_subnet.firewall[each.key].availability_zone_id == each.key
      )
      error_message = "The consumer subnet must be in a physical AZ containing a central primary endpoint."
    }
  }
}

locals {
  secondary_endpoint_ids = {
    for az_id, subnet_id in var.firewall_subnets : az_id => one(flatten([
      for sync in one(aws_networkfirewall_vpc_endpoint_association.secondary[az_id].vpc_endpoint_association_status).association_sync_state : [
        for attachment in sync.attachment : attachment.endpoint_id
        if attachment.subnet_id == subnet_id
      ]
    ]))
  }
}
```

소비자의 Endpoint가 READY가 되면 그 VPC의 `local.secondary_endpoint_ids[az_id]`를 Route Target으로 사용한다. 중앙 Primary Endpoint ID를 대신 넣지 않는다.

소비자의 TGW Attachment는 각 사용 AZ에서 전용 Attachment Subnet을 선택한다. 기본 Route Table Association/Propagation은 false로 설정하고 중앙 수락을 기다린다. 중앙은 수락 후 Spoke RT에 Association한다. 이미 수락된 연결은 `acceptance_mode=existing`, 중앙이 수락하고 계속 관리할 연결은 `acceptance_mode=accept`로 등록한다.

`existing`은 수락 API를 다시 호출하지 않는다는 뜻이다. 이미 다른 Terraform state가 관리하는 Association/Route까지 자동 인수한다는 뜻은 아니다. 해당 객체가 이미 존재하면 소유자와 관리 이관을 조율하고 정확한 객체를 import한 뒤 Plan을 검토한다. 기존 Association을 자동으로 덮어쓰거나 상대 state와 중복 관리하지 않는다.

## ALB VPC와 Backend VPC의 Route

기존 local Route는 유지한다. 아래 표의 Endpoint는 모두 **그 ALB VPC에 실제 생성한 보조 Endpoint**다.

| Route Table | 목적지 | 다음 홉 | 목적 |
| --- | --- | --- | --- |
| ALB VPC의 IGW Edge RT | 각 ALB Subnet CIDR | 같은 AZ의 보조 Endpoint | 인터넷에서 ALB로 들어오는 트래픽 검사 |
| ALB Subnet RT | 인터넷 `0.0.0.0/0` | 같은 AZ의 보조 Endpoint | ALB에서 인터넷으로 응답하는 트래픽 검사 |
| 보조 Firewall Subnet RT | 인터넷 `0.0.0.0/0` | 해당 VPC의 IGW | 검사 후 인터넷으로 반환 |
| 보조 Firewall Subnet RT | Backend 및 다른 등록 사설 CIDR | TGW | 사설 목적지의 인터넷 기본 경로 유출 방지 |
| ALB Subnet RT | Backend 및 다른 등록 사설 CIDR | TGW | ALB가 생성한 Backend 연결을 중앙 검사로 전달 |
| ALB VPC TGW Subnet RT | ALB VPC 내부 | local | 중앙 검사 후 ALB로 도착 |
| Backend Subnet RT | ALB VPC 및 등록된 다른 원격 CIDR | TGW | Backend 응답과 원격 연결을 중앙 검사로 전달 |

IGW Edge RT에는 IGW gateway association이 필요하다. Subnet RT의 association과 다르므로 소비자가 명시적으로 설정한다. ALB는 최소 두 AZ에 배치하고 각 AZ의 왕복 인터넷 경로가 같은 보조 Endpoint를 사용하게 한다.

ALB→Backend 연결은 ALB Subnet의 구체적인 Backend CIDR Route가 TGW를 선택한다. 인터넷 기본 경로보다 구체적인 이 Route로 중앙 Primary Endpoint를 경유한다. 이 연결을 보조 Endpoint에 다시 넣는 이중 검사 경로는 기본 안내에 포함하지 않는다.

Backend Security Group/NACL은 실제 ALB 주소 범위와 서비스/Health Check 포트, 필요한 반환 트래픽을 허용해야 한다. 인터넷 사용자의 주소와 ALB→Backend 연결의 출발지는 다르다. L3 로그에서 Backend로 향하는 출발지는 ALB node의 사설 IP이고, 원래 사용자의 IP는 별도 HTTP `X-Forwarded-For`/ALB 접근 로그에서 확인한다. Network Firewall의 기본 L3 필드에 원본 사용자 IP가 보존된다고 가정하지 않는다.

## 왕복 경로를 추적하기

GCP의 사설 클라이언트가 Account B Backend의 사설 IP로 연결하는 경우:

1. GCP VPC의 유효 Route/BGP 정책이 Backend CIDR을 HA VPN으로 보낸다.
2. AWS VPN Attachment에 도착하면 연결된 Spoke RT가 Backend CIDR을 Inspection Attachment로 보낸다.
3. TGW가 선택한 Inspection AZ의 TGW Subnet RT가 같은 AZ의 Primary Endpoint로 보낸다.
4. Firewall이 정책을 평가한다. 허용되면 Firewall Subnet RT가 Backend CIDR을 TGW로 보낸다.
5. Inspection Attachment에서 TGW에 재진입한 트래픽은 Inspected RT를 조회한다. Backend CIDR의 정적 Route가 Account B Backend Attachment를 선택한다.
6. Backend VPC의 TGW Attachment와 local Route를 거쳐 Backend에 도착한다.

응답 경로:

1. Backend Subnet RT가 GCP CIDR을 TGW로 보낸다.
2. Backend Attachment의 Spoke RT가 GCP CIDR을 Inspection Attachment로 보낸다.
3. appliance mode의 flow 일관성과 양방향 Route를 통해 요청과 같은 Inspection AZ의 Primary Endpoint를 거친다.
4. Firewall Subnet RT에서 GCP CIDR을 TGW로 보내고 Inspection Attachment의 Inspected RT로 재진입한다.
5. Inspected RT에서 실제 학습한 GCP Prefix가 VPN Attachment를 선택한다. AWS VPN과 GCP Cloud Router의 경로를 통해 클라이언트로 돌아간다.

ALB→Backend와 Account A→Account B도 출발/응답 VPC의 구체적인 원격 CIDR Route→TGW Spoke→Inspection→TGW Inspected→목적지 Attachment 순서다. 각 VPC의 반환 Route가 없으면 중앙 설정만으로 왕복 통신을 완성할 수 없다. AZ/터널 장애 시 기존 연결 보존이나 무손실 전환은 별도 검증 대상이다.

## GCP HA VPN과 온프레미스

GCP HA VPN은 interface 0과 1의 공인 IP, 같은 Cloud Router ASN을 중앙에 전달한다. 중앙은 Customer Gateway 2개와 각각 2터널을 갖는 AWS VPN 2개를 생성한다. GCP에는 AWS outside IP 4개를 `FOUR_IPS_REDUNDANCY` 외부 Gateway로 등록하고 interface 0에 AWS 첫 연결의 두 터널, interface 1에 둘째 연결의 두 터널을 구성한다.

`vpn_handoff`에서 실제 AWS/원격 inside 주소, AWS ASN과 협상 조건을 받는다. IKEv2, AES256, SHA2-256, DH/PFS group 14 한 조합을 사용한다. GCP 측의 BGP 광고는 계약한 원격 CIDR만 포함하고 AWS 측에서 받은 업무 CIDR을 검증한다. AWS VPN traffic selector의 기본 전체 범위는 터널 협상 설정이며 인터넷/TGW 기본 Route 생성이나 전체 허용을 뜻하지 않는다.

원격 담당자의 설정 순서는 다음과 같다.

1. 기존 GCP VPC에서 HA VPN Gateway와 Cloud Router를 준비하고 두 interface의 실제 공인 IP와 Router ASN을 전달한다.
2. 중앙 AWS 연결이 준비되면 비밀값 없는 `vpn_handoff`와 별도 보안 경로의 PSK를 받는다.
3. AWS의 두 VPN 연결에서 받은 outside IP 4개를 외부 VPN Gateway의 `FOUR_IPS_REDUNDANCY`로 등록한다.
4. GCP interface 0에는 첫 AWS VPN의 터널 1/2, interface 1에는 둘째 AWS VPN의 터널 1/2를 구성한다. 각 터널에 대응하는 PSK와 동일한 IKE 변환 세트를 사용한다.
5. 네 터널마다 Cloud Router interface/BGP peer를 구성한다. `peer_inside_ip`는 GCP interface 주소, `aws_inside_ip`는 BGP peer 주소이며 peer ASN은 `aws_bgp_asn`이다.
6. GCP에서 계약 CIDR만 광고하고 AWS 업무 CIDR의 수신/유효 Route를 확인한다. 각 터널과 interface의 장애 시험은 별도로 기록한다.

온프레미스 장비도 outside IP, inside /30의 양쪽 주소, local/remote ASN, 두 터널의 PSK 및 IKE 변환을 대응시킨다. 장비/경계 방화벽의 IKE/NAT-T UDP 500/4500 접근, 실제 MTU/MSS와 반환 경로를 확인한다. 장비별 명령은 해당 벤더의 AWS Site-to-Site VPN 설정 지침을 따른다.

온프레미스도 같은 AWS 측 전달 정보를 사용하되 실제 방화벽/VPN 장비의 Gateway 수, 회선 수, 장애 도메인을 확인한다. AWS 한 연결의 두 터널만으로 원격 장비와 회선의 전체 HA가 완성되지 않는다.

```bash
terraform output -json vpn_handoff > vpn-handoff.json
terraform output -json cidr_service_map > cidr-service-map.json
```

두 출력은 전달용이며 커밋 대상에서 제외했다. PSK는 AWS가 생성하며 현재 `Standard` 저장 모드여서 Terraform state와 저장된 Plan/Provider 상태에 들어갈 수 있다. 일반 출력, JSON 전달본, 터미널 로그로 PSK나 native VPN XML을 내보내지 않는다. 원격 담당자는 승인된 AWS 콘솔과 별도 보안 전달 경로로 PSK를 받는다. 권한 관리, state 접근 제어와 암호화는 필수다.

## 로그를 중앙에서 확인하기

Firewall 소유 계정에서 FLOW/ALERT destination을 설정한다. 소비자가 보조 Endpoint를 만들고 알려진 5-tuple의 허용/차단 트래픽을 발생시키면 중앙 `log_groups` 출력의 그룹에서 시간, IP, 포트, SID와 처리 결과를 확인한다. 보조 Endpoint가 만들어지기 전에는 이 검증을 완료로 기록하지 않는다.

원시 로그에 보장되지 않는 소비자 Account ID, CSP, service_id를 기본 필드처럼 사용하지 않는다. `cidr_service_map`의 명시적 CIDR 메타데이터로 나중에 보강한다. AZ 이름을 물리적 AZ ID로 바꿀 때도 계정의 AZ 매핑을 사용한다. 중복 CIDR은 이 중앙 계약에서 거부하므로 서비스 분류가 모호해지는 것을 예방한다.
