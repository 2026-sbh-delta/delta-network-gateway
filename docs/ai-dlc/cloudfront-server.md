# CloudFront에서 사설 EC2 직접 호출

> 과거 직접 Origin 시험 기록이다. 사용자 후속 지시로 [internal ALB와 중앙 ANF를 경유하는 웹](cloudfront-anf-demo.md)으로 전환했다. 이 문서의 직접 경로/리소스/검증 시각은 당시 상태이며 현재 구성은 후속 문서를 따른다.


## 단계와 승인 상태

- 기준일: 2026-11-01 (Asia/Seoul).
- 승인: 사용자 `아 그럼 이전 방식으로 하자` 이후 `ALB말고 VPC Origin 붙여서 하는 거`로 직접 EC2 VPC Origin 방식을 확정했다. 앞서 설명한 Server VPC의 IGW 연결, 사설 Origin, HTTPS CloudFront 주소와 서버 응답 확인을 구현/배포 범위로 기록한다.
- Ideation / Inception / 아래 Unit의 Design 및 Implementation Plan: 위 지시로 승인. ALB 안은 구현하지 않는다.
- 현재: U1~U3 구현/Test/Review와 실제 배포/HTTPS/최종 보안 검증 완료. 격리 init/validate/fmt 및 mock Plan 16개 통과, 실제 초기 7 add와 임시 ingress 1개 제거를 검토 후 적용했다. 최종 관리 리소스 6개다.
- VPC Origin/Distribution 모두 Deployed다. SG 제한 전후 HTTPS 총 네 요청 HTTP 200/실제 앱 JSON/각 query 반영/Miss from cloudfront, HTTP HEAD 301→HTTPS를 확인했다. CloudFront/기존 Server 두 root 최종 Plan은 모두 No changes/상세 종료 코드 0이다.
- URL: https://d228fpdue5s0by.cloudfront.net/health
- 기존 EC2/앱/Route/중앙 ANF는 교체하거나 변경하지 않았다. Server VPC IGW 연결과 별도 state 소유 SG rule만 추가했다. 커밋/푸시와 `terraform destroy`는 미실행이며 임시 bootstrap SG rule 1개의 제거 Apply는 별도 기록한다.

## Ideation

- 문제: 사설 서버의 기존 앱을 인터넷 사용자가 CloudFront HTTPS 주소로 호출하는 작은 시험이 필요하다.
- 사용자: 델타 네트워크 운영자와 시험 사용자.
- 성공 기준: CloudFront HTTPS `/health`에서 기존 EC2의 cloud=aws/service=delta-private-server JSON을 받는다. 캐시를 끄고 Origin 요청을 확인한다.
- Scope: Workload 계정의 별도 root/state, 기존 Server VPC/EC2 재사용, IGW 연결, CloudFront EC2 VPC Origin/Distribution, Origin SG 제한, 실행/관측/롤백 문서.
- Non-goals: ALB/NLB, S3 정적 웹, 브라우저의 서버 IP 직접 API 호출, 새 EC2/VPC, 서버 공인 IP/IPv6/NAT/인터넷 기본 Route, 새 IPS 서명, 중앙 ANF 또는 TGW 변경, TLS 복호화, Destroy.
- 직접 VPC Origin 경로는 Server VPC 안에서 끝나며 기존 중앙 ANF를 경유하지 않는다. 이 시험 성공을 인터넷 ANF 검사 성공으로 기록하지 않는다.

## Inception

### 요구사항과 Architecture

`인터넷 사용자 → CloudFront HTTPS → VPC Origin 서비스 ENI → 기존 사설 EC2 HTTP 8080`

- 기존 Workload 616723161368 / 서울 ap-northeast-2 / Server VPC vpc-0dd4ef153e66803ab / EC2 i-03d824c2a456e71ad / 10.20.1.10을 실제 조회로 재검증한다.
- CloudFront 기본 도메인/인증서를 사용하고 HTTP viewer는 HTTPS로 전환한다. Origin은 HTTP 8080이며 현재 앱을 교체하거나 재설치하지 않는다.
- CloudFront 요구사항에 따라 기존 Server VPC에 IGW를 연결하되 인터넷 Route를 추가하지 않는다. Server의 공인 주소 비노출과 기존 EIC 관리 경로를 유지한다.
- 초기 Origin 생성은 CloudFront 관리형 prefix list의 TCP 8080 ingress로 준비한다. Origin 생성 후 자동 생성된 CloudFront 서비스 SG를 참조하는 ingress를 추가하고 bootstrap prefix list 규칙은 별도 검토 Plan으로 제거한다. 서비스 SG 자체는 수정/생성하지 않는다.
- 기존 Server SG에는 별도 state가 소유하는 새로운 SG rule만 추가한다. 기존 Server root의 인라인 규칙과 중복 관리하지 않는다.
- `terraform/cloudfront-server` 별도 S3 key `delta/lab/cloudfront-server/terraform.tfstate`를 사용한다. 기존 backend/state를 이전하지 않는다.
- Terraform 1.16.4 / AWS Provider 6.71.0과 lock을 고정한다. 계정/Region/리소스 소유자/VPC/EC2 사설 IP/SG/상태를 검증한다.
- cache TTL 0과 query string 전달로 요청을 Origin에 전달한다. default certificate, IPv4 viewer, GET/HEAD/OPTIONS만 사용한다. 실제 사용자 인증과 대규모 서비스 운영은 범위 밖이다.

### Unit별 Design / Implementation Plan / Approval

| Unit | Design | Implementation Plan | Approval | 상태 |
| --- | --- | --- | --- | --- |
| U1. 기반과 Origin | 기존 EC2/VPC/SG 검증, 별도 state, IGW와 단계별 SG | 읽기 전용 inventory, 필수 입력/contract, IGW/EC2 VPC Origin 및 bootstrap/서비스 SG rule | 2026-11-01 직접 VPC Origin 확정 지시 | 구현/Test/Review/실제 Apply 완료. Origin Deployed 및 임시 ingress 제거/서비스 SG만 유지 확인 |
| U2. HTTPS 배포 | CloudFront 기본 URL/인증서, 캐시 없음 | Distribution/Origin 연결, 8080 HTTP, query 전달, HTTPS redirect | 같은 지시 범위 | 구현/Test/Review/실제 배포 완료. Distribution Deployed와 HTTPS 200/캐시 없음/쿼리 전달/301 확인 |
| U3. 검증/운영 | mock과 실제 검증 분리 | 격리 init/validate/mock, 저장 Plan 검토/Apply, Origin/Distribution Deployed, HTTP와 공인 비노출/SG/Route 대조, 최종 Plan, Runbook | 같은 지시 범위 | 격리 mock 16 passed, 실제 HTTP/SG/주소/Route audit, EIC 앱 로그 및 두 root No changes 확인. Review 완료 |

### Acceptance Criteria

1. fmt/격리 init/validate와 AWS mock Plan에서 원본 계정/VPC/EC2/SG 불일치, 잘못된 주소, 인터넷 Route 부재, 캐시/HTTPS와 단계별 SG를 검증한다.
2. 실제 Plan은 추가 리소스/삭제/교체/계정과 원본 참조를 확인한 뒤 적용한다. 새 state key의 명확한 부재와 기존 bucket 보안 조건을 확인한다.
3. EC2 running/private IP/공인 주소 부재, Origin/Distribution Deployed, SG 최종 제한을 확인한다.
4. CloudFront HTTPS `/health`와 서로 다른 query string의 응답, HTTP→HTTPS redirect를 확인한다. 기존 앱의 실제 path JSON과 CloudFront 응답 헤더를 기록한다.
5. 기존 TGW/ANF 경로와 별개의 시험임을 기록하며 관련 세 root의 최종 Plan을 필요 범위에서 확인한다.

## Operation

- 캐시/CloudFront 상태는 API 및 실제 HTTP로 확인한다. 서버 앱 로그는 EIC SSH의 `journalctl -u delta-test`에서 확인하며 ANF 로그 유입을 성공 조건으로 삼지 않는다.
- 비용: CloudFront 요청/데이터 전송과 기존 EC2/디스크, 선택적 관측 비용을 확인한다. ALB/NLB 비용은 추가하지 않는다.
- 롤백은 CloudFront 비활성화/배포 완료, Distribution 연결 해제/삭제, VPC Origin 삭제, 추가 SG rule/IGW 순서로 의존 관계를 확인한다. 기존 Server/Client/Inspection과 state/로그는 보존한다. 실제 삭제는 이번 작업에 포함하지 않는다.

## 실제 확인 기록

| 날짜 | 명령/확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-01 19:54 KST | AWS 공식 VPC Origin 문서, 고정 Provider 6.71.0 문서와 현재 Terraform 코드 확인 | EC2 직접 Origin, EC2 ARN/사설 DNS, VPC IGW 필수이나 Origin용 인터넷 Route 불필요, 서비스 SG 제한 및 Provider 지원 확인 |
| 2026-11-01 19:54~19:55 KST | 두 프로필 `aws sts get-caller-identity` | Workload SSO 세션 만료/refresh 실패. 중앙 482840608926 확인. 로그인 갱신 요청. AWS 변경 없음 |
| 2026-11-01 19:57~19:58 KST | 로그인 갱신 후 Workload STS/DescribeInstances/DescribeSecurityGroups/DescribeRouteTables/DescribeInternetGateways와 S3 조회 | Workload 616723161368, 기존 EC2 running/10.20.1.10/사설 DNS/공인 IPv4 없음, IPv6 없음과 기존 Client/EIC SG 유지 확인. IGW/CloudFront 서비스 SG 없음, 인터넷 기본 Route 없음. bucket versioning Enabled/AES256/PAB 네 항목 true, 신규 state key는 명확한 404 |
| 2026-11-01 19:58~20:00 KST | 격리 schema, `terraform fmt`, `DELTA_KEEP_VALIDATION=1 scripts/validate.sh` | schema 명령은 backend 포함 시 초기화 요구 및 sandbox의 Provider 로컬 소켓 제한으로 재시도. 격리 schema에서 backend 제거/권한 허용 후 실제 6.71.0 스키마 확인. mock의 생성 후 SG 조회는 Plan에서 ID를 확정할 수 없어 접근 범위/포트 검증으로 수정. 최종 격리 init/validate/fmt 및 mock **16 passed, 0 failed**, 7개 기반 리소스 타입/개수 확인. 실제 AWS 생성 없음 |
| 2026-11-01 20:00 KST | 입력/출력/링크/권한 검사와 실제 `init -backend-config=backend.hcl -lockfile=readonly` | 입력 10개/출력 11개 표와 로컬 링크/금지 문자/공백 검사 통과. 실제 tfvars/backend 0600/Git 제외. 새 S3 key 초기화 완료, 기존 state 이전 없음. 초기 설치에서 인증한 lock과 이미 보유한 Provider binary를 재사용했으며 이번 plugin-dir 설치 자체는 서명 재검증하지 않음 |
| 2026-11-01 20:00~20:01 KST | 새 root 저장 `plan -out=.validation/bootstrap.tfplan`, `show -json`의 타입/개수/Origin/SG/HTTPS/cache 검사, 검토한 Plan의 `apply` 착수 | **7 add, 0 change, 0 destroy**. EC2/VPC/Route/ALB/NLB 생성 또는 교체 없음. 예상 EC2 ARN/사설 DNS/8080 직접 Origin, 비 CIDR SG source/HTTPS redirect/CachingDisabled 확인. IGW `igw-08217672ad8553a05`와 임시 SG rule/요청 정책 생성, Origin 배포 진행 |
| 2026-11-01 20:02 KST | 기존 Server root `plan -detailed-exitcode`, 임시 키/EIC SSH의 systemd/ss/localhost HTTP | Server **No changes, 종료 코드 0**. SSH 종료 코드 0, delta-test active, 8080/8081 모두 HTTP 200과 기존 앱 JSON. 기존 검증 증거는 덮어쓰지 않고 새 root ignored `.validation/`에 보관. 임시 키 삭제 |
| 2026-11-01 20:02 KST | `aws cloudfront list-vpc-origins` | `vo_BJoF7SSYZt8C8dV2g8Citg` Deploying. 아직 실제 CloudFront 응답 성공으로 기록하지 않음 |
| 2026-11-01 20:05~20:11 KST | Origin/서비스 SG/ENI API 조회, 실제 Apply 진행 상태 | AWS 서비스 SG `sg-017cef8ca33feaeff`와 사설 ENI `eni-08c2cbba83eaaa32b`/10.20.1.121/공인 주소 없음 확인. Origin은 약 **9분 23초** 후 생성 완료, 서비스 SG source의 Server TCP 8080 ingress `sgr-0f8289f7e6c1a5be7` 추가. Distribution 생성 착수 |
| 2026-11-01 20:11~20:14 KST | Distribution API/Apply 진행 확인 | API 응답 필드명을 VpcOriginConfig로 맞춰 수집 도구를 수정. Distribution `E3H298XCGG4TSD`는 약 **2분 37초** 후 생성 완료. 초기 Apply **7 added, 0 changed, 0 destroyed** |
| 2026-11-01 20:14 KST | 실제 공개 HTTPS 두 GET/HTTP HEAD와 JSON/헤더 확인 | 초기 로컬 Python CA bundle 부재로 인증서 확인 실패. `/etc/ssl/cert.pem`을 명시하고 인증서/hostname 검증을 유지해 재실행. 두 요청 모두 **HTTP 200**, 기존 앱 cloud/service/port/각 query의 path 일치, **Miss from cloudfront**, ICN53-P1, HTTP HEAD **301→HTTPS**. TLS 검증 우회 없음 |
| 2026-11-01 20:14~20:15 KST | bootstrap=false 저장 Plan/JSON 검토와 Apply | **0 add, 0 change, 1 destroy**이며 임시 prefix list ingress `sgr-0d79b3f6a2045116b`만 제거됨을 검사. 검토한 Plan 적용 후 서비스 SG/Origin/Distribution/IGW 유지. 최종 root 관리 리소스 6개 |
| 2026-11-01 20:15 KST | 임시 ingress 제거 후 공개 HTTPS/redirect 재검증 | 서로 다른 query 두 요청 **HTTP 200**, 실제 앱 JSON/path와 **Miss from cloudfront**, HTTP HEAD **301→HTTPS**. 서비스 SG만 허용한 상태의 실제 연결 검증 완료 |
| 2026-11-01 20:15~20:16 KST | Origin/Distribution/EC2/Subnet/SG/IGW/Route API audit, 최종 두 root Plan, EIC 앱 로그 | Origin/Distribution **Deployed**, 서버 running/10.20.1.10/공인 IPv4 및 IPv6 없음. 최종 ingress는 기존 EIC SSH/Client 두 포트와 새 서비스 SG의 8080뿐이며 prefix list 없음/서버 egress 없음. Server VPC 5개 RT 모두 인터넷 기본/IGW Route 없음. 새 CloudFront root와 기존 Server root **No changes/상세 종료 코드 0**. EIC SSH 종료 코드 0/delta-test active, 시험 시각의 CloudFront origin-facing 주소에서 들어온 8080 GET 로그 확인 |

## 배포 결과와 Review

| 항목 | 실제값/결과 |
| --- | --- |
| 공개 시험 URL | https://d228fpdue5s0by.cloudfront.net/health |
| Distribution | `E3H298XCGG4TSD`, Deployed |
| EC2 VPC Origin | `vo_BJoF7SSYZt8C8dV2g8Citg`, Deployed |
| 기존 Server | `i-03d824c2a456e71ad`, `10.20.1.10:8080`, 교체/앱 변경 없음 |
| IGW | `igw-08217672ad8553a05`, Server VPC에 연결. 인터넷 Route 없음 |
| 최종 CloudFront source | AWS 서비스 SG `sg-017cef8ca33feaeff`, TCP 8080 |
| 초기/제한 후 HTTP | 두 단계 각각 두 GET HTTP 200/쿼리 반영/캐시 미사용, HEAD 301 |
| 최종 Plan | CloudFront/Server 두 root 모두 No changes |
| Git | 이번 CloudFront 변경은 미커밋/미푸시 |

실제 증거는 새 root의 ignored `.validation/`에 bootstrap/harden Plan JSON과 Apply 기록, `http-bootstrap.json`, `http-hardened.json`, `final-audit.json`, `app-preflight.txt`, `app-final.txt`, 두 최종 Plan으로 보관한다. 실제 tfvars/backend와 증거/캐시는 Git에 포함하지 않는다.

- 직접 EC2 VPC Origin 요구를 구현했고 ALB/NLB/S3/새 EC2/VPC/서버 공인 주소/인터넷 기본 Route를 생성하지 않았다. 중앙/Client 입력과 state를 수정하지 않았다.
- bootstrap 제거 후 실제 HTTPS 성공과 정확한 SG tuple/Route/주소 audit를 대조해 최종 제한 상태의 연결을 확인했다.
- 앱 로그의 source는 실제 CloudFront origin-facing IP였다. 서비스 ENI의 사설 주소나 원래 viewer IP로 단정하지 않는다. 원래 viewer IP 헤더는 이번 앱이 기록하지 않는다.
- 기존 중앙 ANF를 거치지 않는 직접 경로다. 이번 결과로 인터넷 IPS 탐지/차단을 검증했다고 기록하지 않는다. 실제 브라우저 화면 조작, 인증 API/장시간 부하/장애 전환은 미검증이다.

## 근거

- [CloudFront VPC Origin](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html)
- [고정 Provider의 VPC Origin](https://github.com/hashicorp/terraform-provider-aws/blob/v6.71.0/website/docs/r/cloudfront_vpc_origin.html.markdown)
- [고정 Provider의 Distribution](https://github.com/hashicorp/terraform-provider-aws/blob/v6.71.0/website/docs/r/cloudfront_distribution.html.markdown)
