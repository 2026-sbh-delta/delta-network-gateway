# GCP 실행과 검증

## 현재 상태와 인증

2026-11-05 기존 시험 VM/NLB를 제거하고 별도 [workload-gcp](../workload-gcp/README.md)의 공개 리전 ALB, 사설 x86 FE/BE VM과 Cloud SQL PSC로 교체했다. 현재 앱 배포/초기화/복귀는 [새 배포 Runbook](../../deploy/workload-gcp/README.md)을 따른다. 기존 인터넷 시험 출발지 입력과 server_ip/demo_address 출력은 제거했다. 아래 2026-11-04의 NLB/8081/IDS 시험 결과는 과거 기록이다. 새 앱의 공개 L7 검사 재개는 별도 정책 설계가 필요하며 현재 disabled를 유지한다.

현재 GCP 모드는 disabled이며 endpoint/association 삭제와 통신/로그/No changes 검증을 완료했다. AWS ANF도 disabled를 유지한다. 실제 진행 상태는 [AI-DLC](../../docs/ai-dlc/gcp-central-network.md)를 따른다. 2026-11-04 새 ADC의 프로젝트 조회, Billing/API/권한/기존 CIDR과 실제 저장 Plan 검토를 완료했다. 첫 Apply의 DNS/연결 오류를 원격 state로 복구했고 GCP 기반과 4터널 Apply를 완료했다. 아래 로그인은 인증 준비 절차이며, 현재 인증을 다시 요구하는 단계가 아니다. 양방향 통신/규칙 거부/ALERT 탐지와 AWS 원본 로그 대조를 완료했고, 네 단일 BGP 경로 철회/복구와 실제 저장 실패77건 ack0/재수집도 검증했다. 최종 GCP/AWS 총5개 root의 실제 Plan은 No changes이며 상세 결과는 AI-DLC에 기록한다.

[공식 gcloud CLI](https://docs.cloud.google.com/sdk/docs/install)를 준비한 뒤 새 계정으로 로그인한다. 기존 다른 계정의 프로젝트나 credential 파일을 이 모듈에 복사하지 않는다. 로컬 gcloud 기본 프로젝트만 바꾸는 작업은 ADC 로그인과 다르다. ADC 로그인도 gcloud CLI의 활성 계정을 자동으로 바꾸지 않는다. 이 검증에서는 task 전용 config와 메모리의 ADC 단기 token을 사용했고 전역 CLI 계정을 변경하지 않았다. 이후 CLI 관리 전에는 `gcloud auth list`와 명시한 프로젝트를 확인한다.

```bash
gcloud auth application-default login --project=term2-delta
gcloud auth application-default set-quota-project term2-delta
python terraform/network-security-gcp/scripts/preflight.py --output .validation/gcp-construction/preflight.json
```

Python 실행 환경에는 `google-auth`와 `requests`가 필요하다. preflight의 GET과 `testIamPermissions`는 설정을 변경하지 않는다. Project Number, Billing, 권한, API, Subnet과 쿼터를 확인하고 inventory 미확인을 해결한 뒤 전체 네트워크 배포를 진행한다. API가 꺼져 inventory를 조회하지 못하면, 실제 프로젝트 식별 후 API 활성화 리소스만 별도 저장 Plan으로 검토/적용하고 preflight를 다시 실행한다. 이 준비 단계에 유료 네트워크 리소스 생성이 포함되지 않았는지 확인한다. Billing 변경과 Organization 생성은 하지 않는다.

## 실행 입력과 state 준비

1. `terraform.tfvars.example`은 mock용 합성값이다. 실제 Project Number, 중앙/FE subnet, 추가 application_subnets와 대상 서비스 계정, AWS Prefix/ASN을 확인해 ignored tfvars를 작성한다. 새 연결의 bootstrap에서만 `aws_vpn_tunnels={}`를 사용하며 현재 네 터널의 실제 계약을 빈 맵으로 덮어쓰지 않는다.
2. 기존 중앙 S3 버킷 `sbh-delta-central-tf`의 소유 계정 `482840608926`, 서울 리전, 버전 관리, 암호화, 공개 접근 차단과 접근 권한을 확인한다. GCP 전용 key는 `delta/lab/network-security-gcp/terraform.tfstate`다. 실제 `backend.hcl`에 `encrypt=true`, `use_lockfile=true`, `allowed_account_ids=["482840608926"]`를 지정하고 파일 권한을 0600으로 제한한다. 기존 GCS state가 있으면 아래 이전 절차를 먼저 수행한다.
3. Google/Google Beta Provider를 초기화하고 실제 저장 Plan의 프로젝트, 생성 목록, 검사 정책과 비용을 검토한다. 이 root는 네트워크를 관리한다. VM/ALB/SQL은 별도 workload root의 저장 Plan에서 VM/SQL 공인 IP 부재와 공개 ALB만 확인한다. 실제 Apply는 이 검토 후의 별도 실행 단계다.
4. 현재 검사 모드는 disabled다. 이번 배포에서 검사 endpoint/association을 재생성하지 않는다. 선택적 검사 재개는 새 앱 정책을 별도 검토하고 실제 Plan/비용 확인 후 진행한다.

## GCS에서 S3로 이전

2026-11-05 사용자 요청으로 이 root의 state를 기존 AWS 중앙 버킷의 별도 key로 이전했고 실제 Plan No changes/종료0을 확인했다. 원래 위치는 `gs://delta-network-gateway-tf-366844544284/delta/lab/network-security-gcp/default.tfstate`이며 복구용 원본으로 보존한다. 아래는 최초 이전 때 수행한 절차다. 이미 이전된 다른 작업 디렉터리는 이 절 뒤의 연결 안내를 따른다. [이전과 검증 기록](../../docs/ai-dlc/gcp-central-network.md#2026-11-05-gcp-state의-중앙-s3-이전)

1. 모든 실행 환경의 Plan/Apply/state 쓰기를 중단한다. 기존 저장 Plan도 재사용하지 않는다. Google ADC와 `sbh-delta-security`의 AWS 인증을 확인하고 `aws sts get-caller-identity`의 계정이 `482840608926`인지 확인한다.
2. 현재 GCS backend metadata를 보존하고 모든 workspace, 원격 잠금과 최신 GCS object generation을 확인한다. 현재 source는 default workspace 하나다. 최신 state와 기존 `backend.hcl`, 로컬 backend metadata를 ignored 경로에 0600으로 백업한다. `errored.tfstate`를 최신 원본으로 사용하지 않는다.
3. 중앙 S3의 소유 계정/리전, 암호화, 공개 접근 차단과 새 state key/잠금의 부재를 확인한다. 2026-11-05 조회에서 버전 관리는 미설정이므로 이 작업에서는 GCS 원본과 별도 백업을 보존한다. 기존 버킷 설정과 다른 root의 key는 유지한다.
4. `versions.tf`의 backend를 `s3`로 변경하고 실제 `backend.hcl`을 위 중앙 S3 설정으로 작성한다. 기존 `.terraform`의 GCS metadata는 migration이 읽을 수 있도록 유지한다. 백업 이후 GCS generation이 바뀌었다면 새 원본을 다시 확인한다.

```bash
export AWS_PROFILE=sbh-delta-security
terraform -chdir=terraform/network-security-gcp init \
  -migrate-state -backend-config=backend.hcl -lockfile=readonly
```

5. 이전 확인에는 검토한 source와 비어 있는 destination만 사용한다. 자동 실행의 `-force-copy`는 이전 질문에 자동 동의하므로 목적지 부재와 source 불변을 재확인한 승인 범위에서만 사용한다. 최초 이전에 `-reconfigure`를 사용하면 기존 state가 복사되지 않는다.
6. S3 state의 lineage, serial, 리소스 전체와 출력을 백업과 대조한다. 이번 Terraform 1.16.4 실행에서는 신규 S3 state에 새 lineage/serial1이 생성됐다. 해당 metadata를 기록하고 check 결과의 배열 순서를 정규화해 나머지 모든 내용이 동일함을 확인했다. GCS 원본의 내용/generation 보존, 양쪽 잠금 해제와 로컬 backend가 S3를 가리키는지도 확인한다. 새 실제 `terraform plan -parallelism=2 -detailed-exitcode`로 리소스 변경이 없는지 검증한다. state 이전에는 리소스 Apply가 필요하지 않다.

이전 완료 후 다른 작업 디렉터리를 연결할 때는 최신 코드와 S3용 `backend.hcl`을 준비하고, 기존 S3 state의 lineage와 key를 확인한 다음 `terraform init -reconfigure -backend-config=backend.hcl -lockfile=readonly`로 연결한다. 이 단계에서는 이미 존재하는 S3 state를 사용한다. 모든 실행 환경이 S3를 사용하게 하며 보관한 GCS state로 새 작업을 시작하지 않는다.

롤백 시에는 작업을 중지하고 S3의 최신 state를 새로 백업한다. GCS 보관본과 lineage/serial을 비교한 뒤 S3에서 GCS로 역방향 migration을 검토한다. 이전 후 변경된 S3 state를 보존해야 하므로 오래된 GCS state에 `-reconfigure`만 수행하지 않는다. 이번 작업에서 GCS 원본이나 버킷을 삭제하지 않는다.

## AWS ANF를 끈 채 VPN 연결

AWS ANF는 원복하지 않는다. 중앙 root의 실행 입력에 `inspection_mode="disabled"`를 유지한다. 실제 저장 Plan에 Firewall/endpoint 생성이 포함되면 적용하지 않고 입력과 변경 범위를 확인한다.

1. GCP Gateway 생성 후 `terraform output -json gateway_handoff`를 ignored `gateway-handoff.json`으로 받는다.
2. 기존 중앙 `vpn_networks`에 업무 GCP Subnet을 한 번 등록하고, Gateway 인터페이스 0/1의 주소로 `vpn_peers` 두 개를 준비한다. 네 Inside /30은 전체 TGW와 비중복이어야 한다.
3. AWS Plan에서 기존 VPC 목적지는 직접 attachment로 향하고, 두 VPN attachment가 Spoke RT에 BGP를 전파하는지 확인한다. VPN 목적지를 단일 attachment로 고정하는 정적 경로는 없어야 한다. 기존 VPC/서버/로그 그룹의 교체와 ANF 생성도 없어야 한다.
4. AWS VPN 적용 후 PSK는 아래 도구로 state에서 메모리로만 읽는다. 일반 출력, 일반 handoff와 Git에는 넣지 않는다.

```bash
export AWS_PROFILE=sbh-delta-security
python3 terraform/network-security-gcp/scripts/prepare_vpn_inputs.py \
  --aws-root terraform/network-security \
  --gateway-handoff terraform/network-security-gcp/gateway-handoff.json \
  --central-account-id 482840608926 \
  --output terraform/network-security-gcp/vpn.auto.tfvars.json
```

도구는 STS 계정, 중앙 state 계정, 새 GCP 프로젝트, Gateway 주소/ASN/Prefix와 최신 AWS tunnel handoff를 대조한다. 출력은 터널 개수와 경로뿐이다. 파일은 0600이며 기존 파일과 symlink를 덮어쓰지 않는다. PSK 회전 시 `--secret-version`을 증가시키고 이전 입력 파일을 활성 tfvars 경로에서 안전하게 옮긴 뒤 새 입력을 만든다. 민감 파일의 동시 자동 로드를 피한다.

GCP의 다음 Plan에서 4터널, BGP Peer 4개와 NCC hybrid spoke를 확인한다. PSK는 sensitive/ephemeral 입력과 write-only 필드를 사용한다. 적용 시에도 동일한 ephemeral 입력을 안전하게 제공한다. 저장 Plan/일반 출력에 원문 PSK가 없는지 확인한다.

## 로그 수집

원본은 GCP Cloud Logging에서 확인하고, AWS 중앙 수집은 [플랫폼 로그 안내](../../platform/deploy/logging/README.md)를 따른다. 플랫폼 계정 `616723161368`의 `/delta-platform/central/gcp`로 전달하며 플랫폼 전용 Sink는 `delta-platform-logs`다. 이 네트워크 root에서 전용 AWS 전달 입력이나 출력을 다시 만들지 않는다. 플랫폼 수집은 업무 VPN과 독립적이다.

Pub/Sub 서비스 에이전트는 프로젝트의 공유 자원이며 이 root의 기존 주소로 보존한다. 플랫폼의 전용 Topic, Subscription과 권한은 플랫폼 root가 소유한다. 이전 네트워크 계정 전용 전달의 정리 결과와 보존한 로그는 [AI-DLC 기록](../../docs/ai-dlc/gcp-central-network.md#2026-11-05-gcp-로그-전달-경로-정리-검토)을 따른다.

## 세 흐름을 실제로 대조

| 흐름 | 확인할 증거 | 혼동하면 안 되는 결과 |
| --- | --- | --- |
| Internet -> GCP | ALB HTTP80/FE3000, FE를 통한 BE8080 API와 PostgreSQL CRUD/재시작 보존, ALB HEALTHY | LB 미전달/비정상 헬스/앱 미기동은 방화벽 거부가 아님 |
| GCP IDS | 현재 disabled로 신규 검증하지 않음. 아래 Threat ID는 초기 시험 VM의 과거 검증 | 포트 차단만으로 IDS/IPS 성공 판정하지 않음. 공식 시험 URI에서 실제 IDs 30844/30852/35107/55558과 ALERT를 확인. 앱은 URI만 반환하므로 실제 취약점/파일 접근 성공으로 해석하지 않음 |
| AWS <-> GCP | 4터널/BGP, NCC의 AWS Prefix/업무 subnet, 양쪽 실제 TCP 요청과 반환 경로, 터널별 장애 전환 | 터널 UP만으로 앱 통신이나 장애 전환 성공 판정하지 않음. AWS ANF 검사로 보고하지 않음 |
| GCP -> 플랫폼 AWS 로그 | 새 원본의 project/logName/insertId/시각/요청 정보와 `/delta-platform/central/gcp`의 원본 JSON 및 실제 도착 시각 | Lambda 실행 로그나 합성 메시지 저장만으로 실제 네트워크 로그 수집 완료 판정하지 않음 |

각 요청에 고유한 `request_id`를 붙여 앱 응답과 로그의 tuple/발생 시각을 대조한다. 초기 평문 HTTP 검증을 HTTPS 본문 검사로 확대하지 않는다. AWS 방화벽과 SG는 별개이므로 시험할 사설 서비스의 SG/리스너도 확인한다. 기존 AWS SG를 이 root에서 변경하지 않는다.

## 관측, 중지와 복구

- GCP에서 endpoint/association 준비 상태, 규칙/위협 로그, ALB 헬스와 FE/BE/SQL 상태, NCC 경로, VPN/BGP를 확인한다.
- 플랫폼 전용 Pub/Sub 구독의 backlog, oldest unacked age와 DLQ를 확인한다. 플랫폼 일반 구독은 24시간, 실패 구독은 7일 보존이다. DLQ는 자동 폐기하지 않으며 원인을 해결한 뒤 권한 있는 운영자가 원본을 검토해 재전달한다.
- 플랫폼의 Lambda Errors 경보와 `ingest_summary`의 pulled/stored/acked/failed를 확인한다. `event_id`로 재전달 중복을 식별한다. 실패 원문이나 인증 토큰을 로그에 넣지 않는다.
- 수집 장애 시 플랫폼 운영 절차로 해당 Sink와 실행 규칙을 조정하고 메시지를 보존한다. 원인 해결 후 저장과 ack를 대조한다. 보존 기간을 넘긴 데이터까지 복구된다고 주장하지 않는다. 폐기한 네트워크 계정 수집기를 임의로 재생성하지 않는다.
- 검사 장애나 정리가 필요하면 공개 forwarding rule을 먼저 닫고 association과 endpoint를 정리한다. VM 중지만으로 검사 과금 중단을 선언하지 않는다. state, 원본 로그와 기존 AWS 기반은 보존한다.
- VPN 롤백은 신규 VPN 경로/전파와 계약을 의존 순서에 맞게 정리한다. ANF를 재생성하지 않는다. 실제 적용 뒤 최종 Plan과 기존 AWS 통신을 다시 확인한다.

## 부분 Apply의 state 복구

2026-11-04 첫 Apply에서 API/DNS 연결 오류와 GCS state 저장/잠금 해제 실패가 함께 발생했다. 실제 생성된 주소와 헬스 체크를 API로 확인하고, 종료된 자기 Apply의 lock 생성 시각/소유자 및 GCS generation을 대조했다. 원격/복구 state의 lineage가 같고 원격 serial이 더 낮은 것을 확인한 뒤 복구본을 백업하고 자기 lock만 해제했다. `state push errored.tfstate`와 기존 두 리소스/API import를 순차 실행했다.

일반 복구 시에도 실행 중인 Apply가 없는지, lock 소유자와 state lineage/serial이 일치하는지 먼저 확인한다. 같은 이름의 생성 요청을 반복하거나 이전 저장 Plan을 재사용하지 않는다. 복구 state를 보존하고 실제 리소스를 소유 state에 맞춘 뒤 새 Plan을 검토한다. 연결 오류의 원인은 확정하지 않았으며 재개 시 낮은 parallelism을 사용한다.

## 공개 진입점 변경

현재 진입점은 새 리전 ALB의 HTTP80이다. 과거 NLB 시험의 `internet_source_cidrs`와 8080/8081 인터넷 규칙은 제거했다. 이 필드를 현재 root에 추가해 공개 ALB 접근이 제한된다고 판단하지 않는다. 공개 접근 정책이 필요하면 ALB의 별도 정책과 대상/비용을 설계하고 저장 Plan을 검토한다. 관리 SSH는 계속 IAP/OS Login을 사용한다.

## 검사 비용 중단과 재개

현재 실제 모드는 ignored `inspection-mode.auto.tfvars`의 disabled다. 아래 중단 절차에는 Provider 8.10.0의 참조 제거 오류에 대한 JSON null 우회가 필요하므로 해당 절을 함께 확인한다. 실제 모드는 이 파일에 기록한다. 콘솔에서 endpoint만 삭제하면 Terraform이 다시 생성할 수 있으므로 원하는 모드와 코드를 먼저 맞춘다.

1. 중단 준비: `inspection_mode="bypass"`로 저장 Plan을 생성한다. 검사 규칙3개만 allow로 갱신되고 endpoint는 주소 이동/no-op인지 확인한다. VM/NLB/VPC/NCC/VPN 교체/삭제가 없어야 한다. Apply 뒤 두 방향8080/8081 HTTP와 공개8080 허용/8081 기본 거부를 확인한다.
2. 비용 중단: `inspection_mode="disabled"`로 새 Plan을 만든다. association2개와 endpoint1개만 삭제되는지 확인한 뒤 적용한다. 삭제 완료/API 목록/4 BGP와 양방향 HTTP, 일반 방화벽 로그의 AWS 도착 및 최종 No changes를 확인한다. 프로필과 과거 로그는 유지한다.
3. 재개 준비: 별도 실행 승인을 확인한 뒤 bypass Plan에서 endpoint/association만 생성한다. 생성/연결 ACTIVE와 기존 통신을 확인한다. 이전 생성 검증은 endpoint20분51초/연결약7분으로 준비 시간이 필요했다.
4. 검사 재개: enabled Plan에서 검사 규칙3개를 apply_security_profile_group으로 전환한 뒤 실제 허용/거부/Threat 로그를 확인한다.

각 단계는 `terraform plan -out=...`의 실제 대상/변경 목록을 검토한 뒤 해당 저장 Plan을 Apply한다. 삭제 후 ready 전에 enabled로 바로 전환하지 않는다. AWS ANF 모드와 이 root의 모드는 독립적이다. 현 AWS 정책에는 GCP 허용 흐름이 준비되지 않았으므로 공통 재개는 AWS VPN 경로/허용 정책 보완과 별도 검증이 필요하다. Endpoint가 없어도 VPN/NCC/VM/NLB/로그 비용은 남는다.

### Provider 8.10.0의 프로필 참조 제거 오류

실제 중단에서 inspection -> allow 갱신 시 Provider가 `securityProfileGroup`을 빈 문자열로 보내 API400이 발생했다. 이 버전에서는 단순 생략도 기존 필드를 제거하지 못했다. 동일한 프로젝트/정책/priority/조건을 원래 저장 Plan과 대조한 뒤 `networkFirewallPolicies.patchRule` 요청의 `action`을 allow, `securityProfileGroup`을 JSON null로 지정하면 정상 제거됐다. 실제 세 규칙200/210/300의 조건과 로깅을 보존하고 명시적 null로 전환한 뒤 새 Terraform Plan으로 state를 동기화한다. 오류가 나면 endpoint를 유지한 채 해결하며 disabled 삭제 단계로 진행하지 않는다.

이 우회는 현재 버전의 in-place 프로필 제거 문제에 한정한다. `ignore_changes`로 프로필 차이를 숨기거나 Terraform 밖에서 endpoint부터 삭제하지 않는다. 새 버전의 수정 여부와 공통 자동화는 후속 검토 범위다.

## GCP 단일 VM Compose 운영

기존 FE VM에 FE/BE Compose를 통합하고 기존 BE VM을 제거하는 후속 작업은 [Compose 배포 Runbook](../../deploy/workload-gcp/README.md#단일-vm-compose)과 [실제 검증 기록](../../docs/ai-dlc/gcp-compose-consolidation.md)을 따른다. 네트워크 root의 `compose_service_account`는 통합 호스트의 PSC3307 egress만 추가한다. FE 컨테이너의 metadata/DB 경계와 재부팅 자동 복구는 호스트에서 검증한다. BE 빈 subnet과 NCC/VPN은 보존한다.
