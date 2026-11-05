# 관측 조회 권한

기존 플랫폼 VM 역할에 읽기 전용 관측 정책을 연결한다. VM·네트워크·로그 그룹을 만들거나 변경하지 않는다. 일반 GitHub Apply 역할에는 IAM 변경 권한이 없으므로 IAM 관리자가 별도 root/state로 계획·적용한다. 적용 전에는 대시보드의 권한 없는 조회가 오류로 표시될 수 있다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| account_id | string | 필수 | 변경할 AWS 계정, provider가 현재 계정과 대조 |
| region | string | ap-northeast-2 | 관측 로그 리전 |
| platform_role_name | string | delta-platform-app | 이미 생성된 플랫폼 VM 역할 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| platform_role_name | 조회 정책을 연결한 기존 역할 |
| policy_name | 추가한 inline 정책 이름 |

## 적용과 복구

```bash
cp backend.hcl.example backend.hcl
AWS_PROFILE=delta-platform terraform init -backend-config=backend.hcl -lockfile=readonly
AWS_PROFILE=delta-platform terraform plan -var account_id=616723161368 -out=reviewed.tfplan
# 기존 역할에 읽기 정책 1개 추가만 있는지 검토한다.
AWS_PROFILE=delta-platform terraform apply reviewed.tfplan
```

검토한 Plan과 현재 커밋·state가 달라지면 다시 계획한다. 읽기는 CloudWatch 지표·등록 VM 상태·X-Ray 및 고객 관측 전용 로그 stream으로 제한한다. Collector의 쓰기 권한은 이 root에 포함하지 않는다. 비활성화 시 이 정책만 제거하고 기존 역할을 삭제하지 않는다. 실제 적용 여부는 [진행 기록](../../../../docs/ai-dlc/platform-continuity.md)을 따른다.
