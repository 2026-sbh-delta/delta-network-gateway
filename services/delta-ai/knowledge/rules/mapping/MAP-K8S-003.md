---
rule_id: MAP-K8S-003
doc_type: rule
provider: any
title: 클러스터 정책 대응
---

## 판정 기준

NetworkPolicy의 존재는 CNI 강제·선택자·허용/차단 실측의 증거가 아니다. 정책은 unknown으로 남기고 실제 통신 시험을 요구한다. Secret·ConfigMap·환경변수 값·주석은 매핑·LLM 입력으로 보내지 않는다.

## 출처

- https://kubernetes.io/docs/concepts/services-networking/network-policies/
