# AWS→GCP 차분 기반 Terraform 배포

2026-11-08 KST. 사용자 승인 범위는 AWS→GCP 차분 결과로 GCP 사설 VM Terraform을 생성하고, Plan 승인 뒤 적용부터 앱 헬스체크까지 연결하는 것이다. 최초 지원 범위는 기존 GCP 서브넷과 준비된 PostgreSQL 18을 사용하는 FE·BE 워크로드 한 개다. 데이터 이관과 DNS 전환에는 각각 기존 절차를 적용한다.

운영자는 AWS state에서 배포할 서버와 서브넷을 지정한다. 차분 에이전트는 GCP 기존 서브넷 인벤토리를 사용해 재사용 매핑을 만든다. 그래프는 지정한 `MAP-CMP-001`과 `MAP-NET-001`의 지원되는 결과만 선택한다. FE·BE 운영 배포기의 최소 사양은 2 GiB이므로 `e2-micro` 매핑은 승인 화면에 원래 값과 배포 사양 `e2-small`을 함께 표시한다. 그보다 큰 매핑은 배포 전에 중단한다.

1. 운영 배포 승인에 실행 ID, 대상 인벤토리, 이미지 묶음, AWS state serial을 포함한 매핑의 해시를 묶는다. 신규 `delta-mapped-release` 작업은 서명을 검증하고 배포 ID를 예약한다.
2. 배포기가 기존 VM 모듈을 복사해 `mapping_override.tf.json`과 `mapping_subnet.tf.json`을 생성한다. GCP에서 조회한 서브넷의 리전과 이름은 등록된 경로에 묶이고, CIDR이 매핑과 일치해야 Plan이 통과한다. Plan에는 신규 VM 생성, 기존 VM 유지, 서브넷 조회만 허용한다.
3. 대시보드의 운영 배포 패널에서 Jenkins 빌드로 이동한다. Jenkins의 `terraform-plan.json`에서 매핑, VM 이미지, 서비스 계정, 변경 목록, Plan SHA-256을 검토한다. Jenkins 관리자가 해당 빌드의 입력 단계에서 15분 안에 승인한다. 작업 전체는 60분으로 제한한다.
4. 적용 전에 현재 배포 설정과 저장된 Plan 바이트를 승인 당시와 대조한다. Terraform은 저장된 Plan을 적용한다. 이어서 기존 배포기가 검증된 FE·BE 이미지로 Compose를 실행하고 VM에서 실행 이미지 digest, 앱 헬스체크, PostgreSQL 18 연결을 확인한다. 그래프는 같은 매핑과 적용한 Plan 해시가 포함된 결과를 받아야 배포 성공을 기록한다.

## 운영 연결

- Jenkins 컨트롤러 이미지와 오케스트레이터 및 Diff 서비스를 이 소스로 갱신한다. Jenkins 관리자가 `delta-mapped-release` 작업과 Plan 입력 권한을 확인한다.
- 오케스트레이터의 `DELTA_RELEASE_TARGETS`와 Jenkins의 `release-targets.json`에 같은 대상 항목을 등록한다. [대상 예제](../../deploy/platform/release/targets.example.json)에 AWS 원본 매핑 행을 고르는 `terraform_mapping` 필드가 있다. `source_compute`는 차분 표의 서버 이름과 인스턴스 타입을 포함한 원문이고, `source_subnet`은 차분 표의 원본 서브넷 이름이다.
- Diff 요청 파일 `DELTA_DIFF_REQUEST_FILE`의 `target.existing_subnets`에 `name`, `cidr`, `tier`를 등록한다. 매핑은 각 공개·사설 유형에서 첫 후보를 사용한다. 배포할 사설 후보의 이름은 Jenkins 서버 설정 `connections.gcp.terraform.subnetwork`와 같아야 한다.
- 기존 PostgreSQL 18 프로필은 `mode=existing`으로 선택한다. Cloud SQL 연결에는 인증서 SAN과 같은 DNS 이름을 넣고, DNS 이름 끝에 점이 있으면 그대로 유지한다. GCP 관리 접근, 앱 이미지 읽기, Terraform state 접근에는 기존 배포 자격 증명과 잠금을 사용한다.
- 운영 반영에 실패하면 같은 환경 ID의 원격 state와 Jenkins 결과를 확인한다. 재실행에는 새로 검토한 Plan이 필요하다. 준비된 DB와 VM 상태를 남겨 조사한다.

## 검증

- 차분 에이전트, 파이프라인, 오케스트레이터, 배포기, Jenkins 테스트를 실행해 521개가 통과했다.
- Terraform 1.16.4와 Google provider 7.46.2로 생성 구성의 `validate`를 통과했다. `terraform test`의 모의 provider로 VM Apply, 사설 인터페이스와 Secure Boot, 대상 서브넷 CIDR 불일치 차단을 확인했다.
- 격리된 Jenkins 2.580.2에서 실제 JCasC와 Jenkinsfile을 실행하고 배포 명령은 시험용으로 대체했다. 일반 계정은 Plan을 승인할 수 없었고, 관리자가 승인하면 표시된 Plan 해시가 Apply 단계에 전달됐다. 거절 시 Apply 실행 없이 빌드가 중단됐다.
- 2026-11-08 KST, 격리 러너에서 실제 AWS state serial 21의 `t3.small`을 GCP `e2-small`로 매핑했다. `terraform plan`은 `asia-northeast3-a`의 기존 서브넷에 공인 IP 없는 VM 1대 생성, 변경 0, 삭제 0을 반환했다. 승인된 Plan의 SHA-256은 `0c4595131a7331607aa96d979e0509d1bde5c272c2aec05d3a479aeb5caf2071`이다. `terraform apply`로 생성한 VM의 전용 S3 state를 확인했고 적용 후 Plan은 `No changes`였다.
- 격리 러너에 ADC 경로를 명시하고 Cloud SQL 인증서의 원래 DNS 이름을 사용한 뒤, 이미 생성한 VM에서 앱 배포를 재개했다. 별도 논리 DB에 연결한 `ff64508de0f1` FE·BE의 실행 이미지 digest가 모두 일치했고, Docker inspect는 둘 다 `healthy`, 사설 FE·BE `/healthz`는 각각 HTTP 200이었다. FE를 경유한 `/api/health`와 PostgreSQL `SHOW server_version_num`은 `180006`을 반환했다.
- 실측은 별도 러너와 검증 VM에서 수행했다. 운영 Jenkins WIF와 운영 대상 활성화는 별도 릴리스 작업이다.
