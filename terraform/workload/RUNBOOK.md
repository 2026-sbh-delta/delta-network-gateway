# 공통 워크로드 네트워크 운영

기본 구성은 네트워크 전용이며 `application_enabled=true`로 공개 ALB와 사설 x86 FE/BE/RDS 앱 기반을 추가할 수 있다. 기존 시험 EC2 두 대와 시연 CloudFront의 정리 결과는 [통합 기록](../../docs/ai-dlc/aws-workload-consolidation.md), 새 앱의 실제 상태는 [앱 기록](../../docs/ai-dlc/aws-sample-app.md)을 확인한다.

## Plan과 계정 확인

```bash
aws sts get-caller-identity --profile sbh-delta-workload
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload output -json
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload plan -input=false -detailed-exitcode
```

업무 계정은 `616723161368`, 리전은 `ap-northeast-2`다. backend key는 기존 `delta/lab/workload-client/terraform.tfstate`를 유지한다. 새 key를 만들거나 이전 Server state를 연결하지 않는다.

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

EC2가 없는 상태에서는 네트워크 경로의 존재와 HTTP 도달 성공을 구분한다. 이 구성은 VPC 내부 local 트래픽의 중앙 ANF 경유를 보장하지 않는다. 현재 ANF/NGFW의 검사 모드는 별도 요청 전까지 disabled를 유지한다.

## 롤백과 전체 해체

이번 통합으로 삭제한 EC2의 루트 EBS는 state만으로 복구할 수 없다. 기존 시연 환경 재생성이나 공통 네트워크 전체 해체는 대상과 의존성을 별도로 검토한다. 현재 VPC와 TGW Attachment를 보호하기 위해 단순 디렉터리 정리 목적으로 `terraform destroy`를 실행하지 않는다.
