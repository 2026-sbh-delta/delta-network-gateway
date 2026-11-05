# GCP 중앙 탐지와 멀티클라우드 연결

승인된 프로젝트 `term2-delta`에 중앙 탐지/연결 VPC와 업무 VPC를 분리한다. 중앙 VPC에는 HA VPN과 Cloud Router를 두고 NCC로 업무 VPC를 연결한다. Cloud NGFW Enterprise endpoint/profile은 프로젝트 범위 관리형 리소스다. endpoint를 중앙 VPC 내부 장비로 배치하지 않으며, enabled/bypass에서는 두 VPC의 association이 같은 Zone의 endpoint를 공유한다. 업무 트래픽은 해당 VPC에서 가로채 검사한다.

인터넷은 외부 패스스루 NLB를 통해 사설 시험 VM으로 들어온다. AWS TGW나 ANF를 통하지 않는다. `enabled`에서 TCP8080은 검사하고, `bypass`/`disabled`에서는 일반 허용한다. TCP8081은 세 모드 모두 인터넷 출발지에서 정책으로 거부한다. 두 포트 모두 LB가 전달하고 VM은 두 리스너에서 응답한다. 초기 위협 심각도 정책은 `ALERT`다. L4 거부와 IDS 탐지 증거는 구분한다. TLS 복호화는 구성하지 않는다.

AWS ANF는 사용자 지시대로 disabled를 유지한다. AWS 측은 [중앙 모듈](../network-security/README.md)의 직접 BGP 전파를 사용한다. 이 root는 AWS 리소스를 소유하지 않는다.

AWS 연결 대상은 공통 `10.10.0.0/16` VPC 하나다. `aws_cidrs`는 NCC export 범위와 private ingress/egress에 함께 적용한다. 과거 Server 대역과 두 EC2 시험의 정리 및 실제 경로 철회는 [통합 기록](../../docs/ai-dlc/aws-workload-consolidation.md)을 따른다.

현재 사용자 후속 요청으로 `inspection_mode=disabled`다. Endpoint1개/association2개를 제거했고 네 BGP/두 방향 HTTP와 기본 규칙 로그의 중앙 도착, 실제 No changes를 확인했다. 아래 초기 검사 검증은 중단 전의 기록이다.

2026-11-04 실제 배포를 완료했다. 프로젝트 `366844544284`, Billing 활성과 필요한 권한/API를 확인했고 기존 업무 `10.30.1.0/24`를 보존했다. 새 중앙 `10.31.0.0/24`, 업무 `10.32.1.0/24`와 공유 endpoint를 생성했다. 인터넷 허용/거부 및 ALERT 위협 로그, AWS 양방향 사설 HTTP와 중앙 원본 로그 일치를 확인했다. 네 단일 BGP 경로 철회/복구와 저장 실패 ack0/재수집도 검증했고 총5개 root의 최종 Plan은 No changes다. [승인/실제 결과](../../docs/ai-dlc/gcp-central-network.md), [실행 Runbook](RUNBOOK.md)을 따른다.

## 로그 수집 소유권

방화벽과 VPN 등의 네트워크 로그는 GCP Cloud Logging에 저장한다. AWS 전달은 별도 [플랫폼 중앙 로그 root](../../platform/deploy/logging/README.md)가 관리하며 플랫폼 계정 `616723161368`의 `/delta-platform/central/gcp`에서 확인한다. 이 네트워크 root는 전용 Sink, Pub/Sub 구독이나 AWS 수집 역할 계약을 정의하지 않는다. 공통 Pub/Sub 서비스 에이전트와 프로젝트 API는 기존 Terraform 주소로 보존한다.

이전 네트워크 계정 `482840608926`의 전용 수집 경로 정리와 보존 로그, 실제 적용 상태는 [정리 기록](../../docs/ai-dlc/gcp-central-network.md#2026-11-05-gcp-로그-전달-경로-정리-검토)을 따른다. 2026-11-04의 수집 및 장애 검증은 당시 별도 수집기의 과거 기록이다.

## 상태 파일 관리

2026-11-05 GCS에서 중앙 S3로 state 이전을 완료했다. 이 root의 backend는 AWS 중앙 네트워크와 같은 S3 버킷을 사용하고 GCP 전용 key로 state를 분리한다. 리소스 관리는 기존 Google Provider가 담당한다. 이전 후 실제 Plan은 No changes/종료0이다.

| 항목 | 값 |
| --- | --- |
| AWS 계정 / 리전 | `482840608926` / `ap-northeast-2` |
| 버킷 | `sbh-delta-central-tf` |
| default workspace의 key | `delta/lab/network-security-gcp/terraform.tfstate` |
| 암호화 / 잠금 | `encrypt=true` / `use_lockfile=true` |
| 인증 | S3 접근용 AWS 중앙 계정 인증과 GCP 리소스용 Google ADC |

실제 `backend.hcl`은 Git에서 제외한다. 예시를 복사할 때 중앙 버킷과 계정 ID를 입력하고 파일 권한을 0600으로 제한한다. GCS에서의 기존 state 이전과 다른 작업 디렉터리의 연결은 [Runbook](RUNBOOK.md#gcs에서-s3로-이전)을 따른다. 실제 이전과 Plan 결과는 [AI-DLC](../../docs/ai-dlc/gcp-central-network.md#2026-11-05-gcp-state의-중앙-s3-이전)에 기록한다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `project_id` | `string` | `term2-delta` | 승인된 프로젝트만 허용 |
| `project_number` | `string` | 필수 | API의 실제 프로젝트 번호와 대조 |
| `name` | `string` | `delta-lab` | 리소스 이름 접두사 |
| `inspection_mode` | `string` | `enabled` | enabled 검사, bypass endpoint 유지/일반 허용, disabled endpoint 제거/라우팅 유지 |
| `region` | `string` | `asia-northeast3` | GCP 리전 |
| `zone` | `string` | `asia-northeast3-a` | VM과 공유 검사 endpoint의 동일 Zone |
| `subnets` | `object` | 필수 | 중앙/업무 VPC의 비중복 IPv4 Subnet |
| `aws_cidrs` | `set(string)` | 필수 | NCC에 가져올 AWS 사설 Prefix와 검사 대상 |
| `internet_source_cidrs` | `set(string)` | 필수 | 인터넷 시험을 허용할 출발지. 무제한 기본 경로 금지 |
| `router_asn` | `number` | `65001` | Cloud Router의 private ASN |
| `server_image` | `string` | 필수 | 조회해 고정한 Ubuntu 24.04 이미지 경로 |
| `aws_vpn_tunnels` | `map(object)` | `{}` | Gateway 생성 후 전달받을 전체 4터널 계약 |
| `vpn_shared_secrets` | `map(string)` | `{}` | sensitive/ephemeral PSK. write-only 필드에만 사용 |
| `deletion_protection` | `bool` | `true` | 시험 VM의 의도하지 않은 삭제 방지 |

### `subnets` 내부 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `central` | `string` | 필수 | 중앙 탐지/연결 VPC Subnet CIDR |
| `workload` | `string` | 필수 | 업무 VPC Subnet CIDR. 호스트 10을 시험 VM에 사용 |

GCP VPC 자체에는 AWS의 VPC CIDR과 같은 단일 범위를 설정하지 않는다. 중앙/업무 Subnet과 `aws_cidrs`는 서로 겹치지 않아야 한다. 각 VM은 공인 IP 없이 생성한다. 업무 VM의 NLB 응답에 필요한 태그 제한 기본 Route는 NCC/BGP로 광고하지 않는다. NAT나 일반 인터넷 출구는 만들지 않는다.

### `aws_vpn_tunnels` 내부 속성

맵 키는 `if0t1`, `if0t2`, `if1t1`, `if1t2`와 같이 터널 수명 동안 고정한다. 빈 맵 또는 전체 네 터널만 허용한다.

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `gcp_interface` | `number` | 필수 | GCP Gateway 인터페이스 0 또는 1 |
| `aws_interface` | `number` | 필수 | 외부 Gateway 인터페이스 0, 1, 2, 3 |
| `outside_ip` | `string` | 필수 | AWS 터널 공인 주소 |
| `inside_cidr` | `string` | 필수 | 고유한 link-local /30 |
| `gcp_inside_ip` | `string` | 필수 | /30의 host 2, GCP Router 주소 |
| `aws_inside_ip` | `string` | 필수 | /30의 host 1, AWS BGP 주소 |
| `aws_asn` | `number` | 필수 | AWS TGW ASN, GCP ASN과 달라야 함 |
| `secret_version` | `number` | 필수 | PSK 변경 시 증가시키는 write-only 버전 |

`vpn_shared_secrets`는 동일한 맵 키를 사용한다. PSK는 일반 출력에 포함하지 않으며 GCP의 `shared_secret_wo`에만 전달한다. AWS Standard VPN 원본 state에는 PSK가 저장된다. Terraform write-only 입력은 AWS state의 이 특성을 바꾸지 않는다.

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `inspection_mode` | 현재 GCP 검사/Endpoint 비용 모드 |
| `project` | 검증한 프로젝트 ID/Number |
| `networks` | 분리한 중앙/업무 VPC ID |
| `ncc_hub` | NCC Hub ID |
| `firewall_endpoint` | 프로젝트 범위 공유 검사 endpoint ID. disabled에서는 null이며 root CLI 출력에서 미표시될 수 있음 |
| `gateway_handoff` | AWS에 전달할 Gateway 주소, ASN, 양쪽 Prefix와 프로젝트 식별자. 비밀값 없음 |
| `server_ip` | 시험 서버의 내부 주소 |
| `demo_address` | 외부 패스스루 NLB의 공개 주소 |

## 로컬 검증

Terraform 1.16.4, Google/Google Beta 8.10.0을 고정한다. 두 Provider는 최초 다운로드에서 HashiCorp 서명과 lock 파일을 확인했다. Beta는 Pub/Sub 서비스 에이전트 생성에 사용한다. 프로젝트 범위 endpoint/profile과 write-only PSK 필드는 실제 설치한 Provider 스키마로 확인했다.

```bash
terraform -chdir=terraform/network-security-gcp init -backend=false -input=false
bash terraform/network-security-gcp/scripts/validate.sh
```

검증 스크립트는 별도 임시 디렉터리에서 backend 없는 init/validate와 mock `command=plan`만 실행한다. 기존 state를 초기화/이전하거나 Apply하지 않는다. 로컬 앱과 handoff 도구 테스트도 실행한다. 실제 배포와 로그 증거는 [Runbook](RUNBOOK.md)의 별도 절차다.

## 비용과 소유권

2026-11-04 공식 표의 endpoint 시간 요금은 1개당 USD 1.75/h, 검사량은 USD 0.0193/GiB다. endpoint 1개를 24시간 유지하면 시간 요금만 USD 42다. VM 중지로 endpoint 요금이 끝나지 않는다. VPN 4터널, NCC, NLB/VM/IP, 로그/전송 비용은 별도다. [공식 가격](https://cloud.google.com/firewall/pricing)

Jumbo frame은 기본 비활성 상태와 VPC MTU1460을 사용한다. API가 기본 false를 빈 블록으로 반환하므로 endpoint_settings에 false를 명시하지 않는다. 보안 프로필 URL도 API가 반환하는 canonical 형태로 지정해 반복 갱신을 방지한다.

API를 활성화하는 리소스는 Terraform에서 제거해도 API를 비활성화하지 않는다. 플랫폼이 사용하는 공유 API와 Pub/Sub 서비스 에이전트를 이 root에서 계속 보존한다. 플랫폼 전용 Topic과 구독의 IAM은 플랫폼 root가 관리한다. S3 backend의 소유 계정, 암호화, 공개 접근 차단과 잠금을 확인한다. 2026-11-05 조회에서 기존 중앙 버킷의 버전 관리는 미설정이었으며 이 작업에서는 버킷 설정을 유지한다. 이전 전 state 백업과 GCS 원본은 복구용으로 보존한다. backend 이전과 유료 리소스 배포는 별도 작업이며 실제 배포는 저장 Plan을 검토한 뒤 수행한다.

## 검사 중단과 재개

| 모드 | Endpoint/association | 검사 대상 규칙 | 라우팅과 기본 deny |
| --- | --- | --- | --- |
| `enabled` | 1개/2개 | apply_security_profile_group | 유지 |
| `bypass` | 유지, 시간 요금 발생 | 일반 allow | 유지 |
| `disabled` | 없음 | 일반 allow | 유지 |

중단은 enabled -> bypass에서 통신을 확인한 뒤 disabled로 전환한다. 재개는 disabled -> bypass에서 endpoint/association 준비를 기다린 뒤 enabled로 전환한다. 기존 singleton endpoint 주소는 moved 블록으로 [0]에 옮겨 bypass 전환 시 교체를 방지한다. 실제 `inspection-mode.auto.tfvars`는 ignored 파일이며 각 단계의 저장 Plan과 실제 통신을 검토한다. 프로필/기본 규칙, NCC/VPN/VM/NLB와 별도 플랫폼 로그 수집 경로는 보존한다. 검사 중단 중에는 새 Threat 로그가 생성되지 않는다.

이번 실행은 GCP 중단만 승인받았다. AWS ANF와 함께 켜는 공통 자동화, AWS GCP 허용 규칙/경로 전환 보완은 후속 범위다. [실행 순서](RUNBOOK.md#검사-비용-중단과-재개)를 따른다.

Provider 8.10.0의 검사 -> 일반 허용 갱신은 빈 프로필 참조 때문에 API400이 발생했다. 이번 중단은 검토한 동일 조건의 세 규칙에 JSON null을 명시해 정상 전환한 뒤 Terraform의 refresh/저장 Plan으로 동기화했다. 다음 중단 전에는 [참조 제거 오류 절차](RUNBOOK.md#provider-8100의-프로필-참조-제거-오류)를 확인한다.
