# GCP 사설 시험 서버와 AWS HA VPN

AWS 사설 Client에서 중앙 ANF를 거쳐 접근할 GCP 서버를 구성한다. 신규 custom VPC, `10.30.1.0/24` Subnet, 내부 IP `10.30.1.10`의 `e2-micro` VM 1대, IAP 관리 경로와 HA VPN/Cloud Router를 별도 root/state에서 관리한다. GCP VPC에는 VPC 전체 CIDR이 없으며 `10.30.0.0/16`은 주소 예약 계획이다.

서버에는 공인 IPv4/IPv6, NAT, 공개 LB, 서비스 계정을 구성하지 않는다. 신규 VPC의 인터넷 기본 Route를 제거하고 서버의 새 outbound 연결도 차단한다. GCP의 stateful 방화벽은 허용된 ingress의 응답을 수용한다. IAP/metadata는 플랫폼 경로를 사용하며 일반 인터넷 출구를 추가하지 않는다.

현재 상태: **코드/로컬 검증 완료, 배포 미실행**. 2026-11-01 확인한 Project는 `project-5bb650b3-9c21-4c8b-a16` / Number `120643807854`다. 연결 Billing account는 있지만 `billingEnabled=false`, 해당 계정 `open=false`, Compute API DISABLED라 실제 CIDR/쿼터/Plan/Apply는 미검증이다. 공개 Ubuntu 이미지 `ubuntu-2404-noble-amd64-v20261024`는 READY/X86_64/10 GiB를 조회해 ignored tfvars에 고정했다. 실제 tfvars/backend는 0600이며 실제 입력 mock Plan 1개도 통과했다. GCS bucket은 미생성이다. 결제 계정 변경과 유료 업그레이드는 실행하지 않는다. 상세 승인/명령/결과는 [AI-DLC](../../docs/ai-dlc/gcp-server-vpn.md)를 따른다.

## 먼저 확인할 값

- 현재 무료 크레딧 계정에 프로젝트가 연결되고 Billing이 활성 상태인지 확인한다. 무료 크레딧 잔액/유효 기간과 적용 대상은 Billing 콘솔에서 확인한다. 이 코드는 잔액을 조회하거나 지출을 강제 제한하지 않는다.
- CLI/ADC 모두 현재 프로젝트를 사용한다. 프로젝트 Owner여도 조직 정책, 서비스별 권한과 할당량은 실제 API에서 별도 확인한다. Bucket/object 권한은 프로젝트 수준 `testIamPermissions` 결과만으로 누락을 단정하지 않는다.
- 서울 `asia-northeast3-a`의 VM/디스크/HA VPN 가용성과 쿼터, 프로젝트 전체 Subnet 및 연결 대상 CIDR 중복을 확인한다.
- AWS 중앙 `10.250.0.0/16`, Workload `10.10.0.0/16`, 실제 Client `10.10.1.143`과 서버 후보 /24를 대조한다. 기본/default GCP VPC는 이 root가 변경하지 않는다.

읽기 전용 점검은 로컬 authorized-user ADC를 사용하고 토큰을 메모리에서만 처리한다. `--output`의 상위 디렉터리는 미리 만들고 민감 파일과 함께 Git에서 제외한다. API가 꺼져 있으면 inventory를 비었다고 판정하지 않고 미확인으로 남긴다. 종료 코드 2는 배포 전제 조건 미충족이다. 크레딧 잔액은 성공 시에도 별도 확인해야 한다.

```bash
mkdir -p .validation
python3 terraform/gcp-server/scripts/preflight.py \
  --project-id project-5bb650b3-9c21-4c8b-a16 \
  --project-number 120643807854 \
  --output .validation/gcp-preflight.json
```

Billing이 활성화된 뒤 현재 프로젝트의 API를 활성화한다. 기존 Compute 리소스를 조회하고 이미지 이름을 고정한 후 실제 Plan을 준비한다. 다음은 **아직 실행하지 않은 운영 명령**이다.

```bash
gcloud services enable compute.googleapis.com iap.googleapis.com oslogin.googleapis.com \
  --project=project-5bb650b3-9c21-4c8b-a16
gcloud compute images describe-from-family ubuntu-2404-lts-amd64 \
  --project=ubuntu-os-cloud --format='value(name)'
gcloud compute machine-types describe e2-micro --zone=asia-northeast3-a \
  --project=project-5bb650b3-9c21-4c8b-a16
```

## 별도 GCS state 준비

Bucket은 이 root 밖에서 한 번 준비한다. 아래 이름이 없음을 확인한 후 생성하며, 이미 있으면 소유 Project Number가 `120643807854`인지 확인하고 다른 소유자의 bucket을 사용하지 않는다. 생성 전에 Billing/크레딧 상태와 현재 프로젝트를 확인한다.

```bash
gcloud storage buckets describe gs://sbh-delta-gcp-tf-120643807854 --format=json
# 위 조회가 명확한 404일 때만 다음 생성 명령을 실행한다. 403은 부재 증거가 아니다.
gcloud storage buckets create gs://sbh-delta-gcp-tf-120643807854 \
  --project=project-5bb650b3-9c21-4c8b-a16 --location=asia-northeast3 \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://sbh-delta-gcp-tf-120643807854 --versioning
gcloud storage buckets describe gs://sbh-delta-gcp-tf-120643807854 --format=json
gcloud storage buckets get-iam-policy gs://sbh-delta-gcp-tf-120643807854 --format=json
```

Project Number, 서울 위치, versioning, Uniform bucket-level access, Public access prevention을 실제 재조회한다. Google 관리 키로 기본 저장 암호화를 사용하고 `allUsers`/`allAuthenticatedUsers` 공개 IAM이 없어야 한다. Terraform 실행자에게 이 bucket의 object 읽기/쓰기/삭제 권한이 필요하다. GCS backend는 state locking을 지원하므로 S3의 `use_lockfile` 설정을 넣지 않는다. Bucket과 이전 state 버전은 기본 보존한다.

`backend.hcl.example`을 ignored `backend.hcl`로 복사하고 bucket/prefix를 확인한다. 기존 state가 발견되면 소유 프로젝트/lineage부터 확인하며 자동 `-migrate-state`는 실행하지 않는다.

## 코드 검증과 첫 Plan

```bash
terraform/gcp-server/scripts/validate.sh
```

이 명령은 별도 임시 디렉터리에서 backend 없이 init/validate하고 Google mock Plan 25개, 로컬 HTTP/비밀값 전달 도구 7개를 검증한다. 13개 기반 리소스(terraform_data 1개 포함) 목록과 Plan JSON의 ephemeral 시험 PSK 부재도 확인한다. 실제 tfvars/backend와 클라우드 Apply는 사용하지 않는다. 시험 포트 8080/8081이 로컬에서 사용 중이면 해당 프로세스를 종료하지 않고 테스트를 실패시킨다.

현재 checkout의 ignored `terraform.tfvars`/`backend.hcl`은 작성했다. 다른 checkout에서는 아래 값과 API에서 조회한 **실제** `server_image_name`을 입력한다. example의 이미지 이름은 mock fixture이며 배포 가능 이미지로 확인한 값이 아니다.

```hcl
project_id         = "project-5bb650b3-9c21-4c8b-a16"
project_number     = "120643807854"
address_space      = "10.30.0.0/16"
server_subnet_cidr = "10.30.1.0/24"
server_private_ip  = "10.30.1.10"
connected_cidrs    = ["10.10.0.0/16", "10.250.0.0/16"]
aws_client_cidrs   = ["10.10.1.143/32"]
server_image_name = "ubuntu-2404-noble-amd64-v20261024" # 2026-11-01 공개 API 조회 확인
```

첫 단계는 `vpn_tunnels={}`와 PSK 빈 맵으로 실행한다. 실제 입력에서 `vpn_tunnels`를 생략하면 기본 빈 맵을 사용한다. 이후 자동 입력 파일을 추가할 때 `terraform.tfvars`의 같은 변수를 중복 정의하지 않는다.

```bash
terraform -chdir=terraform/gcp-server init -backend-config=backend.hcl -input=false -lockfile=readonly
umask 077
mkdir -p terraform/gcp-server/.validation
terraform -chdir=terraform/gcp-server plan -input=false -out=.validation/foundation.tfplan
terraform -chdir=terraform/gcp-server show -no-color .validation/foundation.tfplan
# 리소스 목록/현재 프로젝트/외부 IP 부재를 검토한 저장 Plan만 적용한다.
terraform -chdir=terraform/gcp-server apply -input=false .validation/foundation.tfplan
```

예상 기반은 신규 VPC/Subnet/내부 주소, 방화벽 3개, VM 1개, HA VPN Gateway 1개, Cloud Router 1개와 API 3개, contract 1개다. 첫 Plan의 실제 목록은 반드시 다시 검토한다. API/리소스 상태 변화, 실계정 권한/쿼터는 mock이 보장하지 않는다.

## AWS 연결과 비밀키 전달

[연결 및 검증 Runbook](RUNBOOK.md)에 순서를 정리했다. Gateway의 두 공인 주소는 IPsec 전송용이며 서버 외부 IP가 아니다. `gateway_ips_by_interface`는 계산 목록의 위치가 아닌 명시적 `id`로 주소를 대응시킨다.

AWS 중앙은 `VPN 2개 / CGW 2개 / 터널 4개`를 생성한다. GCP는 External Gateway 1개(FOUR_IPS_REDUNDANCY), Tunnel/Router Interface/BGP Peer 각각 4개를 추가한다.

| GCP interface | AWS peer interface | 중앙 handoff |
| --- | --- | --- |
| 0 | 0 | gcp_if0.tunnel1 |
| 0 | 1 | gcp_if0.tunnel2 |
| 1 | 2 | gcp_if1.tunnel1 |
| 1 | 3 | gcp_if1.tunnel2 |

PSK는 AWS Standard VPN state에 저장된다. GCP Provider 8.9.0의 `shared_secret_wo`와 Terraform ephemeral 입력을 사용해 GCP의 Plan/state에 원문을 남기지 않는다. 로컬 입력 파일에는 PSK가 있으므로 0600, 암호화된 로컬 디스크, Git 제외가 필요하다. `TF_LOG`/`set -x`/raw state 출력과 state/Plan의 공유를 피한다. 일반 `vpn_handoff`에는 PSK가 없다.

전달 스크립트는 AWS root의 읽기 전용 output/state 명령을 메모리에서 처리하고 계정/리전/VPN ID 및 Inside/Outside 정보를 대조한다. 기존 입력 파일과 symlink는 덮어쓰지 않는다. 중앙 state에 새 VPN이 생성된 뒤 다음 명령을 실행한다.

```bash
AWS_PROFILE=sbh-delta-security python3 terraform/gcp-server/scripts/import_aws_vpn.py \
  --aws-root terraform/network-security --central-account-id 482840608926 \
  --output-dir terraform/gcp-server --secret-version 1
```

`vpn.auto.tfvars.json`과 `vpn-secrets.auto.tfvars.json`이 생성되며 둘 다 ignored/0600이다. 저장 Plan에는 ephemeral PSK가 포함되지 않으므로 Plan과 Apply 모두 이 파일이 필요하다. 회전 시 기존 파일을 안전하게 정리하고 증가한 `--secret-version`으로 다시 전달한다. 원문만 바꾸고 version을 유지하면 write-only 변경을 Terraform이 감지할 수 없다. 전달 후에도 이후 Plan/Apply가 필요할 때까지 파일을 보안 저장소에서 관리하고 마지막 사용 후 로컬 사본을 제거한다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| project_id | string | 필수 | 생성 대상인 기존 GCP Project ID |
| project_number | string | 필수 | 실제 조회와 대조할 Project Number |
| name | string | `delta-lab` | 리소스/VM target tag 접두사 |
| region | string | `asia-northeast3` | Subnet/Router/VPN 리전 |
| zone | string | `asia-northeast3-a` | 단일 서버 Zone. region과 일치해야 함 |
| address_space | string | 필수 | 비중복 RFC1918 `/16` 주소 계획 |
| server_subnet_cidr | string | 필수 | address_space 안의 실제 `/24` Subnet |
| server_private_ip | string | 필수 | Subnet의 GCP 예약 주소 4개를 제외한 내부 IP |
| server_image_name | string | 필수 | 조회로 확인한 Ubuntu 24.04 standard AMD64의 고정 이미지 이름 |
| connected_cidrs | set(string) | 필수 | 알려진 AWS/타 연결망. `/16` 예약 범위와 중복 검사 |
| aws_client_cidrs | set(string) | 필수 | 두 시험 포트의 AWS source. connected_cidrs 안에 포함 |
| router_asn | number | `65001` | Cloud Router 사설 ASN. AWS ASN과 달라야 함 |
| vpn_tunnels | map(object) | `{}` | 0개 또는 정확히 4개의 비밀값 없는 터널 계약 |
| tunnel_shared_secrets | map(string) | `{}` | vpn_tunnels와 동일한 키의 sensitive/ephemeral PSK. 출력 없음 |

`vpn_tunnels` 내부 속성:

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| gcp_interface | number | 필수 | HA Gateway interface 0 또는 1 |
| aws_interface | number | 필수 | External Gateway의 고유 interface 0~3. floor(id/2)가 GCP interface |
| aws_outside_ip | string | 필수 | 실제 AWS 터널 공인 IPv4. private/reserved/documentation 거부 |
| inside_cidr | string | 필수 | 중복 없는 비예약 `169.254.0.0/16` 안의 `/30` |
| gcp_inside_ip | string | 필수 | Inside CIDR의 두 번째 usable IP, AWS CGW host |
| aws_inside_ip | string | 필수 | Inside CIDR의 첫 번째 usable IP, AWS gateway host |
| aws_bgp_asn | number | 필수 | 실제 handoff의 공통 AWS 사설 ASN |
| secret_version | number | `1` | write-only PSK 갱신 트리거. 회전할 때 증가 |

Map 키는 소문자/숫자/하이픈을 사용하고 리소스 반복 키로 유지한다. 집합 순서로 interface를 선택하지 않는다. IPv4만 지원한다.

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| project_id | 실제 조회로 확인한 Project ID |
| project_number | 실제 조회로 확인한 Project Number |
| region | GCP 리전 |
| network_id | 새 custom VPC ID |
| server_subnet_cidr | AWS로 광고할 실제 서버 `/24` |
| server | VM name/zone/private_ip와 test_ports 8080/8081 |
| gateway_ips_by_interface | 명시적 HA Gateway ID 0/1별 IPsec 공인 주소 |
| aws_vpn_handoff | 중앙용 network(kind/cidrs)와 peers(public_ip/bgp_asn/gcp_interface). Inside CIDR은 중앙에서 중복 검사 후 지정 |
| router_name | 상태 조회에 사용할 Cloud Router 이름 |
| tunnel_names | 안정된 입력 키별 GCP Tunnel 이름. 비밀키/해시 없음 |

## 비용과 해체

서울 VM/디스크, GCP VPN 터널 4개/데이터 전송, AWS VPN 연결 2개/TGW/ANF/로그, GCS 저장/버전/요청은 비용 항목이다. 서울 e2-micro를 미국 일부 리전의 Always Free VM으로 간주하지 않는다. 무료 크레딧과 무료 체험 계정 여부는 결제 화면의 실제 조건을 확인한다. 예산 알림은 지출을 막는 하드 캡이 아니다.

해체 순서와 보호 해제는 [Runbook](RUNBOOK.md)을 따른다. 이 root의 Destroy는 기존 AWS 중앙/Client나 state bucket을 제거하지 않는다. VM 삭제 보호는 기본 활성화이며 Terraform에서 false로 바꾸는 Plan/Apply가 먼저 필요하다. 이번 구현에서 Destroy는 실행하지 않았다.

## 공식 문서

- [GCP HA VPN과 AWS 연결](https://docs.cloud.google.com/network-connectivity/docs/vpn/how-to/connect-ha-vpn-aws-peer-gateway)
- [IAP TCP forwarding](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding), [IAP/metadata 플랫폼 경로](https://docs.cloud.google.com/vpc/docs/routes)
- [지원 IKE cipher](https://docs.cloud.google.com/network-connectivity/docs/vpn/concepts/supported-ike-ciphers)
- [GCS backend와 locking](https://developer.hashicorp.com/terraform/language/backend/gcs)
- [Billing 활성 상태](https://docs.cloud.google.com/billing/docs/how-to/verify-billing-enabled), [무료 체험/크레딧](https://docs.cloud.google.com/free/docs/free-cloud-features)
