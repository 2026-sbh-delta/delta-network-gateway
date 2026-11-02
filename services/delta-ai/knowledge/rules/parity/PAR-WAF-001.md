---
rule_id: PAR-WAF-001
doc_type: rule
provider: any
title: 대상 환경 WAF 동등성
---
## 왜 위험한가
웹 공격 방어를 코드가 아니라 인프라의 WAF 에 맡기던 서비스를 옮기면서 WAF 를 빠뜨리면, 코드는 그대로인데 SQL 주입·XSS 같은 공격이 대상 환경에서만 통과한다.
L3·L4 방화벽이나 HTTPS 를 복호화하지 않는 게이트웨이는 이 공백을 메우지 못한다.

## 판정 기준
출발 환경에 WAF 가 켜져 있으면 대상 환경도 켜져 있고 block 모드여야 하며, 출발 규칙 그룹이 다루는 공격 범주가 대상 규칙에 있어야 한다.
사용 여부나 규칙 목록을 모르면 unknown 이다. 대응 규칙을 정하지 않은 그룹은 동등하다고 보지 않는다.

## 고치는 법
AWS → GCP 는 범주별로 Cloud Armor 사전 구성 규칙을 deny(403) 로 추가한다. 예: evaluatePreconfiguredWaf('sqli-v422-stable', {'sensitivity': 1}).
온프레미스는 클라우드 WAF 를 쓸 수 없으므로 인그레스 앞단에 Coraza 나 ModSecurity 같은 WAF 를 두고 OWASP CRS 를 차단 모드로 켠다.
두 제품의 규칙은 1:1 이 아니므로 추가만으로 동등하다고 말하지 않고 PAR-WAF-002 의 공격 시험으로 확인한다.

## 출처
- https://coraza.io/docs/
- https://docs.cloud.google.com/armor/docs/waf-rules
- https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-baseline.html
- https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-use-case.html
