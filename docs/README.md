# Delta network gateway

2026-11-07 플랫폼 롤백: main을 `3cbf8419` 파일 상태로 복원한 뒤 운영 DB `0013`을 보존하는 호환 수정 PR #79를 `2862e923`으로 병합하고 [Release 42376126370](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/42376126370)으로 운영 배포했다. 사전 검사 20개와 HTTPS 준비 상태, Wazuh 무결성 및 인증 검사가 통과했고 DB 백업과 기존 서비스 2개 및 실행 이력 8개를 보존했다. 인증된 로그인과 실제 새 작업 실행은 미검증이다. 이후 main에 별도 PR #80, #81, #82가 병합됐으며 이번 배포는 그 이전 커밋 기준이다. [작업과 검증 상태](ai-dlc/platform-rollback-3cbf8419.md)

2026-11-07 최신 배포 환경 상세 기준 UI 구현/검증: 사용자가 지정한 `Migration verification 85df8a6`의 실제 화면/운영 번들/조회 API를 기준으로 환경 카드와 완료 상태 전환을 복구하고 정확한 주소 안내를 제거했다. 가독성 개선과 최종 빌드/형식 검사, 로컬 fixture 회귀 46개 및 1440px/390px 캡처 확인을 완료했다. 사용자 승인으로 구현 `090c51d3`을 푸시하고 [PR #73](https://github.com/2026-sbh-delta/delta-network-gateway/pull/73)을 main 대상으로 게시했으며 squash 병합 경로를 사용한다. 최종 원격 CI/병합 상태는 PR과 main 이력을 따른다. 운영 번들 전체 원본 복구나 운영 배포/자원 변경은 포함하지 않는다. [검증과 게시 기록](ai-dlc/platform-presentation.md#pr-게시와-main-squash-병합-경로-2026-11-07-kst)

2026-11-07 15:21 KST ANF 기본 차단 제거 완료: 사용자 전체 범위 명시 승인으로 SID `9000000` 한 줄만 제거했다. 나머지6개 규칙과 Firewall/Endpoint/경로/DNS/ALB 설정을 보존했고 AWS ALB 두 IP의 외부 HTTPS200, Primary2/Secondary2 READY/정책 IN_SYNC 및 중앙 최종 실제 Plan No changes/종료0을 확인했다. managed89개 ID/출력22개 유지, Terraform mock76개/Python22개 통과. 443 이외의 미등록 통신도 이 규칙으로 차단하지 않는다. demo DNS는 GCP를 유지한다. Git 게시와 원격 CI 상태는 Git 이력과 실행 결과를 따른다. [승인과 실제 결과](ai-dlc/anf-board-reactivation.md#2026-11-07-후속-공통-기본-차단-규칙-제거)

2026-11-07 운영 플랫폼의 기존 마이그레이션 화면에 ‘환경 차이와 AI 제안’ 표를 연결했다. 현재 미승인 온프레미스→AWS 계획에서 Deployment 2행·Service 2행·관측 범위 1행과 `needs_human` 판정을 실제 브라우저에서 확인했다. 같은 표를 PR #69의 정식 플랫폼 웹 빌드에 연결하고 재빌드 산출물에서 저장된 결과를 확인했다. 고객 앱 배포·DNS 변경은 실행하지 않았다. [빌드 출처·검증·롤백](ai-dlc/platform-workload-diff.md)

2026-11-07 IAM 수집 누락과 앱 이전 Lambda 범위 제외를 수정하고 실제 시연 Diff API·오케스트레이터 연결을 검증했다. RDS·S3 검사와 SCC 미확인은 유지하고 고객 앱 운영 배포·DNS 전환은 실행하지 않는다. [설계·검증·적용 상태](ai-dlc/diff-iam-scope.md)

2026-11-07 워크로드 차분 병렬 연결을 실제 시연 오케스트레이터에 배포했다. 기존 웹 검사·수정 재검증과 병렬로 Diff를 호출하고, 판정 보류는 사람 확인을 거친 뒤 운영 배포와 도메인 전환으로 넘어간다. 실패는 기존 delta-ai 진단으로 보낸다. 실제 연결 검증은 사람 승인 뒤 `unresolved`로 종료했고 스테이징 점유 해제를 확인했다. DAST 미실행을 보안 통과로 보지 않는다. 운영 배포는 대기하며 기존 컨테이너와 상태 사본을 복구용으로 보존했다. [설정·검증·승인 범위](ai-dlc/parallel-workload-diff.md)

2026-11-07 서비스 도메인 설정 보존 수정과 검증 완료. `deploy/platform/deploy.sh`가 기존 SSM 전환 설정을 보존하고 최초 배포에서만 Terraform 출력으로 초기화한다. 배포 진입점 회귀와 기존 SSM 검사22개가 통과했고, 실제 SSM에서 이전 도메인 출력으로 초기화를 시도해 사용자 도메인 설정 v10과 토큰 v4의 보존을 확인했다. PR과 main의 전용 CI가 배포 테스트를 실행한다. Git 게시와 CI 결과는 PR 및 실행 이력을 따른다. [설계와 검증 기록](ai-dlc/cutover-config-preservation.md)

2026-11-07 AWS ANF 재개 완료: `delta-board-vpc`만 인터넷 입출구와 GCP 양방향 검사를 enabled로 전환했다. Primary2/Secondary2 READY/IN_SYNC, ALB 두 IP 및 사설 양방향 HTTP200/NAT HTTPS200/RDS TLS1.3과 미허용8081의 실제 blocked/drop을 확인했다. FLOW/ALERT 원본21건과 플랫폼 중앙 원문이 일치했고 BGP4개 UP/기존 광고 유지 및 두 최종 실제 Plan No changes/종료0을 확인했다. Terraform mock131개/Python38개 통과. DNS/ALB와 기존 자원 ID를 보존했고 임시 probe/규칙/키/터널을 정리했다. 당시 앱은 inactive/disabled였고 공개 앱은 HTTP502였다. 이후 상태는 위 후속 검증을 따른다. Git 게시와 원격 CI 상태는 Git 이력과 실행 결과를 따른다. [승인과 실제 결과](ai-dlc/anf-board-reactivation.md)를 따르며 별도 GCP 작업의 설정과 문서를 보존한다.

2026-11-07 GCP Cloud NGFW 검사 재개 완료: 사용자 요청으로 `disabled -> bypass -> enabled`를 적용했다. endpoint1/association2 ACTIVE, 두 사설 검사 규칙과 모든 심각도 ALERT, BGP4개 UP 및 기존 ID57개 보존을 확인했다. 양방향 TCP3000 HTTP200과 실제 Threat8건을 포함한 원본12건의 Security/플랫폼 수신까지 대조했고 최종 실제 Plan은 No changes/종료0, 잠금도 없다. Terraform mock14개/Python10개가 통과했다. 공개 앱 L7/IPS 차단과 앱 재시작은 포함하지 않았으며 AWS 모드와 경로는 별도 작업이다. endpoint 비용은 USD1.75/h와 검사량 요금이다. [적용과 실제 검증](ai-dlc/gcp-central-network.md#2026-11-07-operation-gcp-cloud-ngfw-검사-재개)

2026-11-07 GCP 방화벽 로그의 Security 일원화 완료: 사용자 승인으로 `/delta/security/gcp-firewall`, `/delta/security/gcp-firewall-threat`와 전용 수집24개를 실제 생성했다. 기존 Destination을 통해 플랫폼 `/delta-cp/central/gcp`와 GCP 원본 ID 및 JSON을 유지하고, 실제 규칙 로그를 전환 전19건과 후17건 대조한 뒤 기존 직접 Sink에서 두 종류를 제외했다. 합성2종과 나머지 GCP 및 기존 AWS 회귀도 수신 확인했다. Python34개와 Terraform mock7개 및 Ruff 통과, 양쪽 실제 마지막 Plan No changes/종료0. 기존 플랫폼43개 ID, 과거 로그와 양쪽 검사 disabled를 유지했다. 당시 새 NGFW 위협 발생은 미검증이었으며 재개 후 검증은 위 기록을 따른다. 게시와 원격 CI 상태는 Git 이력을 따른다. [승인과 실제 검증](ai-dlc/gcp-security-log-consolidation.md)

2026-11-06 AWS Network Firewall 로그 전달 완료: VPC Flow Log는 제외하고 `/delta/security/nfw-flow`와 `/delta/security/nfw-alert` 및 각 `delta-platform-central` 구독을 실제 적용했다. 신규4개, 기존 정책 변경1개, 삭제0개이며 두 수동 smoke가 `/delta-cp/central/aws`에서 원본 계정, 그룹, 스트림, 시각과 본문까지 일치했다. 기존 두 그룹과 과거 로그, ANF disabled, GuardDuty와 플랫폼 Destination 및 수집기를 보존했다. Python11개와 Terraform mock71개가 통과했고 최종 실제 Plan은 No changes, 종료0이다. ANF가 꺼져 있어 실제 Network Firewall 서비스 FLOW와 ALERT 생성은 미검증이며 게시 상태는 Git 이력을 따른다. [설계와 실제 검증](ai-dlc/aws-traffic-log-forwarding.md)

2026-11-06 검증 사례 피드백 실제 AWS 릴리스 완료: main ad48e2e·공유 PR #60·실행 중 공용6파일을 보존 통합하고 소스523ae5a의 AI/오케스트레이터를 배포했다. AWS 내부 합성 HTTP503→200 성공 사례와 별도503 유지 실패 사례에서 실제Claude4호출, 승인된 이전 사례의 실제 인용, 평가셋 추출·비교·승인취소·재시작 보존을 확인했다. 기존43실행/80영속파일과인증/로그를 보존했고 회귀548개 통과. 합성장애검증이며 고객앱/전체이전/진단정확도향상 실증은 아니다. [실제 결과와 운영 절차](ai-dlc/verified-case-learning.md#2026-11-06-실제-aws-배포claude-검증-완료).

2026-11-06 검증된 장애 사례 피드백: 별도 `codex/verified-case-learning`에서 보고서→조치 후 재검사 결과→버전 사례→로컬 검토/승격→다음 진단 검색과 평가셋 추출을 구현했다. 현재 실행/동일 관측 제외, 승인 취소 이력, outbox 재전송, 사람 확인/접수를 성공으로 보지 않는 경계를 포함한다. 공용 최신본 통합·AWS/실제 Claude 검증·배포·Git 게시 상태와 로컬 검증 결과는 [설계·운영 절차](ai-dlc/verified-case-learning.md)를 따른다.
2026-11-06 사용자 승인으로 DAST 통합 CI의 외부 사전점검 모의 누락·오래된 impact 기대를 수정하고 PR #60→#61 순차 main 병합을 진행한다. 실제 PostgreSQL 통합2·플랫폼248·DAST계약45개와 Ruff 검사를 통과했다. 운영 DAST 판정/사전점검은 유지한다. [원인·검증·병합 절차](ai-dlc/dast-ci-sequential-merge.md).

2026-11-06 사용자 담당 진단·보고서·로그·제한 복구를 `codex/diagnostics-pipeline-handoff`로 인수 정리했다. 최신 main `ad48e2e`(#59)의 운영 배포·도메인 전환/복귀·OIDC를 보존 통합했으며, 팀 Worker 교체나 AWS 추가 배포는 하지 않았다. [인수 안내·설치·결합 접점·남은 조건](ai-dlc/diagnostics-pipeline-handoff.md)을 먼저 읽는다. 현재 게시/CI 상태는 해당 PR과 Git 이력을 따른다.

2026-11-06 온프레미스 실제 DAST·로그 보존 후속: 사용자 선택에 따라 팀 Worker를 보존하고 독립 큐/실행기를 배포했다. core 회귀176개 통과. 독립 AWS 시험에서 실제 SAST→FE/BE 실행→정리 전 두 컨테이너 로그 보존→정리→실제 Claude 실패 진단/보고서를 확인했으나, DAST는 기본 대상 템플릿 미등록 문제로 HTTP422여서 전체 성공 검증은 미완료다. 다른 AWS 계정의 후속 배포2건이 공용 오케스트레이터를 교체한 사실을 감사 기록으로 확인했으며 최신 팀 변경의 보존 통합·배포 조정·실제 화면 검증이 남았다. 소스 da2c15d는 로컬이며 push/main 병합 없음. [실제 결과·충돌 근거·남은 작업](ai-dlc/onprem-dast-evidence.md)

2026-11-06 온프레미스 실행·복구 AWS 연결 완료: 기존 Worker를 고정 SSM 문서/최소 IAM으로 연결하고 실제 새 SAST13파일→온프레미스 실행→제한 자동 복구→사람 재검사→실제 Claude/보고서·화면을 검증했다. 실제2실행은 각 native배포1회·Pod/Secret정리, 기존34/35개board 보존, 시험OFF·정상재시작·보고서인증·최종Terraform No changes 확인. 회귀155+native fixture28+Terraform mock1 통과. DAST 미실행·단일 이미지 sandbox 범위이며 전체 FE/BE/DB/DNS 이전은 별도다. 소스6b8c405 로컬/런타임613c3c64 배포완료, main미병합. [설계·실제 결과·남은 범위](ai-dlc/onprem-runtime.md)

2026-11-06 사용자 AWS 진단·중앙 로그 근거 릴리스 완료: 실제 새 SAST15파일→GCP FE/BE staging→DAST XSS재현1→자동 로그(실행8/GCP40·미해석19)→실제Claude→보고서·화면을 검증했다. AI319+pipeline/orchestrator144개 통과, 고정3그룹 조회 정책1건 추가/최종No changes, 기존28실행 보존·이번실행stop/lease해제. 전체 DB/DNS 이전·현재runtime의onprem복구 연결은 남아 있다. [설계·실제 검증·남은 범위](ai-dlc/ai-aws-readiness.md)

2026-11-06 진단·보고서 실제 콘솔 통합: 최신 main의 FE/BE 묶음·수정 재검증·운영 release 계약을 보존하고 예외 분석→보고서→사람 확인을 이식했다. 새 실제 SAST/DAST 실행8f177a83과 실제 Claude 분석·공개 인증 다운로드·브라우저 화면·재시작 후 보고서 보존을 확인했다. 회귀136개 통과, 런타임 이미지5a67fb9 배포 완료, 이전 기록27개/설정/로그 보존. 소스는 별도 codex/orchestrator-diagnostics-ui의 로컬 커밋이며 main 병합은 아직이다. [통합·기능·남은 작업](ai-dlc/orchestrator-diagnostics-ui.md)

2026-11-06: 사용자 승인 명세에 따라 [FE·BE 이미지 묶음·운영 Compose 배포](ai-dlc/compose-delivery.md)를 구현했다. 스테이징은 ECS·Cloud Run을 유지하고 FE+BE 두 컨테이너와 PG18 단일 사용자 DB(Terraform이 비밀값까지 생성, 수동 단계 없음)로 바꿨다. 게시판 `145b62b`로 플랫폼에서 빌드(FE·BE 묶음)·SAST·GCP 스테이징 배포까지 실행했다. 운영 배포(release 작업)는 오케스트레이터 연결 전이다.

2026-11-06: 사용자 승인으로 중앙 계정 `482840608926`의 GuardDuty Finding 전달 경로를 실제 적용했다. 신규5개/기존 변경과 삭제0개이며 `/delta/security/guardduty`에서 기존 플랫폼 Destination을 거쳐 `/delta-cp/central/aws`로 샘플1건의 본문/원본 메타데이터 일치를 확인했다. 로컬 mock63개/Python11개 통과, 기존 ID58개/출력18개와 탐지/ANF/플랫폼 설정 보존 및 중앙 최종 실제 Plan No changes/종료0을 확인했다. 구현 `c543892d`와 운영 기록 `01ff0708`을 main에 푸시했고 해당 SHA의 [Platform checks](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/42058840223) 17개가 통과했다. 중앙 네트워크 root의 검증은 앞의 로컬 결과다. [설계와 실제 검증](ai-dlc/aws-guardduty-logging.md)

2026-11-05: 사용자 승인으로 Workload와 Network/Security의 서울 GuardDuty를 실제 적용하고 Terraform 등록을 완료했다. 두 계정 모두 ENABLED이며 기존 workload의 ID/보호 설정을 보존하고 중앙은 기본 탐지만 켰다. Terraform mock103개/Python14개 통과, 두 최종 실제 Plan No changes/종료0과 기존 managed127개 ID/기존 출력27개 및 VPC Endpoint 보존을 확인했다. 중앙 초기 S3/EKS API 400은 중지 상태의 일괄 OFF 요청과 새 Plan/Apply로 복구했다. 게시 상태는 Git 이력을 따른다. [실제 결과와 운영 절차](ai-dlc/aws-guardduty.md)

2026-11-05: 사용자 승인으로 공통 워크로드 VPC의 이름을 `delta-lab-client`에서 `delta-board-vpc`로 실제 변경했다. Terraform Apply는 VPC 태그1개 갱신/생성과 삭제0개이며 기존 VPC ID/CIDR과 관리24개 전체 ID 및 출력을 유지했다. mock24개/Python3개 통과, AWS API의 새 이름과 최종 Plan No changes/종료0을 확인했다. 게시 상태는 Git 이력을 따른다. [변경 범위와 검증 기록](ai-dlc/aws-workload-consolidation.md#2026-11-05-후속-vpc-이름-변경)

2026-11-05: 온프레미스 환경 카드의 ‘실행 앱 삭제’를 구현했다. 이름·자원 소유권 확인, Worker의 실제 종료 확인, 실패 재시도와 이력 보존을 지원한다. 별도 임시 앱의 실제 삭제와 기존 앱/PVC 보존을 검증했으며 운영 플랫폼 릴리스·활성화는 아직이다. 플랫폼 VPC의 TGW 연결과 AWS→온프레미스 왕복 경로가 필요하다. 사용자에게 변경 내용을 보고했고 커밋·PR·병합 승인을 받았다. [설계·검증](ai-dlc/platform-onprem-removal.md) · [연결 안내](../platform/deploy/onprem/REMOVAL.md)

게시 상태: 사용자 승인으로 [#28](https://github.com/2026-sbh-delta/delta-network-gateway/pull/28) → [#47](https://github.com/2026-sbh-delta/delta-network-gateway/pull/47) → [#49](https://github.com/2026-sbh-delta/delta-network-gateway/pull/49)를 순서대로 main에 병합했다. #49는 #47 병합 후 main 대상으로 변경했다. 각 PR의 CI 통과를 확인했고 초안 #39는 유지한다. 앱 삭제 브랜치도 재시도에서 업로드됐으며 새 main과 통합한다. 운영 릴리스는 별도다.

2026-11-05: [플랫폼 자동 계측](ai-dlc/platform-auto-instrumentation.md)을 구현하고 온프레미스의 원본 FE·BE 이미지에서 trace·HTTP 지표 수신을 확인했다. 계측 ON/OFF와 Collector 장애 시 앱 응답도 검증했다. 원본 앱 후보는 실행 중이고 중앙 연결 준비 전까지 계측은 OFF다. [FE·BE 실행기](ai-dlc/platform-onprem-release.md)와 자동 계측 코드는 PR #47·#49로 main에 병합했으며 중앙 릴리스는 미실행이다.

## 현재 상태

- AWS ANF(2026-11-07): board의 인터넷은 같은 AZ Secondary Endpoint, GCP는 board 전용 TGW 송신 RT와 중앙 Primary Endpoint를 왕복한다. 두 VPN/네 터널의 BGP/ECMP를 유지하며 VPN 연결 RT에 GCP 정적 대역을 넣지 않는다. 재개 당시 실제 통신/차단/중앙 로그와 두 root No changes를 확인했다. 15:21 KST 후속으로 기본 차단 SID9000000을 제거했으며, 외부 AWS HTTPS200과 중앙 No changes/네 Endpoint READY를 확인했다. [현재 상태와 복귀](ai-dlc/anf-board-reactivation.md#2026-11-07-후속-공통-기본-차단-규칙-제거)를 따른다.

- AWS Compose 중지/EC2 보존 기록(2026-11-05): 당시 게시판 FE/BE2개와 프로젝트 네트워크를 내리고 delta-compose inactive/disabled, VM/DB/이미지/설정 보존과 공개 API HTTP502를 확인했다. 이후 2026-11-07 ANF 후속 검증에서는 기존 AWS 앱의 외부 HTTPS 화면/API HTTP200을 관측했다. 이번 작업은 원격 Compose/systemd 상태를 조회하거나 변경하지 않았으므로 과거 중지 결과를 현재 상태로 재사용하지 않는다. [당시 중지 및 재개 절차](ai-dlc/aws-compose-consolidation.md)를 따른다.

- GCP Compose 중지/VM 보존(2026-11-05): 사용자 후속 요청으로 게시판 FE/BE/Proxy3개와 프로젝트 네트워크를 내리고 delta-compose 자동 시작을 해제했다. 앱 컨테이너3개를 내리고 프로젝트 컨테이너/네트워크 잔여0개, delta-compose inactive/disabled를 확인했다. VM RUNNING, DB RUNNABLE, 디스크/DB 식별자 동일과 호스트 이미지9개/설정 해시 보존을 확인했다. 공개 API는 HTTP503다. 기존 VM/Cloud SQL/PSC/ALB를 유지하며 이번 중지에서 Terraform Plan/Apply나 Git 게시를 실행하지 않았다. [최신 중지 및 재개 절차](ai-dlc/gcp-compose-consolidation.md)를 따른다.

- Sample App GCP 초기 교체 기록(2026-11-05): 위 Compose 통합 전의 VM2대 배포 기록이다. 기존 NLB/시험 VM 제거와 API404 확인 후 공개 리전 ALB, 사설 x86 e2-small FE/BE 각1대와 새 Cloud SQL PostgreSQL16/PSC를 실제 배포했다. ALB/FE/BE/DB 접속점 subnet을 분리했고 VM/SQL 공인 IP 없음, ALB HEALTHY/PSC ACCEPTED, 공개 CRUD와 BE 재시작 후 보존, FE DB 차단 로그와 양방향 TGW 사설 HTTP200/임시 시험 규칙 정리를 확인했다. GCP 두 root와 AWS central 최종 Plan No changes/종료0. 앱은 미커밋 PostgreSQL 변경 snapshot이며 게시/별도 AWS 앱 상태와 구분한다. [공개 앱](http://34.64.134.28) / [실제 검증과 실행 상태](ai-dlc/gcp-sample-app.md)

- Sample App EC2/RDS 최초 배포(2026-11-05): 사용자 승인 및 x86 지시로 공개 ALB, 사설 t3.small FE/BE 각 1대와 db.t3.micro PostgreSQL 18.3, 단일 NAT와 개별 배포 도구를 적용했다. 추가59개/기존 변경과 삭제0, 기존 네트워크 ID와 GCP 경로를 유지한다. [공개 앱](http://delta-lab-notes-alb-1242820262.ap-northeast-2.elb.amazonaws.com)의 CRUD/BE 재시작 후 보존과 실제 TLS1.3/평문 거부/최소 DB 및 IAM 권한/양쪽 CloudWatch 로그를 검증했다. mock33개/Python9개/앱27개 통과, 최종 Plan No changes/종료0. Git 게시와 원격 CI 상태는 Git 이력을 따른다. [설계와 실제 검증](ai-dlc/aws-sample-app.md) 이후 실제 운영은 [단일 EC2 Compose](ai-dlc/aws-compose-consolidation.md)로 전환했다.

- 관리·이전 시연 도메인 분리와 온프레미스 연결(2026-11-05): `https://delta.mungji.com` 관리 도메인·TLS·Origin 적용과 실제 화면을 확인했다. 제한된 Tailscale 경로로 AWS Worker의 실제 K3s 앱 실행·HTTP·정리를 검증했다. demo 도메인은 고객 앱 운영 LB 준비 전이어서 미연결, 팀 DAST 공격 검사·중앙 로그는 후속이다. 소스 PR 병합/릴리스와 실제 적용을 구분한다. [검증·원복·남은 작업](ai-dlc/domain-onprem-connect.md)

- 사용자 선택 배포(2026-11-05): `feat/platform-deployment-targets`에서 최초 환경·사양 선택, GCP/온프레미스 동시 대상 계획, 대상별 Worker 실행·환경 등록과 전용 Jenkins 실행기를 로컬 구현했다. 백엔드 226개·브라우저 35개·새 Terraform 모의 계획 2개 통과. 사용자가 커밋·PR 진행을 승인했으며 게시·CI 상태는 해당 브랜치의 PR 기록을 따른다. 실제 신규 VM 생성·Release는 미실행이며 전용 Agent와 환경별 연결이 필요하다. 기존 운영·공유 네트워크·S3 state는 변경하지 않았다. [설계·검증 기록](ai-dlc/platform-self-service-deployment.md) · [실행기 연결](../platform/deploy/workloads/README.md)

- AWS 워크로드 통합 완료(2026-11-05): 사용자 승인으로 CloudFront 시연37개, 기존 Server 기반29개, Client EC2와 폐기 경로5개를 실제 삭제했다. 두 EC2 terminated와 두 루트 EBS 삭제, `10.20` VPC 부재를 API로 확인했다. 기존 `10.10` VPC/서브넷/TGW/EIC ID와 backend key를 유지하는 공통 `terraform/workload` root로 정리했다. 중앙6개 삭제/정책2개 갱신과 GCP 범위3개 갱신으로 폐기 CIDR을 철회했고 BGP4개 UP을 확인했다. 최종 세 root No changes, Terraform mock82개/Python24개와 AI/Jenkins125개 통과. GCP Plan은 별도 로그 정리의 로컬 코드가 포함된 상태에서 확인했다. EC2가 없어 삭제 후 HTTP 검증은 미실행이다. [설계와 실제 검증](ai-dlc/aws-workload-consolidation.md)

- GCP state S3 통합(2026-11-05): 사용자 승인으로 `network-security-gcp`의 state를 기존 중앙 S3 버킷의 전용 key `delta/lab/network-security-gcp/terraform.tfstate`로 이전했다. 관리 리소스67개와 출력/검사 결과, GCS 원본과 다른 root state 보존 및 실제 Plan No changes/종료0을 확인했다. mock14개와 Python10개 통과. 리소스 Apply는 미실행이며 커밋과 원격 반영은 Git 이력을 따른다. [이전과 검증 기록](ai-dlc/gcp-central-network.md#2026-11-05-gcp-state의-중앙-s3-이전)

- GCP 로그 전달 정리(2026-11-05): U7~U9 구현 승인 후 네트워크 계정의 전용 수집 리소스8개와 GCP 전달 리소스12개를 실제 제거하고 `terraform/gcp-log-ingest-aws`를 정리했다. 기존 로그 그룹2개와 30일 보관 정책, 공통 코드와 플랫폼 수집을 보존했다. 플랫폼의 새 네트워크 로그 원본 일치를 삭제 전35건/후4건 확인했고 관련 테스트51개가 통과했다. 기존 정의로 실행하면 삭제한 수집기를 재생성할 수 있어 현재 코드를 기준으로 Plan을 검토한다. 최종 Plan과 사설 HTTP 검증의 한계는 [실제 결과](ai-dlc/gcp-central-network.md#2026-11-05-gcp-로그-전달-경로-정리-검토)를 따른다. 게시 상태는 Git 이력을 따른다.

- 프로젝트 삭제(2026-11-05): 사용자 승인으로 목록의 삭제 버튼·이름 확인창과 최소 백엔드 API를 추가했다. 실행/정리 중인 작업 및 연결된 환경·계획은 차단하며 다른 서비스·실제 클라우드 자원은 삭제하지 않는다. 로컬 API 40개·전체 브라우저 37개·빌드 검사를 통과했으며 운영 배포·데이터 삭제는 하지 않았다. [설계·검증 기록](ai-dlc/platform-project-delete.md)

- 서랍 드롭다운(2026-11-05): 사용자 요청으로 펼친 서비스 목록을 서랍의 남색/파란 테마에 맞췄다. 빌드·fixture 검사 11개와 밝은/어두운 모드·키보드·모바일 표시를 확인했다. PR #43 검사 11개 통과·병합 후 [Release 41859205247](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41859205247)를 완료했다. 운영의 펼친 목록 색상·서비스 선택·기존 데이터 보존을 확인했다. [검증·미리보기](ai-dlc/platform-presentation.md)

- 플랫폼 서브넷 분리(2026-11-04): 실제 AWS 변경 승인 후 전용 서브넷과 AI/SAST/DAST 실행 기반을 준비 중이다. 기존 VM·데이터·로그 수신 주소를 보존하며 단계적으로 이전한다. 아직 Apply/이전 완료가 아니다. [설계·진행 기록](ai-dlc/platform-subnets.md)

- 기준일: 2026-11-04 (Asia/Seoul)
- 플랫폼 프로젝트 대시보드(2026-11-04): 사용자 요청으로 중복 프로젝트 선택을 제거하고 환경 탭·기간 버튼·지표/실제 추이·서버/로그/요청 경로 중심 화면을 구현했다. 환경 미연결 안내와 재조회를 보완했다. 전체 브라우저 24개·PR #41 검사 11개 통과 후 main `7989f6d`의 [Release 41854211786](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41854211786)를 완료했다(11-05 00:16 KST). 실제 운영에서 프로젝트 문맥·드롭다운 제거·모바일 배치·기존 서비스 2개 보존을 확인했다. [설계·검증](ai-dlc/platform-presentation.md)
- 플랫폼 발표 화면(2026-11-04): 화면 개편 PR #31과 서랍 PR #37을 병합하고 [Release 41849097814](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41849097814)를 완료했다. 운영 로그인·기존 서비스·데스크톱/모바일 서랍을 확인했다. 후속 대시보드 배포는 위 최신 항목을 따른다. [설계·검증·미리보기](ai-dlc/platform-presentation.md)
- 플랫폼 중앙 로그(2026-11-04): [PR #34](https://github.com/2026-sbh-delta/delta-network-gateway/pull/34)를 전체 CI 통과 후 main에 병합했다. AWS CloudWatch Logs Destination/Kinesis/Lambda와 GCP Cloud Logging Sink/PubSub/WIF 수집기를 플랫폼 계정(616723161368, 서울)에 별도 배포했고 양쪽 실제 수신·최종 Plan No changes를 확인했다. 기존 네트워크 계정 수집기는 유지한다. AWS 팀원 로그 그룹의 구독 추가와 플랫폼 웹 조회 연결은 별도다. [팀원 연결 안내](../platform/deploy/logging/README.md) · [진행·검증 기록](ai-dlc/platform-central-logging.md)
- 온프레미스 연결(2026-11-04): [PR #32](https://github.com/2026-sbh-delta/delta-network-gateway/pull/32)를 전체 CI 통과 후 main에 병합했다. k3s 실행기·팀 DAST 등록·OTel 호스트 관측/화면을 추가하고 실제 VM의 동일 이미지·HTTP·권한·통신 차단·정리와 CPU/메모리/디스크 수집을 확인했다. 기존 앱·DB는 보존했고 Collector 서비스는 중지 상태다. 팀에서 VPN/TGW 연결 중이므로 AWS 플랫폼 활성화·중앙 관측 전송·실제 DAST 공격 검사는 후속이다. [진행·검증 기록](ai-dlc/platform-onprem.md)
- GCP 샌드박스(2026-11-04): [PR #29](https://github.com/2026-sbh-delta/delta-network-gateway/pull/29) 병합·[Release 41814681634](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41814681634) 완료. AWS ECS/GCP Cloud Run 선택과 DAST 연결을 배포했다. 실제 WIF·동일 digest·인증 HTTP·익명403·삭제·공개 웹 결과를 확인했고 기존 로그인·서비스2개를 보존했다. 후보0건은 DAST 미실행으로 표시한다. 온프레미스·최종 운영 VM 검증은 후속이다. [검증·한계](ai-dlc/platform-gcp-sandbox.md)
- GCP 중앙 네트워크(2026-11-07): `inspection_mode=enabled`, 공유 Endpoint1개와 VPC 연결2개 ACTIVE다. 현재 AWS 사설 TCP3000/8080 검사 규칙과 ALERT 프로필을 사용한다. 양방향 TCP3000 HTTP200과 실제 Threat8건의 Security/플랫폼 원본 수신, BGP4개 UP와 기존 ID57개 보존 및 최종 실제 Plan No changes/종료0을 확인했다. 초기 인터넷 검사/장애 검증과 비용 중단은 과거 기록이며 현재 공개 앱 L7 검사 증거와 구분한다. 이번 mock14개/Python10개가 통과했고 AWS 모드/라우팅은 별도 재개 작업이 소유한다. [실제 결과와 검증 한계](ai-dlc/gcp-central-network.md#2026-11-07-operation-gcp-cloud-ngfw-검사-재개)
- 플랫폼 관측·서비스 이전(2026-11-04): PR #21~#25를 main에 병합하고 AWS [Release 41700029642](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41700029642)를 완료했다. OTel 대시보드·workflow/AI 식별과 승인·DAST 연결·승인된 DB 이전 CLI의 진행 상태를 반영했다. 실제 로컬 PostgreSQL 복제·앱/세션 이전과 공개 웹의 기존 데이터 보존을 검증했다. 고객 VM/DB·사설 연결·GCP/온프레미스 실제 이전과 웹 자동 전환은 후속이며 현재 등록된 고객 실행 환경은 0개다. [검증·남은 작업](ai-dlc/platform-continuity.md) · [DB 이전 절차](../platform/migration/README.md)
- 플랫폼 DAST 연결: `feat/platform-dast`에서 팀 DAST API·ECS 수명·취소/복구·결과 화면과 사설 네트워크 확장을 구현했다. 초기 AWS 샌드박스 연결 기록이며, GCP 연결은 위 최신 항목을 따른다. 온프레미스는 별도 실행기를 추가한다. 로컬 검증 결과는 [진행 기록](ai-dlc/platform-dast.md), 운영 연결은 [배포 안내](../platform/deploy/dast/README.md)를 따른다. PR #21은 main에 병합됐고 사설 경로·SG 추가 Apply를 완료했다. 도구 이미지 활성화·플랫폼 릴리스를 완료했고 실제 ECS 실행·정리와 후보0건의 DAST 미실행 표시를 검증했다. 실제 XSS 재현 검증은 별도다.
- Jenkins AWS 운영 연결 완료(2026-11-03): PR #19 병합 후 GitHub OIDC로 사설 컨트롤러/전용 에이전트와 플랫폼을 배포했다. 공개 웹에서 실제 Jenkins 빌드 → ECR digest → ECS HTTP 검사·종료 및 데스크톱·모바일 표시를 검증했다. 기존 로그인·DB·서비스와 팀 스테이징을 보존했고 플랫폼/IAM 세 root는 No changes다. 배포 중 발견한 신규 SG 규칙 생성 권한 수정은 `42bbe5b`로 분리했고 운영 문서는 후속 `feat/platform-sast-aws` PR에 함께 포함한다. [진행 기록](ai-dlc/platform-jenkins-aws.md)
- 플랫폼 Jenkins 전환: PR #18/#19는 main에 병합됐다. 최초 로컬 실제 검증은 [기록](ai-dlc/platform-jenkins.md)에 보존하고 현재 운영 상태는 위 AWS 기록을 따른다. 고객 앱 빌드는 Jenkins, 플랫폼 인프라·릴리스는 GitHub Actions/OIDC가 담당한다.
- 플랫폼 SAST 운영 연결 완료(2026-11-04): 사용자가 PR #20을 병합했고 main `d4200f7`의 [GitHub Release](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41666361286)가 성공했다. 기존 로그인·DB·Jenkins 설정을 보존해 SAST를 활성화하고, 공개 웹의 실제 검사·커밋/이미지 연결·SARIF 해시·데스크톱/모바일·새로고침을 검증했다. 검증 도중 웹 콘솔에서 중지된 공용 도구 VM은 사용자 답변 후 재가동했다. JavaScript 예제의 발견 사항 0건은 전체 안전 판정이 아니다. 당시 DAST는 미연결이었으며, 후속 구현 상태는 위 DAST 항목을 따른다. 이 운영 기록도 후속 PR #21에 포함해 병합했다. [안내](../platform/deploy/sast/README.md) · [기록](ai-dlc/platform-security.md)
- 플랫폼 U3: 사용자가 PR #16을 병합했고 main `35aff4a`의 GitHub Release가 성공했다. 공개 웹에서 실제 VM 역할의 ECS 실행·digest·헬스·태스크 0개 정리를 검증했다. 이후 Jenkins 전환은 위 별도 기록을 따른다. [기록](ai-dlc/platform-u3.md)
- 플랫폼 U2: [PR #14](https://github.com/2026-sbh-delta/delta-network-gateway/pull/14) 병합 후 GitHub OIDC Plan·Apply·Release를 완료했다. [공개 플랫폼](https://d3udcz0zch0on.cloudfront.net)에서 기존 로그인·서비스를 유지하고 실제 공개 저장소 빌드 → ECR digest → 결과·로그 표시를 검증했다. 운영 역할의 CodeBuild 조회 권한 오류를 수정·적용했고 회귀 검증, 실제 역할 조회, 데스크톱·모바일 화면과 두 Terraform root의 최종 No changes를 확인했다. 권한 수정 PR #15도 main에 병합됐다. [기록](ai-dlc/platform-u2.md)
- ANF 비용 절감(2026-11-03 17:08 KST): **최종 disabled 적용 완료**. Firewall/엔드포인트 2개를 제거했고 ALB/사설 Client의 두 포트 HTTP 200과 중앙 Plan No changes를 확인했다. 실제 ANF 재생성과 검사 경로/삭제 보호 원복, 허용/차단/양방향 로그까지 검증한 뒤 다시 꺼뒀다. 정책/규칙/기존 로그와 네트워크는 보존했다. 최신 main pull 후에도 중앙 mock 41개와 helper 테스트 11개 통과. 구현/테스트/운영 문서 3개 서명 커밋을 origin/main에 게시했고 원격 `fae8ca04` 일치를 확인했다. 이 게시 확인 기록은 후속 문서 커밋으로 남긴다. [기록](ai-dlc/anf-cost-mode.md)
- AWS → GCP 매핑 판단: [`migration_mapping`](ai-dlc/migration-mapping.md) 구현 완료. 실제 AWS state 로 대조했고 GCP 생성은 안 함. 앞단은 HAProxy 기각, AWS 기능 사용 방향(ADR-AI-37), 값의 기준은 Terraform(ADR-AI-38), GCP 담당은 동규 님(2026-11-02 사용자 전달).
- 통합 시연: `deploy/demo-console/` 에서 온프레미스 증거·DAST·AI 판단·Jenkins 카나리를 한 흐름과 화면으로 돌린다. 2026-11-02 로컬 리허설 3개 시나리오 확인, 브랜치 `feat/demo-integration` 에 커밋·푸시했고 서버 배포는 안 함. 배포는 Mac Docker 대역이고 AWS·GCP 는 미연결. [기록](ai-dlc/demo-integration.md)
- Jenkins 배포·AI 판단: `deploy/jenkins-local/`에서 카나리 승격·자동 롤백·사람 확인을 로컬 시나리오로 확인했다. main 병합 후 2026-11-02 delta-ai `.venv/bin/pytest -q` 85개, 파이프라인 스크립트 51개 통과, 병합 후 Jenkins 빌드 #53~65(가짜 LLM)로 사전 확인과 시나리오 경로 확인. 커밋은 로컬 브랜치 `feat/jenkins-on-main` 까지이고 푸시·AWS 배포는 안 했다. [기록](ai-dlc/jenkins-local.md)
- 플랫폼 OIDC 연결(2026-11-03): `feat/platform-oidc`에서 Free/private 저장소에 맞춰 Environment 없이 main 전용 OIDC와 역할 3개를 구성했다. AWS CLI/SSO 인증으로 업무 계정의 IAM 기반 11개 추가를 적용했고, 정책 검증·권한 시뮬레이션 19개·최종 Plan No changes를 확인했다. 후속 권한 보완까지 로컬 배포 도구 테스트 17개·IAM mock 7개가 통과했다. GitHub 변수 9개는 사용자가 등록했고 값 일치를 확인했다. 구현 `a0714e8`을 푸시하고 PR #8의 CI 전체 성공 후 main에 병합했다(`6b404fc`). 사용자가 CI를 활성화했다. 실제 OIDC Plan에서 발견한 CloudFront prefix list 조회 권한을 해당 목록에만 추가했고, 재실행은 리소스 변경 없이 성공했다. 실제 GitHub Release도 성공했고 HTTPS·기존 로그인·기존 서비스 기록과 새 Worker 작업을 검증했다. [설정값](../platform/deploy/GITHUB.md) · [진행 기록](ai-dlc/platform-oidc.md)
- 플랫폼 CI 수정(2026-11-03): PR #7의 앱 테스트는 통과했고 Terraform은 Linux용 Provider 검증값 누락으로 실패했다. 깨끗한 Linux 환경에서 재현한 뒤 lock 파일에 해당 검증값을 추가해 `init`·`validate`·mock 3개 통과를 확인했다. 최종 PR Checks는 모두 성공했고 PR #7은 병합됐다. [원인·검증 기록](ai-dlc/platform-aws.md#ci의-linux-검증값-수정)
- 플랫폼 U1(2026-11-03): 사용자 구현 승인 후 고정 로그인·공개 GitHub 소스 확인·PostgreSQL 상태/이력·API/Worker·웹을 구현했다. 실제 PostgreSQL 통합 56개, 실제 GitHub 1개, 브라우저 3개를 통과했고 ARM64 이미지의 실제 검사·재시작 후 상태 보존도 확인했다. 추가 지시에 따라 AWS 업무 계정 616723161368/서울에 별도 기반을 배포했다. HTTPS 로그인·실제 GitHub 확인·재시작·S3 백업의 임시 DB 복원·브라우저를 검증했고 최종 Plan은 No changes다. GitHub 워크플로 3개를 게시했고 PR 테스트 실행을 시작했다. AWS OIDC 연결·GitHub를 통한 실제 배포는 미실행이다. 구현 커밋 `5390c63`을 `feat/platform`에 푸시하고 [PR #7](https://github.com/2026-sbh-delta/delta-network-gateway/pull/7)을 열었다. 최종 GitHub 테스트 상태는 PR Checks를 따른다. [U1 기록](ai-dlc/platform-u1.md) · [AWS 배포](ai-dlc/platform-aws.md)
- 플랫폼 작업 시작(2026-11-02): 로컬 `feat/platform` 브랜치와 전용 `platform/` 디렉터리를 준비했다. 웹, 백엔드 API/Worker, 계약, VM 배포 자동화, HTML 설명을 분리한다. 당시 U0는 구조·설계 문서 단계였고 기능 구현·테스트·배포는 수행하지 않았다. 현재 U1 상태는 위 항목을 따른다. 구조·링크·ignore 규칙 검증 후 사용자에게 Conventional Commit 메시지를 제시했고 로컬 커밋 승인을 받았다. U0 커밋은 `0567762`이며 이후 U1과 함께 PR #7에 게시했다. 후속 커밋도 사전에 알린다. [작업 영역](../platform/README.md) · [설계/진행 기록](ai-dlc/platform.md)
- AI 판단 서비스 SAST 경로 제거(2026-11-02): `services/delta-ai/`에서 코드 보안 판단(`code_security`, SARIF 입력, 코드 패치)을 뺐다. SAST는 별도 서비스가 SARIF와 코드 흐름으로 다시 설계한다. 환경 차이 판단과 피드백 규칙 후보·승인 API는 유지하며, 승인 규칙을 적용할 코드 검사는 아직 없다. 같은 날 `.venv/bin/pytest -q` 66개 통과, `scripts/demo.py fake` 실행 완료.
- SAST 점검 서비스: `services/delta-sast/`에 빠른 모드(Opengrep 위치 표시 → 자체 AST 경로 → 결정적 분류 → 애매한 경로만 LLM → SARIF)와 HTTP 도구 API를 구현했다. 2026-11-03 테스트 38개 통과, Claude Code 로그인으로 실제 LLM 판단 1건 확인, 게시판 예제 SARIF를 DAST에 넣어 저장형 XSS 2건 `REPRODUCED`까지 왕복 확인. 2026-11-03 보안 도구 호스트(`terraform/tooling-aws/`, `deploy/tooling/`)에서 SAST → DAST(AWS 스테이징) → SAST 피드백 체인을 LLM 없이 실행했다(합계 22.3초). 전체 LLM 검증 모드와 서버의 LLM 연동은 미구현이다. [설계/검증 기록](ai-dlc/sast.md)과 [실행 안내](../services/delta-sast/README.md)를 따른다.
- 스테이징 도구: 관리 에이전트가 호출하는 파이프라인 스테이징 도구(`services/delta-staging/`)와 AWS 기반 모듈(`terraform/staging-aws/`). 2026-11-02 mock `terraform test` 11개 통과, 2026-11-03 도구 단위 테스트 34개 통과. 2026-11-03 Apply 후 실제 ECS 배포 확인: 첫 배포 56초, 이미지 교체 57초, 내리기 3초. [설계/검증 기록](ai-dlc/staging-tool.md)을 따른다.
- AI 판단 서비스: `services/delta-ai/`에 `2026-sbh-delta/delta-ai` `007d1c6`을 subtree로 추가했다. 2026-11-01 `.venv/bin/pytest -q` 98개 통과, `scripts/demo.py fake` 실행 완료. Terraform 변경은 없고, Dockerfile/배포/다른 서비스와의 실제 연동은 미구현이다. [실행 안내](../services/delta-ai/README.md)를 따른다.
- AI DAST 점검 서비스: `services/delta-dast/`에 추가했다. 2026-11-03 Delta SAST SARIF를 입력 형식으로 받게 했다. SAST 결과를 받아 스테이징에서 저장형 XSS를 재현·판정하고 수정안·보고서를 만든다. 2026-11-02 기존 가상환경으로 주요 모듈 import와 `compileall` 성공을 확인했다. 자동 테스트는 아직 없다. 컨테이너 이미지/배포/delta-ai 연동은 미구현이다. [실행 안내](../services/delta-dast/README.md)와 [API 계약](../services/delta-dast/docs/API.md)을 따른다.
- CloudFront ANF 웹: 사용자 지시로 private S3/OAC의 두 버튼 페이지와 Client VPC internal ALB/ALB VPC Origin을 배포했다. URL은 https://d228fpdue5s0by.cloudfront.net/ 이다. API는 ALB→TGW→중앙 ANF→기존 사설 Server로 연결한다. 웹200/허용8080 HTTP200/차단8081 HTTP504와 SID9000000 blocked/drop, 허용 마커 SID2000001 및 양방향 FLOW를 실제 대조했다. 새 경로 mock21개/중앙 회귀35개 통과. 직접 Origin/Server IGW/임시 ingress 정리와 검증 JSON 게시, 네 root No changes까지 확인했다. 브라우저 차단 버튼 완료 화면 재확인은 도구 오류로 미검증이며 사용자 지시로 이번 변경을 분할 커밋하고 origin/main 게시 성공을 확인했다. [승인/실제 결과](ai-dlc/cloudfront-anf-demo.md)를 따른다.
- CloudFront Git/푸시: 구현 `5c807f58`, 회귀 검증 `1801463e`, 운영 문서 `12587b42`로 분할 서명 커밋했다. 2026-11-01 21:00 KST `git push origin main` 성공과 `git ls-remote --heads origin main`의 `12587b42600ba05fe3aa6564e427cd52edd4f6c6` 일치를 확인했다. 당시 작업 트리는 깨끗했으며 이번 문서 갱신은 해당 게시 결과를 기록한다. 실제 입력/backend/state/Plan/원본 증거는 제외했다.
- 이전 CloudFront 직접 EC2 Origin 시험은 중앙 ANF를 경유하지 않았다. 당시 배포/HTTPS/보안 검증은 [과거 기록](ai-dlc/cloudfront-server.md)에 보존하고, 현재 구성과 구분한다.
- Ideation: 사용자 승인 완료.
- Inception 및 U1~U5 Design / Implementation Plan: 사용자의 `PLEASE IMPLEMENT THIS PLAN` 지시로 승인 완료.
- Construction: 중앙 U1~U5와 후속 Client 역할 구현/Review 완료. 중앙 mock Plan은 같은 계정 Client/Backend 사례를 포함한 최신 회귀 실행에서 34개 통과했다.
- AWS Rule Group DryRun: SCP 수정 후 재시도한 두 규칙 집합 모두 통과. 초기 예제 소비 용량은 기본 차단 1, TCP/UDP/ICMP 및 추가 서명 5. 실제 Server 시험 규칙도 DryRun 소비 용량 3으로 수락됐고 두 VPC HTTP 허용/차단을 실제 검증했다.
- 중앙 실행 입력: `terraform.tfvars` 작성 완료. VPC `10.250.0.0/16`, Subnet 4개는 `/24`, AZ IDs는 `apne2-az1`, `apne2-az2`. 실제 입력으로 격리된 중앙 기반 mock Plan 1개 통과.
- Backend: S3 초기화 메타데이터 확인. Bucket `sbh-delta-central-tf`, encrypt/use_lockfile=true, 허용 계정 `482840608926`. 현재 `backend.hcl`과 초기화된 bucket/key/region이 일치한다.
- 배포: 중앙 Inspection과 Workload 계정의 사설 Client/Server 두 VPC 배포 완료. 이번 Server 기반 23개와 반환 Route 2개, 중앙 연결 9개/정책 2개 갱신, Client 원격 Route/egress 4개를 검토 후 적용했다. 기존 Client 교체/리소스 삭제는 없다. Attachment available/Spoke associated, Firewall/두 Primary Endpoint READY 및 IN_SYNC, 검사/반환 Route active를 실제 확인했다.
- RAM: 두 Share와 Resource Association에 이어 업무 계정 `616723161368`의 두 principal 연결을 추가했다. 업무 계정에서 중앙 TGW available을 조회했다. 조직 외 공유는 비활성화다.
- 로그/통신: Client `10.10.1.143` → Server `10.20.1.10:8080` HTTP 200 및 같은 flow_id/AZ의 양방향 FLOW 확인. 8081은 timeout과 SID 9000000 blocked/verdict=drop, 마커 SID 2000001은 HTTP 200과 allowed/verdict=alert를 실제 요청 source port와 대조했다. 같은 계정 두 VPC의 ANF 왕복 검증 완료다.
- 실계정 Plan: 실제 저장 Plan의 계정/변경 목록/보안 속성을 검토한 뒤 적용했다. 배포 후 중앙/Client/Server 세 root의 최종 Plan 모두 No changes, 상세 종료 코드 0이다. Terraform SSO refresh 오류는 유효한 CLI 역할 자격 증명을 메모리에서만 전달해 재검증했다.
- 기존 기반 Git/푸시: 기존 Client 배포의 원격 체크포인트는 `9033794`다. 후속 변경을 GCP 구현 `7c31c885`, GCP 검증 `fd9abcd1`, AWS Server 구현 `d62eaae8`, Server/중앙 회귀 검증 `86659848`, 배포 기록 `14ed879c`로 분할 커밋했다. 2026-11-01 19:25 KST `git push origin main` 성공과 `git ls-remote --heads origin main`의 `14ed879c37aa29b1b6582299a24a623b6f770ec2` 일치를 확인했다. 당시 작업 트리는 깨끗했으며 이번 문서 갱신은 해당 게시 결과를 기록한다. 실제 tfvars/backend/state/Plan은 제외한다.
- 과거 2026-11-03 검증: 전체 Destroy는 미실행이며 당시 ANF 부분 제거/원복 검증 뒤 disabled로 마쳤다. 당시 같은 계정 두 VPC의 HTTP 허용/차단/로그를 확인했다. 현재 AWS enabled와 GCP 사설 통신의 실제 검증은 맨 위 2026-11-07 재개 기록을 따른다. 물리 장애 전환/부하/TLS 복호화 검증으로 확대하지 않는다.
- 실행 프로필: `AWS_PROFILE=sbh-delta-security`. 이번 DryRun에서 STS로 중앙 계정 `482840608926`을 재확인했다. 코드에서는 계정 ID를 필수 입력으로 받아 검증한다.
- AWS Workload: 사용자 `이렇게 만들고, public 노출없이 해` 지시로 Inception과 U1~U3 승인 및 구현/Review 완료. Workload mock Plan 26개 및 state bootstrap 보호 테스트 3개 통과. 별도 S3 state와 VPC `10.10.0.0/16`, `/24` Subnet 4개, 사설 EC2 `10.10.1.143` 및 EIC Endpoint 생성 완료. 초기 배포 때 공인 IP/IPv6, IGW/NAT/인터넷 Route 부재와 EIC 사설 SSH 성공을 확인했다. 후속 CloudFront root가 Client VPC에 IGW와 ALB 전용 Subnet/RT를 추가했으며 공인 주소/인터넷 Route는 추가하지 않았다.
- AWS Server: 같은 Workload 계정의 별도 root/state에 VPC `10.20.0.0/16`, `/24` Subnet 4개, 사설 EC2 `10.20.1.10`와 EIC Endpoint/TGW Attachment를 생성했다. Server mock 31개/로컬 앱 1개, 중앙 회귀 34개, Client 회귀 26개와 기존 bootstrap 보호 3개 통과. 세 root 최종 Plan No changes와 실제 ANF 통신/로그 검증 완료. 구현/검증/배포 기록을 분할 커밋하고 origin/main 게시를 확인했다.
- 이전 GCP 프로젝트의 과거 기록: 사설 서버/HA VPN 구현과 Google mock Plan 25개, 실제 입력 mock Plan 1개, 앱/전달 도구 7개 통과를 기록했지만 현재 tracked tree에는 당시 `terraform/gcp-server` 코드가 없다. 이전 Project `project-5bb650b3-9c21-4c8b-a16` / Number `120643807854`의 2026-11-01 조회는 Billing 계정 `open=false`, 프로젝트 `billingEnabled=false`, Compute/IAP/OS Login API DISABLED였다. GCS bucket/실제 Plan/Apply/VPN 통신은 미실행이었다. 당시 준비/게시 기록은 [과거 AI-DLC](ai-dlc/gcp-server-vpn.md)에 보존하고 새 프로젝트의 상태와 구분한다.
- 스테이징 도구: 관리 에이전트가 호출하는 파이프라인 스테이징 도구를 `services/delta-staging/`, AWS 기반을 `terraform/staging-aws/`에 추가했다. 2026-11-03 도구 단위 테스트 34개 통과, Apply 후 실제 ECS 배포 확인(첫 배포 56초, 이미지 교체 57초). GCP 구현은 미실행이다. [AI-DLC](ai-dlc/staging-tool.md)를 따른다.

## 문서

- [중앙 GuardDuty Finding의 플랫폼 로그 전달 설계와 승인 상태](ai-dlc/aws-guardduty-logging.md)

- [AWS Workload와 Network/Security의 GuardDuty 활성화 설계 및 승인 상태](ai-dlc/aws-guardduty.md)

- [Sample App의 GCP ALB/x86 VM/Cloud SQL 교체와 승인 상태](ai-dlc/gcp-sample-app.md)

- [Sample App의 EC2/RDS 배포와 승인 상태](ai-dlc/aws-sample-app.md)

- [AWS FE/BE의 단일 x86 EC2 Compose 전환안](ai-dlc/aws-compose-consolidation.md)

- [GCP 중앙 네트워크와 AWS 로그 수집 요구사항/구현 계획](ai-dlc/gcp-central-network.md)
- [ANF 비용 절감과 검사 경로 원복](ai-dlc/anf-cost-mode.md)

- [AWS → GCP 매핑 판단](ai-dlc/migration-mapping.md)
- [Jenkins 배포와 AI 판단 로컬 검증](ai-dlc/jenkins-local.md)
- [개발자용 플랫폼 설계·구현 단위](ai-dlc/platform.md)
- [플랫폼 U1: 구현·검증 기록](ai-dlc/platform-u1.md)
- [플랫폼 U2: 공개 저장소 이미지 빌드](ai-dlc/platform-u2.md)
- [플랫폼 U3: 샌드박스 실행 확인](ai-dlc/platform-u3.md)
- [플랫폼 Jenkins 빌드 전환](ai-dlc/platform-jenkins.md)
- [Jenkins AWS 운영 연결](ai-dlc/platform-jenkins-aws.md)
- [플랫폼 SAST 결과 연결](ai-dlc/platform-security.md)
- [플랫폼 DAST·검사 환경 연결](ai-dlc/platform-dast.md)
- [플랫폼 AWS 배포·운영 기록](ai-dlc/platform-aws.md)
- [플랫폼 GitHub OIDC 연결](ai-dlc/platform-oidc.md)
- [플랫폼 구성 설명 HTML](../platform/docs/overview.ko.html)
- [SAST 점검 서비스 설계(빠른 모드·전체 모드)](ai-dlc/sast.md)
- [파이프라인 스테이징 도구 설계/검증 상태](ai-dlc/staging-tool.md)
- [CloudFront 웹의 중앙 ANF 허용/차단 시험](ai-dlc/cloudfront-anf-demo.md)
- [CloudFront 직접 EC2 VPC Origin](ai-dlc/cloudfront-server.md)
- [CloudFront 시연 제거와 워크로드 통합](ai-dlc/aws-workload-consolidation.md)
- [승인된 요구사항과 구현 단위](ai-dlc/central-network.md)
- [AWS Workload 클라이언트 구성안과 승인 상태](ai-dlc/aws-workload-client.md)
- [같은 Workload 계정의 Server VPC와 실제 ANF 검증](ai-dlc/aws-workload-server.md)
- [GCP 사설 서버와 HA VPN 설계/실제 상태](ai-dlc/gcp-server-vpn.md)
- [실행 및 연결 안내](../terraform/network-security/README.md)
- [공통 Workload 실행 및 전체 입력/출력](../terraform/workload/README.md)
- [공통 Workload 운영 Runbook](../terraform/workload/RUNBOOK.md)
- [GCP 중앙 네트워크 실행과 전체 입력/출력](../terraform/network-security-gcp/README.md)
- [GCP 검사 중단/재개와 관제 Runbook](../terraform/network-security-gcp/RUNBOOK.md)
- [플랫폼 AWS/GCP 중앙 로그 수집과 운영](../platform/deploy/logging/README.md)
- [이전 GCP와 AWS VPN 준비/검증 기록](ai-dlc/gcp-server-vpn.md)
- [파이프라인 스테이징 도구 설계/검증 상태](ai-dlc/staging-tool.md)

## 다음 작업

AWS ANF는 [delta-board-vpc 한정 재개](ai-dlc/anf-board-reactivation.md)를 완료해 enabled로 운영한다. 후속 요청으로 공통 기본 차단을 해제했으므로 현재 미등록 통신도 이 규칙으로 차단하지 않는다. 복귀가 필요하면 [기본 차단 복귀 Runbook](../terraform/network-security/RUNBOOK.md#기본-차단-규칙-제거와-복귀)에서 `default_deny_enabled=true`의 새 저장 Plan을 검토한다. 네 Endpoint/정책과 기존 경로/로그 점검은 계속하며 DNS 전환과 코드/문서 Git 게시는 이번 제거 작업과 구분한다.

중앙 GuardDuty 로그 전달의 [U4/U5 구현/적용/실제 수신 검증](ai-dlc/aws-guardduty-logging.md)을 완료했다. 운영 시 [조회와 전달 중지/재개 절차](../terraform/network-security/RUNBOOK.md#guardduty-finding-로그-전달)를 따른다. 현재 실제 플랫폼 그룹은 `/delta-cp/central/*`이지만 플랫폼 Terraform 코드는 이전 이름을 정의하므로 해당 root를 실행하기 전에 별도로 수렴 범위를 검토한다. 대시보드 UI 검증은 미실행이며 게시 상태는 Git 이력과 원격 ref를 따른다.

AWS 앱은 2026-11-07 후속 외부 HTTPS 검증에서 화면과 API가 HTTP200이며 environment=aws를 반환했다. 2026-11-05의 [Compose 중지 및 재개 절차](ai-dlc/aws-compose-consolidation.md#2026-11-05-후속-operation-compose만-내리고-ec2-보존)는 당시 기록이다. 현재 원격 Compose/systemd 상태는 이번 작업에서 조회하지 않았으며 재시작/배포도 수행하지 않았다.

GCP 앱은 사용자 요청으로 Compose를 내린 상태이며 기존 FE VM 한 대와 Cloud SQL/ALB/이미지/설정은 유지한다. 재개가 필요하면 [중지 및 재개 절차](ai-dlc/gcp-compose-consolidation.md#후속-operation-compose만-내리고-vm-보존)의 `sudo systemctl enable --now delta-compose`를 사용한다. 중지 작업에서는 재개와 추가 Terraform Apply를 실행하지 않았다. Git 게시 상태는 Git 이력을 따른다.

GCP Sample App의 승인된 교체와 실제 배포/검증을 완료했다. [운영 Runbook](../deploy/workload-gcp/README.md)으로 개별 재배포/복귀하고, 최초 배포 당시 미커밋 PostgreSQL snapshot의 게시 상태는 앱 저장소 Git 이력을 따른다. 실제 상태/해시와 테스트 결과는 [교체 기록](ai-dlc/gcp-sample-app.md)을 따른다.

AWS Sample App 최초 두 EC2의 실제 배포/검증은 [기존 기록](ai-dlc/aws-sample-app.md)에 보존한다. 이후 단일 EC2 Compose 전환을 완료했고 현재는 위 사용자 요청으로 앱만 중지했다. 기존 네트워크/state/GCP 경로를 유지한다.

GCP `term2-delta`는 2026-11-07 사설 검사를 재개해 enabled로 운영한다. 중단/재개 시 [GCP Runbook](../terraform/network-security-gcp/RUNBOOK.md)의 단계별 저장 Plan과 ACTIVE/실제 통신/Threat 원본 수신을 확인한다. AWS 모드와 경로는 별도 AWS 재개 작업을 따르며 공개 앱 L7 정책 추가, 공통 자동화와 물리 터널/Zone 장애 검증은 이번 완료 범위에 포함하지 않는다.

과거 ANF disabled 유지 기록은 2026-11-07 AWS 재개로 대체됐다. ignored `terraform/network-security/inspection-mode.auto.tfvars`는 현재 enabled이며 Endpoint 시간 요금과 검사량 요금이 발생한다. 비용 중단이 필요하면 먼저 인터넷/GCP 경로를 함께 복귀하고 Secondary 참조를 정리하는 현재 Runbook을 따른다. 과거 VPC 두 개만 대상으로 한 원복 명령을 현재 인터넷/VPN 구성에 그대로 적용하지 않는다.

플랫폼의 Jenkins 운영 전환·ECR·ECS 실행/종료와 공개 웹 SAST 검증을 완료했다. PR #20은 사용자가 병합했고 설정 활성화와 GitHub Release도 성공했다. DAST 코드·화면·취소/정리 계약을 구현했다. PR #21/#22를 병합했고 DAST 사설 네트워크와 관측 조회 IAM Apply를 마쳤다. 도구 이미지·런타임 갱신·GitHub Release와 실제 후보0건 처리를 확인했다. 실제 XSS 재현은 별도 후속이다. GCP·온프레미스로 이전할 때에는 각 대상 환경의 실행기를 연결하고 별도로 검사한다. 이후 AI 수정 제안·사람 승인·재빌드/재검증, 고정 digest의 Terraform/VM 배포로 연결한다. 이 후속 전체 흐름은 아직 플랫폼에서 구현·검증되지 않았다.

CloudFront 두 버튼 웹의 ANF 허용/차단 대조와 네 root의 No changes는 과거 시연 검증 기록이다. 현재는 사용자 승인으로 시연 환경을 정리하며, 남은 화면 재검증은 진행하지 않는다. 실제 삭제와 최종 네트워크 상태는 [통합 기록](ai-dlc/aws-workload-consolidation.md)을 따른다.

과거 **동일 Workload 계정 두 VPC 간 ANF 왕복 검사**에서 기존 Client와 Server 사이 HTTP 200, 미허용 8081의 blocked/drop, 양방향 FLOW와 허용 마커 ALERT 및 당시 세 root의 No changes를 확인했다. 해당 두 EC2와 Server VPC는 현재 통합 작업의 삭제 대상이다.

GCP `term2-delta`와 AWS 중앙 TGW 사이에 총4개 IPsec/BGP 터널을 적용했고, AWS Client와 GCP VM, GCP VM과 AWS Server의 두 포트 사설 HTTP200을 실제 확인했다. 네 단일 BGP 세션의 경로 철회/복구를 검증했으며 물리 터널이나 Zone 장애 검증과 구분한다. GCP 검증은 [GCP AI-DLC](ai-dlc/gcp-central-network.md), 앞선 AWS 두 VPC 검사 기록은 [Server AI-DLC](ai-dlc/aws-workload-server.md)를 따른다.

이전 GCP 프로젝트의 공개 이미지 조회와 mock/앱 검증은 과거 기록이다. 새 프로젝트는 Billing 활성, Project Number/권한, 기존 CIDR/쿼터와 Provider 지원을 별도로 확인했고 실제 배포와 통신/탐지/로그 도착을 검증했다. 2026-11-07 Endpoint를 재생성해 사설 L7 ALERT 탐지를 재개했으며 기존 라우팅/기본 규칙/중앙 로그를 보존했다.

중앙 실행 CIDR/AZ와 GCP 중앙/업무 CIDR은 기존 AWS/GCP Subnet과 비중복을 확인했고 GCP VPN 계약을 등록/적용했다. 추가 계정이나 온프레미스를 연결할 때는 각 Prefix와 기존 경로의 중복을 별도로 확인한다.

RAM 오류 진단은 두 Association의 실제 성공 확인으로 종료했다. 관리 계정 프로필 로그인은 더 이상 요청하지 않는다. Organizations는 이번 작업에서 변경하지 않는다. Destroy의 최신 실행 결과는 [워크로드 통합 기록](ai-dlc/aws-workload-consolidation.md)을 따른다. 실제 `terraform.tfvars`, `backend.hcl`, state/Plan, 검증 로그와 Provider 캐시는 커밋 대상에서 제외한다.

- 콘솔 사례 검토·승인·평가셋 추출(2026-11-06): 기존 관리자 화면과 전용 검토 권한의 연결을 로컬 구현했다. 최종 회귀562개·로컬 브라우저6시나리오 통과. 기존 AWS AI/콘솔에 배포·분리 권한/프록시 연결·실제 화면 승인/평가셋 다운로드/취소·재시작 검증 완료, 기존45실행/118파일 보존. main 병합은 별도다. [설계·검증 상태](ai-dlc/case-review-console.md)
