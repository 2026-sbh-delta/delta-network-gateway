---
rule_id: MAP-CMP-002
doc_type: rule
provider: any
title: CPU 구조 변경
---

## 왜 위험한가

t4g 는 arm64 이고 E2 는 x86 계열이다. arm64 이미지만 있으면 대상 서버에서 실행되지 않는다.

## 판정 기준

구조가 같으면 exact 이다. 구조가 다르면 대상 구조 이미지가 확인된 경우 approximate, 확인되지 않으면 fail 이다.

## 고치는 법

대상 구조로 이미지를 빌드해 multi_arch_image 를 true 로 다시 보낸다. 네이티브 라이브러리가 있으면 스모크 시험으로 확인한다.

## 출처

- https://docs.cloud.google.com/compute/docs/general-purpose-machines
- https://aws.amazon.com/ec2/instance-types/t4/
- https://docs.docker.com/build/building/multi-platform/
