# 검증, 장애 대응과 해체

## ANF 비용 중단과 원복

승인 범위와 실제 결과는 [ANF 비용 절감 AI-DLC](../../docs/ai-dlc/anf-cost-mode.md)를 따른다. 나머지 검사/관제 절차는 `inspection_mode=enabled` 기준이다. 아래 명령을 실행했다는 사실만으로 통신이나 원복을 성공으로 기록하지 않는다.

2026-11-04 GCP 작업은 사용자 지시로 ANF를 원복하지 않는다. disabled를 유지하며 VPN의 BGP 경로를 Spoke RT에 전파하는 구현을 추가했다. 새 GCP 연결은 [GCP Runbook](../network-security-gcp/RUNBOOK.md)의 절차를 따른다. 아래 VPC 전환 helper는 정적 Route의 추가/삭제를 처리하지 않으므로, VPN이 붙은 상태의 모드 전환에 그대로 사용하지 않는다. 해당 전환은 VPN 전파와 정적 검사 경로의 추가/삭제를 포함한 별도 저장 Plan 검토가 필요하다.

### 전환 전 확인

- 중앙 계정 STS, 기존 backend/state, Firewall/두 Endpoint READY와 IN_SYNC, 실제 VPC Attachment/RT를 확인한다.
- 아래 helper의 적용 범위는 기존 VPC 정적 Route의 다음 홉 교체다. 직접 모드의 VPN은 BGP 전파로 처리한다. 보조 Endpoint Association이 있으면 소유자의 제거와 영향을 확인하기 전 Firewall을 제거하지 않는다.
- 정적 웹, 8080 허용 API와 8081 차단 API를 기준 결과로 기록한다. 우회 중의 8081 성공은 예상 동작이다.
- `terraform.tfvars` 대신 모드 전용의 ignored `inspection-mode.auto.tfvars`를 사용한다. 이 파일을 지우면 기본 enabled로 돌아갈 수 있으므로 비용 중단 중에는 보존한다. SSO 인증정보와 Plan/JSON은 커밋하지 않는다.
- 고정 Provider 6.71.0은 TGW 다음 홉 변경을 Route 삭제/재생성으로 처리한다. 다음 helper는 검토한 JSON Plan에서 기존 Spoke Route의 다음 홉 변경만 추출해 AWS ReplaceTransitGatewayRoute를 호출한다. 기본은 dry run이고 `--execute`가 있어야 변경한다. 실제 계정, 경로 변경 여부와 검사 원복 전 READY/IN_SYNC를 확인하며, 도중 실패하면 이번에 바꾼 다음 홉을 되돌린다. 복구 실패 메시지가 있으면 실제 Route를 직접 확인한다.

```bash
cd terraform/network-security
export AWS_PROFILE=sbh-delta-security
export AWS_REGION=ap-northeast-2
umask 077
aws sts get-caller-identity
```

### 비용을 중단하기

1. `enabled -> bypass`: Firewall을 보존한 채 직접 경로와 삭제 보호 해제를 계획한다.

```bash
printf 'inspection_mode = "bypass"\n' > inspection-mode.auto.tfvars
terraform plan -input=false -out=bypass.tfplan
terraform show -json bypass.tfplan > bypass.tfplan.json
python3 scripts/replace_inspection_routes.py bypass.tfplan.json --profile "$AWS_PROFILE"
# Plan을 검토한다. 같은 Spoke RT의 등록 CIDR -> 각 VPC Attachment만 바뀌어야 한다.
python3 scripts/replace_inspection_routes.py bypass.tfplan.json --profile "$AWS_PROFILE" --execute
# 기존 Plan은 쓰지 않는다. 새 Plan에서 Route 삭제/재생성이 사라졌는지 확인한다.
terraform plan -input=false -out=bypass-confirmed.tfplan
terraform apply bypass-confirmed.tfplan
```

실제 양방향 Route가 active인지, 기존 8080/8081 API가 서버 응답을 받는지 확인한다. Firewall은 유지돼 있어야 하고 DeleteProtection은 false여야 한다. 이 단계는 아직 엔드포인트 시간 요금이 발생한다. 통신이 실패하면 `enabled`로 돌아가 아래 원복 경로 교체 절차를 수행한다.

2. `bypass -> disabled`: 통신을 확인한 뒤 Firewall과 그 엔드포인트 참조만 제거한다.

```bash
printf 'inspection_mode = "disabled"\n' > inspection-mode.auto.tfvars
terraform plan -input=false -out=disabled.tfplan
# Firewall, 로그 연결, Firewall RAM Resource Association과 to_firewall Route만 제거되는지 검토한다.
terraform apply disabled.tfplan
terraform plan -input=false -detailed-exitcode
terraform output inspection_mode
terraform output primary_endpoint_ids_by_az_id
```

현재 VPC 2개/2 AZ 구성에서는 삭제 대상이 7개다. Firewall 1개, 로그 연결 1개, Firewall RAM Resource Association 1개, 엔드포인트 Route 4개다. AWS Firewall 목록과 이전 두 VPC Endpoint의 실제 부재, No changes, API 응답을 확인한다. 정책/규칙/로그 그룹/VPC/서브넷/TGW/Attachment와 RAM Share/Principal은 보존한다. 로그 보관, TGW, ALB, EC2 등 다른 비용은 계속 발생한다.

삭제 전에 발생한 검사 로그가 늦게 도착할 수 있다. 로그 수신 시각만으로 현재 검사 여부를 판단하지 말고 실제 이벤트 시각과 요청 tuple을 대조한다.

### 검사 경로로 원복하기

1. `disabled -> bypass`: 같은 VPC/서브넷과 보존한 정책으로 ANF를 다시 만든다. 이 동안 직접 경로를 유지한다.

```bash
printf 'inspection_mode = "bypass"\n' > inspection-mode.auto.tfvars
terraform plan -input=false -out=restore-ready.tfplan
terraform apply restore-ready.tfplan
aws network-firewall describe-firewall --firewall-name delta-lab-central \
  --query '{Status:FirewallStatus.Status,Sync:FirewallStatus.ConfigurationSyncStateSummary,Endpoints:FirewallStatus.SyncStates}'
```

Firewall과 모든 Endpoint READY/IN_SYNC, 로그 연결과 RAM Resource Association, 새 Endpoint ID의 Route를 확인한다. 엔드포인트 ID는 이전 값을 재사용하지 않는다.

2. `bypass -> enabled`: 준비된 ANF로 경로를 바꾸고 입력의 삭제 보호를 복원한다.

```bash
printf 'inspection_mode = "enabled"\n' > inspection-mode.auto.tfvars
terraform plan -input=false -out=restore.tfplan
terraform show -json restore.tfplan > restore.tfplan.json
python3 scripts/replace_inspection_routes.py restore.tfplan.json --profile "$AWS_PROFILE"
# 양쪽 등록 CIDR이 기존 Inspection Attachment를 가리키는지 검토한다.
python3 scripts/replace_inspection_routes.py restore.tfplan.json --profile "$AWS_PROFILE" --execute
terraform plan -input=false -out=restore-confirmed.tfplan
terraform apply restore-confirmed.tfplan
```

허용 8080의 실제 HTTP 응답과 양방향 FLOW, 차단 8081의 실제 실패와 SID 9000000 blocked/drop을 확인한다. 최종 Plan No changes를 확인하고 실제 복구 시간을 기록한다. 경로 전환 시 기존 연결은 재접속이 필요할 수 있다.

### 로컬 회귀

```bash
scripts/validate.sh
```

이 명령은 경로 교체 helper의 단위 테스트도 실행한다. helper만 확인하려면 `python3 -m unittest discover -s scripts -p 'test_replace_inspection_routes.py' -v`를 사용한다. mock 성공은 실제 AWS 전환이나 원복 성공을 뜻하지 않는다.

## 검증 수준을 구분하기

| 확인 | 의미 | 의미하지 않는 것 |
| --- | --- | --- |
| fmt/validate | Terraform 문법, 타입과 Provider schema 일치 | AWS 배포 가능성 전체 및 통신 성공 |
| mock Plan | 리소스 연결, 조건부 입력과 경로 불변식 | 실계정 조회, 실제 Suricata 패킷 처리 |
| AWS Rule Group DryRun | 변경 없는 AWS 요청 유효성 검사 | Rule Group 생성, Firewall 배포, 실제 패킷 처리 |
| 실계정 Plan | 실제 계정과 입력 기반의 예정 변경 검토 | Apply 완료 |
| Apply 후 상태 확인 | AWS 리소스/경로 상태 확인 | 상대 환경의 실제 허용/차단 통신 성공 |
| 양방향 통신과 SID 로그 | 해당 시나리오의 경로/정책 증거 | 모든 네트워크와 모든 IPS 탐지 검증 |

실행한 날짜, 명령, 결과를 AI-DLC 기록에 적는다. 로그 전송 지연을 고려하고 준비되지 않은 상대 환경을 PASS로 표시하지 않는다.

## 로컬 검사

```bash
export AWS_PROFILE=sbh-delta-security
DELTA_KEEP_VALIDATION=1 DELTA_VERBOSE_PLAN=1 ./scripts/validate.sh
```

스크립트는 실제 AWS Provider를 mock으로 대체하고 모든 run에서 Plan만 사용한다. 임시 디렉터리의 JSON 이벤트에는 가짜 fixture만 들어 있다. 필요 시 다음 명령으로 컴파일된 두 Rule Group을 AWS DryRun에 전달한다. 기대 계정은 운영자가 확인한 중앙 Account ID를 사용한다.

```bash
python3 scripts/check_rules_dry_run.py \
  --events "$DELTA_TEST_EVENTS" \
  --expected-account-id "$DELTA_CENTRAL_ACCOUNT_ID" \
  --profile sbh-delta-security --region ap-northeast-2
```

이 스크립트는 identity 확인 뒤 `CreateRuleGroup`에 `DryRun=true`와 `--dry-run`을 함께 고정한다. 실제 Rule Group 생성 경로는 없다. 이것도 실제 Endpoint에서 처리한 패킷 검증으로 기록하지 않는다.

## RAM Resource Association이 실패할 때

`AssociateResourceShare`가 조직 내부 공유 또는 Organizations 온보딩 관련 `OperationNotPermittedException`을 반환하면 조직 관리 계정에서 RAM 사전 설정을 확인한다. 현재 기본값 `allow_external_principals=false`는 조직 내부 공유를 사용하며, 공유 대상 계정이 비어 있어도 TGW/Firewall Resource Association은 생성한다.

1. 조직 관리 계정으로 AWS RAM Settings에 접속해 Enable sharing with AWS Organizations를 켜고 저장한다. CLI를 쓰는 관리자는 해당 관리 계정의 자격 증명으로 `aws ram enable-sharing-with-aws-organization`을 실행한다. 이 작업은 중앙 Terraform 밖에서 수행한다.
2. `AWSServiceRoleForResourceAccessManager` 서비스 연결 역할과 활성화 반영 상태를 확인한다. Organizations 화면에서 trusted access만 활성화하면 이 역할이 생성되지 않을 수 있다. RAM 화면 또는 RAM CLI를 통해 활성화해야 한다.
3. 중앙 프로필과 기존 backend/state로 새 `terraform plan`을 실행해 남은 Association 변경을 검토한 뒤 승인된 Apply를 재시도한다. 이미 생성된 리소스와 부분 Apply 결과를 기존 state에서 계속 관리한다.

관리 계정의 활성화에는 `ram:EnableSharingWithAwsOrganization`, `iam:CreateServiceLinkedRole`, `organizations:EnableAWSServiceAccess`, `organizations:DescribeOrganization` 권한이 필요하다. 중앙 프로필의 조직 조회가 거부되면 미활성화와 온보딩 진행 중을 구분하지 못한 상태로 기록한다. [AWS RAM 조직 공유 사전 설정](https://docs.aws.amazon.com/ram/latest/userguide/getting-started-sharing.html)

관리 계정의 RAM Settings에서 이미 활성화했는데도 같은 오류가 반복되면 다음을 관리 계정의 인증된 세션에서 읽기 전용으로 확인한다. `DELTA_CENTRAL_ACCOUNT_ID`는 중앙 계정 ID로 지정한다.

```bash
aws iam get-role --role-name AWSServiceRoleForResourceAccessManager \
  --query 'Role.{Arn:Arn,Trust:AssumeRolePolicyDocument}'
aws organizations list-aws-service-access-for-organization \
  --query "EnabledServicePrincipals[?ServicePrincipal=='ram.amazonaws.com']"
aws organizations describe-organization --query 'Organization.{Id:Id,FeatureSet:FeatureSet}'
aws organizations describe-account --account-id "$DELTA_CENTRAL_ACCOUNT_ID" \
  --query 'Account.{Id:Id,State:State}'
```

정상 조건은 RAM 역할이 존재하고 `ram.amazonaws.com`을 신뢰하며, 조직 trusted access 목록에 RAM이 있고, 조직 FeatureSet이 ALL이며 중앙 계정이 그 조직의 ACTIVE 멤버인 것이다. 인증 오류나 권한 거부로 조회하지 못한 항목은 미확인으로 기록한다. 중앙 사용자의 `DescribeOrganization` 거부만으로 RAM 서비스의 실패 원인이 SCP라고 단정하지 않는다. 모든 조건을 확인해도 재현되면 실패 시각/Region/두 RequestID와 활성화 결과를 근거로 AWS 측 온보딩 상태를 조사한다.

## 배포 후 제어 영역 확인

아래 읽기 전용 명령은 실제 배포 및 상대 환경 준비 후 실행한다. 명령에 쓰는 값은 Terraform 출력과 연결 계약에서 받는다.

```bash
DELTA_FIREWALL_ARN="$(terraform output -raw firewall_arn)"
DELTA_TGW_ID="$(terraform output -raw tgw_id)"
DELTA_SPOKE_RT="$(terraform output -raw spoke_route_table_id)"
DELTA_INSPECTED_RT="$(terraform output -raw inspected_route_table_id)"

aws network-firewall describe-firewall --firewall-arn "$DELTA_FIREWALL_ARN" \
  --query '{Firewall:FirewallStatus.Status,Sync:FirewallStatus.SyncStates}'
aws network-firewall describe-logging-configuration --firewall-arn "$DELTA_FIREWALL_ARN"
aws ec2 describe-transit-gateway-attachments \
  --filters "Name=transit-gateway-id,Values=$DELTA_TGW_ID" \
  --query 'TransitGatewayAttachments[].{Id:TransitGatewayAttachmentId,State:State,Type:ResourceType,Owner:ResourceOwnerId,Association:Association}'
aws ec2 get-transit-gateway-route-table-associations --transit-gateway-route-table-id "$DELTA_SPOKE_RT"
aws ec2 get-transit-gateway-route-table-associations --transit-gateway-route-table-id "$DELTA_INSPECTED_RT"
aws ec2 get-transit-gateway-route-table-propagations --transit-gateway-route-table-id "$DELTA_SPOKE_RT"
aws ec2 get-transit-gateway-route-table-propagations --transit-gateway-route-table-id "$DELTA_INSPECTED_RT"
aws ec2 search-transit-gateway-routes --transit-gateway-route-table-id "$DELTA_SPOKE_RT" \
  --filters Name=state,Values=active
aws ec2 search-transit-gateway-routes --transit-gateway-route-table-id "$DELTA_INSPECTED_RT" \
  --filters Name=state,Values=active
```

확인 기준:

- Firewall 전체와 선택한 모든 AZ의 Endpoint가 READY이고 기대한 Subnet에 있어야 한다.
- RAM Resource/Principal Association이 ASSOCIATED이며 대상은 TGW와 Firewall 자체여야 한다.
- Inspection Attachment의 appliance mode가 enable이고 Inspected RT에 연결돼야 한다.
- 업무/VPN Attachment는 Spoke에 연결하고 Spoke에는 목적지 직결 Route나 Propagation이 없어야 한다.
- 실제 VPC/TGW Route를 모두 대조해 더 구체적인 Prefix로 우회하거나 잘못 전달하는 경로가 없어야 한다.

소비자의 보조 Endpoint는 소비자 계정에서 `describe-vpc-endpoint-association`으로 READY와 실제 Endpoint ID/Subnet을 확인한다. 중앙 ARN을 공유받았다는 사실만으로 보조 Endpoint 생성이나 로그 수집 완료로 판정하지 않는다.

## VPN과 BGP

```bash
aws ec2 describe-vpn-connections --vpn-connection-ids "$DELTA_VPN_ID" \
  --query 'VpnConnections[].{Id:VpnConnectionId,State:State,Tunnels:VgwTelemetry[].{OutsideIp:OutsideIpAddress,Status:Status,AcceptedRouteCount:AcceptedRouteCount,LastStatusChange:LastStatusChange}}'
```

양쪽 터널 UP만 확인하지 말고 BGP 세션, accepted route count와 실제 Inspected RT의 학습 Prefix를 비교한다. 원격 계약 CIDR과 광고/학습 Prefix가 일치하는지, 원격이 업무 VPC CIDR이나 기본 경로를 잘못 광고하지 않는지 확인한다. 이 구성의 TGW VPN Propagation에는 Prefix 필터를 추가하지 않았으므로 원격 광고 필터와 실제 Route 감사를 운영자가 수행해야 한다.

GCP에서는 각 Cloud Router의 BGP 상태와 HA VPN 터널 4개를 확인한다. interface 0/1, 두 AWS VPN의 outside IP 네 개, 중복 없는 /30, 양쪽 ASN, IKE 변환 세트가 일치해야 한다. 제어된 장애 시험은 각 터널, Gateway interface와 원격 장비/회선의 장애 도메인을 구분한다. 상태값만으로 전체 SLA 충족이나 무손실 전환을 단정하지 않는다.

VPN 조회 시 `--query`를 제거해 전체 응답을 공유하지 않는다. 원본 응답에는 PSK가 포함된 CustomerGatewayConfiguration이 들어갈 수 있다.

## 실제 허용과 차단 통신

| 시나리오 | 허용 시험 | 차단 시험 | 필수 증거 |
| --- | --- | --- | --- |
| GCP→Account B Backend | 등록된 출발지/서비스 포트로 새 연결과 응답 | 미허용 포트 또는 출발지 | 양방향 Route, 실제 요청/응답, FLOW 및 차단 SID 9000000 |
| ALB VPC→Workload VPC | 서비스 요청과 ALB Health Check | 미허용 Health Check/서비스 포트 | ALB/Backend VPC Route, 대상 상태, 중앙 FLOW/ALERT |
| Account A→Account B | 명시적으로 허용한 private 연결 | 미허용 protocol/port | 두 계정의 반환 Route와 중앙 Endpoint/로그 |
| 인터넷→ALB | ALB listener 요청과 반환 | 미허용 listener/출발지 | IGW Edge/ALB/보조 Firewall RT와 중앙 로그 유입 |

TCP handshake와 실제 데이터/응답, UDP 요청/응답, 허용한 ICMP type의 요청/응답을 각각 확인한다. 허용 목록이 비어 있으면 default deny를 확인한다. 필요할 때 선택적 테스트용 HTTP 서명을 추가하고 새 연결과 후속 패킷에서도 평가되는지 확인한다. HTTPS 암호문에 같은 테스트 문자열을 보낸 결과로 본문 IPS 검사 성공을 주장하지 않는다.

FLOW는 단방향 netflow 형식이다. 요청/응답을 각각 조회하고 flow_id, 시간과 5-tuple을 함께 대조한다. ALERT에는 `event.alert.signature_id`, `event.alert.action`을 사용하고 `event.verdict.action`이 실제 존재하면 함께 확인한다. `allowed` ALERT가 발생할 수 있으므로 ALERT의 존재만으로 차단이라고 판단하지 않는다.

- [ALERT 조회](queries/alerts.cwli): 필요하면 `filter event.alert.signature_id = 9000000`을 추가한다.
- [FLOW 조회](queries/flows.cwli): packet/byte 수와 단방향 흐름을 확인한다.

조회 필드는 원시 스키마에서 확인하고 없는 필드의 값을 추정하지 않는다. CSP/service_id는 별도 CIDR 메타데이터로 보강하는 필드다. ALB 이후 출발지 사설 IP와 X-Forwarded-For의 원본 사용자 IP는 별개다.

## 장애 대응과 롤백

1. 실제 배포 설정과 마지막 검증된 코드/Plan, 변경 시각, 새 연결 여부를 확인한다.
2. RAM/Attachment/Endpoint READY와 양방향 RT를 먼저 비교한다. Endpoint set 순서나 AZ 이름의 계정 간 일치를 가정하지 않는다.
3. VPN 문제는 터널, BGP 학습, 원격 Route와 협상 알고리즘을 분리해 확인한다.
4. Firewall drop은 SID와 verdict를 확인한다. 과도한 전체 허용이나 Spoke 직결 Route를 긴급 우회로 추가하지 않는다.
5. 마지막 검증 설정으로 되돌릴 때도 Plan을 검토하고 승인된 Apply로 복원한다. state 파일만 과거 버전으로 덮어쓰는 것을 인프라 롤백으로 취급하지 않는다.
6. 기존 flowbit/세션 상태가 유지될 수 있으므로 정책 변경 검증은 새 연결로 반복하고 기존 연결의 처리도 따로 확인한다.

## 의존 순서에 맞춰 해체하기

모든 명령은 별도 해체 지시가 있을 때 실행한다. 먼저 로그 및 state 보존 여부와 업무 영향 범위를 정한다. 기본값은 30일 보존 로그 그룹을 남기고 S3 state bucket/version을 유지하는 것이다.

1. 소비자 계정에서 IGW Edge/ALB RT가 보조 Endpoint를 참조하는 경로를 계획된 방식으로 정리한다. 서비스 중단 또는 대체 경로가 준비돼 있어야 한다.
2. 소비자가 보조 Endpoint association을 모두 삭제하고 AWS에서 제거됐는지 확인한다. Firewall RAM 공유 해제만으로 Endpoint가 삭제되지는 않는다.
3. 소비자와 조율해 소비자 state에서 실제 TGW VPC Attachment를 삭제하고 완료를 확인한다. 중앙에서는 해당 계약 입력을 제거하고 refresh 결과 및 Route/RT Association 정리를 검토한다. Provider 6.71.0의 accepter destroy는 실제 Attachment를 삭제하므로 `prevent_destroy=true`로 보호했다. 연결을 유지한 채 소유권만 바꿀 때는 일반 mode/key 변경으로 처리하지 않고 state 백업과 명시적인 관리 이관을 별도로 수행한다.
4. VPN의 원격 광고/연결을 정리한 뒤 중앙 VPN Association/Propagation, AWS VPN과 Customer Gateway를 삭제한다. 상대 GCP/온프레미스 삭제는 해당 담당자가 수행한다.
5. 남은 중앙 CIDR Route를 제거하고 `firewall_protection.delete/subnet/policy=false`를 별도 Plan 및 Apply로 먼저 적용한다. destroy 명령에 변수만 주는 것으로 선행 보호 해제가 완료되지 않는다.
6. Firewall의 logging configuration을 해제하고 모든 보조 Endpoint/Route 참조가 없는지 확인한 후 Firewall과 정책/Rule Group을 삭제한다. Terraform 의존 그래프를 사용하되 외부 state 리소스는 자동 정리되지 않는다.
7. Inspection TGW Attachment, RT, Subnet, VPC, TGW/RAM 공유 등 나머지 중앙 리소스를 의존 순서로 삭제하고 사후 목록으로 잔여 리소스를 확인한다.
8. `retain_logs_on_destroy=true`인 Log Group은 state 관리에서 빠져도 AWS에 남는다. 다시 관리할 때는 import한다. 완전 삭제가 필요하면 별도 보존 결정을 거친다.
9. state bucket과 버전은 이 root module의 관리 대상이 아니다. 백업/복구 및 민감정보 보존 정책을 적용하고 해체 성공과 state 삭제를 같은 작업으로 묶지 않는다.

Terraform destroy 요청 성공만으로 보조 Endpoint, 상대 환경과 로그/state 정리 완료를 기록하지 않는다.
