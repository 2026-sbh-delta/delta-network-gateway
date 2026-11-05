# ANF 비용 절감과 검사 경로 원복

2026-11-04 후속: 새 GCP 작업에서 사용자가 ANF 원복 금지와 VPN 연결을 승인했다. disabled/bypass에서 VPN을 Spoke RT에 BGP로 전파하도록 구현하고 실제 연결을 적용했다. 아래의 2026-11-03 VPC 전용 제약과 실제 전환 검증은 당시 기록이며, 새 VPN 코드/검증/배포 상태는 [GCP AI-DLC](gcp-central-network.md)를 따른다. 이번 작업에서 ANF는 재생성하지 않았다.

## 승인과 현재 상태

- 기준일: 2026-11-03 (Asia/Seoul).
- 사용자가 통신을 유지하면서 ANF 비용을 줄이고 원복할 수 있는 구성을 요청했다. 사전 설명에서 TGW 직접 경로로 우회한 뒤 ANF만 제거하고, 정책과 서브넷을 보존해 다시 생성하는 방식을 제시했다.
- 사용자 `일단 그렇게 해줘`를 아래 Ideation, Inception, U1 Design/Implementation Plan과 해당 범위의 실제 전환 승인으로 기록한다. 당시 커밋과 푸시는 포함하지 않았다. 이후 사용자 `pull받고 커밋 푸시 ㄱㄱ` 지시로 최신 main 반영과 분할 커밋/푸시를 승인받았다.
- 현재: **최종 `disabled` 적용 및 실제 검증 완료**. Firewall 부재와 엔드포인트 0개, 기존 ALB/Client의 8080/8081 HTTP 200, 기존 사설 서버/네트워크/정책/로그 보존, 최종 Plan No changes를 확인했다. 실제 ANF 재생성과 검사 경로/삭제 보호 원복, 허용/차단/양방향 로그도 검증한 뒤 다시 비용 중단으로 마무리했다.
- 로컬 실행 입력: ignored `terraform/network-security/inspection-mode.auto.tfvars`에 `inspection_mode = "disabled"`를 저장했다. 이 파일을 지우면 기본 enabled로 돌아갈 수 있으므로 비용 중단 중에는 보존한다.
- 검증: 표준 명령에서 Python helper **11 passed**, Terraform mock **41 passed**. 실제 전환과 별개인 로컬 검사 결과다.
- Git: 최신 origin/main `22b91803`의 33개 커밋을 fast-forward로 pull하고 ANF 변경을 복원했다. 충돌 없이 문서 인덱스를 자동 병합했으며 기존 원격 변경을 보존했다. pull 후 Python 11개와 Terraform mock 41개를 다시 통과했다. 구현/테스트/운영 문서 3개 서명 커밋을 origin/main에 게시했고 원격 HEAD 일치를 확인했다. 이 게시 확인 기록은 별도 후속 문서 커밋으로 저장한다.

## Ideation

- 문제: 사용하지 않는 시간에도 2 AZ의 ANF 엔드포인트 시간 요금이 발생한다.
- 사용자: Delta 팀의 중앙 네트워크 운영자와 데모 사용자.
- 성공 기준: 사설 서버와 기존 웹 API 통신을 유지하고 ANF와 그 엔드포인트만 제거한다. 이후 원래 규칙으로 재생성하고 검사 경로를 복구할 수 있다.
- Scope: 중앙 Terraform에 검사, 우회 준비, 비용 중단 모드를 추가한다. 기존 정책, 규칙 집합, 로그 그룹, VPC, 서브넷, TGW, Attachment, RAM Share는 보존한다. 실제 전환은 저장 Plan 검토와 통신 검증을 거친다.
- Non-goals: EC2/ALB/CloudFront 교체, 공인 노출, SG 변경, 전체 해체, VPN 우회 지원, 새로운 보안 정책 또는 데모 화면 개편.

## Inception

### Functional Requirements / Architecture

- `inspection_mode=enabled`(기본): 기존 TGW Spoke -> Inspection VPC -> ANF -> TGW Inspected 경로.
- `inspection_mode=bypass`: ANF를 유지하면서 Spoke RT의 등록 VPC 목적지를 해당 VPC Attachment로 직접 전달한다. 기존 RT Association과 VPC Route는 유지한다.
- `inspection_mode=disabled`: 우회 경로를 유지하고 Firewall, 엔드포인트 참조 Route, Firewall 로그 연결과 RAM Resource Association만 제거한다.
- 원복은 disabled -> bypass(ANF 재생성/READY 확인) -> enabled(양방향 검사 경로 복원) 순서다. 중지 전환은 enabled -> bypass(통신 확인/삭제 보호 해제) -> disabled 순서다.
- 현재 연결은 AWS Client와 Server 두 VPC뿐이다. VPN이 등록된 상태의 bypass/disabled는 사전 검증으로 거부한다.

### Non-Functional Requirements

- 기존 Firewall/로그 연결에 `moved` 블록을 사용해 count 도입만으로 교체하지 않는다.
- 수락 관리 리소스의 `prevent_destroy`와 상태 소유권은 유지한다. Provider 6.71.0은 TGW Route의 다음 홉 변경을 ForceNew로 처리한다. 실제 전환은 저장 Plan을 먼저 검토한 뒤 AWS `replace-transit-gateway-route`로 같은 목적지의 다음 홉을 갱신하고, 새 Plan으로 state를 대조해 불필요한 삭제/재생성을 피한다.
- 실제 입력과 Plan/원본 증거는 ignored 파일 또는 권한을 제한한 임시 디렉터리에 둔다. 인증정보는 출력하거나 파일에 보관하지 않는다.
- 기본값은 기존 검사 동작을 유지한다. 현재 배포 입력에는 비용 중단 상태를 명시적으로 저장해 후속 Plan이 ANF를 다시 만들지 않도록 한다.
- 로그 그룹과 정책은 state에서 계속 관리한다. 기존 로그는 설정된 보존 기간을 따른다.

### Unit of Work / Acceptance Criteria

| Unit | Design | Implementation Plan | Approval | 상태 |
| --- | --- | --- | --- | --- |
| U1 | 3단계 모드와 직접 TGW 경로, 기존 기반 보존 | 조건부 Firewall/로그/공유/Route, moved/출력/검증, README/Runbook, mock 회귀, 저장 Plan, 실제 우회/제거/통신 확인 | 2026-11-03 사용자 실행 지시 | 구현/로컬 테스트/실제 제거/원복/최종 disabled/Review 완료 |

- 기존 enabled 회귀와 bypass/disabled의 다음 홉, 엔드포인트 부재, 보존 리소스, 부적절한 모드/VPN 입력 거부를 실제 로컬 테스트로 검증한다.
- 실행 전 enabled 기준 Plan에서 기존 리소스 교체/삭제가 없는지 확인한다.
- bypass 적용 후 기존 HTTPS API의 8080/8081 응답과 양쪽 TGW 목적지를 확인한 다음 ANF를 제거한다.
- disabled 최종 Plan No changes, Firewall 부재와 이전 엔드포인트 부재, 기존 VPC/TGW/정책/로그 보존 및 API 통신을 확인한다.
- 실제 원복을 수행한 경우에만 원복 검증 완료로 기록한다. 모드 구현과 mock 성공만으로 실제 원복을 성공으로 기록하지 않는다.

## Operation

- 최종 상태: 2026-11-03 17:08 KST 실제 `disabled`, Firewall 없음, VPC Endpoint 0개, 두 등록 TGW 목적지는 각각 해당 VPC Attachment를 가리키며 active다.
- 통신: CloudFront 웹 200, ALB의 8080/8081 모두 서버 HTTP 200, Client EC2의 8080/8081 모두 서버 HTTP 200. 우회 중에는 ANF 차단과 신규 검사 로그가 없고 기존 deny 시험도 성공한다.
- 보존: 원래 정책/규칙 각 1개, FLOW/ALERT 두 로그 그룹과 30일 보존, 서브넷 4개, TGW Attachment 3개 available/associated, 기존 EC2 IDs/사설 IP/공인 IP와 IPv6 부재를 확인했다. 전체 teardown은 수행하지 않았다.
- 원복: 실제 재생성 준비 Apply는 400.261초, HTTP 허용/차단 대조 완료까지 약 8분28초였다. 이번 실측이며 다음 실행의 보장 시간은 아니다. 원복 명령은 [중앙 Runbook](../../terraform/network-security/RUNBOOK.md#anf-비용-중단과-원복)을 따른다.
- 비용: 엔드포인트의 기본 시간 요금과 ANF 처리료가 발생하는 리소스를 제거했다. TGW/ALB/EC2와 로그 보관 등 다른 비용은 남는다. 실제 청구액이나 Cost Explorer 반영은 별도로 확인하지 않았다.

- Observability: 단계별 AWS Firewall/Endpoint 상태, TGW의 양방향 목적지와 HTTP 응답을 기록한다. 우회 중의 8081 성공은 예상 동작이며 ANF 차단 증거로 사용하지 않는다.
- Rollback: bypass에서 통신이 실패하면 기존 Firewall이 READY인 상태로 enabled를 재적용한다. disabled 이후에는 bypass로 ANF를 재생성하고 READY/IN_SYNC 확인 후 enabled로 전환한다.
- Runbook: 중앙 모듈 Runbook에 중지와 원복 명령, 보호 플래그, 로그/공유 및 endpoint ID 재생성 주의점을 기록한다.

## 검증 기록

| 날짜 | 명령 또는 확인 | 결과 |
| --- | --- | --- |
| 2026-11-03 | `aws sso login --profile sbh-delta-security --use-device-code --no-browser` | 만료된 세션 재인증 완료 |
| 2026-11-03 | 보안 프로필 STS와 `network-firewall describe-firewall --firewall-name delta-lab-central` | 중앙 계정 482840608926, Firewall READY/IN_SYNC, 두 AZ Endpoint READY, 삭제 보호 true, 보조 Endpoint Association 0개 확인 |
| 2026-11-03 | Provider 6.71.0의 `transitgateway_route.go` 확인 | Attachment 다음 홉은 ForceNew다. AWS의 ReplaceTransitGatewayRoute API로 먼저 다음 홉을 갱신하고 새 Plan으로 대조하는 운영 순서를 사용한다 |
| 2026-11-03 | `terraform/network-security/scripts/validate.sh` | 샌드박스의 Provider 시작 실패 후 정상 실행 환경에서 재검증. 초회 40 pass/1 fail의 컬렉션 타입 비교식을 수정한 최종 실행은 fmt/init/validate와 mock **41 passed, 0 failed** |
| 2026-11-03 | `python3 -m unittest discover -s terraform/network-security/scripts -p 'test_replace_inspection_routes.py' -v` | **11 passed**. 양방향 교체/원복, 다른 계정과 stale Plan 거부, 부분 실패 시 이전 다음 홉 복구 확인 |
| 2026-11-03 | enabled 기준 `terraform plan`/저장 Plan `apply` | 0 add/0 change/0 destroy. Firewall과 로그 연결의 주소 이동 및 새 출력만 기존 state에 기록 |
| 2026-11-03 16:40 KST | 업무 계정 STS, EC2 조회와 실제 HTTPS 기준 요청 | 업무 계정 616723161368, 기존 Client/Server running, 공인 IP/IPv6 없음. 웹 200, 허용 200(0.144s), 차단 504(10.146s) |
| 2026-11-03 | bypass 저장 Plan | 기존 Spoke RT의 두 CIDR 다음 홉과 Firewall 삭제 보호만 변경. Provider가 표시한 두 Route 교체는 후속 helper와 새 Plan으로 제거했다(아래 16:42 실행 기록) |
| 2026-11-03 16:42 KST | `replace_inspection_routes.py ... --execute`, 새 bypass Plan/Apply와 실제 HTTPS/AWS snapshot | 같은 Spoke RT의 두 다음 홉을 각 VPC Attachment로 교체. 새 Plan/Apply는 0 add/1 change/0 destroy로 삭제 보호만 해제했다. 웹/8080/8081 모두 HTTP 200, 두 경로 active, 같은 Client/Server ID와 사설 주소/공인 주소 부재 유지 |
| 2026-11-03 16:43 KST | disabled 저장 Plan과 JSON 주소/액션 대조 | 정확히 Firewall/로그 연결/Firewall RAM Resource Association 각 1개와 to_firewall Route 4개만 삭제. 0 add/0 change/7 destroy, 다른 기반/정책/로그 그룹 변경 없음 |
| 2026-11-03 16:45 KST | 기존 EIC Endpoint/임시 키/SSH 터널로 Client에서 Server에 HTTP 요청 | Client 10.10.1.143 -> Server 10.20.1.10의 8080 HTTP 200(0.008s), 8081 HTTP 200(0.004s), 실제 서버 JSON과 고유 probe 확인. 공인 경로와 장기 키를 추가하지 않음 |
| 2026-11-03 16:47 KST | disabled 저장 Plan `apply`와 실제 AWS/HTTPS snapshot | 0 add/0 change/7 destroy 완료. Firewall 삭제 3m14s. Firewall ResourceNotFound와 VPC Endpoint 목록 0개, 같은 두 다음 홉 active, 웹/8080/8081 모두 HTTP 200, 같은 Client/Server ID/사설 주소와 공인 IP/IPv6 부재 확인 |
| 2026-11-03 16:47 KST | 정책/규칙/로그/서브넷/Attachment 읽기 전용 조회 | 기존 정책/규칙, FLOW/ALERT 두 로그 그룹과 30일 보존, 기존 서브넷 4개, available/associated인 TGW Attachment 3개 보존 확인 |
| 2026-11-03 | disabled -> bypass 원복 준비 저장 Plan | 정확히 기존 ANF 관련 7개만 생성, 다른 리소스 변경/삭제 없음. 아래 실제 Apply와 통신/로그 대조로 원복 검증 완료 |
| 2026-11-03 16:55 KST | restore-ready 저장 Plan `apply` 및 AWS 상태/로그 연결 | 7 add/0 change/0 destroy. Firewall 생성 6m36s, 전체 준비 Apply 400.261s. 같은 정책/서브넷, 새 엔드포인트 2개 READY/IN_SYNC, FLOW/ALERT 연결과 RAM Resource Association 복원 |
| 2026-11-03 16:56~16:57 KST | helper로 두 다음 홉 복원, 새 enabled Plan/Apply, 실제 ALB/Client HTTP | 새 Plan/Apply 0 add/1 change/0 destroy로 삭제 보호 true 복원. 웹 200, ALB 8080 HTTP 200/8081 HTTP 504(10.070s), Client 8080 HTTP 200/8081 timeout(12.012s). 두 목적지의 Inspection Attachment와 같은 서버 ID/사설 주소 유지. 재생성 시작부터 ALB HTTP 대조 완료까지 약 8분28초(Plan/경로 전환/확인 포함) |
| 2026-11-03 16:57 KST | enabled `terraform plan -detailed-exitcode` | No changes, 종료 코드 0. 실제 원복과 코드/state 일치 확인 |
| 2026-11-03 17:00~17:01 KST | 원복 요청 시각/고유 probe로 FLOW/ALERT 원본 로그 대조 | 전달 지연 후 확인. ALB 허용 flow 947046881210714와 Client 허용 flow 735956279992884의 SID2000001 allowed/alert 및 각각 양방향 FLOW. ALB 10.10.4.148:39474/Client 10.10.1.143:38996 -> Server8081의 SID9000000 blocked/drop 12건(재전송 포함). timeout만으로 차단 판정하지 않음 |
| 2026-11-03 17:01~17:03 KST | 최종 bypass helper/새 Plan/Apply/실제 HTTPS snapshot, disabled Plan JSON 대조 | 두 다음 홉을 다시 직접 VPC로 교체하고 0 add/1 change/0 destroy로 삭제 보호 해제. 웹/8080/8081 모두 HTTP 200. 최종 disabled Plan도 정확히 기존 ANF 관련 7개 삭제만 포함 |
| 2026-11-03 | 통합된 `terraform/network-security/scripts/validate.sh` | helper 단위 테스트 11개를 표준 명령에 포함한 뒤 최종 재실행. Python **11 passed**, Terraform fmt/init/validate와 mock **41 passed, 0 failed** |
| 2026-11-03 17:08 KST | 최종 disabled 저장 Plan `apply`, AWS/HTTPS snapshot 및 EIC 사설 Client HTTP | 0 add/0 change/7 destroy, Firewall 삭제 4m5s. Firewall ResourceNotFound와 엔드포인트 0개. 웹 200, ALB 8080/8081 각각 HTTP 200(0.182s/0.182s), Client 두 포트 HTTP 200(0.006s/0.004s). 기존 서버 IDs/사설 IP/공인 IP와 IPv6 부재 및 두 직접 TGW 경로 active 확인 |
| 2026-11-03 17:08 KST | disabled `terraform plan -input=false -no-color -detailed-exitcode`와 실제 입력 대조 | No changes, 종료 코드 0. ignored 모드 파일 disabled와 배포/state 일치 |
| 2026-11-03 17:09 KST | AWS 정책/규칙/로그/서브넷/Attachment 재조회, 임시 관리 경로 정리 | 정책 1/규칙 1/로그 그룹 2/서브넷 4/Attachment 3 보존. 모든 Attachment available/associated. 이번 EIC 터널 종료와 리스너 부재, 임시 SSH 개인/공개 키 파일 제거 확인 |
| 2026-11-03 | `terraform fmt -check -recursive`, `git diff --check`, 입력/출력/변경한 로컬 링크/추가 문자/ignore 검사 | 형식과 diff 통과, 입력 21개/출력 18개 모두 README 표에 있음. 이번에 추가하거나 갱신한 로컬 링크 정상. 실제 입력, state/Plan/원본 검증 증거는 Git 제외 |
| 2026-11-03 | `git stash push -u`, `git pull --ff-only origin main`, 보관한 변경 복원 | 83d90396 -> 22b91803, 원격 33개 커밋 반영. 문서 인덱스 자동 병합, ANF 구현/테스트/운영 파일은 원본과 동일하게 복원. ignored disabled 입력 보존 |
| 2026-11-03 | pull 후 `terraform/network-security/scripts/validate.sh` | Python **11 passed**, Terraform fmt/init/validate 및 mock **41 passed, 0 failed**. 실제 AWS Apply는 다시 실행하지 않음 |

## Git 게시 기록

- 사용자 승인: 2026-11-03 `pull받고 커밋 푸시 ㄱㄱ`.
- 반영한 원격 기준: `22b91803362769b133065747dfcdeaa5d6cd4e58`.
- 구현 커밋: `8be51b4e` (`feat(network): add staged ANF cost control`).
- 테스트 커밋: `7341a2c4` (`test(network): verify ANF bypass and restoration guards`).
- 운영 문서 커밋: `fae8ca04` (`docs(network): record ANF shutdown and restoration`).
- 2026-11-03 17:19 KST `git push origin main` 성공, `22b91803..fae8ca04 main -> main` 확인.
- `git ls-remote --heads origin main`의 `fae8ca04aa21b644ee15611f829b2c0c13cb41e9`와 로컬 HEAD 일치, 당시 작업 트리 clean을 확인했다. 위 3개 커밋의 SSH 서명도 검증했다.
- 이 게시 확인 기록 자체는 별도 서명 커밋으로 저장한다. 최종 원격 HEAD는 그 문서 커밋을 푸시한 뒤 다시 대조한다.
- 실제 tfvars/backend/state/Plan/검증 원본과 모드 입력은 커밋 대상에서 제외한다.
