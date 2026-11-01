---
rule_id: CWE-89
doc_type: rule
provider: any
title: SQL 주입
---
## 왜 위험한가
사용자 입력을 문자열로 이어 붙여 SQL 을 만들면 입력이 쿼리 구조를 바꿀 수 있다. 데이터 유출·변조나 인증 우회로 이어진다.

## 판정 기준
외부 입력이 파라미터 바인딩 없이 SQL 문자열에 들어가면 취약하다. 입력이 고정 허용 목록으로만 결정되면 오탐일 수 있다.

## 고치는 법
파라미터화된 쿼리를 쓴다. 입력 검증과 DB 최소 권한은 보조 수단이다. WAF 는 추가 방어이지 코드 수정을 대신하지 않는다.

## 출처
- https://cwe.mitre.org/data/definitions/89.html
- https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html
