# GCP 샌드박스 기반

고객 빌드 이미지를 GCP Cloud Run에서 임시 실행하기 위한 기반이다. 운영 VM이나 DB 이전 모듈이 아니다. Terraform은 네트워크·IAM·이미지 저장소를 소유하고, Worker는 실행별 `delta-sbx-<run UUID>` 서비스를 생성·삭제한다.

AWS 플랫폼/DAST 역할은 WIF로 GCP의 runner/scanner SA를 사용한다. JSON 출력은 인증 설정이며 비밀 키가 없다. runtime SA에는 프로젝트 역할을 주지 않는다. Cloud Run 관리 API의 최소 custom role은 프로젝트 범위다. 이름 조건만으로 API 권한을 제한할 수 있다고 가정하지 않는다. 코드가 run ID·커밋·이미지·runtime SA·네트워크를 대조한 뒤 자기 실행만 변경한다. 운영 확장 시 검사 전용 프로젝트를 분리해 IAM 범위도 격리한다. 서비스는 IAM 인증 필수, 최소 0/최대 1 인스턴스다. VPC에 기본 인터넷 경로·NAT·VPN·피어링을 만들지 않고 외부 송신을 차단한다. 메타데이터 등 Cloud Run 자체 기능까지 VPC 방화벽이 차단한다는 의미는 아니다. 앱에는 운영 비밀값을 주지 않는다.

## 입력 속성

| 속성 | 타입 | 기본값 | 역할 |
|---|---|---|---|
| project_id | string | 필수 | GCP 프로젝트 |
| region | string | asia-northeast3 | Cloud Run/저장소/서브넷 리전 |
| subnet_cidr | string | 10.33.0.0/24 | 격리된 검사 전용 대역 |
| aws_account_id | string | 필수 | 플랫폼·검사기 AWS 계정 |
| platform_role_name | string | delta-platform-app | Worker EC2 역할 |
| scanner_role_name | string | delta-lab-tooling-host | DAST EC2 역할 |

## 출력 속성

| 속성 | 역할 |
|---|---|
| runtime_env | 기존 SSM app_env에 병합할 GCP 대상과 runner WIF JSON |
| dast_env | DAST 환경 파일에 병합할 GCP 대상과 scanner WIF JSON |
| subnet_cidr | 검사 전용 CIDR. TGW에 연결하지 않는다. |

## 적용과 검증

```sh
terraform init -backend-config=backend.hcl
terraform validate
terraform plan -out=sandbox.tfplan
terraform show sandbox.tfplan
terraform apply sandbox.tfplan
terraform output -json > outputs.local.json
```

별도 GCS state prefix를 사용한다. `backend.hcl`, 실제 tfvars, plan, 출력은 커밋하지 않는다. AWS는 `AWS_PROFILE=delta-platform`, GCP는 ADC를 사용한다. 현재 프로젝트에 이미 켜진 API는 새 state에서 활성 상태를 관리하며 destroy로 끄지 않는다. 기존 네트워크·SA·서비스는 제거하지 않는다.

`configure-runtime.py`는 기존 로그인·빌드·ECS 설정을 보존해 SSM에 두 값만 병합한다. DAST에는 같은 출력의 `dast_env`를 `platform/deploy/dast/configure.py --gcp-outputs`로 병합한 후 이미지를 재배포한다. 웹의 GCP 선택 가능 여부는 구성 유무이며 실제 검사 성공을 보장하는 표시는 아니다.

검사 대상은 ECR의 단일 linux/amd64 manifest다. 현재 작은 Worker를 위해 압축 레이어 512 MiB, 전체 2 GiB까지만 복사하며 Worker 디스크 임시 볼륨에서 한 레이어씩 처리한다. 원본과 목적지 digest를 대조하며 재빌드하지 않는다. 저장소 이미지는 하루 후 정리 대상이 된다. Cloud Run 삭제 실패 시 작업 공간 잠금을 유지하므로 먼저 원격 자원과 소유권을 확인한다. 임의로 DB 실행 상태를 완료 처리하지 않는다.

비활성화할 때 새 GCP 요청을 막고 진행 중 실행을 먼저 정리한 뒤 기반을 제거한다. 이 root의 destroy는 새 검사 기반에만 적용한다. 기존 AWS ECS 검사는 별도 root에 유지한다.
