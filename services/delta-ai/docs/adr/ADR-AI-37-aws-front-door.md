# ADR-AI-37: 트래픽 앞단을 HAProxy 로 둘 것인가, AWS 기능을 쓸 것인가

> 상태: 제안 · 오너: 김연재 · 작성: 2026-11-02. 방향은 김연재 결정("우선 HAProxy 기각, AWS 기능 활용")이고 구체 구성은 시험 전이다.

## Context

- 결정할 질문: 고객 앱 앞단에서 비율 전환과 원복을 무엇으로 할 것인가?
- 배경: 11/02 회의에서 HAProxy 의 이점이 할 일에 비해 애매하다는 의견이 나왔다. 앞에 LB 를 또 두면 이중화 문제가 꼬리를 물고, HAProxy 만으로는 IPS 가 안 된다는 지적도 있었다.
- 배경: 호원 님 "중앙관리 솔루션 설계"는 고객사별 진입점과 장애 전파 차단을 중요하게 본다.
- 배경: Jenkins 시험대는 HAProxy `set weight` 로 카나리 비율을 바꾼다. 이 코드는 로컬 대역이다.
- 범위: 트래픽 전환 방식을 다룬다. 중앙 ANF·TGW 구성은 다루지 않는다.

## Decision Drivers

1. 카나리 비율 변경과 즉시 원복이 파이프라인에서 된다.
2. 운영할 구성 요소를 늘리지 않는다.
3. 고객사별로 진입점을 나눌 수 있다.
4. 본선까지 구현하고 시험할 수 있다.

## Considered Options

### Option A: ALB 가중치 대상 그룹 (권고)

- 설명: 고객사별 ALB 리스너의 forward 액션에 안정·카나리 대상 그룹을 두고 가중치를 바꾼다. GCP 서버는 VPN 너머 사설 IP 를 IP 대상으로 등록한다.
- 장점: 관리형이라 이중화를 직접 하지 않는다. 비율이 DNS 캐시에 묶이지 않는다. `terraform/cloudfront-server` 에 ALB 사용 사례가 있다.
- 단점 및 위험: IP 대상은 사설 대역만 되고 공인 IP 는 등록할 수 없다. 그래서 GCP 쪽은 VPN 이 먼저 필요하다. GCP 대상을 VPN 너머로 쓰는 구성은 시험하지 않았다.

### Option B: Route 53 가중치·장애 조치

- 설명: 고객사 도메인의 가중치 레코드로 나눈다.
- 장점: VPN 없이 공개 진입점 2개로 나눌 수 있고, AWS 리전 장애에도 GCP 로 넘길 수 있다.
- 단점 및 위험: 비율이 부정확하고 원복이 TTL 과 클라이언트 캐시만큼 늦다. 카나리 판단과 즉시 원복에 맞지 않는다.

### Option C: HAProxy 유지

- 장점: 시험대 코드를 그대로 쓰고 클라우드에 묶이지 않는다.
- 단점 및 위험: 이중화, 패치, 모니터링을 직접 맡는다.

## Decision

- 선택: Option A 권고. HAProxy 는 기각하고 시험대의 로컬 대역으로만 남긴다.
- 호출 계약: 파이프라인은 제품이 아니라 `split(stable, canary, percent)`, `active()`, `rollback()` 세 동작에 의존한다. 서버용은 ALB 구현을 새로 만든다.
- 지표: 버전별 오류율·지연은 대상 그룹 지표에서 읽는 안을 시험한다.
- 다시 볼 조건: GCP 대상을 VPN 경유로 등록할 수 없거나, 팀이 다른 앞단을 택할 때.

## Consequences

- 긍정: 앞단 관련 다른 ADR 이 기다리던 결정이 풀린다(16, 34). DNS 분산의 카나리 문제를 피한다.
- 부정: **AWS 리전 장애 시 앞단도 같이 죽는다.** 회의에서 나온 리전 장애 우려를 이 구성만으로는 막지 못한다. 필요하면 Option B 의 장애 조치를 바깥에 더한다.
- 부정: "클라우드 락인을 넘어선다"는 주제와 앞단의 AWS 의존이 긴장 관계다. 발표 질문에 대비해 답을 준비한다.
- 후속 작업: 서버용 전환 구현, ALB 권한 시험, VPN 뒤 GCP 대상 등록 시험. 담당은 정하지 않았다.
- 입력 계약: `network.gateway` 값에 있는 `haproxy` 는 온프레미스·시험대 입력에 쓸 수 있어 지금 지우지 않는다.

## 출처

- https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html
- https://docs.aws.amazon.com/prescriptive-guidance/latest/load-balancer-stickiness/target-group-stickiness.html
