# Development Process

모든 변경은 다음 순서를 따른다.

## 1. Ideation
- 문제 정의
- 사용자
- 성공 기준
- Scope
- Non-goals

코드를 작성하지 않는다.
사용자의 승인을 받은 뒤 다음 단계로 이동한다.

## 2. Inception
- Functional Requirements
- Non-Functional Requirements
- Architecture
- Unit of Work
- Acceptance Criteria

승인 전 구현 금지.

## 3. Construction
Unit별로:
1. Design
2. Implementation Plan
3. Approval
4. Implementation
5. Test
6. Review

테스트를 실제 실행하지 않았다면 PASS라고 표시하지 않는다.

## 4. Operation
- Deployment
- Observability
- Rollback
- Runbook

## 5. Documentation Continuity

- 작업을 시작할 때 `docs/README.md`와 관련 `docs/ai-dlc/*.md`가 있으면 먼저 읽는다.
- Ideation 승인 후 관련 AI-DLC 문서를 생성하거나 갱신한다.
- 승인된 단계 또는 검증된 상태가 바뀌면 같은 작업에서 관련 AI-DLC 문서를 갱신한다.
- 프로젝트 전체 진행 상태나 다음 작업이 바뀌면 `docs/README.md`를 갱신한다.
- 계획, 구현, 테스트, Terraform Plan, Apply, 배포, 커밋, 푸시 상태를 구분한다.
- 검증 기록에는 날짜, 실행한 명령, 결과를 포함하며 실행하지 않은 테스트를 PASS로 기록하지 않는다.
- 코드와 문서가 다르면 현재 코드와 실제 검증 결과를 기준으로 문서를 바로잡는다.
- 프로젝트 상태를 바꾸지 않는 사소한 작업은 새 AI-DLC 문서를 만들지 않는다.
- 모든 Terraform 모듈 README에 `## 입력 속성`과 `## 출력 속성` 표를 유지한다. `variables.tf`의 전체 입력은 속성명, 타입, 기본값(없으면 필수), 역할을 적고 `outputs.tf`의 전체 출력은 속성명과 역할을 적는다. `map(object)` 등 복합 입력의 내부 속성도 별도 표로 설명한다. 모듈 속성을 변경할 때 같은 작업에서 README 표를 갱신한다.