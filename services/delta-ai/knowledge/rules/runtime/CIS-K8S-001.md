---
rule_id: CIS-K8S-001
doc_type: rule
provider: any
title: 클러스터 보안 기준
---
## 왜 위험한가
API 서버와 kubelet 설정이 약하면 앱과 상관없이 클러스터 전체가 위험해진다. 다만 자동 점검은 배포판 구조를 잘못 읽어 오탐을 낼 수 있다.

## 판정 기준
kube-bench FAIL 은 실측으로 오탐이 확인되기 전까지 실패로 둔다. 오탐 처리에는 포트 시험이나 실행 인자 같은 근거가 있어야 한다.
WARN 은 수동 확인 항목이라 이 규칙에 넣지 않는다.

## 고치는 법
설정 변경은 재시작과 워크로드 영향이 있어 자동으로 고치지 않는다.
k3s 는 공식 강화 가이드에 맞춰 설정한 뒤 호스트에서 kube-bench 를 다시 돌린다. 파드로 돌리면 `journalctl` 이 없어 k3s 항목을 읽지 못한다.

## 출처
- https://docs.k3s.io/security/hardening-guide
- https://github.com/aquasecurity/kube-bench
