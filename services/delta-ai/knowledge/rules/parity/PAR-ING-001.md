---
rule_id: PAR-ING-001
doc_type: rule
provider: any
title: 인그레스 TLS
---
## 왜 위험한가
대상 환경 로드밸런서에 TLS 가 없으면 인증 정보와 사용자 데이터가 평문으로 오간다. 인증서는 CSP 마다 관리 방식이 달라 이전할 때 자주 빠진다.

## 판정 기준
대상 환경 공개 진입점에 TLS 가 켜져 있어야 한다. 모르면 unknown 이다.

## 고치는 법
GCP 는 Application Load Balancer 에 SSL 인증서를 연결한다. 전환 전에 대상 환경 인증서가 유효한지 확인한다.

## 출처
- https://docs.cloud.google.com/load-balancing/docs/ssl-certificates
