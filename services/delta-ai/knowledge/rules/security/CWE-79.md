---
rule_id: CWE-79
doc_type: rule
provider: any
title: 크로스 사이트 스크립팅
---
## 왜 위험한가
사용자 입력이 이스케이프 없이 HTML 로 응답되면 다른 사용자의 브라우저에서 스크립트가 실행될 수 있다.

## 판정 기준
외부 입력이나 그 영향을 받은 값이 HTML 로 해석되는 응답에 들어가면 취약하다. 응답 형식이 HTML 이 아니거나 값이 입력과 무관하면 오탐일 수 있다.

## 고치는 법
출력 위치에 맞게 인코딩하고, HTML 이 필요 없는 응답은 text/plain 이나 JSON 으로 내보낸다.

## 출처
- https://cwe.mitre.org/data/definitions/79.html
