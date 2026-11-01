---
rule_id: CWE-78
doc_type: rule
provider: any
title: OS 명령 주입
---
## 왜 위험한가
사용자 입력이 셸 명령에 들어가면 공격자가 서버에서 임의 명령을 실행할 수 있다. 권한에 따라 서비스 전체가 장악된다.

## 판정 기준
외부 입력이 함수 호출을 거쳐서라도 셸 실행까지 도달하면 취약하다. 래퍼 함수를 거치면 단순 패턴 검사가 놓치기 쉽다.

## 고치는 법
OS 명령 호출을 피하는 것이 먼저다. 불가피하면 셸을 거치지 않고 인자를 분리해 넘기며 허용 목록으로 입력을 검증한다.

## 출처
- https://cwe.mitre.org/data/definitions/78.html
- https://cheatsheetseries.owasp.org/cheatsheets/OS_Command_Injection_Defense_Cheat_Sheet.html
