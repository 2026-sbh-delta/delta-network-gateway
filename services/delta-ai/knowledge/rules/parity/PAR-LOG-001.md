---
rule_id: PAR-LOG-001
doc_type: rule
provider: any
title: 중앙 로그 연결
---
## 왜 위험한가
이전한 서비스의 로그가 중앙으로 오지 않으면 공격 탐지와 장애 조사가 환경마다 따로 이뤄지고, 이전 전후 비교도 할 수 없다.

## 판정 기준
대상 환경의 앱·WAF·방화벽 로그가 중앙 저장소로 들어와야 한다. 연결 여부를 모르면 unknown 이다.

## 고치는 법
GCP 는 Cloud Logging 싱크로 로그를 내보내 중앙 저장소에 연결한다. AWS Network Firewall 은 flow·alert·TLS 로그 대상을 설정한다.
연결 뒤 실제 로그가 도착했는지와 전달 지연을 확인한다.

## 출처
- https://docs.cloud.google.com/logging/docs/export/configure_export_v2
- https://docs.aws.amazon.com/network-firewall/latest/developerguide/firewall-logging.html
