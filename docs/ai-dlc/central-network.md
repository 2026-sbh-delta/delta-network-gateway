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

배포는 별도 지시 대상이다. Runbook에 Firewall/Endpoint READY, RAM/Attachment, BGP/Tunnel, 실제 경로와 통신/SID 로그, 롤백 및 의존 순서에 따른 해체를 제공했다. 로그와 state는 기본 보존한다. 실제 리소스 배포, Endpoint READY, Tunnel/BGP, 로그 전달, 패킷 처리 및 장애 전환은 모두 미검증이다.

## 중앙 실행 입력 확정

- 승인: 2026-11-01 사용자 `terraform.tfvars` 작성 요청 및 VPC `/16`, Subnet `/24` 지정.
- `AWS_PROFILE=sbh-delta-security`의 STS 계정은 `482840608926`으로 재확인했다. 기존 로컬 tfvars의 다른 계정 ID는 해당 프로필의 중앙 계정으로 수정했다.
- Inspection VPC는 기존 예시 대역을 확장한 `10.250.0.0/16`으로 지정했다. 중앙 계정 서울 리전에는 조회 시 `172.31.0.0/16` 기본 VPC만 있어 겹치지 않는다. 타 계정/GCP/온프레미스 대역은 이번 확인 범위에 포함하지 않았다.

| 물리 AZ ID | 조회된 계정별 AZ 이름 | TGW Subnet | Firewall Subnet |
| --- | --- | --- | --- |
| `apne2-az1` | `ap-northeast-2a` | `10.250.0.0/24` | `10.250.1.0/24` |
| `apne2-az2` | `ap-northeast-2b` | `10.250.2.0/24` | `10.250.3.0/24` |

- 공유 대상, 업무 Attachment, VPN, 허용 정책은 기존 빈 입력을 유지했다. 중앙 기반만 생성할 설정이며 실제 통신은 기본 차단한다.
- 사용자 작성 `backend.hcl`은 변경하지 않았다. 현재 `allowed_account_ids=["437358133988"]`가 실행 계정과 달라 backend 초기화 전에 정합성을 맞춰야 한다. Bucket의 존재/소유 계정/접근 권한은 미확인이다.

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

검증 로그는 로컬의 `terraform/network-security/.validation/test-events.jsonl`에 보관하며 커밋 대상에서 제외했다. 재시도는 격리 디렉터리 `delta-network-validation.vEh4aL`의 해당 이벤트 파일을 사용했고, 위 명령은 보관한 동일 파일 경로로 표기했다. AWS DryRun의 실제 실행 명령과 결과는 같은 디렉터리의 `aws-rule-group-dry-run.txt`에 보관했다. 검증 스크립트로 재생성할 수 있다. 초기 mock fixture의 Plan 시점 override와 ARN 형식을 수정한 뒤 전체 검사를 재실행했다. Attachment 소유 계정 검사는 TGW 생성 의존성과 분리해 Plan에서 오류를 거부하도록 보완했다.

중앙 입력 조회 결과는 `.validation/central-input-inventory.json`, 실제 입력 검사 명령과 종료 코드는 `.validation/central-input-validation.json`, 해당 mock Plan 로그는 `.validation/central-input-test-events.jsonl`에 보관했다. Backend 파일은 검사 전후 SHA-256 일치를 확인했다. 실제 tfvars와 backend 설정 및 검증 파일은 gitignore 대상이다.

## 버전 관리

- 승인: 2026-11-01 사용자 `커밋 단위 쪼개서 커밋 푸시` 지시.
- 원격: `git@github.com:2026-sbh-delta/delta-network-gateway.git`, 브랜치 `main`.

| 단위 | 커밋 | 메시지 |
| --- | --- | --- |
| 저장소 기본 설정 | `3468a9d` | `chore: initialize repository conventions` |
| 중앙 Terraform 구현 | `84a832a` | `feat: implement central network security Terraform stack` |
| 검증 도구와 mock Plan | `e735170` | `test: add mock plans and AWS rule dry-run validation` |
| 연결/운영/진행 문서 | 이 문서를 포함하는 docs 커밋 | `docs: add integration runbooks and implementation records` |

실제 tfvars/backend 설정, state/Plan, `.validation`, `.terraform`는 포함하지 않는다. 푸시는 자동 승인 검토의 원격 대상 확인 요구로 대기 중이다. 승인 전 다른 경로를 이용해 전송하지 않는다.

## 정적 Review 결과

- 중앙 단독 mock Plan에서 TGW 1개, Firewall 1개, VPC 1개, Subnet 4개, Subnet RT/명시적 Association 각 4개를 확인했다. NAT/IGW/Public ALB/GWLB 및 기본 인터넷 Route를 선언하지 않았다.
- Primary Endpoint는 AZ ID와 API AZ 이름 및 실제 Subnet ID를 대응시킨다. 누락되거나 두 AZ가 같은 Endpoint ID를 반환하면 거부한다. computed set의 인덱스를 AZ 식별에 사용하지 않는다.
- Spoke의 등록 CIDR Route는 Inspection Attachment만 가리킨다. VPC 정적 목적지 Route와 VPN Propagation은 Inspected에만 구성한다.
- accepter destroy가 실제 소비자 Attachment를 삭제하는 Provider 동작을 확인해 `prevent_destroy=true`를 추가했다. 수락 상태 갱신으로 리소스 주소를 제거하지 않는다.
- 추가 규칙, 비종료형 허용 표시, 마지막 차단 순서를 유지한다. pass와 TLS 복호화, 기본 IPS 서명 집합은 없다. Terraform 구문 검사를 Suricata 실제 패킷 검증으로 취급하지 않는다.
- VPN PSK/native XML은 일반 출력에서 제외했다. state/Plan에는 민감정보가 포함될 수 있다.

## 실행 준비 및 미실행 상태

- 실제 `terraform.tfvars`는 작성하고 입력 검사를 완료했다. 사용자 작성 backend 설정의 계정 불일치는 남아 있다.
- 실제 S3 backend 준비 상태/접근 권한 확인, backend init 및 실계정 `terraform plan -out=central.tfplan`은 미실행이다.
- 공유 계정, 업무 VPC Attachment, VPN Gateway/ASN/Inside CIDR, 실제 허용 목록은 아직 연결되지 않았다. 기본 입력은 빈 상태다.
- AWS DryRun은 SCP 수정 후 두 규칙 집합 모두 통과했다. 이는 AWS 규칙 요청 검증 결과이며, 전체 배포 권한 또는 실제 Suricata 패킷 처리의 증거로 취급하지 않는다. 이 작업에서 Organizations/SCP는 변경하지 않았다.
- 로컬 코드는 단위별 커밋했고 운영 문서는 이번 docs 커밋에 포함한다. 원격 푸시는 자동 승인 검토에 의해 실행 전 차단됐다.
- Apply, Destroy, 실제 AWS 배포/통신 검증은 수행하지 않았다.
