# 플랫폼 전용 AI 실행 기반

> 2026-11-05: 사용자 범위 정정으로 별도 AI 서버 추가를 중단했다. EC2는 생성되지 않았으며 준비 자원 일부만 있다. 이 모듈을 자동 적용하지 않는다. 현재 플랫폼은 팀원 AI 서비스와의 연결을 기준으로 정리한다. [최신 범위](../../../docs/ai-dlc/platform-subnets.md)

`플랫폼 API/Worker → 사설 HTTPS → delta-ai` 구성이다. AI는 판단 결과를 반환하고 승인·배포 실행 권한은 플랫폼이 갖는다. 서비스 구현은 `services/delta-ai/`를 사용하며 실행 기반만 이 폴더에서 관리한다.

팀원의 `delta-lab-ai-host`, `delta-ai/api` ECR, `/delta/ai/*`, `sbh-delta-workload-tf-616723161368` 버킷/state를 가져오거나 수정하지 않는다. 우리 자원은 **`delta-platform-ai`**, 런타임은 **`/delta-platform/ai/runtime`**, state는 **플랫폼 전용 버킷의 `platform/ai.tfstate`**로 분리한다. Terraform state에 토큰·모델 API 키·인증서 개인키를 넣지 않는다.

## 준비와 배포

1. 플랫폼 root의 `service_subnets.ai.id`와 플랫폼 API/Worker SG ID를 입력한다. 기존 NAT·전용 subnet은 플랫폼 root 소유다.
2. 아래 backend 예시를 별도 `backend.hcl`로 만들고 `terraform init -backend-config=backend.hcl`을 실행한다. 이 root의 초기 IAM 생성은 운영자 SSO로 검토한 Plan을 적용한다.
3. `terraform validate`, `terraform test`, 실제 저장 Plan 검토 후 Apply한다. 생성 완료만으로 API가 동작한다고 판정하지 않는다.
4. 현재 main의 `services/delta-ai/Dockerfile`을 ARM64로 빌드해 **이 root의 ECR**에 게시하고 digest로 배포한다. 런타임 비밀값은 SSM SecureString으로 준비한다.
5. 플랫폼 VM에서 TLS 검증을 유지한 `/healthz`, 인증 없는 판단 요청 거부, 인증된 판단 요청과 결과 보존을 확인한다. 확인 후 플랫폼의 AI 연결 설정을 갱신한다.

```hcl
bucket = "delta-platform-tf-616723161368"
key = "platform/ai.tfstate"
region = "ap-northeast-2"
encrypt = true
use_lockfile = true
allowed_account_ids = ["616723161368"]
```

팀원 AI root의 backend를 여기에 복사하거나 `terraform import/state mv`로 기존 자원을 가져오지 않는다. 팀원의 변경은 서비스 코드/이미지 계약을 통해 받아 테스트한다. PR #28의 아직 병합되지 않은 기능을 현재 main에 배포됐다고 설명하지 않는다.

런타임 준비와 설치 명령(출력 JSON은 ignored 경로에 저장):

```bash
terraform output -json > ../../.runtime/ai-outputs.json
uv run --project platform/deploy python platform/deploy/ai/operate.py prepare --profile delta-platform --outputs platform/.runtime/ai-outputs.json
uv run --project platform/deploy python platform/deploy/ai/operate.py deploy --profile delta-platform --outputs platform/.runtime/ai-outputs.json --image <전용_ECR@sha256:digest>
```

`prepare`는 기존 비밀값을 덮어쓰지 않는다. 인증서는 30일, CA는 90일이며 자동 갱신은 아직 없다. 배포는 플랫폼 전용 아티팩트 버킷의 `releases/ai/`를 사용한다. 팀원 버킷은 사용하지 않는다.

## 통신과 저장

- 주소: `10.80.20.10:443`, 공인 IP/SSH 없음. 플랫폼 SG만 API에 접근한다.
- `t4g.small`, 단일 AZ. 외부 LLM API 호출 위주이며 모델을 VM에서 직접 호스팅하지 않는다.
- 전용 암호화 20 GiB EBS에 판단 기록을 저장하고 `prevent_destroy`로 보호한다. 이는 백업/HA를 대신하지 않는다.
- SSM 관리, ECR 이미지 pull, 해당 런타임과 플랫폼 배포 파일 읽기만 구성한다. 고객 AWS/GCP 인프라 변경 권한은 부여하지 않는다.
- 기본 테스트 모드는 `DELTA_LLM=none`이다. 이때 규칙/HTTP 연결 검증과 실제 LLM 분석을 구분한다. 실제 모델 사용에는 플랫폼용 비밀값을 따로 설정한다.
- CloudWatch 중앙 로그 수신 경로는 이 모듈과 독립적이다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `account_id` | string | 필수 | 배포할 플랫폼 AWS 계정 |
| `region` | string | ap-northeast-2 | AWS 리전 |
| `subnet_id` | string | 필수 | 플랫폼 AI 전용 10.80.20.0/24 subnet |
| `platform_security_group_id` | string | 필수 | AI 호출을 허용할 플랫폼 API/Worker SG |
| `ami_id` | string | 필수 | 고정 AL2023 ARM64 AMI |
| `instance_type` | string | t4g.small | small/medium으로 제한한 VM 크기 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `instance_id` | SSM 배포 대상 |
| `private_ip` | AI 사설 주소 |
| `api_url` | 인증서 검증이 필요한 AI HTTPS 주소 |
| `runtime_parameter` | 별도로 준비할 SecureString 이름 |
| `repository_url` | 플랫폼 전용 AI 이미지 저장소 |
| `security_group_id` | 플랫폼 호출만 허용하는 AI SG |
| `data_volume_id` | 보호된 판단 기록 EBS |
| `account_id` | 배포 계정 |
| `region` | 배포 리전 |

실제 구현·Plan·Apply·기능 검증 상태는 [진행 기록](../../../docs/ai-dlc/platform-subnets.md)을 따른다. 새 환경을 만든 뒤 기존 팀원 서버를 종료하는 작업은 이 모듈의 범위가 아니다.
