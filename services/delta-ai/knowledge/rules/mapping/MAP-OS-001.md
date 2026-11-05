---
rule_id: MAP-OS-001
doc_type: rule
provider: any
title: 운영체제 대응
---

## 왜 위험한가

Amazon Linux 는 Compute Engine 공개 이미지 목록에 없다. 다른 배포판으로 옮기면 패키지 이름, 저장소, 부팅 스크립트가 달라질 수 있다.

## 판정 기준

같은 배포판이 있으면 exact, Amazon Linux 는 RHEL 계열로 approximate 이다. GCP 의 Container-Optimized OS(cos)는 AWS 에 같은 이미지가 없어 Bottlerocket 또는 Amazon Linux 2023 으로 approximate 이다. 대응을 정하지 않았거나 모르면 unknown 이다.

## 고치는 법

대상 이미지에서 패키지 설치와 부팅 스크립트를 시험한다. 호환을 가정하지 않는다.

## 출처

- https://docs.cloud.google.com/compute/docs/images/os-details
- https://docs.aws.amazon.com/linux/al2023/ug/what-is-amazon-linux.html
- https://docs.cloud.google.com/container-optimized-os/docs/concepts/features-and-benefits
