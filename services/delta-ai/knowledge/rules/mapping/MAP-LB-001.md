---
rule_id: MAP-LB-001
doc_type: rule
provider: any
title: 로드밸런서 대응
---

## 판정 기준

리스너 프로토콜·포트·공개 범위·TLS가 알려진 ALB/NLB와 GCP Application/Network LB를 근사 대응한다. 종류나 리스너를 모르면 unknown이다. k3s에서는 Service/Ingress 구성과 실제 진입 경로를 별도로 검증한다. DNS 변경·가중치 전환은 수행하지 않는다.

## 출처

- https://kubernetes.io/docs/concepts/services-networking/ingress/
