---
rule_id: MAP-IAM-001
doc_type: rule
provider: any
title: 권한 대응
---

## 왜 위험한가

인스턴스 프로파일과 서비스 계정은 정책 구조가 다르다. 넓게 옮기면 권한이 늘고 좁게 옮기면 호출이 실패한다. 와일드카드나 표에 없는 동작은 대응 역할을 기계적으로 정할 수 없다.

## 판정 기준

프로파일(GCP 출발이면 서비스 계정)이 없으면 exact, 권한 목록을 모르면 unknown, 전부 표에 있으면 approximate, 표에 없는 동작이나 역할이 있으면 gap 이다. GCP → AWS 는 역할 하나가 동작 여러 개가 된다. 예를 들어 roles/storage.objectViewer 는 s3:GetObject 와 s3:ListBucket 으로 옮기고, 역할 안의 나머지 권한은 옮기지 않는다.

## 고치는 법

권한 담당자가 필요한 동작을 확인해 역할이나 정책을 정한다. 대상 서비스 계정이나 인스턴스 프로파일로 실제 호출을 시험한다.

## 출처

- https://docs.cloud.google.com/iam/docs/service-account-overview
- https://docs.cloud.google.com/iam/docs/understanding-roles
- https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/iam-roles-for-amazon-ec2.html
