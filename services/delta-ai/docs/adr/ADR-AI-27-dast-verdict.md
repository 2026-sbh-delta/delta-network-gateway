# ADR-AI-27: DAST 결과를 누가 판정하고 배포와 어떻게 이을 것인가

> 상태: 제안 · 오너: 김연재 · 작성: 2026-11-02. 준우 님과 합의 전이다. 로컬 시연으로만 구현·검증했다.

## Context

- 결정할 질문: DAST 가 관측한 결과로 배포를 멈출지 누가 정하고, 온프레미스 증거·DAST·배포를 어떤 순서로 이을 것인가?
- 배경: `delta-dast` 와 `delta-ai` 는 서로 호출하지 않았고, 온프레미스 연동과 Jenkins 게이트는 각각 다른 브랜치에 있었다.

## Decision Drivers

1. 판정 기준을 한곳에서 관리한다.
2. 관측 도구가 바뀌어도 판정 계약은 유지한다.
3. 결과를 모르면 통과로 보지 않는다.
4. 대회 전까지 준우 님 서비스 변경을 최소화한다.

## Considered Options

### Option A: DAST 는 관측, delta-ai 는 판정 (`dast_verification`)

- 장점: DAST 코드를 바꾸지 않고 API 결과만 받는다. 기존 사람 확인·사유 체계를 그대로 쓴다.
- 단점: 호출자(콘솔 또는 파이프라인)가 결과를 옮겨야 한다.

### Option B: DAST 가 배포 여부까지 판정

- 장점: 호출이 한 번으로 끝난다.
- 단점: 판정 기준이 두 곳으로 나뉜다.

### Option C: 판단 로직을 DAST 에 공유 라이브러리로 넣기

- 장점: 네트워크 호출이 없다.
- 단점: 배포 시점이 묶이고 ADR-AI-01 의 HTTP 통합과 어긋난다.

## Decision

- 선택: Option A 권고.
- `REPRODUCED`·`BLOCKED`·`INCONCLUSIVE` 는 막고, `NOT_REPRODUCED` 만 통과시키며 실측 통제로 기록한다. 대상 전체가 안전하다는 뜻은 아니다.
- 근거 문서는 `knowledge/rules/dast/` 에 두고 문구는 시험한 절차 범위에서만 쓴다.

## 아직 정하지 않은 것

- **구동 주체:** 시연은 콘솔이 구동한다. DAST 가 루프백 전용이라 Jenkins 컨테이너에서 부를 수 없기 때문이다. 서버 배포에서는 Jenkins 구동을 권고하며, 이때 DAST 의 Host 검사와 토큰 방식을 준우 님과 맞춰야 한다.
- **패치 흐름:** `/fix` 패치를 delta-ai 가 검토하는 경로는 만들지 않았다. 시연의 재검증은 준비된 수정 비교군(`fixed`)을 점검한다.
- **대상 일치:** DAST 대상은 실습 게시판이고 배포 대상은 Jenkins 데모 앱이다. 같은 릴리스를 점검한다고 말할 수 없다.

## Consequences

- delta-ai 계약에 `dast_verification` kind 와 `evidence.dast` 가 늘었고 프롬프트 버전이 `delta-judge-v6` 이 됐다.
- DAST 의 상태 값이 바뀌면 계약 검증에서 요청이 거부된다. 통과로 처리되지 않는다.
