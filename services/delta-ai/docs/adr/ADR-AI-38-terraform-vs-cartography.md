# ADR-AI-38: 출발 환경의 값을 Terraform 과 Cartography 중 어디서 얻을 것인가

> 상태: 제안 · 오너: 김연재 · 작성: 2026-11-02. Terraform 기준은 김연재 결정이다. Cartography 는 문서로만 비교했고 실행하지 않았다.

## Context

- 결정할 질문: AWS 환경의 세부 값을 어디서 읽어 GCP 매핑의 입력으로 쓸 것인가?
- 배경: 11/02 회의에서 세부 값 전달이 가장 큰 개발 이슈로 꼽혔다. Terraform 코드가 있으면 값이 코드에 있고, PR diff 로 변경도 보인다는 의견과 Cartography 로 수집해 이전값과 비교하자는 의견이 나왔다. ADR-001 OPTION C 도 Cartography 를 제안했다.
- 범위: 입력 출처를 다룬다. 입력으로 무엇을 판단할지는 [ADR-AI-39](ADR-AI-39-migration-mapping.md)에서 다룬다.

## Decision Drivers

1. 본선까지 구현하고 시연 중에 안정적으로 돌릴 수 있다.
2. 값의 출처를 설명할 수 있다.
3. 운영할 구성 요소를 늘리지 않는다.
4. Terraform 을 쓰지 않는 고객 질문에 답할 수 있다.

## Considered Options

| 기준 | Terraform (`terraform show -json`) | Cartography |
|---|---|---|
| 보는 것 | Terraform 이 관리하는 자원의 선언과 state | API 로 읽은 계정의 실제 자원 |
| 관리 밖 자원, 콘솔 수정 | 관리 밖 자원은 안 보인다. 관리 자원의 변경은 refresh plan 으로 확인 | 수집 범위 안이면 보인다 |
| 변경 감지 | PR diff, plan. 변경 전에 검토할 수 있다 | 저장한 쿼리 결과 두 개를 비교. 일어난 뒤에 알게 된다 |
| 필요한 것 | Terraform, state 읽기 권한 | Neo4j 5.23 이상, Python, 클라우드 읽기 권한, 수집 실행 |
| 실행 방식 | 필요할 때 명령 실행 | 배치 수집. 스케줄은 직접 구성 |
| 대상 환경 생성 | 같은 도구로 GCP 코드까지 이어 간다 | 수집만 한다. 자원을 만들지 않는다 |
| 값 표현 | CSP 자원 속성 그대로 | 그래프 노드와 관계. 관계 질의에 강하다 |
| 주의점 | state 에 민감한 값이 들어 있을 수 있다 | 수집 모듈이 다루는 속성만 보인다. 필요한 값이 있는지 미확인 |
| 지금 준비 상태 | 레포에 AWS·GCP 코드가 있다. Mac 에는 미설치 | 없음 |

### Option A: Terraform 만 사용

- 장점: 추가 인프라가 없고 팀 코드와 바로 이어진다.
- 단점 및 위험: 실제 환경이 코드와 다르면 틀린 값으로 매핑한다.

### Option B: Terraform 기준, 입력만 교체 가능하게 설계 (권고)

- 설명: Terraform 추출기를 만들고, 판단 서비스는 정규화된 입력과 값의 출처만 받는다. Cartography 는 구현하지 않고 나중에 추출기로 붙일 자리만 둔다.
- 장점: 비용은 Option A 와 같고 고객 질문에 구조로 답할 수 있다.
- 단점 및 위험: 정규화 입력을 설계해야 한다. 어댑터는 실제로 시험하기 전까지 "교체 가능"이라고 단정할 수 없다.

### Option C: Cartography 를 기준으로 사용

- 장점: Terraform 없는 환경과 관리 밖 자원까지 본다.
- 단점 및 위험: Neo4j 운영이 늘고, 대상 환경은 어차피 Terraform 으로 만든다.

## Decision

- 선택: Option B. Terraform 을 기준으로 한다.
- 추출 규칙: 정해 둔 속성만 꺼내고 민감 표시 값은 입력에 넣지 않는다. state 원본을 판단 서비스로 보내지 않는다.
- 정합성: 시연 전에 `terraform plan -refresh-only` 로 코드와 실제의 차이를 확인한다. 차이가 있으면 매핑을 믿을 수 없다고 표시한다.
- 발표 답변: "기준은 Terraform 이고, 코드가 없는 고객은 수집기로 같은 입력을 만드는 구조다. 수집기는 아직 구현하지 않았다."
- 다시 볼 조건: 시연 대상에 Terraform 으로 관리하지 않는 자원이 생길 때, 또는 인프라 관계도를 Cartography 로 보여 주기로 정할 때.

## Consequences

- 긍정: 시연 준비에 새 서버가 필요 없다.
- 부정: Terraform 밖에서 만든 자원은 매핑에 나오지 않는다. 결과에 이 한계를 적는다.
- 후속 작업: 작업 환경에 Terraform 설치, 추출기 구현, state 읽기 권한 확인.

## 출처

- https://github.com/cartography-cncf/cartography
- https://docs.cartography.dev/install.html
- https://docs.cartography.dev/usage/drift-detect.html
