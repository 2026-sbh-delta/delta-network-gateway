# GCP Sample App workload

`term2-delta`의 공개 리전 ALB, 사설 x86 VM과 Cloud SQL PostgreSQL을 관리한다. `single_vm_compose=true`이면 기존 FE VM 한 대에서 FE/BE Compose를 운영하고, false이면 이전의 VM 2대 구성을 정의한다. 네트워크와 네 subnet은 [network-security-gcp](../network-security-gcp/README.md)가 소유하며 이 root는 전달받은 ID를 사용한다. ALB는 FE 3000, FE는 Compose BE 8080, BE와 namespace를 공유한 loopback Auth Proxy는 DB PSC 3307로 연결한다.

소스는 `main.tf.json`의 Terraform JSON 형식과 HCL의 입력/provider 파일로 구성한다. Terraform 1.16.4와 Google 8.10.0을 고정한다. 상태는 중앙 S3의 별도 key `delta/lab/workload-gcp/terraform.tfstate`다. backend 예시의 계정과 암호화/잠금 설정을 확인하고 실제 파일은 Git에서 제외한다.

FE/BE는 Ubuntu 24.04 AMD64와 e2-small을 기본으로 사용한다. 공유 vCPU 2개/메모리 2GB이며 물리 전용 코어 2개를 뜻하지 않는다. DB는 Enterprise/PostgreSQL16/db-g1-small/SSD10GiB/단일 Zone이다. VM/DB에는 public IP를 만들지 않는다. 공개 ALB의 초기 listener는 HTTP80이며 도메인/인증서는 이번 기본 구성에 포함하지 않는다.

IAP와 health-check 응답은 Google 관리 special routing path를 사용한다. Google API 전용 경로만 별도로 관리하며 일반 인터넷 기본 경로나 NAT는 추가하지 않는다.

Cloud SQL 본체는 Google 관리 네트워크에 있다. DB 전용 subnet에는 PSC endpoint를 두며 별도 private DNS가 해당 endpoint를 가리킨다. Compose 통합 호스트에는 해당 DB의 SQL Client와 앱 secret 읽기 권한을 부여한다. FE 컨테이너는 secret을 받지 않으며 host iptables의 출발지 제한으로 metadata/DB/host 접근을 차단한다. 호스트 관리자는 이 컨테이너 경계 밖의 신뢰 주체다. 이전 BE 서비스 계정은 복귀용으로 보존한다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `project_id` | `string` | `term2-delta` | 승인된 프로젝트, 다른 값 거부 |
| `project_number` | `string` | `366844544284` | 실제 프로젝트 번호 대조 |
| `name` | `string` | `delta-board-gcp` | 기존 demo/staging과 구분한 자원 이름 |
| `region` | `string` | `asia-northeast3` | ALB/DB/VM의 서울 리전 |
| `zone` | `string` | `asia-northeast3-a` | FE/BE VM Zone |
| `network` | `object({network=string,subnets=map(string),cidrs=map(string)})` | 필수 | 네트워크 root의 application_network 출력 |
| `machine_type` | `string` | `e2-small` | x86 E2만 허용하는 VM 유형 |
| `image` | `string` | `ubuntu-2404-noble-amd64-v20261024` | 고정 Ubuntu AMD64 이미지 이름 |
| `db_tier` | `string` | `db-g1-small` | Cloud SQL 단일 Zone 사양 |
| `deletion_protection` | `bool` | `true` | FE VM/SQL의 삭제 보호 |
| `backend_deletion_protection` | `bool` | `true` | 이전 BE만 별도 단계에서 보호 해제 |
| `compose_host_access` | `bool` | `false` | 통합 FE 호스트에 앱 secret과 해당 SQL 연결 권한 추가 |
| `single_vm_compose` | `bool` | `false` | 검증된 Compose 전환 후 FE VM 한 대만 유지 |

### `network` 내부 속성

| 속성명 | 타입 | 역할 |
| --- | --- | --- |
| `network` | `string` | 기존 workload VPC의 resource ID |
| `subnets` | `map(string)` | frontend/backend/database/proxy 각 subnet ID. 서로 다른 네 ID 필요 |
| `cidrs` | `map(string)` | 같은 네 역할의 IPv4 subnet CIDR |
| `subnets.frontend`, `cidrs.frontend` | `string` | 기존 FE subnet, VM host20 |
| `subnets.backend`, `cidrs.backend` | `string` | BE subnet, VM host20 |
| `subnets.database`, `cidrs.database` | `string` | DB PSC subnet, endpoint host10 |
| `subnets.proxy`, `cidrs.proxy` | `string` | 리전 ALB proxy-only subnet, 일반 VM 배치 금지 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `application` | 프로젝트/리전/Zone, 공개 URL, 비공개 산출물 버킷, VM 이름/ID/사설 IP, DB 연결 이름/PSC IP와 secret ID. 비밀번호 없음 |

## 실행과 검증

먼저 네트워크 root를 검증/적용한다. 최초에는 네트워크 정책에 필요한 서비스 계정 2개만 별도 bootstrap Plan으로 생성하고 전체 workload Plan을 다시 만든다. 기존 state key를 재사용하거나 서로의 자원을 import하지 않는다.

```sh
terraform -chdir=terraform/workload-gcp init -backend-config=backend.hcl
terraform -chdir=terraform/workload-gcp validate
terraform -chdir=terraform/workload-gcp test
python3 -m unittest discover -s deploy/workload-gcp -p 'test_*.py' -v
```

운영자는 S3용 AWS 네트워크 계정 세션과 Google ADC를 사용한다. 현재 gcloud 활성 사용자 권한과 ADC가 다르므로 활성 사용자로의 자동 전환을 가정하지 않는다. 저장 Plan과 민감 원본은 ignored 경로에 0600으로 보관한다. Apply는 실제 생성/삭제 목록을 검토한 저장 Plan을 사용한다.

빌드/배포/DB 초기화/복귀는 [배포 Runbook](../../deploy/workload-gcp/README.md)을 따른다. 실제 검증 결과는 [AI-DLC](../../docs/ai-dlc/gcp-sample-app.md)에 기록한다.

## 단일 VM 전환

기존 FE VM의 startup metadata는 보존한다. Compose는 [별도 배포 도구](../../deploy/workload-gcp/README.md#단일-vm-compose)로 설치하고 systemd에서 관리한다. VM metadata를 바꾸어 ForceNew를 유발하지 않는다. 기존 BE를 둔 채 `compose_host_access=true`와 네트워크의 `compose_service_account`를 적용한 뒤 실제 Compose/DB/재부팅 검증을 완료한다. 이후 `backend_deletion_protection=false`를 저장 Plan으로 적용하고, 다음 Plan에서 `single_vm_compose=true`로 BE VM만 제거한다. SQL/FE의 삭제 보호는 계속 true다.

최종 출력 `application.instances`는 `frontend` 하나다. 이름은 기존 ID 보존을 위해 유지하며 이 VM이 FE/BE를 모두 실행한다. 빈 BE subnet과 복귀용 서비스 계정/기존 릴리스는 이번 작업에서 삭제하지 않는다.

2026-11-05 실제 운영은 `compose_host_access=true`, `single_vm_compose=true`, `backend_deletion_protection=false`다. VM1대와 공개 앱의 CRUD/VM 재부팅 자동 복구, 이전 BE VM/디스크404 및 최종 두 GCP root No changes를 확인했다. [실제 실행 기록](../../docs/ai-dlc/gcp-compose-consolidation.md)을 따른다.
