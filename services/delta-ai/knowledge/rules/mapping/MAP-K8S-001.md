---
rule_id: MAP-K8S-001
doc_type: rule
provider: any
title: k3s 워크로드 대응
---

## 판정 기준

Pod·Deployment·StatefulSet·DaemonSet·Job·CronJob의 이미지와 자원 요청을 추출한다. VM을 Pod로 옮기는 것은 이미지·CPU 구조·노드 용량 확인 전에는 unknown이다. YAML은 선언이며 readiness 실측이 아니다. 미지원 kind는 gap으로 남긴다.

## 출처

- https://kubernetes.io/docs/concepts/workloads/
