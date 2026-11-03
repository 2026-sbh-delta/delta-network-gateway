# 플랫폼 샌드박스 기반

플랫폼 U3의 앱 실행 확인용 ECS Fargate·내부 ALB·제한된 IAM 역할을 만든다. 기존 `delta-lab-staging`의 팀 앱과 분리한다. [API 계약](../../contracts/u3-sandbox.md)과 [실제 검증·배포 상태](../../../docs/ai-dlc/platform-u3.md)를 함께 본다.

## 네트워크와 비용

| 대역 | 용도 | 관리 위치 |
| --- | --- | --- |
| `10.80.0.0/16` | 플랫폼 VPC 전체 | 기존 `deploy/terraform` |
| `10.80.0.0/24` | NAT EC2 | 기존 기반 |
| `10.80.1.0/24` | 플랫폼 VM·ALB 한쪽 | 기존 기반 |
| `10.80.2.0/24` | ALB 두 번째 AZ | 이 root |
| `10.80.3.0/24` | 고객 이미지 검사 태스크 | 이 root |

내부 ALB에는 클라이언트 ingress를 열지 않는다. U3는 ALB가 태스크에 보내는 헬스 체크와 AWS API 결과로 실행 여부를 확인한다. 브라우저에서 고객 앱을 열거나 DAST가 접속하는 기능은 후속 단계다. 태스크 앱 포트는 ALB 보안 그룹만 허용한다. 태스크는 공인 IP·ECS Exec·플랫폼 권한이 없다. ECS 실행 역할은 지정 ECR의 읽기·전용 로그 쓰기, 앱 태스크 역할은 권한 없이 생성한다.

태스크의 외부 통신은 기존 NAT를 통한 TCP 443으로 제한한다. 이는 목적지 도메인 허용 목록이나 고객별 보안 격리를 완성한 구성이 아니다. 플랫폼과 VPC·NAT를 공유하므로 공개 다중 고객 서비스로 확대하기 전 별도 VPC/계정, egress 정책, 비용·자원 한도를 설계한다. 기존 플랫폼 보안 그룹을 앱 태스크에 붙이지 않는다.

실행은 0.25 vCPU / 512 MiB 1개이며 대기 중 `desired_count=0`이다. 실행 후 태스크는 종료하지만 **내부 ALB·로그 저장 비용은 남는다.** 새 VM·NAT Gateway는 만들지 않는다. 플랫폼·NAT와 실행 태스크는 한 AZ이므로 고가용성 운영을 검증한 상태는 아니다.

## 초기 구성과 앱 배포

이 root는 IAM 기반을 포함하므로 기존 CodeBuild root와 같이 SSO 관리자가 최초 구성한다. GitHub Apply 역할이나 앱에 IAM 생성 권한을 추가하지 않는다. 이후 플랫폼 앱은 기존 GitHub OIDC Release로 배포한다. GitHub 변수·Secret 추가는 없다.

1. `backend.hcl`에 기존 state 버킷, 별도 key `platform/sandbox.tfstate`, `encrypt=true`, `use_lockfile=true`를 지정한다.
2. `terraform.tfvars`에 계정·기존 플랫폼 private subnet/NAT ID·성공한 amd64 빌드 digest를 지정한다. 지정 대역의 기존 사용 여부와 다른 AZ를 확인한다.
3. 저장 Plan에서 교체·삭제·기존 팀 서비스 변경 여부를 검토한 뒤 적용한다. 두 파일과 state/Plan은 Git에서 제외한다.

```bash
AWS_PROFILE=delta-platform terraform -chdir=platform/deploy/sandbox init -backend-config=backend.hcl
AWS_PROFILE=delta-platform terraform -chdir=platform/deploy/sandbox plan -out=sandbox.tfplan
AWS_PROFILE=delta-platform terraform -chdir=platform/deploy/sandbox apply sandbox.tfplan
AWS_PROFILE=delta-platform terraform -chdir=platform/deploy/sandbox output -json > platform/.runtime/sandbox-outputs.json
uv run --project platform/deploy python platform/deploy/sandbox/configure-runtime.py \
  --profile delta-platform --account-id 616723161368 \
  --outputs platform/.runtime/sandbox-outputs.json
```

설정 도구는 기존 SSM `/delta-platform/runtime`의 로그인·DB·빌드 값을 보존하고 `PLATFORM_SANDBOX_TARGET`만 병합한다. 비밀값은 출력하지 않는다. 병합만으로 실행 중인 앱은 바뀌지 않으며 PR 병합 후 Platform release를 실행해야 한다.

앱이 관리하는 ECS 개정판·desired count·앱 포트와 TG 헬스 경로는 Terraform의 복원 대상에서 제외한다. AWS 기반의 subnet·SG·IAM은 Terraform이 관리한다. 앱은 자기 서비스·대상 그룹만 변경한다. 권한 범위는 [AWS ECS 권한 표](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ecs.html)와 [ELB V2 권한 표](https://docs.aws.amazon.com/service-authorization/latest/reference/list_elbv2.html)를 기준으로 제한했다. `DescribeTaskDefinition`과 `DescribeTargetHealth`는 리소스 단위 권한을 지원하지 않아 조회에만 `Resource=*`를 사용한다. 변경·PassRole 권한은 전용 자원에 제한한다.

## 실패·정리·종료

- `sandbox_occupied`: 다른 실행이 남아 있다. 강제 교체하지 말고 기존 작업 ID와 상태를 먼저 확인한다.
- `sandbox_cleanup_required`: 플랫폼 실행 상세 API의 `external_id`와 ECS 서비스의 task definition이 같은지 확인한다. 태스크 정의의 `DELTA_RUN_ID`가 해당 플랫폼 실행 ID인지 확인하고, **둘 다 일치할 때만** 해당 서비스의 desired count를 0으로 변경한다. desired/running/pending 모두 0인지 확인한 후 새 검사를 요청한다.
- IAM/AWS 장애로 정리 API가 실패하면 태스크가 남을 수 있다. DB 결과가 실패라고 자원이 종료된 것으로 판단하지 않는다. Worker가 정상 실행 중이면 중단 버튼으로 먼저 정리한다. 운영자가 서비스를 수동 변경하는 동안 플랫폼 Worker를 함께 실행하지 않는다.
- Worker 중단·재시작은 작업 임대 만료 후 복구한다. 플랫폼 서비스의 장기 중단 전에는 진행 중 검사를 취소하고 ECS의 태스크 0개를 확인한다. 등록된 미사용 task definition 개정판은 실행 비용이 없으며 U3는 자동 삭제하지 않는다.
- 샌드박스 비용을 완전히 끝내려면 새 접수를 막고 태스크 정리와 전용 root의 Destroy Plan을 검토한다. 같은 VPC의 플랫폼 VM·NAT나 기존 팀 스테이징 root를 삭제하지 않는다.
- U3 schema `0003` 롤백은 배포 전 DB 백업 또는 스키마 호환 수정 릴리스가 필요하다. 운영 DB에 강제 downgrade를 하지 않는다.

## 검증

```bash
terraform -chdir=platform/deploy/sandbox init -backend=false -lockfile=readonly
terraform -chdir=platform/deploy/sandbox validate
terraform -chdir=platform/deploy/sandbox test
```

mock 검증은 실제 AWS 실행 증거가 아니다. 실제 검증은 빌드의 digest → ECS 태스크의 digest → 태스크 IP의 ALB healthy → 종료 후 태스크 0개를 대조한다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| account_id | string | 필수 | 생성 계정과 provider 계정 제한 |
| region | string | ap-northeast-2 | 플랫폼과 같은 리전 |
| platform_private_subnet_id | string | 필수 | 기존 `10.80.1.0/24` 서브넷, VPC·AZ 조회와 ALB 한쪽 |
| nat_instance_id | string | 필수 | 기존 NAT의 ENI·SG 조회, 새 태스크의 HTTPS egress |
| second_az | string | ap-northeast-2b | 기존 서브넷과 다른 ALB AZ |
| bootstrap_image_digest | string | 필수 | 기존 `delta-platform-builds`의 성공한 amd64 이미지 digest |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| runtime_env | SSM app_env에 병합할 공개 설정 맵 |
| network | 사용한 VPC·새 서브넷 대역과 태스크 서브넷 ID |

`runtime_env.PLATFORM_SANDBOX_TARGET`는 JSON 문자열이며 내부 속성은 다음과 같다.

| 속성명 | 역할 |
| --- | --- |
| cloud, region, account_id | AWS 실행 대상과 계정 검증 |
| cluster, service, task_family | 전용 `delta-platform-sandbox` 실행 공간 |
| container_name | 이미지·포트를 바꿀 `app` 컨테이너 |
| target_group_arn | 헬스 경로 변경·상태 조회 대상 |
| url | 내부 ALB 주소, 이 단계에서는 클라이언트 접속 불가 |
| dynamic_scanner_access | false, 공개 scanner 접근 규칙 변경 금지 |
| allowed_image_prefixes | `delta-platform-builds@` digest 이미지 주소만 허용 |

`network` 내부 속성:

| 속성명 | 역할 |
| --- | --- |
| vpc_id, vpc_cidr | 기존 플랫폼 VPC와 전체 대역 |
| alb_cidr | 새 ALB 서브넷 `10.80.2.0/24` |
| task_cidr | 새 태스크 서브넷 `10.80.3.0/24` |
| task_subnet_id | ECS 태스크가 실행될 서브넷 ID |
