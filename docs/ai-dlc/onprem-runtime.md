# 기존 오케스트레이터 온프레미스 실행·복구 AWS 연결

2026-11-06 사용자 승인: 남은 온프레미스 실행/복구를 기존 오케스트레이터에 연결하고 실제 AWS에서 진단/보고서까지 검증한다.

## 설계와 범위 — 로컬 ADR 제안

최신 검증된 진단/중앙 로그 릴리스2d397a3 기반 별도 worktree에서 기존 시험본의 제한 자동 복구·사람 조치 후 재개를 이식한다. 팀의 FE/BE 묶음·GCP·운영 release 계약을 보존한다. 기존 AWS 플랫폼 Worker의 큐/잠금/소유권/취소/정리를 재사용하며 플랫폼 소스·이미지·설정·고객 DB·PVC·SG/ACL은 변경하지 않는다.

전송은 시험본의 임의 AWS-RunShellScript 대신 고정 코드/버전 해시를 가진 전용 SSM 문서로 제한한다. 호출자는 고정 Worker 한 대와 해당 문서만 SendCommand할 수 있다. 요청은 허용 action/UUID/멱등 키/JSON 스키마로 검증하고 비밀값은 Worker 안에서만 사용한다. Store·DB·Kubernetes 토큰을 다른 호스트에 복사하지 않는다.

온프레미스는 등록된 성공 단일 이미지 build를 선택하고, 같은 commit을 현재 실제 SAST로 검사한 뒤 native sandbox_check를 실행한다. 현재 Worker의 과거 SAST/DAST URL은 연결 불가로 확인됐으므로 이를 정상 결과로 만들지 않는다. 실행 전용 결과는 DAST 미실행으로 보고서/사람 확인에 남긴다. FE/BE/DB 전체 앱 이전·DAST 공격/운영 승인·DNS 전환은 이 연결의 성공으로 간주하지 않는다.

복구는 같은 ID/키 조회·재접수와 정리 확인된 동일 이미지 재배포에 한정한다. 자동 최대2회·사람 재검사 최대2회, 재개 전에 소유권/commit/image/target·SAST/Pod/Secret 정리를 재검증한다. 실제 시험의 호출자 응답 유실은 고정 run/build와 만료시간에 묶인 운영자 전용 시험 파일로만 활성화하고 시험 뒤 끈다. 인프라/네트워크 중단 시험이 아니다.

## 작업 단위·성공 기준

1. 기존 GCP/FE·BE/로그/보고서 회귀를 유지하며 onprem/recovery/assistance를 선택적으로 연결한다.
2. 전용 SSM 문서와 최소 IAM을 Terraform Plan/Apply로 관리하고 실제 역할 호출을 확인한다.
3. 기존 콘솔/API에서 등록 build·온프레미스 대상·재개/취소를 선택할 수 있게 한다.
4. AWS 실제 오케스트레이터 → 실제 SAST → 기존 Worker/k3s → 응답 유실 복구/실제 Claude 진단·제안 → 사람 재검사 → UID/digest/HTTP/정리·보고서를 확인한다.
5. 기존 실행·설정·로그/진단과 앱/DB/PVC 보존, 시험 Pod/Secret 부재·점유 해제·보고서 인증을 확인한다.

승인된 기존 기능 연결 범위로 설계/구현/배포를 진행한다. 미실행 결과는 통과로 기록하지 않는다.

## 중간 검증과 통합 기준 갱신

- 초기 파이프라인/오케스트레이터 회귀153개, 강제 종료·동일 키 재개 포함. 실제 로컬 PostgreSQL/Store/Worker/native runner 통합28개 통과. 외부 K8s/스캐너/모델 응답은 fixture이며 실제 클라우드 시험은 별도다.
- 고정 SSM 문서/정책 생성 후 실제 Worker의 등록 성공 build 목록3개 조회 성공. 기존 Worker의 SAST/DAST 옛 URL은 연결 불가이며 임의로 설정을 고치지 않았다.
- 배포 직전 실제 오케스트레이터가 별도 후속 이미지7593dbea/실행33개로 변경됨을 발견했다. 서비스 코드를 읽어 c6bfb9a를 공통 기준으로3방향 통합했다. 후속 변경은 운영 대상/서명 승인 연결·ECR/AR 같은 digest 사본·운영 배포 제한3600초와 화면이다. 진단/로그 경로는 새 이미지에서 빠져 있어 함께 복원했다. 팀 변경 제거/역행 배포를 하지 않는다.
- 소스 스냅샷/통합 증거는 notes/onprem-runtime-20261106/source-*.json과merge/에 있다. 사용자 비밀번호/서명 키/설정 값은 조회·복사하지 않았다. 실제 배포는 해당 이미지 일치와 idle 검사를 통과할 때만 수행한다.


## 2026-11-06 실제 AWS 최종 결과

- 구현/배포 완료. 브랜치 `codex/onprem-orchestrator-runtime`, 서비스 스냅샷 `6b8c405`(기능 b5e8ea5, Terraform mock 보완64a7adc). 런타임 ARM64 digest `613c3c648508b7b9589ad864fef5257646fc81941a97a12aa815f6a785524c38`. push/PR/main 병합은 미실행이다.
- 실제 팀 운영 변경7593dbea의 release·서명·ECR/AR 묶음과 기존 환경/로그/마운트를 보존했다. 최종 정상화 재시작에서도 영속63파일 전체 해시 보존을 확인했다. 별도 고정 SSM 문서와 정책만 Terraform으로 관리하며 기존 Worker/플랫폼 이미지·설정·K8s 권한·SG/ACL을 바꾸지 않았다.
- 최종 고정 문서 `delta-cp-onprem-bridge-a209f55948f0`, SHA256 `a209f55948f03723c71a499b556029a660e2350a2baebfa1b665c3e8ee5cb9af`, version1. 마스킹 수정은 문서1개 교체/정책1개 갱신/구 문서1개 삭제이며 최종 실제 Plan은 No changes다. JSON action만 받으며 최종 결과의 provider 오류도 scrub한다.

### 실제 실행1 — 자동 한도 소진·사람 재검사

`dda0ed47e9fa442aac4ff5669ee1f24a`: 기존 등록 backend 단일 이미지 build `bdc3ec8b-f5c9-46d4-a508-c9025c819f8b`/commit `fd9a661`/digest `41dfe764…`를 재사용하고 새 실제 CP SAST13파일을 검사했다. 고정 graph/build·15분 만료 시험 파일로 실제 접수 응답1회/조회 응답10회를 호출자 쪽에서만 유실시켰다. 실제 인프라 장애를 만들지 않았다.

자동 복구2회 뒤 실제 Claude 진단·wait_for_operator 제안·사람 확인으로 넘어갔다. 검증 프로그램이 별도 승인 행위자로 `retry_same_job`을 선택했다. 실제 native ID `b1b0e62d-387a-42a5-b3a5-58c2af9212cc`는 그대로이며 attempt1/sandbox_deploy1회다. 독립 조회에서 runtime passed·같은 UID/digest/commit·HTTP·cleanup stopped·Pod와 Secret 부재를 확인했다. 재검사 전후 실제 진단 보고서2개가 보존됐다. 이전34개 board 해시가 동일하고 이번 run만 stop/active=null이다.

### 실제 실행2 — 최종 이미지 회귀·화면

`0b52bda1bab844e4a7f6fb93b55e546c`: 새 실제 SAST13파일 → 동일 등록 이미지 → 접수 응답1회 유실 → 같은 키 자동 복구1회 → native `a089ccf4-2fde-4823-9717-509cd325ddd4` attempt1/배포1회 → UID·digest·HTTP·정리 검증 → 실제 Claude15.1초 → 보고서 `case-36c11c64b24863a1a0a6eb20cc174507`을 확인했다. 소스 보완으로 복구 노드는 완료로 표시된다. CUA로 기존 AWS 웹의 복구 완료와 보고서 관측/가설/로그/한계/다운로드를 직접 확인했다.

자기 실행 CloudWatch10건, 환경 GCP6건(ALLOWED3·미해석3), 해당 시간 AWS0건을 근거로 수집했다. 환경 로그는 앱과 연관성 미확인으로 구분한다. Pod는 정상 정리돼 컨테이너 로그가 없음을 한계로 남겼다. 이전35개 board 해시가 동일하고 이번 run stop/active=null, Pod/Secret 부재다.

두 실행 모두 공개 인증 JSON/MD200, 익명401, 다른 run404, 보고서 SHA256 일치를 확인했다. 정상 설정 재시작 후에도 보고서를 다시 다운로드했다. 최종 시험 파일 삭제/`DELTA_ONPREM_VERIFICATION=0`, 임시 SSM 화면 터널·로컬 테스트 PostgreSQL 종료를 확인했다.

### 실행한 검사와 남은 범위

- `PYTHONPATH=services/delta-pipeline:services/delta-ai:services/delta-orchestrator <venv>/python -m pytest -q services/delta-pipeline/tests services/delta-orchestrator/tests`: **155 passed**. 강제 종료·재개·기존 GCP/FE·BE/release/진단 회귀 포함.
- 별도 로컬 PostgreSQL의 기존 Store/Worker/native runner `tests/test_bounded_graph_recovery.py tests/test_incident_evidence.py`: **28 passed**. 외부 K8s·스캐너·모델은 fixture이고 실제 AWS 결과와 구분한다.
- Terraform `test` mock **1 passed**, 실제 Plan/Apply 및 최종 No changes. 최종 서버 소스 해시·정리·시험 OFF는 notes/onprem-runtime-20261106/verified-normal.json.
- 실제 `sandbox_check`는 **DAST not_run**이다. 미실행을 정상·보안 통과로 승인하지 않으며 보고서/사람 확인에서 명시한다. health 실패 후 재배포·취소·프로세스 종료 복구는 위 fixture 통합 검사 범위이고 이번 AWS 실제 장애 시험은 응답 유실이다.
- 등록 단일 이미지 실행 흐름 검증이다. 고객 FE/BE·DB·쓰기 전환·DNS/전체 이전, Worker의 옛 스캐너 URL 수정, 정리 전 Pod 로그 영속 수집은 후속이다. 호스트 VM의 대회 후 원복은 기존 11/08 런북을 따른다.

[최종 실제 보고서](https://d2rjxujgsi1fjg.cloudfront.net/?run=0b52bda1bab844e4a7f6fb93b55e546c&node=exception-report) · [사람 재검사 보고서 이력](https://d2rjxujgsi1fjg.cloudfront.net/?run=dda0ed47e9fa442aac4ff5669ee1f24a&node=exception-report). 관리자 로그인 필요. 증거 정본은 프로젝트 루트 `notes/onprem-runtime-20261106/summary.json`과 상세 산출물이다.
