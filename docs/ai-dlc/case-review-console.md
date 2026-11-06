# 콘솔의 장애 사례 검토와 평가셋 추출

## 승인·설계

2026-11-06 사용자가 기존 CLI 검토·승인과 평가셋 추출을 현재 콘솔에서 수행하도록 구현을 요청했다. 이 요청을 Ideation/Inception 및 아래 Construction 단위의 승인으로 기록한다. 별도 worktree `codex/case-console`, 기준 main e9f4629. 기존 파이프라인·DAST·배포 게이트는 변경하지 않는다.

로컬 ADR 초안(팀 번호 미할당): 관리자 콘솔 → 전용 서버 어댑터 → AI 사례 저장소로 검토·추출을 연결한다. 기존 그래프 생산자 토큰은 검토 권한을 갖지 않는다. 별도 `DELTA_CASE_REVIEW_TOKEN`을 AI와 오케스트레이터 서버에만 설정하며 생산자 토큰과 동일하면 거부한다. 기존 공개 관리자 세션/같은 Origin 검사를 사용하고, 이 기능의 쓰기는 정확한 Origin과 JSON을 필수로 검사한다. localhost 개발 예외를 포함해 관리자 세션 없는 요청은 거부한다. 공유 관리자 계정이므로 입력한 검토자 이름은 운영자가 작성한 감사 표기이며 개별 사용자 인증을 뜻하지 않는다.

## 작업 단위·수용 조건

1. AI: 페이지당 최대50 사례 요약, 최신 원본·재검사·버전/감사 이력 조회. 정확한 최신 revision에 승인/거절을 저장하며 unknown/stopped 승인 불가. 승인+독립 기대값이 있는 동일 revision만 EvaluationDataset으로 추출. 기존 CLI/DB 계약 재사용.
2. 콘솔 서버: 서버 간 비밀값을 브라우저에 노출하지 않는 전용 어댑터. 미인증401, 다른/없는 Origin 쓰기403, JSON 외415, 크기초과413, 오래된 revision409. 설정 없으면503으로 명시. 모델/클라우드 작업 호출 없음.
3. 화면: 기존 어두운 운영 콘솔 스타일의 두 열 검토 작업실. 사례 목록/페이지, 결과 범위와 원본 진단·검사 근거, 검토자/사유/기대 항목/금지 단정, 승인/거절/취소, JSON 다운로드. 비동기 응답 역전·중복제출·버전 충돌·빈목록/연결오류 처리. 다운로드는 승인된 저장값을 사용하고 미저장 입력은 적용하지 않는다.
4. 검증: API 권한 분리·실제 세션/CSRF·버전 충돌·평가 독립 라벨·다운로드, 기존 AI/pipeline/orchestrator 회귀, 실제 브라우저 조작과 모바일 레이아웃 확인. 검증 데이터는 별도 로컬 저장소를 사용한다.

## 상태

구현과 로컬 검증 완료. AI **346 passed**, pipeline/orchestrator **215 passed**, 총 **561 passed**. Chrome 실제 브라우저 **6개 시나리오 통과**: 로그인·페이지 이동, 근거/XSS/확인 검증, 평가 JSON/미저장 차단/승인 취소·감사, unknown 승인 차단, 동시 새 버전 충돌·재조회, 모바일/오류 복구. API/브라우저 fixture는 별도 임시 DB의 합성 사례만 사용했다. 실제 모델 호출·고객 클라우드 작업0건이다.

2026-11-06 실행: 기존 Python3.12 환경에 이 worktree의 `PYTHONPATH=services/delta-ai:services/delta-pipeline:services/delta-orchestrator`를 명시했다. AI와 pipeline/orchestrator는 별도 pytest로 전체 실행했다. 새 Python5파일 Ruff check/format, 두 UI JS와 브라우저 검증기 Node 구문, compileall, git diff --check 통과. 브라우저는 Playwright1.56.1/로컬 Chrome·테스트 전용 HTTPS로 실제 콘솔→HTTP AI API를 사용했다. 데스크톱1440×1080과 모바일390×844 스크린샷을 눈으로 확인했다. 기본 fixture-loop 설정의 기존 pytest 경고는 실패와 구분했다.

인증/CSRF/입력 상한/토큰 분리/최신 버전 바인딩/승인 취소/독립 평가 라벨/HTML escaping을 검토했으며 변경 범위에서 미해결 critical/high 항목은 발견하지 않았다. 원격 공격 시험·실제 AWS 프록시/비밀 설정 검증은 수행하지 않았다.

증거: 작업 루트 `notes/case-console-20261106/{summary.json,ai-tests.xml,pipeline-tests.xml,browser-summary.json,desktop.png,desktop-review.png,mobile.png,evaluation-case-00.json}`. 구현과 재현/운영 연결 방법은 [런북](../../deploy/case-review-console/README.md)에 있다.

Git 커밋·푸시·PR/main 반영·원격 CI·AWS 배포는 미실행이다. 기존 AWS 콘솔에 이 새 화면이 이미 활성화됐다고 설명하지 않는다. 실제 활성화에는 AI/오케스트레이터 새 이미지, 생산자와 분리된 검토 토큰, AI 프록시 경로 연결이 필요하다.


## 2026-11-06 AWS 배포 승인

사용자가 AWS 배포·완료 Notion 문서·Slack 붙여넣기 초안을 요청했다. 최신 main e9f4629와 실행 중 소스523ae5a의 모듈 해시를 대조해 일치함을 확인했다. 기존 실행45개/영속118파일, AI영속5파일 보존·기존 인증/TLS/env/logging 유지·idle/동시 교체 방지·검증된 백업·이전 컨테이너 원복을 적용한다. 기존 두 호스트의 SSM 읽기 권한으로 별도 SecureString 검토 토큰을 주입하고 AI 프록시에 검토 경로만 추가한다. IAM/SG/DNS/팀 Worker 변경은 하지 않는다. 현재 배포 준비 중이며 실제 결과는 완료 후 기록한다. Git main 머지와 팀 Slack 발송은 이번 요청에 포함하지 않는다.
