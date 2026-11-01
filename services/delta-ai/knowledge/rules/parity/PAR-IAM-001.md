---
rule_id: PAR-IAM-001
doc_type: rule
provider: any
title: 워크로드 권한 이식
---
## 왜 위험한가
IRSA 와 EKS Pod Identity 는 AWS IAM 역할을 쿠버네티스 서비스 계정에 연결하는 AWS 방식이라 GKE 로 그대로 옮겨지지 않는다.
앱이 S3 같은 출발 CSP API 를 직접 호출하면 이전 뒤 권한 오류로 실패하고, 급하게 정적 키를 넣으면 유출 위험이 커진다.

## 판정 기준
대상 환경이 GCP 면 Workload Identity Federation for GKE, AWS 면 IRSA 나 Pod Identity 여야 한다. 그 밖의 방식이나 모르는 값은 통과가 아니다.
출발 환경 API 의존이 있고 환경이 다르면 대상 환경에서 접근이 확인돼야 한다.

## 고치는 법
권한 범위는 보안 결정이므로 자동으로 고치지 않는다. 필요한 최소 권한을 사람이 정하고 대상 환경 방식으로 다시 연결한다.
출발 CSP API 의존은 대상 환경 서비스로 바꾸거나, 짧은 수명의 자격증명 페더레이션으로 접근하도록 설계한다.

## 출처
- https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html
- https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html
- https://docs.cloud.google.com/kubernetes-engine/docs/concepts/workload-identity
