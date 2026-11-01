---
rule_id: PAR-SEC-001
doc_type: rule
provider: any
title: 시크릿 저장소 이식
---
## 왜 위험한가
AWS Secrets Manager 에 있는 값은 대상 환경으로 저절로 옮겨지지 않는다. 이전을 서두르다 평문 환경 변수로 넣으면 로그와 설정 조회로 값이 샐 수 있다.

## 판정 기준
대상 환경 저장소가 그 환경에서 쓸 수 있어야 하고 평문 환경 변수가 없어야 한다. 저장소를 모르면 unknown 이다. 이 서비스는 시크릿 이름만 받고 값은 받지 않는다.

## 고치는 법
값 등록과 교체는 사람이 한다. GCP 면 Secret Manager 에 등록하고, 평문으로 노출됐던 값은 교체한다.

## 출처
- https://docs.cloud.google.com/secret-manager/docs/overview
