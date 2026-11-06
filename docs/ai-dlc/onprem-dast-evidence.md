# 온프레미스 실제 DAST·정리 전 로그

## 2026-11-06 최신 검증 상태와 배포 충돌 확인

- 사용자 선택은 **팀 Worker 유지·독립 경로**다. 공유 Worker 교체 승인 대기라는 앞선 기록은 과거 상태다. 실제 shared Worker는 fea4c097 유지, 독립 실행기는 b4739d4a/소스 da2c15d다. private SQLite와 로그 디렉터리를 쓰며 팀 큐에는 기록하지 않는다. 최종 core 회귀176개 통과.
- 사용자 요청에 따라 이 채팅에서 통합 오케스트레이터를 배포한 뒤, 17:51:33와 18:01:07 KST에 다른 AWS 사용자 계정의 SSM 배포가 공용 오케스트레이터를 다시 교체했다. CloudTrail event와 CommandId를 대조했다. 사용자 본인이 직접 추가 배포했다거나 특정 팀원의 실명으로 단정하지 않는다. 근거: notes/onprem-dast-20261106/replacement-attribution.json.
- 마지막 확인 공용 이미지5c7b6cd9에는 onprem 모듈이 없다. 그 버전의 domain.py 등 새 팀 변경은 아직 우리 소스와 통합하지 않았다. 최신 팀 코드를 덮어쓰는 재배포를 하지 않는다. 초기 승인된 통합 배포와 다른 계정의 후속 교체를 구분한다.
- 검증을 위해 같은 AWS 호스트의 127.0.0.1:8081에 delta-onprem-validation/fb8314cf를 분리했다. 전용 /opt/delta/onprem-validation/state를 쓰며 공용 컨테이너는 보존했다. 외부 검증 주소/공개 화면 확인은 아직이다.
- 실제 시험 daa4646866be4178aff5385838521ec9: fresh SAST15파일·XSS후보1개, 온프레미스 FE/BE 실제 실행·연결 확인까지 진행했다. Native 92a1cebf-1328-46ad-bf99-92ca268ff4f2의 CP DAST 제출은 HTTP422로 실패했다. 실제 Claude 진단·보고서 case-1ed4b35e0df69626dfb5c5ef0113909a는 보존했다. 사람 확인에서 stop하여 active=None으로 종료했다.
- DAST의 실제 Submission 스키마·SARIF 해시·변환은 통과했다. 실제 서비스 카탈로그에는 local-fixture/staging-aws/staging-gcp만 있고 onprem.register의 기본 platform-sandbox가 없어 KeyError를 재현했다. 네트워크/CA/토큰으로 Kubernetes 서비스 GET200은 확인했지만 이 사실을 브라우저 DAST 성공으로 해석하지 않는다. 팀 DAST 소스/이미지를 수정하지 않았다. 기본 대상 템플릿의 독립 설정 또는 팀 합의 후 공용 수정이 남았다.
- 정리 전 FE와 BE 로그 모두 available, immutable artifact SHA256 ed4dda08cb9986c33f2914472dafadf763ec596b15e7317abeff82703a39ec51. 정리 뒤 두 Pod와 두 pull Secret 부재를 실제 확인했다. 실패 run의 로그 보존·진단까지 검증한 것이며 DAST 성공 흐름은 미완료다. 근거: notes/onprem-dast-20261106/stopped-validation.json, dast-request-validation.json, dast-transform-validation.json, dast-register-validation.json.
- 다음: 공용 배포 담당/시간 조정 또는 독립 검증 주소 확정 → 팀 최신 코드 보존 통합 → 온프레미스 전용 DAST 대상 설정 해결 → fresh 전체 실행과 실제 화면·인증 다운로드 검증. 공용 Worker 교체·main 병합·push·팀 Slack/Notion 발송은 하지 않았다. scanner token은 11/07 16:37 KST경 만료하므로 후속 실제 검사 전 확인한다.

2026-11-06 사용자 승인: 실제 DAST와 정리 전 컨테이너 로그 보존을 기존 오케스트레이터에 연결하고 실제 AWS에서 진단/보고서까지 검증한다. 이 요청으로 기존 연결의 후속 설계·구현·배포·시험을 진행한다. Notion ADR 번호는 임의로 만들지 않고 로컬 ADR 제안으로 남긴다.

## 설계와 성공 기준

- 현재 CP DAST는 기존 Tailscale 사설 경로가 있으나 온프레미스 대상 설정이 없고, 기존 Worker는 중지된 옛 검사 주소를 가리킨다. 옛 서버를 재가동하거나 기존 Worker의 공용 설정을 덮어쓰지 않는다.
- 고정 SSM 검사 문서를 통해 현재 CP SAST/DAST를 호출한다. Worker는 문서 해시와 두 대상 인스턴스만 호출한다. 인증 토큰은 검사 서버 안에서 해석하며 다른 호스트/채팅으로 반환하지 않는다. 임의 URL/명령/경로는 받지 않는다.
- 사용자 담당 모듈에 Worker 확장 진입점을 두고 기존 process_one·Store/heartbeat/잠금/정리/취소를 그대로 사용한다. 기존 Worker 이미지 위에 확장만 추가해 배포한다. 기존 platform/ 및 DAST 서비스 소스는 수정하지 않는다. 기존 검사 경로는 원래 실행기로 위임한다.
- 네이티브 SAST job을 실제 CP SAST UUID와 일치시키고 동일 build/commit/SARIF hash만 DAST에 전달한다. 가능한 기존 FE/BE 이미지 쌍을 같은 서비스 프로젝트/commit으로 검증해 재사용한다.
- 정리 호출 직전 실행 소유권·Pod UID·image·commit을 검증한 제한 로그를 마스킹해 기존 Worker 영속 볼륨에 원자 저장한다. 수집 실패가 정리를 막지 않으며 성공으로 가장하지 않는다. 정리 뒤 브리지가 실행 바인딩/해시를 확인해 진단 근거로 반환한다.
- namespace 전용 pods/log get만 별도 Role/Binding으로 추가한다. 기존 권한/네트워크를 넓게 바꾸지 않는다.
- 실제 AWS 새 실행에서 SAST→온프레미스 FE/BE→DAST 브라우저 시도/검사 근거→로그 저장→Pod/Secret 정리→실제 Claude→보고서를 확인한다. DAST 후보 없음/실패/해석 불가는 별도로 표시한다. 전체 안전·운영 DB/DNS 이전으로 해석하지 않는다.
- 기존 이력·설정·서명·로그 보존, 중복 실행 방지, 재시작 후 로그/보고서 유지·다운로드 인증을 검증한다. 소스/테스트/Plan/Apply/배포/main 병합 상태를 구분한다.


## 2026-11-06 배포 전 검증·승인 상태

- 서비스 소스 c2f8139. 회귀169개 통과, 새 로그 소유권/마스킹/UID 교체/영속성·고정 검사 요청 회귀 포함. Node 구문 검사 통과. Terraform mock2개 통과.
- Worker 확장 ARM64 이미지는 기존 플랫폼 fea4c097 위에 사용자 모듈만 추가한 e4b1b403이다. 기존 platform/·DAST 소스 파일 변경0이며 실제 기존 Worker 코드3파일과 로컬 기준 일치를 확인했다. 오케스트레이터 후보2f52d8e8은 아직 미배포다.
- namespace 전용 pods/log get Role/Binding2건 적용, controller의 해당 namespace yes/default no 확인. CP DAST는 기존 이미지3680a917을 유지하고 대상/24시간 scanner 토큰 설정2값만 추가했다. 기존36기록/4792파일 해시 보존을 확인했다. 토큰은11/07 16:37 KST경 만료하므로 실제 사용 전 회전 절차가 필요하다.
- 고정 검사 문서/IAM을 Apply했고 누락돼 있던 기존 브리지 역할 정책도 복원했다. 공유 Worker 배포는 자동 승인 검토에서 거부됐다. 근거는 루트AGENTS.md의 팀 영역 사전 합의 제한이며 사용자에게 공유 Worker 교체의 영향과 승인을 질문했다. 후속 사용자 질문은 영향 설명 요청으로 승인으로 처리하지 않는다. Worker 이미지·진입점은 아직 교체하지 않는다.
- 실제로 바뀌는 것은 공용 Worker 컨테이너의 image·command·검사 문서 이름/해시 환경값2개다. 기존process_one·잠금·heartbeat·취소·정리/일반작업위임을 보존하지만 공용 실행 프로세스가 바뀌므로 다른 플랫폼 작업 지연·확장 진입점 결함·후속 팀 배포 덮어쓰기·활성 작업 중 원복 위험이 있다. API/DB/운영 앱을 직접 교체하지 않는다는 의미와 공용 런타임 영향이 없다는 의미를 구분한다.
- 승인 대기 중 이미 배포된 이전 오케스트레이터의 고정문서a209f559도 유지해 기존 실행·복구/rollback 경로를 보존한다. 새 실제 온프레미스 DAST·정리 전 로그→보고서 검증은 공유 Worker 배포 승인 전에는 완료로 표시하지 않는다.


## 사용자 선택 반영 — 팀 Worker 유지·독립 경로

사용자는 공유 Worker 교체를 승인하지 않고 **팀 Worker 유지·독립 경로로 진행**을 선택했다. 공유 Worker 교체 진입점/배포 스크립트를 제거했다. 별도 `delta-onprem-executor` 컨테이너, 전용 SQLite 큐/로그 볼륨, CPU0.5/메모리512MiB/읽기 전용 rootfs를 사용한다. 기존 platform Store는 등록 이미지 조회와 기존 PostgreSQL advisory lock(72401032)에만 사용하며 연결의 default_transaction_read_only=on을 강제한다. 팀 큐·테이블에는 접수/수정하지 않고 실행 잠금만 공유한다. 기존 process_one/runner/heartbeat/취소/정리를 자기 큐에 재사용한다.

독립 소스eaf1f2d/이미지120b6a11 배포를 완료했다. 실제 팀 Worker의 컨테이너ID/Config 동일, 이미지fea4c097 유지와 DB읽기전용·전용큐준비를 검증했다. 초기/통합 회귀173개 통과. 최종브리지e0d56dc5/검사bcf96ee1 고정문서로 호출한다. 원래a209문서는롤백호환으로보존한다.

작업 중 실제 오케스트레이터가 팀953edd35로 다시 배포돼 onprem 모듈이 없는 것을 확인했다. 이전7593dbea 실측 소스를 공통 기준으로 현재953의 종료후 deployment_gate 설명과 도메인전환 완료 표시를 3방향 통합했다. 신규 진단 서비스가 활성화되면 legacy 종료후진단은중복호출하지않는다. 신규서비스가없을때는팀의기존설명경로를유지한다. 팀원platform/·DAST 소스수정0이다.

검증 오류 정정: 새 Terraform test 파일의 mock 선언이 빠져 최초 실행은 실제 provider를 사용했고 기존 브리지 inline policy가 teardown에서 삭제됐다. 이를 정본 plan으로 복원하고, 모든 test 파일에 mock_provider를 확인한 뒤 최종2개 mock검사를 통과했다. 현재 실제Plan No changes를 확인했다. 최종 실패했던 호환조회는 정책 문제가 아닌 팀 오케스트레이터 재배포로 onprem 모듈이 사라진 것이었다. 미확인 결과를 성공으로 기록하지 않는다.
