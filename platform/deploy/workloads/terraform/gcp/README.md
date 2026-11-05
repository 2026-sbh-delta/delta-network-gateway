# 고객 앱 GCP VM

플랫폼이 승인한 환경 ID마다 서울 리전의 새 VM 한 대를 생성한다. 기존 VPC·VPN·방화벽·S3 state는 수정하지 않는다. Debian 12 **x86_64 고정 이미지**를 지정한다. 사설 subnet의 Cloud NAT/Private Google Access, Artifact Registry reader SA, OS Login/IAP 관리 접근 및 Agent→앱 TCP 8088 경로는 연결 담당자가 준비한다. 8088 방화벽은 지정한 LB·검사 Agent에만 허용한다.

## 입력 속성

| 속성 | 타입 | 기본값 | 역할 |
|---|---|---|---|
| project_id | string | 필수 | 생성 프로젝트 |
| zone | string | 필수 | asia-northeast3-a/b/c 중 하나 |
| subnetwork | string | 필수 | 준비된 사설 subnet 전체 경로 |
| service_account | string | 필수 | 지정 Artifact Registry 읽기 권한의 VM SA |
| image | string | 필수 | Debian 12 x86_64 고정 이미지 경로 |
| network_tag | string | 필수 | 사전 준비한 방화벽 규칙의 대상 태그 |
| environment_id | string | 필수 | 신규 환경 UUID·소유 라벨 |
| service_id | string | 필수 | 서비스 UUID·소유 라벨 |
| profile_id | string | 필수 | e2-micro 또는 e2-small |

## 출력 속성

| 속성 | 역할 |
|---|---|
| instance_id | 실제 GCP VM 숫자 ID |
| instance_name | OS Login/IAP 배포 대상 이름 |
| private_ip | Agent에서 실행 검사를 하는 주소 |
| zone | 환경 등록과 SSH 대상 zone |

ECR 이미지는 같은 digest로 Artifact Registry에 복사한다. 소스를 다시 빌드하지 않는다. 해당 이미지 저장소는 운영자가 미리 만든 것을 사용한다. 환경별 영속 로컬 state를 보존하며 실행기는 새 생성과 변경 없음만 허용한다.
