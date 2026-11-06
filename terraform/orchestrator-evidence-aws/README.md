# 진단 근거 로그 읽기

사용자 승인된 중앙 로그 자동 근거 수집을 위한 별도 최소 권한 root다. 기존 오케스트레이터 역할에 `logs:FilterLogEvents`만 추가한다. 중앙 AWS/GCP와 자기 파이프라인 로그 3개 그룹만 읽는다. 기존 역할·배포 정책·Secret·네트워크·로그 수집 설정은 변경하지 않는다. backend는 기존 Workload 버킷의 전용 key를 사용한다(`backend.hcl.example`).

`terraform init -backend-config=backend.hcl.example` → `terraform test` → `terraform plan -out=evidence.tfplan` → 변경이 role policy 생성1건인지 확인한 뒤 같은 plan을 apply한다. 실행 프로필은 변경용 delta-finals, 리전 ap-northeast-2다. 적용 뒤 실제 역할의 읽기를 확인하고 최종 plan이 No changes인지 확인한다.

## 입력 속성

입력 변수 없음. 계정·리전·역할·로그 그룹을 고정해 범위가 넓어지지 않게 한다.

## 출력 속성

| 속성명 | 역할 |
|---|---|
| role_name | 추가 정책이 붙은 기존 오케스트레이터 역할 |
| log_groups | 읽기 허용된 3개 그룹 |

원복은 이 root의 정책1건만 제거하고 `DELTA_LOG_EVIDENCE_ENABLED`를 끈다. 기존 역할/기본 정책이나 로그 그룹을 삭제하지 않는다.
