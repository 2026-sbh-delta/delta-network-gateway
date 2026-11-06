# 기존 오케스트레이터 진단·보고서 통합

## 승인·범위

2026-11-06 사용자 요청: 검증된 진단·보고서 흐름을 기존 오케스트레이터에 통합하고 실제 화면에서 확인한다. 최신 main c6bfb9a를 별도 `codex/orchestrator-diagnostics-ui`에서 확장한다. 시험본 전체를 덮지 않고 기존 FE/BE 묶음·운영 release 계약·수정 재검증·로그 이벤트·스테이징 선택을 유지한다.

## 설계 결정 — 로컬 ADR 제안, 팀 번호 미할당

정상 흐름과 승인·실행 권한은 기존 코드가 유지한다. 실패나 사람 판단이 필요한 결과에만 delta-ai 원인 분석→JSON/Markdown 저장→기존 확인 단계 또는 실패 보고서 확인을 넣는다. 보고서 저장/무결성 실패 시 중단한다. AI 장애는 분석 불가 보고서로 표시하며 자동 승인/실행하지 않는다.

공개 화면에는 분석·보고서·확인 노드와 관측/가설/한계·모델·다운로드를 추가한다. 기존 실행과 체크포인트를 보존하고 완료된 과거 실행을 새 분석으로 덮어쓰지 않는다. 실제 새 실행으로 화면과 인증·다운로드를 확인한다.

## 작업 단위·성공 기준

1. 검증한 contract/client/report/exception 경로를 최신 main에 이식, 기존 bundle/release 테스트 유지.
2. 인증 보고서 API와 화면 통합, 실행/사례 범위·이전 회차·변조 검증.
3. 커밋한 소스로 ARM64 이미지 생성. 기존 설정과 로그/볼륨을 보존하고 SSM의 기존 진단 연결만 활성화. 후보 점검·idle 검사·rollback 가능한 교체.
4. 새 실제 스캐너 실행→실제 진단→화면/보고서·미승인 대기·stop/lease 해제 및 기존 기록 보존 검증.

## 진행 상태

설계는 위 사용자 실행 요청 범위로 승인됨. 구현·로컬 커밋·이미지·실제 런타임 교체·화면 검증 완료. GitHub push/main 병합·팀 Slack/Notion 쓰기는 미실행이다.

## 실제 검증 결과

- 최신 main과 이전 실행 중이던 pipeline/orchestrator 소스14개 해시가 일치함을 확인했다. 별도 시험본 전체가 아니라 진단 경로만 이식했다.
- 준비된 Python venv, 해당 worktree 서비스 경로의 PYTHONPATH로 `python -m pytest -q services/delta-pipeline/tests services/delta-orchestrator/tests --tb=short --junitxml=../notes/orch-diagnostics-release-20261106/regressions.xml` → **136 passed, 14.64초**. 기존 FE/BE bundle·수정 루프·release 계약·체크포인트 회귀 포함. `node --check …/static/app.js`, `git diff --check`, 배포 도구 compile 통과.
- 이미지 서비스 소스5a67fb9, 기존 ECR digest `sha256:e2ba16d83f8c9081badfae281be3b80d0bc3dc2538860fc113630fc99c16d425`. 배포 복구 helper 보완093ef76. 최초 이미지c469fa6에서 기능을 검증한 뒤 UI만 최종 이미지로 교체했다.
- 후보 컨테이너는 state mount 없이 기존 진단 서버의 TLS/인증·빈 JSON422를 확인했다. preflight 모델 호출0, 그래프 실행0. 설정/로그/볼륨 보존, idle 재확인, 실패 시 이전 컨테이너 복구를 적용했다.
- 최초 교체33개/최종 UI 교체37개 기존 영속 파일의 모든 해시가 동일했다. 이전 컨테이너는 stopped 상태로 보존했다. 자격증명/SG/IAM/DNS 변경 없음.
- 새 실제 pipeline `8f177a83622b4a62a95fb8666eb141f2`: 게시판731ebbb→SAST15파일/후보1건→FE/BE build→GCP staging revision00007-gvw→DAST `6fe1589a-feb1-5020-b921-1062f7e779e2`의 저장형XSS1건 재현→**배포된 오케스트레이터 자체의** 실제 Claude Sonnet 진단→JSON/Markdown 저장→기존 DAST 사람 확인 단계의 보고서 표시. 분석13.4초, status=analyzed.
- 실제 공개 CloudFront 경로에서 인증 JSON/Markdown200, 익명401, 다른 실행404와 보고서 해시를 확인했다. 재배포·재시작 후 보고서 JSON/Markdown이 바이트 단위로 같았다.
- 실제 런타임의 브라우저 화면을 기존 SSM 권한의 일시 루프백 포워딩으로 확인했다. 진단 그룹·실행 중인 노드 표시·관측/원인 후보/한계·실행/코드 근거·JSON/MD 다운로드를 확인했다. 공개 로그인 보호는 그대로다. 세션 플러그인은 AWS 공식 배포본을 /private/tmp에만 내려받아 사용했고 시스템 설치는 하지 않았다.
- 이번 실행만 API로 stop, lease=released·active=null. 이전27개 실행 board 해시를 보존했다. 신규 운영 앱 release·DNS 변경·수정 PR 없음.
- 최종 근거: 작업 루트 `notes/orch-diagnostics-release-20261106/{summary.json,report-check.json,report-after.json,release-final-result.json,diagnosis.md}`.

실제 화면: https://d2rjxujgsi1fjg.cloudfront.net/?run=8f177a83622b4a62a95fb8666eb141f2&node=exception-report (기존 로그인 필요). 테스트가 끝나 중단 상태로 남았으며, 이는 XSS를 승인해 넘긴 실행이 아니다.

## 담당과 현재 제공 범위

오케스트레이션의 핵심은 검사/배포 도구를 순서대로 묶고, 상태·승인·실패 경로를 관리하는 것이다. 프로젝트 인계 기준 연재 담당은 그래프·Jenkins·AI 판단/진단·문서, 준우는 SAST/DAST, 동규는 플랫폼/GCP/온프레미스 연결이다. 현재 코드에는 준우의 실제 도구·스테이징·수정 루프 연결도 포함된다. 따라서 단독 소유의 새 구동기를 추가하지 않고 공동 main에 연재의 진단 기능을 통합했다. 플랫폼의 서비스 워크스페이스(delta.mungji.com)와 이 파이프라인 콘솔은 별도 화면이다.

| 영역 | 현재 이 오케스트레이터 | 남은 연결/검증 |
|---|---|---|
| 실행 제어 | 소스/커밋 고정, 빌드∥SAST, SQLite 재개, 1개 활성 실행, 사람 게이트 | 여러 서비스 동시 실행·사용자별 권한/감사 |
| 검사 | 실제 SAST·GCP FE/BE staging·PG18 초기화·DAST·라이브 로그/브라우저 | AWS/온프레미스 전체 흐름과 일반화된 대상 선택 |
| 진단/보고서 | 이번에 운영 콘솔까지 연결, 인증 다운로드/무결성/근거 보존 | 상세 DAST 증적·문서 근거 확장, 고급 복구는 별도 |
| 코드 수정 | 수정 PR 후 새 커밋으로 빌드·SAST·DAST 1회 재검증 코드 | 이번 검증에서는 수정/승인을 실행하지 않음 |
| 운영 배포 | FE/BE 묶음·서명 승인·Compose release 계약 및 도구 구현 | 현재 공개 API/factory/화면에서 운영 release 도구까지 연결 미완 |
| 인프라/보안 이전 | 별도 차분 서비스/시험본 존재 | 이 runtime의 차분→plan→승인→apply/SCC 흐름 미연결 |
| DB/트래픽 이전 | DNS 전환 코드 및 별도 이전 도구 존재 | 실제 데이터 보존·쓰기 전환·DNS까지 전체 실증 미완 |
| 장애 복구 | 오류 진단·보고서·사람 확인 | 제한 재시도/사용자 조치 후 재개/알림은 온프레미스 시험본에 있고 현재 runtime에는 미이식 |

정상 순서·분기·승인은 코드와 사람이 결정하고, delta-ai는 원인 후보와 근거·추가 확인만 제공한다.
