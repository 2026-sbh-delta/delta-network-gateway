# 공통 워크로드 네트워크 운영

기본 구성은 공통 네트워크와 계정/리전 GuardDuty이며 `application_enabled=true`로 공개 ALB와 사설 x86 FE/BE/RDS 앱 기반을 추가할 수 있다. 기존 시험 EC2 두 대와 시연 CloudFront의 정리 결과는 [통합 기록](../../docs/ai-dlc/aws-workload-consolidation.md), 새 앱의 실제 상태는 [앱 기록](../../docs/ai-dlc/aws-sample-app.md)을 확인한다.

## Plan과 계정 확인

단일 EC2 Compose는 `application.deployment_mode="compose"`를 사용한다. 기존 native 두 EC2에서 전환할 때 `retain_legacy_backend=true`로 먼저 호스트 권한/DB 경로만 적용하고 [Compose 배포 절차](../../deploy/workload/README.md#단일-ec2-docker-compose)를 수행한다. 공개 CRUD와 BE 컨테이너/EC2 재시작 검증 후 false로 별도 Plan/Apply해 기존 BE만 제거한다. FE/ALB/RDS 교체나 의도하지 않은 삭제가 나오면 적용하지 않고 원인을 해결한다. 기존 user_data를 변경하는 방식으로 Docker를 설치하지 않는다.

Compose의 현재/직전 이미지와 systemd/secret/방화벽 부팅 순서, 실패 복귀는 [전환 기록](../../docs/ai-dlc/aws-compose-consolidation.md)과 배포 도구를 따른다. `application_enabled=false`는 전체 앱 삭제이므로 버전 복귀에 사용하지 않는다.

```bash
aws sts get-caller-identity --profile sbh-delta-workload
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload output -json
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload plan -input=false -detailed-exitcode
```

업무 계정은 `616723161368`, 리전은 `ap-northeast-2`다. backend key는 기존 `delta/lab/workload-client/terraform.tfstate`를 유지한다. 새 key를 만들거나 이전 Server state를 연결하지 않는다.

## GuardDuty 등록과 운영

GuardDuty는 계정 616723161368의 서울 리전 전체를 대상으로 한다. [승인 범위와 현재 상태](../../docs/ai-dlc/aws-guardduty.md)를 먼저 확인한다. 앱 실행 여부나 ANF disabled 모드와 독립적이다.

```bash
aws sts get-caller-identity --profile sbh-delta-workload
aws guardduty list-detectors --profile sbh-delta-workload --region ap-northeast-2
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload state list
```

기존 detector ID가 정확히 1개이고 이 root의 state에 `aws_guardduty_detector.account`가 없을 때만 아래 Import를 실행한다. 이미 등록돼 있으면 생략한다. 다른 Terraform state가 관리하는 detector를 중복 등록하지 않는다. 기존 detector를 삭제하거나 중지하지 않는다.

```bash
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload import \
  -input=false -lock-timeout=2m aws_guardduty_detector.account d4d0cf9edb23ea3875d2c9248d0844bc
umask 077
mkdir -p terraform/workload/.validation/guardduty
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload plan \
  -input=false -parallelism=1 -lock-timeout=2m -out=.validation/guardduty/register.tfplan
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload show .validation/guardduty/register.tfplan
```

Plan에서 GuardDuty 이외 기존 자원의 변경/교체/삭제가 없는지 확인한다. 기존 SIX_HOURS와 S3/EKS Audit/EBS Malware/RDS/Lambda 및 RDS_DATA_RISK ENABLED, Runtime/자동 에이전트/AI Protection DISABLED를 유지한다. Detector에는 공통 태그와 Name만 추가한다. Feature 리소스는 Import를 지원하지 않으며 같은 기존 설정으로 Update해 Terraform에 등록한다.

```bash
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload apply \
  -input=false -parallelism=1 .validation/guardduty/register.tfplan
aws guardduty get-detector --profile sbh-delta-workload --region ap-northeast-2 \
  --detector-id d4d0cf9edb23ea3875d2c9248d0844bc
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload output -json guardduty
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload plan -input=false -detailed-exitcode
```

최종 detector ID/Status/기능/추가 설정과 No changes/종료0을 확인한다. 실제 공격이나 sample finding 생성은 이 활성화 검증에 포함하지 않는다.

향후 별도 중지 요청이 있으면 실제 tfvars에 `guardduty_enabled=false`를 저장하고 새 Plan을 검토해 적용한다. detector와 findings를 보존한 일시 중지이며, true로 바꾸면 재개한다. CLI의 일회성 `-var`만 쓰면 다음 일반 Plan은 기본 true로 다시 활성화하려 하므로 지속할 설정은 실제 입력에 보존한다. 이번 기존 workload 등록의 원복을 위해 탐지를 중지하지 않는다.

Detector는 `prevent_destroy`로 보호한다. Feature를 맵에서 빼거나 state에서 제거해도 AWS 기능은 꺼지지 않는다. 기능을 중단하려면 전체 필수 맵을 유지하며 status를 DISABLED로 바꾸고, 해당 추가 설정도 명시적으로 끈다. Detector 삭제 및 GuardDuty 전체 비활성화는 findings와 설정을 잃으므로 일반 원복에 사용하지 않는다.

## 실제 인벤토리와 경로 확인

```bash
aws ec2 describe-instances --profile sbh-delta-workload --region ap-northeast-2 \
  --filters Name=vpc-id,Values=vpc-056871f49eecfeef4 \
  --query 'Reservations[].Instances[].[InstanceId,State.Name,PrivateIpAddress]'
aws ec2 describe-route-tables --profile sbh-delta-workload --region ap-northeast-2 \
  --filters Name=vpc-id,Values=vpc-056871f49eecfeef4
aws ec2 describe-transit-gateway-attachments --profile sbh-delta-workload --region ap-northeast-2 \
  --transit-gateway-attachment-ids tgw-attach-0cd5f3d3c73ee7643
```

공통 VPC의 기존 전용 서브넷은 두 AZ의 `10.10.0.0/24`, `10.10.1.0/24`, `10.10.2.0/24`, `10.10.3.0/24`다. GCP 목적지 `10.32.1.0/24`의 명시적 TGW 경로를 유지한다. 네트워크 전용 모드는 IGW/NAT/인터넷 기본 경로와 공인 주소를 추가하지 않는다. 앱 모드는 신규 public /24 두 개와 DB /24 두 개, ALB/IGW/NAT를 추가하고 선택 AZ의 기존 workload RT에만 NAT default route를 추가한다. 기존 TGW 전용 RT와 새 DB RT에는 인터넷 경로가 없다. 앱 EC2와 RDS에는 공인 주소를 부여하지 않는다.

## 이후 앱 연결

승인된 Sample App은 이 root의 선택적 `modules/sample-app`에서 공통 네트워크를 참조한다. 비활성화 기본값만으로 앱이 배포됐다고 판단하지 않는다. 실제 서브넷 사용 현황과 예약 대역, state/계정 및 최신 Plan을 확인한다. 실제 활성화 입력은 ignored application.auto.tfvars에 유지한다.

EIC Endpoint는 유지한다. 네트워크 전용 모드에는 EC2가 없고 앱 모드는 역할별 EC2와 management SG에서 오는 SSH만 추가한다. 접속과 개별 배포/DB 초기화/관측/rollback은 [배포 도구](../../deploy/workload/README.md)를 따른다. 종료된 시험 인스턴스 ID나 과거 HTTP 시험 명령을 재사용하지 않는다.

## 중앙과 GCP 연결

중앙 `network-security`의 안정된 `aws_client` spoke 키와 Attachment를 유지한다. GCP는 AWS 목적지 대역을 공통 `10.10.0.0/16` 하나로 등록한다. 네 BGP 세션과 수신/광고 경로를 API에서 확인한다.

EC2가 없는 상태에서는 네트워크 경로의 존재와 HTTP 도달 성공을 구분한다. 이 구성은 VPC 내부 local 트래픽의 중앙 ANF 경유를 보장하지 않는다. 2026-11-07 사용자 승인으로 board의 인터넷/GCP ANF 검사를 재개했다. 현재 AWS 검사는 마지막 절과 [실제 검증](../../docs/ai-dlc/anf-board-reactivation.md)을, 별도 GCP NGFW 모드는 해당 root의 운영 기록을 따른다.

## 롤백과 전체 해체

이번 통합으로 삭제한 EC2의 루트 EBS는 state만으로 복구할 수 없다. 기존 시연 환경 재생성이나 공통 네트워크 전체 해체는 대상과 의존성을 별도로 검토한다. 현재 VPC와 TGW Attachment를 보호하기 위해 단순 디렉터리 정리 목적으로 `terraform destroy`를 실행하지 않는다.


## 공유 ANF로 인터넷 검사하기

대상은 [승인한 delta-board-vpc](../../docs/ai-dlc/anf-board-reactivation.md) 한 개다. DNS는 기존 ALB를 계속 가리키며, IGW edge 라우팅으로 같은 AZ의 Secondary Endpoint를 왕복한다. 내부 ALB-앱/앱-DB/EIC 경로는 local을 유지한다. NAT 뒤 검사 로그는 NAT 사설 IP와 변환된 출발지 포트를 표시할 수 있으므로 요청 시각/목적지와 실제 NAT tuple을 대조한다.

1. 중앙 계정의 Primary 두 개 READY/IN_SYNC와 RAM 공유를 확인한다. 실제 ARN/물리 AZ IDs를 ignored `internet-inspection.auto.tfvars.json`의 `internet_inspection`에 입력한다. `expected_vpc_id`는 현재 VPC와 반드시 같아야 한다.
2. `mode=bypass`에서 전용 두 subnet, Secondary 두 개, Endpoint RT/IGW 반환 경로와 미연결 ingress RT를 준비한다. 저장 Plan에서 기존 public/NAT/TGW 경로와 ALB/EC2/RDS 교체가 없어야 한다. 준비 Apply 후 Secondary의 READY, 실제 VPC/subnet/AZ/Endpoint ID를 대조한다.
3. mode를 enabled로 바꾸고 새 저장 Plan과 JSON을 만든다. 기존 public default 두 개를 해당 AZ Endpoint로 바꾸고 IGW edge Association 한 개를 생성하는 변경만 있어야 한다.

```bash
terraform plan -input=false -out=internet-enabled.tfplan
terraform show -json internet-enabled.tfplan > internet-enabled.tfplan.json
python3 scripts/switch_internet_routes.py internet-enabled.tfplan.json --profile sbh-delta-workload --firewall-profile sbh-delta-security
# 정확한 대상과 양방향 변경을 검토한 뒤 동일 JSON으로 실행한다.
python3 scripts/switch_internet_routes.py internet-enabled.tfplan.json --profile sbh-delta-workload --firewall-profile sbh-delta-security --execute
```

4. 출력의 실제 `import_id`로 새 edge Association을 기존 state에 등록한다. 예시 문자열을 실제 ID 대신 쓰지 않는다.

```bash
terraform import 'aws_route_table_association.internet_ingress[0]' '<실제-IGW-ID>/<실제-edge-RT-ID>'
terraform plan -input=false -out=internet-confirmed.tfplan
# API 전환 뒤 생성한 새 Plan만 검토/적용한다.
terraform apply internet-confirmed.tfplan
```

5. 실제 인터넷 요청/응답, 호스트의 NAT HTTPS, ALB health/기존 DB TLS와 관리 접속 및 FLOW/ALERT 원본과 중앙 수신을 확인한다. 앱이 중지돼 있으면 한시적 probe 결과를 실제 앱 정상화로 보고하지 않는다. probe/키/터널/임시 SG는 제거하고 기존 앱 상태를 확인한다.

helper는 다른 계정/VPC/예상 밖 변경, stale 다음 홉, unready/다른 AZ Endpoint와 기존의 다른 IGW edge Association을 거부한다. Route나 Association API가 부분 실패하면 기존 public default와 기존 edge 상태로 복귀한다. 두 방향 API 변경은 원자적이지 않으므로 기존 연결은 재접속할 수 있다.

전환 후 통신이 실패하면 저장해 둔 원래 IGW를 두 public default에 ReplaceRoute로 복원하고 이번 edge Association을 해제한다. 실제 입력의 mode를 bypass로 되돌린 새 Plan으로 state를 대조한다. Endpoint는 경로 참조를 복구하기 전에 제거하지 않는다. 다른 VPC나 IGW의 Association을 해제하지 않는다.
