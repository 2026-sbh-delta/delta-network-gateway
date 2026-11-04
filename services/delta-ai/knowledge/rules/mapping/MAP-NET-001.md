---
rule_id: MAP-NET-001
doc_type: rule
provider: any
title: 서브넷 구조 대응
---

## 왜 위험한가

AWS 서브넷은 하나의 가용 영역에 속하고, 인터넷 게이트웨이로 가는 경로가 있으면 공개 서브넷이다. GCP VPC 는 전역이고 서브넷은 리전 단위이며 공개·사설 구분이 없다. 서브넷을 같은 수로 옮겨도 인터넷 노출과 장애 범위가 같아지지 않는다.

## 판정 기준

공개·사설을 알면 approximate 로 대응 구성을 낸다. AWS → GCP 는 공개 쪽을 외부 IP, 경로, 방화벽으로, 사설 쪽을 외부 IP 없음과 필요 시 Cloud NAT 로 본다. GCP → AWS 는 공개 쪽을 인터넷 게이트웨이 경로와 공인 IP 로, 사설 쪽을 공인 IP 없음과 필요 시 NAT 게이트웨이 경로로 보고, 어느 가용 영역에 둘지 정해야 한다. 대상 대역은 대상 클라우드 쪽 기본 범위에서 고르고, 그 클라우드의 대상 VPC 자신은 충돌로 보지 않는다. 공개·사설을 모르면 unknown 이다.

## 고치는 법

출발 서브넷의 경로 테이블을 확인해 tier 를 채운다. 만든 뒤 실제 연결 시험으로 인터넷 경로와 방화벽을 확인한다.

## 출처

- https://docs.aws.amazon.com/vpc/latest/userguide/configure-subnets.html
- https://docs.cloud.google.com/vpc/docs/vpc
- https://docs.cloud.google.com/vpc/docs/subnets
