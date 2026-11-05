---
rule_id: MAP-CMP-001
doc_type: rule
provider: any
title: 서버 스펙 대응
---

## 왜 위험한가

AWS 와 GCP 의 타입은 CPU 세대와 코어 공유 방식이 달라 vCPU 와 메모리 수가 같아도 성능이 같지 않다. E2 공유 코어는 정해진 비율만 상시 쓰고 잠깐씩 더 쓴다. AWS T 계열은 CPU 크레딧으로 버스트해서, 크레딧이 떨어지면 지속 부하에서 느려질 수 있다.

## 판정 기준

대응표에서 메모리와 vCPU 가 원본 이상인 가장 작은 타입을 고르고 approximate 로 낸다. GCP → AWS 는 E2 와 같은 x86 인 t3 에서만 고른다. t4g 는 더 싸지만 이미지를 다시 빌드해야 해서 후보에 두지 않는다. 표에 없는 타입은 unknown 이다. 사용률이 낮아도 자동으로 줄이지 않고 후보만 제안한다.

## 고치는 법

대상에서 부하 시험을 돌려 확인한다. 작은 후보로 줄일지는 사람이 정한다.

## 출처

- https://docs.cloud.google.com/compute/docs/general-purpose-machines
- https://aws.amazon.com/ec2/instance-types/t4/
- https://aws.amazon.com/ec2/instance-types/t3/
