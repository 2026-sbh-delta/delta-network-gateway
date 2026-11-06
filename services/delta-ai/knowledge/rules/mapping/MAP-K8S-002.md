---
rule_id: MAP-K8S-002
doc_type: rule
provider: any
title: 영속 볼륨 대응
---

## 판정 기준

PVC·StatefulSet volumeClaimTemplates의 용량·접근 모드·storageClass를 추출한다. 선언된 PVC는 실제 프로비저닝·백업/복원·노드 장애시 가용성을 증명하지 않으므로 unknown이다. 단일 노드 local-path는 관리형 HA 스토리지와 같지 않다.

## 출처

- https://kubernetes.io/docs/concepts/storage/persistent-volumes/
