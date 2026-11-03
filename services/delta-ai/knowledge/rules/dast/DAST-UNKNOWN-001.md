---
rule_id: DAST-UNKNOWN-001
doc_type: rule
provider: any
title: DAST 판정 불가
---
## 왜 위험한가
입력 지점을 찾지 못했거나 브라우저 증거가 부족해 실행 여부를 모른다. 검사가 끝나지 않은 것을 안전으로 읽으면 취약점을 놓친다.

## 판정 기준
`INCONCLUSIVE` 는 통과로 보지 않고 사람에게 넘긴다.

## 고치는 법
점검 대상과 입력 지점 설정을 확인해 다시 점검한다.

## 출처
- https://owasp.org/www-project-web-security-testing-guide/
