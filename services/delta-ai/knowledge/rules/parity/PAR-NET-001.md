---
rule_id: PAR-NET-001
doc_type: rule
provider: any
title: 중앙 검사 경로
---
## 왜 위험한가
다른 CSP 로 옮긴 서비스가 중앙 게이트웨이를 지나지 않으면 같은 정책과 로그가 적용되지 않는다.
경로를 잘못 설정하면 방화벽 우회나 통신 단절이 생긴다. 게이트웨이가 ANF 든 HAProxy 든 판단 기준은 같다.

## 판정 기준
출발 환경이 중앙 검사 경로를 지났다면 대상 환경도 지나야 한다. 모르면 unknown 이다.
그 경로에서 복호화된 L7 을 보지 못하고 WAF 도 없으면 HTTP 공격을 막을 수 없으므로 안내를 남긴다.
ALERT 로그만으로 차단됐다고 판단하지 않는다.

## 고치는 법
경로 변경은 통신 단절 위험이 있어 자동으로 고치지 않는다. 경로표를 사람이 검토해 적용하고, 대상 환경 트래픽이 중앙 로그에 나타나는지 확인한다.
L7 공격 방어는 TLS 를 종단하는 위치의 WAF 나 프록시에서 처리한다.

## 출처
- https://docs.aws.amazon.com/network-firewall/latest/developerguide/firewall-logging.html
- 팀 ADR-001 Option A 의 HTTPS 본문 검사 제약
