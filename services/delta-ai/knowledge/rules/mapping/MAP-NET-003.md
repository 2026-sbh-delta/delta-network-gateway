---
rule_id: MAP-NET-003
doc_type: rule
provider: any
title: 기존 네트워크 대조
---

## 왜 위험한가

이미 만든 네트워크에 올릴 때 공개·사설 구성이 같은 서브넷이 없거나 원본보다 작거나 출발·온프레미스 대역과 겹치면, 서버를 올려도 같은 수의 주소와 같은 경로를 보장하지 못한다.

## 판정 기준

network_mode 가 reuse 일 때 기존 목록이 없으면 unknown 이다. 같은 tier 가 없거나 원본보다 작거나 출발·온프레미스 대역과 겹치면 gap 이다.

## 고치는 법

기존 네트워크를 고칠지 새로 만들지(network_mode create) 네트워크 담당자가 정한다. 자동으로 고치지 않는다.

## 출처

- https://docs.cloud.google.com/vpc/docs/subnets
- https://docs.cloud.google.com/vpc/docs/vpc
