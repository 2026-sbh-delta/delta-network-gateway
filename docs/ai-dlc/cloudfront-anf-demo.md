# CloudFront 웹에서 중앙 ANF 허용과 차단 시험

2026-11-05 후속: 사용자 승인으로 시연용 CloudFront와 ALB/S3 및 두 EC2를 삭제하고 워크로드 네트워크를 통합했다. 아래는 과거 검사 시연의 증거이며 현재 자원이나 실행 안내가 아니다. 실제 제거 결과는 [워크로드 통합 기록](aws-workload-consolidation.md)을 따른다.

2026-11-03 후속 작업: 중앙 ANF 비용 중단과 실제 원복을 검증한 뒤 최종 Firewall/엔드포인트를 제거했다. 현재는 직접 TGW 경로로 두 API가 HTTP 200을 응답한다. 아래는 2026-11-01 검사 모드의 실제 결과다. 현재 경로와 새 검증 상태는 [ANF 비용 절감](anf-cost-mode.md)을 따른다.

## 승인과 현재 상태

- 기준일: 2026-11-01 (Asia/Seoul).
- 사용자 `그러면 internal ALB로 해줘. Cloudfront는 간단한 웹페이지 하나만 띄우고 버튼 하나 누르면 ANF allow돼서 정상 응답 받는거, 다른 버튼은 deny된 결과 보여주는 정도` 지시를 Ideation/Inception과 아래 Unit의 Design/Implementation Plan/실제 배포 및 검증 승인으로 기록한다.
- 기존 직접 EC2 VPC Origin은 서버 연결만 확인했고 ANF를 경유하지 않았다. 이번 구성으로 교체하며 과거 결과는 [직접 Origin 기록](cloudfront-server.md)에 보존한다.
- 현재: 코드/웹/운영 문서 구현, mock21개/중앙 회귀35개, AWS 규칙 DryRun, 실제 배포와 HTTP/ANF 로그/최종 보안 검증 완료. 이전 직접 Origin/Server IGW/직접 SG rule과 임시 ALB ingress4개 정리 완료. 실제 검증 JSON 게시와 네 root 최종 Plan No changes 확인. 브라우저 허용 버튼200은 확인했고 차단 버튼 화면 재확인은 UI 도구 오류로 미검증이다. 전체 `terraform destroy`는 미실행이다. 후속 사용자 지시로 구현/검증/운영 문서를 분할 커밋하고 origin/main 게시 성공을 확인했다.

## Ideation

- 문제: 브라우저의 버튼 두 개로 동일 사설 서버에 대해 중앙 ANF 허용과 차단을 실제 비교해야 한다.
- 사용자: 델타 팀 네트워크/관제 시험 사용자.
- 성공 기준: 정적 웹 로드, 허용 버튼 HTTP 200/실제 서버 JSON/양방향 FLOW와 마커 ALERT, 차단 버튼 실패/실제 ANF blocked-drop SID 9000000, 두 Target Group healthy와 서버 양쪽 리스너/SG 수용 확인.
- Scope: 기존 CloudFront 도메인/state 재사용, private S3/OAC의 작은 웹페이지, Client VPC internal ALB와 전용 /24 Subnet/RT, ALB VPC Origin, 중앙 정책과 Server ingress, 실제 HTTP/로그/브라우저/최종 Plan.
- Non-goals: 새 EC2/VPC, 서버 또는 ALB의 공인 주소, NAT/인터넷 기본 Route, 서버 앱 교체, 새 IPS 서명/Managed Rule Group, WAF/인증 서비스, GCP/VPN/온프레미스, 기존 Client/Inspection 삭제.

## Inception

### Architecture / Functional Requirements

- 정적 파일: `브라우저 → CloudFront HTTPS → private S3 (OAC)`.
- API: `브라우저 → CloudFront /api/* → Client VPC internal ALB VPC Origin → TGW Spoke → 중앙 ANF → TGW Inspected → Server VPC EC2`.
- 기존 Workload 616723161368 / 중앙 482840608926 / 서울을 검증한다. 기존 Client VPC 10.10/16과 Server VPC 10.20/16, 중앙 TGW/Inspection 경로를 재사용한다.
- Client VPC에 전용 ALB Subnet 10.10.4/24, 10.10.5/24와 Server /16→TGW Route를 추가한다. 중복/기존 사용 여부와 실제 AZ ID를 검증한다. 기존 Server의 Client /16 반환 Route와 중앙 /16 검사/반환 Route를 유지한다.
- ALB의 /api/allow 경로는 Server 8080, /api/deny 경로는 Server 8081로 전달한다. Source는 ALB 전용 /24들이다. 중앙은 해당 Source→Server /32 TCP 8080만 SID 1000002로 허용하고 8081은 기존 SID 9000000으로 차단한다.
- Server SG와 ALB egress는 두 시험 포트를 모두 수용한다. **두 Target Group Health Check는 모두 8080**으로 수행해 denied Target Group도 healthy 상태에서 실제 8081 요청을 ANF에 보낸다. Unhealthy target/SG 실패를 ANF 차단으로 오인하지 않는다.
- ALB 요청/응답은 TGW/Inspection 대칭 경로를 사용하며 같은 VPC local, Peering 또는 직접 Propagation 우회를 추가하지 않는다.
- Origin HTTP 80→ALB→8080/8081로 검사 가능한 HTTP를 사용한다. Viewer는 기본 CloudFront 인증서의 HTTPS다. API/오류 캐시를 끄고 query ID를 전달한다.
- 웹은 정상 응답과 실제 HTTP 오류/소요 시간을 표시한다. HTTP timeout/504만으로 ANF 원인을 확정하지 않는다. 배포 검증에서 수집한 실제 SID/action/시간을 별도 검증 기록으로 표시한다.

### 비기능 / Migration

- Terraform 1.16.4 / AWS Provider 6.71.0 / 기존 lock과 별도 CloudFront state를 유지한다. 입력/출력 및 map 내부 속성 표를 갱신한다.
- Client VPC에 CloudFront 필수 IGW를 연결하지만 인터넷 Route는 생성하지 않는다. 초기 CloudFront prefix list ingress는 서비스 SG rule을 확인한 뒤 제거한다.
- 기존 직접 Server Origin/SG/Server IGW는 새 ALB 경로와 Distribution 전환을 확인한 뒤, 이 root가 소유한 리소스만 순차 정리한다. AWS 서비스 SG/ENI 자체를 임의로 삭제하지 않는다.
- private S3의 암호화/Public Access Block/소유권과 Distribution SourceArn에 한정한 OAC 읽기만 허용한다. Access key/PSK/state를 정적 웹에 올리지 않는다.
- ALB/S3/CloudFront/TGW/ANF/로그 사용 비용이 발생한다. 단일 서버 시험이며 부하/장애 전환/인증 API 운영은 범위 밖이다.

### Unit별 Construction

| Unit | Design | Implementation Plan | Approval | 상태 |
| --- | --- | --- | --- | --- |
| U1. 검사 경로 | Client VPC의 internal ALB, dedicated /24, 명시적 TGW 경로 | 입력/실제 VPC/CIDR/AZ/TGW 검증, ALB/두 TG/Health Check 8080, SG/중앙 허용 SID 1000002 | 2026-11-01 internal ALB와 두 버튼 지시 | 구현/실제 배포/HTTP/로그 검증 및 Review 완료 |
| U2. CloudFront 웹 | private S3 정적 웹 + ALB VPC Origin /api 분기 | 두 버튼/실제 결과/검증 기록, S3/OAC/PAB/HTTPS/캐시 없음, 기존 도메인 전환 | 같은 지시 범위 | 구현/배포/웹200/허용 버튼200 확인. 차단 버튼 UI 재확인은 미검증 |
| U3. 검증/이전 정리 | mock/실제 HTTP/로그/브라우저/Route를 구분 | 격리 검증, 저장 Plan/DryRun/Apply, healthy/리스너 대조, 양방향 FLOW/ALERT, 직접 Origin과 임시 ingress 정리, 최종 Plan/문서 | 같은 지시 범위 | 실제 HTTP/로그/최종 보안/네 Plan Review 완료. UI 제약은 아래 기록 |

## Acceptance Criteria / Operation

1. 격리 fmt/init/validate/mock 및 기존 중앙 회귀 검사. ALB internal/두 포트/두 TG healthy용 8080 Health Check, API 경로, S3 private/OAC, 소스 및 Route 우회 부재 검증.
2. 실제 저장 Plan에서 계정/타입/변경/삭제 대상을 검사하고 중앙 규칙 DryRun 후 적용한다. 기존 EC2/Client/Inspection 교체나 state 이전은 하지 않는다.
3. ALB active/두 TG healthy, Origin/Distribution Deployed 후 허용 200/마커 ALERT/FLOW, denied HTTP 실패/실제 blocked/drop SID를 요청 시간/IP/port로 대조한다.
4. 브라우저에서 두 버튼을 실제 클릭하고 정상/차단 대조군 결과 및 오류 처리/레이아웃을 확인한다. 타임아웃을 확인된 ANF 차단으로 단독 표시하지 않는다.
5. 전환 완료 후 직접 EC2 Origin/이 root 소유의 직접 SG/Server IGW와 임시 bootstrap ingress만 정리한다. 최종 CloudFront/중앙/기존 Server Plan과 실제 공인 주소/인터넷 Route 부재를 확인한다.
6. 전체 해체는 미실행. 롤백은 검토한 Distribution/정책 변경과 소유권을 기준으로 하며 기존 서버/중앙 기반/state/로그는 보존한다.

## 실제 확인 기록

| 날짜 | 명령/확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-01 | 기존 코드/문서, AWS VPC Origin/ALB Health Check/CloudFront 오류 캐시 문서 확인 | 직접 Origin은 ANF 우회임을 재확인. 두 TG 모두 health port 8080과 API 오류 캐시 없음으로 실제 denied 포트 요청을 검사하도록 설계 |
| 2026-11-01 | `terraform/cloudfront-server/scripts/validate.sh` | fmt/backend=false init/validate 및 AWS mock Plan 21개 통과. 최초 computed Origin ID mock 미확정/중복 override/계정 테스트 값 충돌을 수정하고 전체 재실행 |
| 2026-11-01 | `terraform/network-security/scripts/validate.sh` | ALB CIDR/8080 허용과 우회 부재 사례 포함 35개 통과 |
| 2026-11-01 | 두 profile STS 및 실제 저장 `terraform plan`, JSON 변경 목록 검토 | 중앙 Rule Group 1개 in-place 갱신. Workload 35개 생성/2개 갱신/삭제 없음. 기존 EC2/Client/Inspection 교체 없음 |
| 2026-11-01 | `aws network-firewall create-rule-group --cli-input-json ...` (DryRun=true) | ALB SID1000002 포함 실제 규칙 수락, 소비 용량4. 리소스 생성 없음 |
| 2026-11-01 | 중앙 검토한 저장 Plan `terraform apply` | Rule Group 1개 갱신 성공. 기존 Client 허용/마커/최종 drop 보존, 8081 허용 없음 |
| 2026-11-01 20:29~20:34 KST | EIC SSH localhost HTTP, ELBv2 DescribeTargetHealth, Network Firewall DescribeFirewall/DescribeLoggingConfiguration | 서버 두 포트 모두200/delta-test active. internal ALB active, allow8080/deny8081 두 Target Group 모두 healthy/HealthCheckPort8080. Firewall/두 Endpoint READY/IN_SYNC, FLOW/ALERT 그룹 유지 |
| 2026-11-01 20:36 KST | FLOW/ALERT 두 그룹 Logs Insights 사전 조회 | ALB source10.10.5.90→서버8080 요청/응답의 동일 flow_id/AZ FLOW 실제 확인. 외부 버튼 시험과 구분 |
| 2026-11-01 20:40 KST | CloudFront Origin 생성/서비스 SG 조회/Distribution update | 새 ALB Origin `vo_F2fqx6DGY0HDGpcXhiDOZf` 생성 완료(8m19s). Client VPC AWS 서비스 SG `sg-027180d5c4cae2162` source로 ALB80 ingress 추가. 기존 Distribution `E3H298XCGG4TSD` 경로 전환 진행 |
| 2026-11-01 20:41 KST | Workload 저장 Plan `apply`, 실제 AWS API audit | 35개 생성/2개 갱신/삭제 없음으로 배포 완료. Distribution과 ALB Origin Deployed, private S3/PAB/OAC/암호화, ALB active/internal, 두 TG healthy/HealthCheckPort8080, Server 공인 주소/IPv6 및 양 VPC 인터넷 Route 없음. 중앙 왕복 Route/Firewall READY 확인 |
| 2026-11-01 20:41~20:44 KST | 실제 HTTPS GET + FLOW/ALERT 두 그룹 Logs Insights, probe/tuple/flow_id/AZ 대조 | 웹200, 허용200(0.053s)/query 반영, 차단504(10.035s). 허용 source10.10.5.90:27326→10.20.1.10:8080, flow_id65312734599782/ap-northeast-2b, 마커SID2000001 allowed/alert와 동일 AZ 양방향 FLOW. 차단 source10.10.5.90:51326→서버8081, flow_id270068931440658/ap-northeast-2a, 요청 구간의 SID9000000 blocked/drop4건(재전송 포함) |
| 2026-11-01 20:42 KST | Chrome 실제 배포 페이지 로드/허용 버튼 클릭 | 두 버튼 페이지와 실제 서버 JSON/HTTP200/0.19s 표시 확인. 이 버튼 query도 SID2000001 allowed/alert와 대조. 이후 UI 도구 timeout/ScreenCapture 오류/사용자 탭 전환으로 차단 버튼 화면 확인은 재시도 중이며 PASS로 기록하지 않음 |
| 2026-11-01 20:44~20:45 KST | 정리 저장 Plan/JSON 검토 및 apply 착수 | 삭제 대상은 이전 직접 EC2 Origin/Server IGW/직접 서비스 SG ingress와 ALB prefix-list ingress 정확히4개. 두 SG rule 제거 완료, 직접 Origin 삭제 진행. 새 ALB/S3/서버/중앙 리소스 삭제/교체 없음 |
| 2026-11-01 20:45 KST | 임시 ingress 제거 후 실제 HTTPS 및 익명 S3 GET | 웹200, 허용200(0.051s), 차단504(10.095s), S3 직접 익명403. 최종 규칙의 별도 실제 요청 로그는 전달 대기 중 |
| 2026-11-01 20:45 KST | 직접 Origin을 제거한 최종 코드 `scripts/validate.sh` | backend=false init/validate/fmt 및 새 경로 mock21개 모두 다시 통과. 리소스 타입/개수로 직접 EC2 Origin과 우회 Route 생성 부재 검사 |
| 2026-11-01 20:47 KST | 사설 EIC SSH/localhost HTTP 및 원본 앱 journal 필드 확인 | service active/두 포트200. ALB source8080 요청366건, ALB8081 도달 없음. 기존 앱은 client/port/method만 기록하므로 path/query journal 대조는 불가능했다. 앱을 변경하지 않고 HTTP 응답과 ANF 마커로 고유 query를 대조하도록 Runbook 수정 |
| 2026-11-01 20:52 KST | 최종 HTTP의 원본 ALERT 직접 검색 및 FLOW/ALERT 통합 조회, probe/tuple/flow_id/AZ 대조 | 최종 허용 source10.10.5.90:60482→서버8080, flow_id2039707214325772/ap-northeast-2a, SID2000001 allowed/alert와 동일 AZ 양방향 FLOW. 차단 source10.10.5.90:8046→서버8081, flow_id2222212331863314/ap-northeast-2b, 실제10초 구간의 SID9000000 blocked/drop4건. 원본 메시지 IP 기준 통합 조회로 검증했으며 필드/정규식 조회 결과 차이의 원인은 확정하지 않음 |
| 2026-11-01 20:52 KST | 정리 저장 Plan `apply` 및 AWS API 최종 audit | 정확히4개 삭제 성공. 기존 직접 Origin(7m03s 후 삭제), Server IGW와 직접 source SG rule 없음. Client IGW만 연결, 양 VPC 인터넷 Route/서버 공인 주소/IPv6 없음. ALB active/internal, 두 TG healthy, 서비스 SG source80/Server 양 포트 CIDR ingress, S3 private/OAC, ANF READY/IN_SYNC 및 왕복 Route 확인 |
| 2026-11-01 20:50~20:54 KST | 중앙/Client/Server 및 CloudFront 네 root `plan -input=false -detailed-exitcode` | 네 root 모두 No changes, 각각 종료 코드0. CloudFront 최종 관리 리소스37개. 서버/Client 교체 없음 |
| 2026-11-01 20:53~20:54 KST | verification.json 저장 Plan/JSON 검토/Apply, HTTPS 재조회 및 AWS ListVpcOrigins | S3 객체1개만 in-place 갱신. 공개 JSON과 실제 검증/로컬 파일 일치, status=verified. 새 ALB Origin 존재/이전 직접 Origin 부재 재확인. 비밀값/Viewer IP/원본 로그/계정 ARN은 검증 JSON에 게시하지 않음 |
| 2026-11-01 20:54 KST | `terraform fmt -check -recursive terraform`, `git diff --check`, 입력/출력 표/로컬 링크/ignore 확인 | fmt와 diff 검사 통과. 14입력/17출력 및 map 내부 설명 유지. 실제 tfvars/backend/state/Plan/캐시/원본 증거는 Git 제외 |

## Review와 남은 검증

- 승인한 검사 경로로 교체했고 실제 정상 응답과 default-deny를 HTTP, 고유 query, 원본 ALERT, 양방향 FLOW 및 실제 Route/SG 상태로 검증했다. SG와 두 healthy Target Group을 대조군으로 유지했다.
- 브라우저 페이지 로드/레이아웃/허용 버튼200은 실제 확인했다. 차단 버튼의 완료 화면과 최종 검증 문구 재확인은 CUA browser host unavailable, ScreenCaptureKit 오류, timeout 및 사용자 탭 전환 감지로 완료하지 못했다. 이 항목은 PASS가 아니다. 실제 API504와 ANF blocked/drop, 게시된 JSON의 HTTPS 일치는 별도 검증 완료다.
- 단일 서버/평문 HTTP 검사 데모다. 멀티클라우드/VPN, 인증 API 운영, TLS 복호화/새 IPS, 부하/장애 전환은 미검증이며 전체 해체는 수행하지 않았으며 커밋/푸시 결과는 아래 별도 기록을 따른다.

## 근거

- [CloudFront VPC Origin](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html)
- [ALB Health Check](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html)
- [CloudFront 오류 캐시](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/DownloadDistValuesErrorPages.html)

## Git 게시

2026-11-01 사용자 `커밋 단위 쪼개서 커밋 푸시 ㄱㄱ` 지시로 현재 변경의 분할 커밋과 origin/main 게시를 승인받았다. 구현(필수 웹 파일/입력/출력 문서 포함), 회귀 테스트, 운영/배포 기록 순서로 나눈다. 실제 입력/backend/state/Plan/원본 로그/자격 증명/캐시는 제외하며 기존 SSH 서명을 유지한다. 세 커밋의 게시와 원격 일치를 실제 확인했으며 이번 문서 갱신은 그 결과를 기록한다.

| 구분 | 커밋 | 내용 |
| --- | --- | --- |
| 구현 | `5c807f58` | CloudFront/ALB/S3, 두 버튼 웹, 입력/출력 계약과 lock |
| 검증 | `1801463e` | 격리 mock21개와 중앙 회귀35개, Route/SG/OAC/입력 오류 검사 |
| 운영 문서 | `12587b42` | 실제 HTTP/ANF 로그/전환 정리와 기존 Client/Server 상태 갱신 |
| 날짜 | 명령/확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-01 20:59 KST | `git ls-remote --heads origin main`, 변경 파일/ignore/자격 증명 패턴 검사, `terraform fmt -check -recursive terraform`, `git diff --check` | 원격 기준점 a452ad3a 확인. 변경26개에 자격 증명 패턴/민감 실행 파일 없음. 실제 tfvars/backend/state/Plan/원본 증거 제외. fmt/공백 검사 통과 |
| 2026-11-01 20:59 KST | 두 root의 `scripts/validate.sh` | CloudFront 격리 init/validate/mock21개 및 중앙 회귀35개 모두 다시 통과. 실제 Plan/Apply/Destroy는 이번 게시 작업에서 재실행하지 않음 |
| 2026-11-01 20:59 KST | 명시적 경로 `git add`, 기존 SSH 서명 `git commit` | 구현5c807f58/검증1801463e 커밋 완료. 운영 문서 커밋과 푸시는 대기 |
| 2026-11-01 21:00 KST | `git cat-file -p`, `git status --porcelain`, `git push origin main`, `git ls-remote --heads origin main` | 세 커밋 모두 기존 SSH 서명 존재 확인. push 성공(a452ad3a→12587b42), 원격 main이 로컬 `12587b42600ba05fe3aa6564e427cd52edd4f6c6`와 일치. 당시 작업 트리 깨끗함. 이번 문서 갱신은 해당 게시 결과를 기록 |
