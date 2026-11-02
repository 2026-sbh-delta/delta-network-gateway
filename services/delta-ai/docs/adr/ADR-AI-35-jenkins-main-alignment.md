# ADR-AI-35: Jenkins 작업을 바뀐 main 에 어떻게 맞출 것인가

> 상태: 제안 · 오너: 김연재 · 작성: 2026-11-02. 로컬 브랜치 `feat/jenkins-on-main` 에 구현했고 푸시·리뷰 전이다.

## Context

- 결정할 질문: PR #2·#3 이후 어긋난 Jenkins 작업을 어떻게 합치고, 사라진 `code_security` 의 자리를 어떻게 처리할 것인가?
- 현재 문제와 배경: `feat/jenkins-local` 이 main 과 7개 파일에서 충돌했고, 파이프라인이 삭제된 `code_security`·`code_diffs` 를 호출하고 있었다. 원래 작업 폴더는 다른 세션이 Jenkins 빌드에 쓰고 있었다.
- 범위: 브랜치 정리와 SAST·수정 루프 처리만 다룬다.

## Decision Drivers

1. 준우 님의 PR #3 결정을 되돌리지 않는다.
2. 진행 중인 다른 작업과 그 커밋을 망가뜨리지 않는다.
3. 판단할 곳이 없는 경로에서 자동 통과가 생기지 않는다.

## Considered Options

### Option A: 새 브랜치에 main 병합, SAST 탐지는 사람 확인

- 설명: `feat/jenkins-on-main` 을 별도 워크트리에 만들어 main 을 병합한다. Semgrep 이 탐지하면 사람 확인에서 멈춘다.
- 장점: 기존 커밋이 보존되고 탐지가 통과로 처리되지 않는다.
- 단점 및 위험: 패치 출처가 없어 자동 수정 시연이 빠진다. 사람이 볼 때까지 파이프라인이 기다린다.

### Option B: 기존 브랜치를 rebase

- 설명: `feat/jenkins-local` 을 main 위로 옮긴다.
- 장점: 이력이 깔끔하다.
- 단점 및 위험: 사용 중인 브랜치의 해시가 바뀌고 문서가 인용한 해시가 사라진다.

### Option C: `code_security` 를 되살림

- 설명: 삭제된 경로를 복구해 수정 루프를 유지한다.
- 장점: 기존 수정 루프가 그대로 돈다.
- 단점 및 위험: PR #3 과 충돌하고 코드 보안 판단이 두 곳으로 갈라진다.

## Decision

- 선택: Option A. `sast_findings.py` 를 추가했고, `fix_loop.py` 는 라이브러리로 남긴 채 파이프라인에서 분리했다. 병합 중 `assess()` 가 `deployment_gate` 를 parity 로 보내는 문제는 `_assess_checks` 로 나눠 고쳤다.
- 검증: 빌드 #53~60(가짜 LLM)에서 정상 배포, SAST 중단·승인, 테스트 실패, 카나리 롤백, 재발 차단, 판단 불가, 표본 부족을 확인했다. 실제 Claude 는 이후 `feat/demo-integration` 콘솔 빌드(#68~81)에서 확인했다. 기록은 [jenkins-local.md](../../../../docs/ai-dlc/jenkins-local.md).
- 감수한 트레이드오프: 자동 수정 루프는 패치 출처가 생길 때까지 파이프라인에서 돌지 않는다.
- 결정자: 미정 / 결정일: 미정
- 다시 볼 조건: [ADR-AI-28](ADR-AI-28-fix-guidance-review.md)이 합의되어 패치 출처가 생길 때.

## Follow-up

- `feat/demo-integration` 을 푸시하고 PR 을 올린다. 이 브랜치가 앞의 작업을 포함한다.
- SAST 단계는 지금 Semgrep 이다. 준우 님 `delta-sast`(설계 완료, 구현 미착수)가 나오면 이 단계를 교체한다.
