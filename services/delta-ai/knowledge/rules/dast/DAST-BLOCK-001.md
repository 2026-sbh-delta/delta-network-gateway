---
rule_id: DAST-BLOCK-001
doc_type: rule
provider: any
title: CSP 로만 막힌 취약점
---
## 왜 위험한가
입력이 인코딩 없이 저장·출력되고 있고, 실행만 CSP 가 막았다. CSP 설정이 바뀌거나 이전 대상 환경에 같은 헤더가 없으면 곧바로 실행된다.

## 판정 기준
`BLOCKED` 는 통과가 아니다. 이전이나 배포를 계속하려면 대상 환경에도 같은 CSP 가 적용되는지와 출력 인코딩 계획을 사람이 확인한다.

## 고치는 법
출력 인코딩을 먼저 고친다. CSP 는 두 번째 방어선으로 유지한다.

## 출처
- https://owasp.org/www-community/controls/Content_Security_Policy
