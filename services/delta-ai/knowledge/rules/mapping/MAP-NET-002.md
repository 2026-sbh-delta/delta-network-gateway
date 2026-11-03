---
rule_id: MAP-NET-002
doc_type: rule
provider: any
title: 대상 대역 충돌
---

## 왜 위험한가

대상 서브넷 대역이 출발 환경, 온프레미스, 다른 대상 서브넷과 겹치면 VPN 이나 피어링으로 이을 때 경로가 모호해지고 이전 중 통신이 끊길 수 있다.

## 판정 기준

대상 대역이 예약 대역, 출발 서브넷, 다른 대상 서브넷과 겹치면 fail 이다.

## 고치는 법

응답의 ops 에 있는 비어 있는 대역으로 바꾼다. 기본값 대역은 임시 값이므로 네트워크 담당자가 확인한다.

## 출처

- https://docs.cloud.google.com/vpc/docs/subnets
- https://docs.aws.amazon.com/vpn/latest/s2svpn/VPC_VPN.html
