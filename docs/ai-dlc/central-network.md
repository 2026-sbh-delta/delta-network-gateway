# 중앙 네트워크 AI-DLC

## Ideation

- 문제: 여러 AWS 계정과 GCP, 온프레미스 사이의 등록된 사설 통신을 중앙에서 검사할 기반이 필요하다.
- 사용자: 중앙 네트워크 운영자, 업무 계정 담당자, 원격 네트워크 담당자.
- 성공 기준: 실행 가능한 중앙 Terraform과 연결/운영 문서, 실제 실행한 검증 기록을 제공한다.
- 범위: Inspection VPC, TGW, Network Firewall, RAM 공유, AWS 측 VPN, 중앙 로그.
- Non-goals: 업무 계정 및 원격 리소스 생성, 중앙 인터넷 출구, Organizations 변경, Apply/Destroy.
- 승인: 2026-11-01 사용자 `Ideation 승인`.

## Inception

- 기능: 빈 연결 입력으로 중앙 기반 생성, AZ ID 기준 Endpoint 연결, Spoke/Inspected RT 분리, 명시적 Attachment 수락, Firewall 자체 RAM 공유, 동적 VPN, FLOW/ALERT 로그.
- 비기능: IPv4, 기본 2 AZ, 필수 계정 검증, CIDR/경로 충돌 거부, 기본 차단, 비밀값 출력 금지, 고정 버전과 S3 lockfile.
- 구조: `terraform/network-security` 하나의 root module 및 중앙 state. 업무 계정은 별도 Provider/state.
- 승인: 2026-11-01 사용자의 `PLEASE IMPLEMENT THIS PLAN` 지시. U1~U5에 기재된 Design/Implementation Plan도 같은 지시로 승인.

## Construction 단위

| Unit | Design | Implementation Plan | Approval | Implementation / Test / Review |
| --- | --- | --- | --- | --- |
| U1 | 필수 계정/CIDR/AZ 입력, 고정 버전, 부분 S3 backend | Provider, 입력 검증, 태그, backend 예시 작성 | 2026-11-01 승인 | 구현 완료 / 로컬 검사 통과 / 정적 Review 완료 |
| U2 | AZ별 TGW/Firewall 전용 Subnet, 하나의 TGW와 Firewall | VPC, 명시적 RT Association, appliance mode, 실제 Endpoint 매핑 | 2026-11-01 승인 | 구현 완료 / mock Plan 통과 / 정적 Review 완료 |
| U3 | Spoke에서 Inspection만 경유, Inspected에서 목적지 분기 | 수락 관리 방식 분리, 정적 VPC Route, VPN 전파, 별도 RAM 공유 | 2026-11-01 승인 | 구현 완료 / mock Plan 통과 / 정적 Review 완료 |
| U4 | STRICT_ORDER, flowbits 허용 추적과 명시적 마지막 차단 | 규칙/SID, HOME_NET, FLOW/ALERT와 로그 전달 정책 | 2026-11-01 승인 | 구현 완료 / Terraform 및 mock 검사 통과 / SCP 수정 후 AWS DryRun 2개 통과 |
| U5 | 입력된 VPN만 생성, GCP HA는 2 연결/4 터널 | AWS VPN, 비밀값 없는 출력, 연결/검증/해체 문서 | 2026-11-01 승인 | 구현 완료 / mock Plan 통과 / 정적 Review 완료 |

## Acceptance Criteria

- 기본 구성에서 TGW 1개, Firewall 1개, 2 AZ의 전용 Subnet 4개, 명시적 Association.
- Endpoint computed set의 순서와 무관한 AZ ID별 매핑, 입력 기반 for_each 키.
- 기본 TGW Association/Propagation/자동 수락 비활성화. Spoke 우회 Route 및 중앙 인터넷 기본 경로 없음.
- 잘못된 계정, 중복/범위 밖 CIDR, 잘못된 AZ, 중복 Attachment/Route/SID, VPN 입력 오류 거부.
- fmt/init/validate 및 mock Plan 테스트 실제 실행. 실계정 Plan, Apply, 통신 검증과 구분.
- README의 전체 입력/출력 및 복합 입력 표와 실행/연결/해체 절차 유지.

## Operation

에이전트의 배포는 별도 지시 대상이다. 사용자의 초기 Apply는 RAM Resource Association 두 개에서 실패했으나 후속 Apply가 2 added, 0 changed, 0 destroyed로 완료됐다. 2026-11-01 15:27~15:28 KST 읽기 전용 조회에서 두 Association ASSOCIATED, 두 Share ACTIVE/조직 외 공유 비활성, Firewall/두 AZ Endpoint READY, Firewall IN_SYNC, TGW와 Inspection Attachment available 및 appliance mode enable을 확인했다. FLOW/ALERT 목적지는 CloudWatch Logs로 설정돼 있다. 중앙 단독 기반 배포 완료이며 업무/VPN 연결 입력은 비어 있다. 로그와 state는 기본 보존한다. 실제 로그 유입, Tunnel/BGP, 패킷 처리 및 장애 전환은 미검증이다.

## 후속 멀티클라우드 검증 제안

- 제안 기록: 2026-11-01. 사용자가 AWS를 클라이언트, GCP를 서버로 연결해 ANF 경유와 관제 로그를 검증하는 구성을 제안했다.
- 후보 경로: AWS 업무 VPC의 EC2→TGW Spoke RT→중앙 Inspection VPC의 ANF Endpoint→같은 TGW의 Inspected RT→AWS Site-to-Site VPN→GCP HA VPN→서버 VM의 사설 IP. 응답도 중앙 검사를 거치도록 양방향 Route를 구성한다.
- AWS 측 VPN 코드는 기존 중앙 root의 vpn_networks/vpn_peers를 사용한다. GCP HA 기준 AWS Customer Gateway 2개, VPN 연결 2개, 총 4터널을 대응시키고 Cloud Router는 BGP 경로 교환을 담당한다.
- 새 AWS 클라이언트와 GCP 리소스 생성은 이번 중앙 기반 구현 범위에 포함하지 않았으며 이 후속 제안에서도 아직 실행하지 않았다. 업무 계정/GCP 프로젝트/리전, 기존 VPC 및 VM, 비중복 주소 대역, Gateway IP/ASN은 미확정이다.
- 수용 기준 후보: Tunnel/BGP 정상과 양방향 경로 확인, 허용 포트 성공 및 FLOW 로그, 미허용 포트의 통신 실패와 SID 9000000 차단 결과, 별도 테스트 서명 경보. SG/GCP 방화벽과 서버 리스너는 대조군 트래픽을 수용하도록 준비해 ANF 차단과 구분한다.
- 현재는 연결 방식 제안이며 추가 리소스 구현/Plan/Apply 또는 통신 검증 완료를 뜻하지 않는다.

## 중앙 실행 입력 확정

- 승인: 2026-11-01 사용자 `terraform.tfvars` 작성 요청 및 VPC `/16`, Subnet `/24` 지정.
- `AWS_PROFILE=sbh-delta-security`의 STS 계정은 `482840608926`으로 재확인했다. 기존 로컬 tfvars의 다른 계정 ID는 해당 프로필의 중앙 계정으로 수정했다.
- Inspection VPC는 기존 예시 대역을 확장한 `10.250.0.0/16`으로 지정했다. 중앙 계정 서울 리전에는 조회 시 `172.31.0.0/16` 기본 VPC만 있어 겹치지 않는다. 타 계정/GCP/온프레미스 대역은 이번 확인 범위에 포함하지 않았다.

| 물리 AZ ID | 조회된 계정별 AZ 이름 | TGW Subnet | Firewall Subnet |
| --- | --- | --- | --- |
| `apne2-az1` | `ap-northeast-2a` | `10.250.0.0/24` | `10.250.1.0/24` |
| `apne2-az2` | `ap-northeast-2b` | `10.250.2.0/24` | `10.250.3.0/24` |

- 공유 대상, 업무 Attachment, VPN, 허용 정책은 기존 빈 입력을 유지했다. 중앙 기반만 생성할 설정이며 실제 통신은 기본 차단한다.
- 사용자 작성 `backend.hcl`은 변경하지 않았다. 최초 확인 시 `allowed_account_ids=["437358133988"]`가 실행 계정과 달랐으나, 2026-11-01 15:07 KST 재확인에서 사용자 수정 값 `["482840608926"]`가 실행 계정과 일치했다. 당시 Bucket 입력은 `sbh-delta-central-terraform`이었고, 최종 초기화 메타데이터와 현재 backend.hcl은 `sbh-delta-central-tf`로 일치한다. 사용자의 Apply 성공과 backend 초기화 상태는 확인했으며 Bucket 자체의 별도 보안 설정 감사는 수행하지 않았다.

## 검증 기록

| 날짜 | 명령 또는 확인 | 결과 |
| --- | --- | --- |
| 2026-11-01 | `terraform version` | 로컬 Terraform 1.16.4, darwin_arm64 확인 |
| 2026-11-01 | `aws --version` | AWS CLI 2.36.39 확인 |
| 2026-11-01 | `terraform -chdir=terraform/network-security init -backend=false -input=false -no-color` | Provider 6.71.0 설치 및 HashiCorp 서명 확인, lock 파일 생성. S3 backend는 준비하거나 이전하지 않음 |
| 2026-11-01 | `terraform fmt -check -recursive` | 통과. root와 하위 Terraform/test 파일 형식 확인 |
| 2026-11-01 | `DELTA_KEEP_VALIDATION=1 TERRAFORM_BIN=/opt/homebrew/bin/terraform terraform/network-security/scripts/validate.sh` | 별도 임시 디렉터리에서 `init -backend=false -input=false -lockfile=readonly`, `validate`, mock Plan 테스트 실행. **32 passed, 0 failed**, 종료 코드 0 |
| 2026-11-01 | `terraform test -var-file=terraform.tfvars.example -json` (격리 디렉터리) | 중앙 단독 생성, AZ/Endpoint 매핑, 연결 수락 유지, 경로 분리, GCP 2연결, 32비트 ASN, CIDR/계정/포트/SID/터널/중복 CGW 오류 거부 확인. 모든 run은 mock AWS 및 command=plan |
| 2026-11-01 | `python3 scripts/check_rules_dry_run.py --events <mock-events> --expected-account-id <verified-account> --profile sbh-delta-security --region ap-northeast-2` | STS 계정 확인 후 CreateRuleGroup DryRun 호출. SCP explicit deny로 AccessDeniedException. 규칙 검증 성공 아님, 리소스 생성 없음 |
| 2026-11-01 | `bash -n terraform/network-security/scripts/validate.sh` 및 Python `compile()` | Shell/Python 구문 검사 통과. Python bytecode cache 쓰기 제약은 메모리 내 compile로 회피 |
| 2026-11-01 | 전체 입력/출력 표, 로컬 Markdown 링크, 금지 문자 및 source whitespace 확인 | 입력 20개/출력 16개 모두 README 표에 존재. 로컬 링크와 기존 AGENTS.md 보존 확인 |
| 2026-11-01 14:32 KST | `DELTA_KEEP_VALIDATION=1 DELTA_VERBOSE_PLAN=1 TERRAFORM_BIN=/opt/homebrew/bin/terraform terraform/network-security/scripts/validate.sh` | DryRun 재시도를 위해 최신 규칙을 추출. fmt, 격리된 init/validate, mock Plan **32 passed, 0 failed**, 종료 코드 0 |
| 2026-11-01 14:32 KST | `AWS_PROFILE=sbh-delta-security /usr/bin/python3 terraform/network-security/scripts/check_rules_dry_run.py --events terraform/network-security/.validation/test-events.jsonl --expected-account-id 482840608926 --profile sbh-delta-security --region ap-northeast-2 --aws-bin /opt/homebrew/bin/aws` | 사용자 SCP 수정 안내 후 재시도. STS 중앙 계정 확인 및 `central_only`, `tcp_udp_icmp_and_signature` 두 AWS DryRun 통과, 소비 용량 각각 **1**, **5**, 종료 코드 0. 기존 SCP 거부 재현 없음. 리소스 생성 없음 |
| 2026-11-01 14:50 KST | `AWS_PROFILE=sbh-delta-security aws sts get-caller-identity`, `aws ec2 describe-vpcs --region ap-northeast-2`, `aws ec2 describe-availability-zones --region ap-northeast-2` | 프로필 계정 `482840608926`, 기존 VPC CIDR `172.31.0.0/16`, 사용 가능한 표준 AZ ID 4개 조회. 기존 키인 `apne2-az1`, `apne2-az2` 선택. 모두 읽기 전용 |
| 2026-11-01 14:52 KST | `terraform fmt terraform/network-security/terraform.tfvars`, `terraform fmt -check -recursive`, Python `ipaddress` 검사 | VPC `/16`, Subnet 4개 `/24`, VPC 포함 관계 및 Subnet 상호 비중복, 조회한 기존 중앙 VPC와 비중복, 계정/AZ 일치 확인 |
| 2026-11-01 14:52 KST | 격리 디렉터리 `delta-central-input-validation.54qty3uc`에서 `terraform init -backend=false -input=false -lockfile=readonly -no-color -plugin-dir=<existing-provider-mirror>`, `terraform validate -no-color`, `terraform test -var-file=terraform.tfvars -json` | 새 실제 tfvars로 기존 `central_only` mock Plan만 재사용. Mock 계정 fixture는 확인된 입력 계정과 일치하도록 임시 복사본에서 변경. init/validate 통과, **1 passed, 0 failed**. 실계정 Plan 아님. S3 backend 연결/이전 및 리소스 생성 없음 |
| 2026-11-01 15:00 KST | `git ls-remote --symref origin HEAD refs/heads/main` | SSH 원격 조회 종료 코드 0, 기존 HEAD/main ref 없음 |
| 2026-11-01 15:00 KST | `git commit -m <단위별 메시지>` | 아래 기본 설정/구현/검증 도구 커밋 3개 생성. 기존 SSH 서명 설정 유지 |
| 2026-11-01 15:00 KST | `terraform fmt -check -recursive`, `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --cached --check` | 커밋 검사에서 테스트 파일 2개의 끝 빈 줄을 제거한 뒤 통과. 코드 동작 변경 없음. 이 공백 정리 후 mock Plan은 재실행하지 않음 |
| 2026-11-01 15:00 KST | `git push -u origin main` 실행 요청 | 자동 승인 검토가 코드/인프라 문서 외부 전송 대상의 명시적 확인을 요구해 실행 전 거부. Git 푸시가 실행된 결과가 아니며 원격 전송 없음 |
| 2026-11-01 15:07 KST | `backend.hcl`, `versions.tf`, `.terraform/terraform.tfstate` 존재 확인 및 `terraform init -help` | Bucket과 실행 계정 ID는 로컬 설정에 입력돼 있고, backend 초기화 메타데이터는 없음. `backend "s3" {}`의 부분 설정 파일은 `-backend-config=backend.hcl`로 지정해야 한다는 CLI 및 공식 문서 동작 확인. 설명 문서만 수정했으며 실제 init/Plan/Apply는 실행하지 않음 |
| 2026-11-01 15:19 KST | 사용자 Apply 오류 및 `AWS_PROFILE=sbh-delta-security aws ram get-resource-shares --resource-owner SELF --region ap-northeast-2`, `aws ram get-resource-share-associations --association-type RESOURCE --resource-share-arns <reported-share-arns> --region ap-northeast-2` | TGW/Firewall Share 두 개 모두 ACTIVE, allowExternalPrincipals=false. 리소스 Association 목록은 비어 있음. 사용자 오류는 두 AssociateResourceShare 요청의 Organizations 공유 활성화/온보딩 관련 OperationNotPermittedException |
| 2026-11-01 15:19 KST | 같은 프로필의 `aws network-firewall describe-firewall --firewall-name delta-lab-central --region ap-northeast-2`, `aws ec2 describe-transit-gateways --transit-gateway-ids <reported-tgw-id> --region ap-northeast-2` | Firewall READY/IN_SYNC, ap-northeast-2a 및 2b Endpoint READY, TGW available. 실제 패킷 처리 검증은 아님 |
| 2026-11-01 15:19 KST | 같은 프로필의 `aws organizations describe-organization --region ap-northeast-2` | AccessDeniedException. 현재 프로필에서 Organizations 공유 활성화 상태나 관리 계정을 확정하지 못함. AWS RAM 공식 문서의 관리 계정 활성화 절차와 서비스 연결 역할 요건을 확인했고 Organizations 변경 및 Apply 재실행은 하지 않음 |
| 2026-11-01 15:23 KST | `sbh-delta-security`의 STS 계정 재확인, `organizations describe-organization`, `ram get-resource-shares`, `ram get-resource-share-associations`, `cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=AssociateResourceShare --max-results 10` | 중앙 계정 일치. Share 두 개 ACTIVE/STANDARD, allowExternalPrincipals=false, Resource Association 없음. CloudTrail에서 사용자 재시도 요청 두 건이 15:22:12 KST에 OperationNotPermittedException으로 거부된 것을 확인. 조직 조회는 계속 AccessDeniedException. CloudTrail에 상세 errorMessage가 없어 원인을 추가 확정하지 못함 |
| 2026-11-01 15:25 KST | 사용자 답변 및 `AWS_PROFILE=default aws sts get-caller-identity --region ap-northeast-2` | 사용자는 활성화 위치가 관리 계정의 AWS RAM Settings라고 확인. 관리 계정 진단용 후보 default 프로필의 STS 조회는 세션 만료로 실패. 계정 확인 이전이므로 해당 프로필의 Organizations/IAM 조회를 수행하지 않았고 인증 갱신을 요청함 |
| 2026-11-01 15:27 KST | 사용자 제공 `tf apply` 로그 | 전체 Refresh 후 Plan 2 to add, 0 to change, 0 to destroy. 사용자가 승인해 TGW/Firewall RAM Resource Association 두 개가 생성됐으며 Apply complete, 2 added, 0 changed, 0 destroyed. 모든 중앙 출력 제공. 에이전트가 실행한 Apply가 아님 |
| 2026-11-01 15:27 KST | `sbh-delta-security`의 STS 및 RAM Association, Network Firewall DescribeFirewall/DescribeLoggingConfiguration, EC2 DescribeTransitGateways/DescribeTransitGatewayVpcAttachments 읽기 전용 조회 | 두 Association ASSOCIATED, Firewall READY/IN_SYNC, 두 Endpoint READY, TGW/Inspection Attachment available, appliance mode enable, FLOW/ALERT 목적지 설정 확인. 실제 로그 유입과 패킷 통신 검증은 아님 |
| 2026-11-01 15:28 KST | 같은 프로필의 `aws ram get-resource-shares --resource-owner SELF --resource-share-arns <reported-share-arns> --region ap-northeast-2` 및 로컬 `.terraform/terraform.tfstate`의 backend 필드 확인 | 두 Share ACTIVE, allowExternalPrincipals=false 유지. 초기화 backend는 S3의 `sbh-delta-central-tf`, encrypt/use_lockfile=true. 현재 backend.hcl의 bucket/key/region과 일치 |
| 2026-11-01 15:36 KST | `sbh-delta-security`의 STS 확인 후 EC2 DescribeTransitGatewayVpcAttachments, DescribeSubnets, DescribeRouteTables, GetTransitGatewayRouteTableAssociations, SearchTransitGatewayRoutes 읽기 전용 조회 | Inspection Attachment 하나가 TGW 전용 Subnet `10.250.0.0/24`, `10.250.2.0/24`를 사용하고 Inspected RT에 associated. Firewall Subnet은 각각 `10.250.1.0/24`, `10.250.3.0/24`. 명시적으로 연결한 VPC RT 4개는 local 경로만 있고 두 TGW RT의 active/blackhole Route는 비어 있음. 업무/VPN 입력이 비어 있는 중앙 단독 기반 상태와 일치 |
| 2026-11-01 15:51~15:53 KST | `git ls-remote origin refs/heads/main`, `git diff --check`, 변경 문서의 로컬 링크/금지 문자/일반 자격 증명 패턴 검사, `git check-ignore` | 원격 main은 `a1d32ec7090d94f83bb64cfc147b3beaf785f288`로 기존 로컬 4개 커밋과 일치. 문서 검사 통과. 실제 tfvars/backend 및 검증 산출물 제외 확인. 이번 변경은 문서뿐이므로 Terraform 검증은 재실행하지 않음 |
| 2026-11-01 15:53 KST | `git add -- terraform/network-security/README.md terraform/network-security/RUNBOOK.md`, `git diff --cached --check`, `git commit -m 'docs: clarify backend initialization and RAM troubleshooting'` | staged 공백 검사 통과. backend/RAM 안내 문서 2개를 서명된 커밋 `f7b6244`로 기록 |
| 2026-11-01 15:53~15:54 KST | `git diff --check`, `git diff --cached --check`, `git commit -m 'docs: record deployment verification and AWS GCP test proposal'`, `git push origin main`, `git ls-remote origin refs/heads/main` | 문서 검사와 커밋 성공. `a1d32ec..9252a76 main -> main` 푸시 종료 코드 0. 원격 main은 `9252a766a1bc4763d248659cf36df7a13fe969e3`로 로컬 검증 체크포인트와 일치. Terraform/AWS 리소스 변경은 실행하지 않음 |

검증 로그는 로컬의 `terraform/network-security/.validation/test-events.jsonl`에 보관하며 커밋 대상에서 제외했다. 재시도는 격리 디렉터리 `delta-network-validation.vEh4aL`의 해당 이벤트 파일을 사용했고, 위 명령은 보관한 동일 파일 경로로 표기했다. AWS DryRun의 실제 실행 명령과 결과는 같은 디렉터리의 `aws-rule-group-dry-run.txt`에 보관했다. 검증 스크립트로 재생성할 수 있다. 초기 mock fixture의 Plan 시점 override와 ARN 형식을 수정한 뒤 전체 검사를 재실행했다. Attachment 소유 계정 검사는 TGW 생성 의존성과 분리해 Plan에서 오류를 거부하도록 보완했다.

중앙 입력 조회 결과는 `.validation/central-input-inventory.json`, 실제 입력 검사 명령과 종료 코드는 `.validation/central-input-validation.json`, 해당 mock Plan 로그는 `.validation/central-input-test-events.jsonl`에 보관했다. Backend 파일은 검사 전후 SHA-256 일치를 확인했다. 실제 tfvars와 backend 설정 및 검증 파일은 gitignore 대상이다.

RAM 오류 진단과 당시 실제 리소스 상태는 `.validation/ram-sharing-diagnosis.json`에 보관했다. 최초 조회에서는 Organizations 공유 미활성화와 활성화 진행 중을 구분할 권한이 없었다. 이후 사용자의 재시도와 두 Association의 실제 성공 확인으로 해당 오류 진단을 종료했다.

활성화 후 재진단은 `.validation/ram-sharing-recheck.json`, 진단용 후보 프로필의 인증 실패는 `.validation/ram-management-diagnosis.json`, 성공 후 실제 상태는 `.validation/central-deployment-status.json`에 보관했다. 후속 로컬 설정 확인에서 default 프로필은 대상 관리 계정과 다른 계정 설정이었다. 해당 프로필 재로그인 요청은 불필요해 철회했고, Organizations/IAM 조회나 인증 갱신은 수행하지 않았다. 활성화 후 동일 Share/리소스 연결이 성공해 전파 지연 가능성이 있으나 AWS 내부 원인을 확정한 것은 아니다. 중앙 사용자의 조직 조회 거부를 RAM 실패의 직접 원인으로 판정하지 않는다.

실제 TGW/Attachment/Subnet/Route Table 연결 조회는 `.validation/tgw-subnet-topology.json`에 보관했다. Endpoint와 Attachment의 READY/available 상태가 목적지별 검사 Route 설정 또는 실제 통신 성공을 뜻하지 않는다. 목적지 CIDR을 입력한 후 생성되는 검사 Route와 왕복 통신은 별도로 확인한다.

## 버전 관리

- 승인: 2026-11-01 사용자 `커밋 단위 쪼개서 커밋 푸시` 지시.
- 후속 승인: 2026-11-01 사용자 `일단 지금까지 내용 commit push` 지시. 이미 안내한 아래 origin/main을 대상으로 후속 문서 변경을 커밋하고 푸시한다.
- 원격: `git@github.com:2026-sbh-delta/delta-network-gateway.git`, 브랜치 `main`.

| 단위 | 커밋 | 메시지 |
| --- | --- | --- |
| 저장소 기본 설정 | `3468a9d` | `chore: initialize repository conventions` |
| 중앙 Terraform 구현 | `84a832a` | `feat: implement central network security Terraform stack` |
| 검증 도구와 mock Plan | `e735170` | `test: add mock plans and AWS rule dry-run validation` |
| 연결/운영/진행 문서 | `a1d32ec` | `docs: add integration runbooks and implementation records` |
| backend/RAM 운영 안내 | `f7b6244` | `docs: clarify backend initialization and RAM troubleshooting` |
| 배포 검증 및 멀티클라우드 제안 | `9252a76` | `docs: record deployment verification and AWS GCP test proposal` |

실제 tfvars/backend 설정, state/Plan, `.validation`, `.terraform`는 포함하지 않는다. 에이전트의 초기 푸시 요청은 자동 승인 검토에서 실행 전 차단됐다. 이후 원격 main의 기존 `a1d32ec` 반영을 확인했고 사용자의 후속 지시에 따라 같은 origin/main에 문서 커밋 두 개를 정상 푸시했다. 2026-11-01 15:53~15:54 KST 읽기 전용 원격 조회에서 검증 체크포인트 `9252a76` 일치를 확인했다. 이 문서 갱신은 해당 완료 결과를 기록한다. 차단된 푸시를 다른 경로로 전송하지 않았다.

## 정적 Review 결과

- 중앙 단독 mock Plan에서 TGW 1개, Firewall 1개, VPC 1개, Subnet 4개, Subnet RT/명시적 Association 각 4개를 확인했다. NAT/IGW/Public ALB/GWLB 및 기본 인터넷 Route를 선언하지 않았다.
- Primary Endpoint는 AZ ID와 API AZ 이름 및 실제 Subnet ID를 대응시킨다. 누락되거나 두 AZ가 같은 Endpoint ID를 반환하면 거부한다. computed set의 인덱스를 AZ 식별에 사용하지 않는다.
- Spoke의 등록 CIDR Route는 Inspection Attachment만 가리킨다. VPC 정적 목적지 Route와 VPN Propagation은 Inspected에만 구성한다.
- accepter destroy가 실제 소비자 Attachment를 삭제하는 Provider 동작을 확인해 `prevent_destroy=true`를 추가했다. 수락 상태 갱신으로 리소스 주소를 제거하지 않는다.
- 추가 규칙, 비종료형 허용 표시, 마지막 차단 순서를 유지한다. pass와 TLS 복호화, 기본 IPS 서명 집합은 없다. Terraform 구문 검사를 Suricata 실제 패킷 검증으로 취급하지 않는다.
- VPN PSK/native XML은 일반 출력에서 제외했다. state/Plan에는 민감정보가 포함될 수 있다.

## 실행 준비 및 미실행 상태

- 실제 `terraform.tfvars`는 작성하고 입력 검사를 완료했다. 사용자 수정 backend 설정의 계정 ID도 실행 계정과 일치한다.
- 에이전트는 실제 S3 backend init 및 실계정 Plan/Apply를 실행하지 않았다. 사용자 제공 마지막 Plan/Apply 성공 로그와 초기화 backend 메타데이터, 실제 AWS 리소스 상태로 중앙 단독 기반 배포 완료를 확인했다.
- 공유 계정, 업무 VPC Attachment, VPN Gateway/ASN/Inside CIDR, 실제 허용 목록은 아직 연결되지 않았다. 기본 입력은 빈 상태다.
- AWS DryRun은 SCP 수정 후 두 규칙 집합 모두 통과했다. 이는 AWS 규칙 요청 검증 결과이며, 전체 배포 권한 또는 실제 Suricata 패킷 처리의 증거로 취급하지 않는다. 이 작업에서 Organizations/SCP는 변경하지 않았다.
- 초기 구현 4개와 후속 문서 커밋 `f7b6244`, `9252a76`의 원격 반영을 확인했다. 이 기록은 원격 조회로 확인한 검증 체크포인트 `9252a76`을 기준으로 한다.
- 사용자 Apply로 중앙 단독 기반과 RAM Resource Association 두 개가 완료됐다. 실제 상태는 읽기 전용으로 확인했다. 에이전트의 Apply/Destroy 및 실제 통신 검증은 수행하지 않았다.
