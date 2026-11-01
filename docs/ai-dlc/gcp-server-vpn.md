# GCP 사설 서버와 HA VPN AI-DLC

## 단계와 승인 상태

- 기준일: 2026-11-01 (Asia/Seoul).
- Ideation: 사용자 `이어서 gcp도 하려는데 필요한거 말해줘`와 `설정 다 했어`를 AWS Client에 대응하는 GCP Server 준비 착수로 기록한다.
- Inception 및 아래 U1~U4 Design / Implementation Plan: 앞서 안내한 서울 리전의 신규 사설 VPC/서버와 HA VPN 구성을 기준으로, 사용자 `현재 무료 크레딧 계정이야. 여기에 한해서 구현해줘` 지시를 현재 프로젝트 안의 구현 승인으로 기록한다.
- 제약: 프로젝트 `project-5bb650b3-9c21-4c8b-a16` 안에서만 작업한다. 유료 계정 전환, 결제 계정 재연결, 다른 프로젝트 생성은 범위 밖이다. 서버에 공인 주소를 할당하지 않는다.
- 현재 실제 상태: CLI/ADC의 프로젝트 조회 성공. 연결된 Billing account는 있으나 `billingEnabled=false`, 해당 계정 `open=false`. 접근 가능한 결제 계정 목록에도 이 닫힌 계정 하나만 보였다. Compute API는 DISABLED다. 무료 크레딧 잔액/적용 가능 여부, 기존 Compute 리소스 목록은 미확인이다.
- Construction: U1~U4 코드/문서 구현과 로컬 Test/Review 완료. Google mock Plan 25개, 실제 프로젝트 입력의 격리 mock Plan 1개, 로컬 앱/전달 도구 테스트 7개 통과. fmt/격리 init/validate와 비밀키 출력 부재/기반 리소스 목록 검증을 실행했다. 실제 API 활성화, 실계정 Plan/Apply, AWS VPN 추가 및 실제 통신 시험은 미실행이다.
- 실제 입력: 공개 이미지 `ubuntu-2404-noble-amd64-v20261024`(READY, X86_64, 10 GiB)를 조회해 고정했다. ignored `terraform.tfvars`/`backend.hcl` 작성 및 0600 확인. GCS bucket은 아직 생성하지 않았으며 backend 초기화/기존 state 이전은 실행하지 않았다.
- Git: 이 작업의 커밋/푸시는 미실행이다.

## Ideation

- 문제: 배포된 AWS 사설 Client에서 중앙 ANF를 거쳐 요청할 GCP 사설 Server가 없다.
- 사용자: 델타 멀티클라우드 네트워크와 관제 검증 담당자.
- 성공 기준: 서버의 TCP 8080/8081은 모두 정상 리슨하고 GCP 방화벽도 두 포트를 수용한다. AWS ANF에서 8080만 허용해 실제 응답/로그와 8081 차단 SID를 비교한다.
- Scope: 별도 GCP Terraform root와 state 준비, 신규 custom VPC와 /24 Subnet, 사설 VM과 시험 앱, IAP 관리 경로, HA VPN/Cloud Router와 AWS 전달 계약, 실행/관측/해체 문서.
- Non-goals: 결제 변경, 다른 프로젝트, 공개 서비스, NAT/인터넷 출구, 단일 VM의 HA 보장, 자동 Destroy, 무료 크레딧 잔액을 API로 확정하거나 비용을 하드 캡한다고 주장하는 것.

## Inception

### 확인값과 구성

| 항목 | 값 | 확인 상태 |
| --- | --- | --- |
| Project ID / Number | `project-5bb650b3-9c21-4c8b-a16` / `120643807854` | ADC 조회 확인 |
| 프로젝트 이름 | `softbank-delta-workload` | ACTIVE |
| Region / Zone | `asia-northeast3` / `asia-northeast3-a` | 서울 제안값, Compute API 활성 후 가용성/쿼터 확인 |
| 주소 예약 범위 | `10.30.0.0/16` | AWS 10.10/16, 10.250/16과 비중복. GCP 전체 기존 대역은 조회 불가 |
| 실제 Subnet / 서버 IP | `10.30.1.0/24` / `10.30.1.10` | 제안값. GCP VPC 자체에는 VPC CIDR이 없음 |
| 서버 | `e2-micro` 1대, Ubuntu 24.04 LTS standard AMD64 | 공개 이미지 `ubuntu-2404-noble-amd64-v20261024` READY/X86_64/10 GiB 조회 및 고정. 실제 VM/쿼터는 미확인 |
| 관리 | IAP TCP forwarding + OS Login | 새 VM에 TCP 22, source `35.235.240.0/20`만 허용 |
| 서비스 | Python 표준 라이브러리 HTTP 앱, systemd, TCP 8080/8081 | 외부 패키지 다운로드 없음 |
| HA VPN / Router | Gateway 1개(인터페이스 2개), Router ASN 65001 | AWS ASN은 실제 handoff에서 사용 |
| AWS 대응 | CGW 2개, VPN 연결 2개, 터널 4개 | 기존 중앙 root 입력 계약 재사용 |
| GCS state | `sbh-delta-gcp-tf-120643807854`, prefix `delta/lab/gcp-server` | 별도 bootstrap 대상. 아직 생성하지 않음 |

### Functional Requirements

1. `terraform/gcp-server` root를 분리하고 Project ID/Number를 읽기 전용 조회로 검증한다. Terraform 1.16.4와 공식 Google Provider 8.9.0을 고정하고 lock 파일을 남긴다.
2. 신규 custom VPC만 관리한다. 자동 Subnet/IPv6를 끄고 신규 VPC의 기본 인터넷 Route를 제거한다. 기존/default VPC는 변경하지 않는다.
3. 사설 Subnet과 고정 내부 IP, 공인 주소 및 서비스 계정 없는 소형 VM을 생성한다. OS Login, 프로젝트 SSH 키 차단, Shielded VM을 사용한다.
4. 서버에 두 시험 포트를 실제 구성한다. AWS Client `10.10.1.143/32`만 두 포트로 접근한다. GCP ingress와 앱이 두 포트에 응답해야 AWS ANF의 차단을 구분할 수 있다.
5. Gateway/Router 기반은 빈 터널 맵으로 먼저 만들 수 있다. Gateway의 0/1 인터페이스 주소를 입력으로 AWS VPN 2개를 생성한 후, 비밀값 없는 handoff와 별도 PSK 입력으로 4터널을 구성한다.
6. Cloud Router는 서버 /24만 광고한다. AWS Client /16을 VPN으로 학습하며 AWS VPN은 Spoke에 연결하고 Inspected에만 전파한다. AWS의 원격 Route와 ANF 허용은 기존 root에서 명시적으로 등록한다.
7. PSK는 일반 출력/전달 JSON/로그/커밋에서 제외한다. Provider의 write-only PSK 필드와 Terraform ephemeral 입력을 사용한다. 원본 AWS Standard VPN state에는 PSK가 저장된다는 사실은 유지한다.
8. GCS backend는 버전 관리, Uniform bucket-level access, Public access prevention을 설정하고 기존 state를 자동 이전하지 않는다. bucket은 root 밖에서 준비한다.

### Non-Functional Requirements

- 현재 프로젝트와 기존 AWS 두 계정 이외에는 리소스를 생성하지 않는다. 결제 계정과 계정 유형을 변경하지 않는다.
- 크레딧 전용 무료 체험인지, 유료 계정에 크레딧이 붙은 상태인지는 Billing 기본 메타데이터만으로 확정할 수 없다. 서울 VM, VPN, 데이터 전송, state 저장은 과금 항목이며 크레딧 잔액을 자동 제한하는 구성은 아니다.
- 실제 배포 전 Billing 활성 상태와 Compute API, 기존 CIDR, 이미지, 쿼터를 확인한다. 현재 닫힌 Billing 상태에서는 유료 리소스를 생성하지 않는다.
- 일반 tfvars/backend/state/Plan과 PSK는 Git에서 제외한다. 민감 파일 권한은 0600으로 제한한다.
- mock Plan, 로컬 앱 실행, 실계정 Plan, Apply 및 실제 VPN/통신 결과를 구분해서 기록한다.

### Architecture와 실행 순서

`AWS Client → Workload TGW Attachment → Spoke RT → Inspection ANF → Inspected RT → AWS VPN → GCP HA VPN → Server 10.30.1.10`

응답은 Cloud Router가 학습한 AWS 경로와 동일한 중앙 검사 단계를 사용한다. 관리 IAP/SSH는 서비스 ANF 검사 증거가 아니다. GCP는 IAP와 metadata용 플랫폼 경로를 제공하므로 새 VPC의 사용자 가시 인터넷 기본 Route를 추가하지 않는다.

1. Billing 활성화는 사용자가 현재 무료 크레딧 계정에서 해결한다. 계정 전환/재연결을 자동으로 실행하지 않는다.
2. 승인된 프로젝트에 필요한 API를 활성화하고 기존 네트워크/쿼터 및 이미지 이름을 조회한다. 그 후 실제 tfvars와 GCS backend를 준비한다.
3. 빈 `vpn_tunnels`로 GCP 기반을 Plan하고 리소스/보안 속성을 검토한 뒤 적용한다. Gateway 2개 주소를 중앙 `vpn_peers`에 등록한다.
4. AWS 중앙의 VPN 2개/4터널을 Plan/적용한다. 두 Peer는 하나의 `vpn_networks.gcp`와 서버 /24를 참조한다. Inside CIDR 4개는 기존 TGW VPN과 비중복인지 확인한다.
5. AWS state의 민감 PSK를 메모리에서 읽어 GCP ephemeral 입력 파일로 전달한다. 출력은 터널 개수/파일 경로만 표시한다. GCP 4터널/BGP를 Plan/적용한다.
6. Workload `remote_cidrs=["10.30.1.0/24"]`, 중앙 8080 허용 목록 및 CIDR 서비스 맵을 등록한다. GCP의 TCP 8081 허용은 유지해 ANF 차단 대조군으로 사용한다.
7. 4터널/BGP, 양방향 경로, 앱 리스너, 8080 응답과 FLOW, 8081 실패와 실제 blocked action/SID 9000000을 확인한다. 준비되지 않은 항목은 미검증으로 둔다.

## Construction: Unit별 Design / Implementation Plan / Approval

| Unit | Design | Implementation Plan | Approval | 상태 |
| --- | --- | --- | --- | --- |
| U1. 실행 기반 | 현재 Project/Number만 허용, 별도 GCS state, 신규 사설 VPC | 버전/Provider/검증/API/네트워크, 예시 설정, bucket bootstrap 안내 | 2026-11-01 현재 프로젝트 구현 지시 | 구현/격리 검증/Review 완료. 실제 bucket/API/배포 미실행 |
| U2. 사설 서버 | 소형 VM 1대, IAP/OS Login, 두 포트 앱 | 이미지 입력 검증, 내부 주소/VM/방화벽, systemd 시작 스크립트, 로컬 앱 테스트 | 같은 지시 범위 | 구현/mock/로컬 두 리스너 응답/Review 완료. GCP 기동/IAP 미검증 |
| U3. HA VPN 계약 | 2 Gateway 인터페이스와 AWS 4터널의 명시적 매핑 | Gateway/Router/External Gateway/Tunnel/BGP, ephemeral PSK, 안전한 AWS 전달 도구 | 같은 지시 범위 | 구현/mock/전달 보호/회전 버전 테스트/Review 완료. 실제 VPN 미생성 |
| U4. 검증/운영 | mock Plan과 실제 상태를 분리 | 잘못된 프로젝트/CIDR/IP/ASN/터널 매핑 거부, 보안 속성 검증, 통합/관측/비용/해체 Runbook | 같은 지시 범위 | 코드/전체 속성 표/운영 문서/로컬 검증 완료. 실제 ANF 왕복 시험 미실행 |

## Acceptance Criteria

- fmt, 별도 디렉터리의 backend 없는 init, validate, mock Google Plan을 실제 실행한다.
- 빈 터널로 기반이 계획되고, 서버 공인 IP/IPv6/서비스 계정/NAT/공개 LB와 인터넷 기본 Route가 없음을 확인한다.
- 4터널에서 GCP 0/0/1/1 인터페이스와 AWS 외부 0/1/2/3 매핑, 서버 /24 광고 및 BGP inside IP 쌍을 검증한다.
- 잘못된 Project Number/Region/Zone, 사설 범위 밖 주소/중복 CIDR, 부분 터널/중복 인터페이스/주소/Inside CIDR, 잘못된 ASN/PSK 계약을 거부한다.
- 앱의 8080/8081을 실제 로컬에서 기동하고 HTTP 응답을 확인한다. 이 결과는 실제 GCP 배포 성공을 의미하지 않는다.
- 실제 배포 후 외부 IP 부재, IAP SSH, 4터널/BGP 및 ANF 왕복 시험을 별도로 수행한다.

## Operation

- 비용: 서울 e2-micro/디스크는 미국 일부 리전의 Always Free VM 조건과 구분한다. GCP VPN 터널 4개 및 데이터 전송, AWS VPN 연결 2개/TGW/ANF/로그, GCS state가 비용 항목이다. 무료 체험/크레딧은 해당 계정의 실제 조건을 따른다.
- 해체: Workload/중앙의 원격 Route/허용 정책 제거, VPN BGP/터널과 AWS VPN/CGW 정리, GCP Gateway/Router/VM/VPC 순서로 의존 관계를 따른다. 기존 AWS 기반과 GCS state bucket/로그는 기본 보존한다. 이번 작업에서 Destroy는 실행하지 않는다.

## 실제 확인 기록

| 날짜 | 명령 또는 확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-01 16:39~16:46 KST | gcloud CLI/ADC token을 메모리에서 사용한 Project GET, BillingInfo GET, Compute inventory GET | Project ACTIVE와 ID/Number 확인. Billing account 연결은 있으나 billingEnabled=false. Compute API DISABLED로 VPC/VM 목록은 미확인. 토큰 저장/출력 없음 |
| 2026-11-01 16:48~16:49 KST | ADC로 BillingInfo/연결 Billing account GET 및 billingAccounts list | 프로젝트 billingEnabled=false, 연결 계정 open=false, 접근 가능한 계정 목록은 같은 닫힌 계정 하나. 결제 변경 없음 |
| 2026-11-01 16:49 KST | 공식 Registry 메타데이터, 격리 `terraform init -backend=false` 및 `providers schema -json` | Google 8.9.0(2026-10-27 게시) 설치/서명 확인, write-only PSK 등 실제 스키마 확인. 클라우드 Plan/Apply 없음 |
| 2026-11-01 16:50~16:57 KST | `terraform fmt`, `validate`, Google mock `terraform test -var-file=terraform.tfvars.example -json` | 초기 괄호/Provider mock 중첩 블록 형식/문자열 version/null 목록/Plan 시점 이미지 검증 오류를 수정했다. 최종 **25 passed, 0 failed**. 실제 클라우드 리소스 변경 없음 |
| 2026-11-01 16:58~17:00 KST | `DELTA_KEEP_VALIDATION=1 terraform/gcp-server/scripts/validate.sh` | 별도 임시 디렉터리 backend 없는 init/validate, fmt, Google mock Plan **25개**와 Python 앱/전달 도구 테스트 **7개** 통과. 기반 13리소스 목록과 ephemeral 시험 PSK의 Plan JSON 부재 확인. 마지막 격리 결과 디렉터리 `delta-gcp-validation.qXW6Q9` |
| 2026-11-01 16:58~17:00 KST | `scripts/preflight.py --project-id project-5bb650b3-9c21-4c8b-a16 --project-number 120643807854 --output .validation/gcp/final-preflight.json` | 예상 종료 코드 **2**. Billing enabled=false/account open=false, Compute/IAP/OS Login API 모두 DISABLED, inventory_verified=false. 크레딧 잔액 미확인. API/Billing 변경 없음 |
| 2026-11-01 17:00 KST | ADC의 공개 Ubuntu 이미지 family GET | `ubuntu-2404-noble-amd64-v20261024`, READY, X86_64, 10 GiB 확인. 현재 프로젝트의 Compute inventory와 별개의 공개 이미지 카탈로그 조회 |
| 2026-11-01 17:00~17:01 KST | ignored 실제 tfvars/backend 작성, 별도 `actual-input-check` 디렉터리의 backend 없는 init 및 mock Plan | Provider 캐시 상대 경로 오류 수정 후 실제 입력 mock **1 passed, 0 failed**. 실제 Project Number/서버 사설 IP/빈 VPN/외부 IP 부재 확인. 실계정 Plan이 아님 |
| 2026-11-01 17:01 KST | README 전체 입력/출력 및 링크 검사, `stat`, `git check-ignore`, `git diff --check` | 입력 14개/출력 10개 문서화, 연결 문서 링크/금지 문자 검사 통과. 실제 tfvars/backend 0600 및 Git 제외, PSK 입력 패턴 Git 제외 확인 |
| 2026-11-01 17:03 KST | 같은 읽기 전용 preflight 재실행, `terraform fmt -check -recursive`, `bash -n`, Python compile 및 문서 검사 | preflight 종료 코드 2로 동일한 Billing 비활성/세 API DISABLED/Compute inventory 미확인 재확인. 코드/스크립트 문법과 전체 입력/출력/복합 속성/문서 링크 검사 통과. 클라우드 변경과 커밋/푸시 없음 |

## Review와 남은 실제 작업

- 구현한 새 GCP root의 리소스는 Project/Number contract와 Provider에 묶인다. 기존 AWS root의 코드/실제 입력/state는 이번 작업에서 변경하지 않았다.
- 신규 VPC 기본 인터넷 Route 제거, VM의 access_config/IPv6/service account 부재, IAP source 제한, 두 대조군 포트만 Client `/32`에서 수용하는 계획을 mock으로 확인했다. 실제 배포 후 같은 항목을 다시 확인해야 한다.
- PSK 입력은 sensitive/ephemeral이며 write-only 필드에만 전달한다. 원문 PSK가 일반 전달 JSON/Plan 출력에 포함되지 않는지 시험했고 로컬 파일 0600/덮어쓰기 및 symlink 거부/계정과 stale handoff 거부/회전 version 증가를 확인했다. AWS 원본 state의 PSK 보관 특성은 유지한다.
- 현재 닫힌 Billing 때문에 실제 API 활성화, GCS bucket, GCP 기반/VPN, AWS VPN/라우팅/허용 정책 추가, 실계정 Plan/Apply, IAP SSH와 ANF 실제 트래픽은 남아 있다. 사용자의 무료 크레딧 계정 활성 상태 확인을 기다린다.

## Git 게시 기록

2026-11-01 사용자의 전체 변경 분할 커밋/푸시 지시에 따라 GCP 코드와 검증을 나눴다. 배포 상태와 게시 상태는 구분하며 실제 tfvars/backend와 PSK 파일, state/Plan, 검증 캐시는 제외한다.

| 구분 | 커밋 | 내용 |
| --- | --- | --- |
| 구현 | `7c31c885` | 사설 GCP 서버/HA VPN root, write-only PSK 전달, 읽기 전용 preflight와 실행 문서 |
| 검증 | `fd9abcd1` | Google mock Plan, 로컬 앱/전달 보호 테스트와 격리 검증 스크립트 |

| 날짜 | 명령/확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-01 19:24~19:25 KST | 후보 경로/자격 증명 검사, `terraform fmt -check -recursive`, `git diff --check`, 명시적 경로의 `git add`/`git commit` | 민감 입력/캐시 후보와 자격 증명 패턴 검출 없음. fmt/공백 검사 통과. 위 2개 커밋 완료, 문서 커밋/푸시 대기. 실제 GCP 조회/Plan/Apply와 기존 테스트는 이번 게시 작업에서 재실행하지 않음 |

## 근거

- [AWS와 GCP HA VPN 연결](https://docs.cloud.google.com/network-connectivity/docs/vpn/how-to/connect-ha-vpn-aws-peer-gateway)
- [Billing 상태 해석](https://docs.cloud.google.com/billing/docs/how-to/verify-billing-enabled)
- [Free Trial와 크레딧 조건](https://docs.cloud.google.com/free/docs/free-cloud-features)
- [IAP와 플랫폼 경로](https://docs.cloud.google.com/vpc/docs/routes)
- [IAP TCP forwarding](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding)
- [공식 Google Provider](https://github.com/hashicorp/terraform-provider-google)
