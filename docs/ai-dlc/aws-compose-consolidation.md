# AWS FE/BE의 단일 x86 EC2 Compose 통합

기준일: 2026-11-05 (Asia/Seoul).

## 승인과 현재 상태

- 사용자 변경 요청: `상황이 바뀜.. VM 한대에 FE/BE docker-compose로 그냥 말아서 올려줘.`
- Ideation 방향: 사용자가 직접 지정한 x86 VM 1대와 FE/BE Docker Compose를 기준으로 한다. 이전의 FE/BE 별도 EC2 조건을 이 방향으로 대체한다.
- Inception 및 U1~U3 Design/Implementation Plan: 2026-11-05 사용자 `가자잇`으로 아래 전환안의 구현/테스트/실제 배포와 정상화 후 기존 BE EC2 제거를 승인받았다.
- 완료: U1~U3 구현/Test/Review와 실제 두 단계 Apply, Compose 전환, 공개 CRUD/데이터 보존/컨테이너 및 EC2 재시작, TLS/IAM/방화벽/로그 검증을 완료했다. Terraform mock37개, bootstrap Python3개/배포 Python14개가 통과했고 최종 실제 Plan은 No changes/종료0이다. Git 게시 상태는 Git 이력을 따른다.
- 현재 운영 상태(2026-11-05 21:53 KST): 사용자 요청으로 FE/BE Compose 컨테이너와 프로젝트 네트워크를 내리고 메인 Compose 서비스의 자동 시작을 껐다. 기존 x86 EC2는 running, RDS는 available 상태이며 이미지/릴리스/데이터를 보존했다. 공개 API는 중지에 따른502다. 아래 전환/검증은 중지 전 기록이며 후속 중지 결과는 마지막 Operation 기록을 따른다.

## Ideation

- 문제 정의: 작은 데모 앱의 FE/BE를 별도 EC2 두 대에서 운영하는 구성을 한 대의 Docker Compose로 단순화한다.
- 사용자: Delta 팀 운영자와 기존 공개 데모 접속자.
- 성공 기준: x86 EC2 1대에서 FE/BE 컨테이너가 healthy이고 기존 공개 URL에서 CRUD와 BE 컨테이너/EC2 재시작 후 RDS 데이터 보존을 확인한다.
- Scope: 기존 FE EC2 재사용, Docker/Compose 설치, 독립 FE/BE 이미지와 배포 도구, 기존 RDS 연결, 호스트 IAM/SG와 컨테이너 접근 경계 조정, 자동 시작/복귀, 정상 전환 후 기존 BE EC2 제거, Terraform/Runbook/문서 동기화.
- Non-goals: RDS 교체/DB 컨테이너 추가/DB 재초기화, ALB/공개 URL 변경, 신규 HTTPS/도메인, NAT/VPC/TGW/EIC/state 이전, GCP/플랫폼 변경, 고가용성/자동 확장, 초기 전환 요청에 포함되지 않았던 Git 커밋/푸시.

## Inception

### Functional Requirements

1. 기존 FE EC2 `i-0637ad326681c7480` / `10.10.1.211`을 통합 호스트로 재사용한다. x86_64 `t3.small`, 암호화 EBS 20 GiB와 공인 IP 없는 배치를 유지한다.
2. FE/BE를 각각 linux/amd64 이미지로 빌드하고 같은 Compose 프로젝트에서 실행한다. FE의 호스트 포트 3000만 ALB에 제공하고 BE 8080은 컨테이너 내부에서만 사용한다. FE의 API_HOST는 Compose 서비스 이름 `backend`다.
3. RDS `delta-lab-notes` / PostgreSQL 18.3 / `db.t3.micro`와 기존 DB/앱 역할/데이터를 사용한다. 기존 RDS CA 및 endpoint 이름을 검증하는 TLS 연결을 유지한다.
4. 기존 앱 secret을 호스트의 제한된 런타임 파일로 받아 BE에만 Compose secret으로 제공한다. 현재 앱은 PGPASSWORD 환경변수를 요구하므로 BE 시작 wrapper가 파일을 읽고 앱 프로세스에 전달한다. 비밀번호를 Compose 환경 정의나 이미지에 넣지 않는다.
5. 운영자 도구가 clean 앱 커밋의 두 Docker build context를 비공개 S3로 전달하고 기존 FE 호스트에서 이미지를 빌드한다. 소스 커밋, source_dirty 여부, base image digest와 각 최종 image ID를 기록한다. 기존 FE/BE의 독립 Dockerfile/build 경계를 유지한다.
6. Docker/Compose 설치와 secret 조회, 방화벽 준비, Compose 시작을 멱등 호스트 배포 도구와 systemd로 관리한다. EC2 재부팅 후 같은 이미지/설정으로 자동 복구한다.
7. 기존 native FE 서비스를 중지/비활성화한 뒤 Compose FE로 전환한다. BE EC2는 초기 전환과 공개 검증이 끝날 때까지 유지한다. 정상화 후 실제 Plan으로 기존 BE EC2와 불필요해진 역할별 자원만 제거한다.
8. 배포 도구/출력/문서에서 실제 호스트 수와 FE/BE 서비스 배치를 구분한다. 기존 native 전용 install/restart/rollback 명령이 Compose 호스트에 잘못 적용되지 않도록 실행 모드를 검사한다.

### Non-Functional Requirements

- AWS 계정 `616723161368`, 리전 `ap-northeast-2`, backend key `delta/lab/workload-client/terraform.tfstate`를 유지한다. 기존 ALB/RDS/VPC/네 기본 서브넷/TGW/EIC ID와 GCP 목적지 경로를 보존한다.
- 현재 공개 주소는 `http://delta-lab-notes-alb-1242820262.ap-northeast-2.elb.amazonaws.com`이다. 데모용 HTTP라는 조건도 그대로다.
- 이전 두 EC2의 IAM/SG 경계는 한 호스트의 컨테이너 경계로 바뀐다. 호스트 역할은 두 앱 산출물/로그와 앱 secret에 접근해야 하지만 RDS 마스터 secret 권한은 주지 않는다.
- FE 컨테이너에는 DB secret/인증정보를 mount하지 않는다. 앱 컨테이너의 EC2 metadata 접근과 FE의 RDS 직접 접속을 호스트 방화벽으로 제한하고 실제 차단을 확인한다. 기존 IMDSv2 required/hop limit 1을 유지하며 컨테이너에 AWS 자격증명을 전달하지 않는다.
- 앱에 Docker socket, privileged, host network/PID를 제공하지 않는다. 비루트 이미지와 필요한 서비스 통신만 사용한다. Docker bridge 대역은 실제 VPC/TGW/GCP 목적지와 겹치지 않게 선택한다.
- 비밀번호는 호스트의 제한된 `/run` 파일과 BE의 secret mount에서만 취급한다. 이미지, 소스/S3 context, Terraform state/user data, Compose YAML/일반 환경 출력과 검증 로그에 넣지 않는다. Docker inspect의 설정 환경에도 비밀값이 남지 않게 한다.
- Docker daemon은 기존 역할별 CloudWatch 로그 그룹에 서비스별 로그를 전송한다. 로그 그룹을 BE EC2 수와 분리해 기존 BE 로그 이력과 보관 기간을 유지한다.
- FE의 기존 user_data는 이번 전환에서 수정하지 않는다. 현재 `user_data_replace_on_change=true`이므로 Compose 호스트 준비는 별도 배포 도구로 수행해 불필요한 FE 교체를 피한다. 새 호스트도 공통 bootstrap 후 같은 Compose 배포 절차를 사용한다.
- 기존 FE의 약 2 GiB 메모리와 루트 디스크 여유 약 18 GiB를 기준으로 시작한다. 빌드/실행 자원과 Docker 로그/이미지 사용량을 확인하고 임의로 인스턴스 사양을 올리지 않는다. 활성/직전 이미지를 임의 prune하지 않는다.
- 같은 호스트의 FE 포트 전환과 재부팅 검증에는 짧은 중단이 발생한다. 한 대 구성은 호스트 장애 시 FE/BE가 함께 중단되며 자동 장애 전환을 제공하지 않는다.
- 구현한 모듈 속성과 관련 README의 전체 입력/출력/복합 속성 표를 같은 작업에서 갱신한다. 실행하지 않은 테스트를 성공으로 기록하지 않는다.

### Architecture

```text
Internet -> 기존 Public ALB :80
                -> 기존 x86 FE EC2 / 사설 10.10.1.211
                     Docker Compose
                       frontend :3000 -> backend :8080
                                             -> TLS -> 기존 사설 RDS :5432

기존 EIC -> 통합 EC2 SSH
통합 EC2 -> 기존 NAT -> 이미지/설치물/S3/Secrets Manager/CloudWatch
```

ALB와 RDS의 두 AZ 서브넷은 유지한다. BE를 Compose로 옮겨도 PostgreSQL은 계속 RDS에 둔다. 호스트가 앱 secret을 조회하고 BE 컨테이너만 이를 읽는다. FE/BE 컨테이너 자체는 EC2 역할을 이용하지 않는다.

### Unit of Work, Design와 Implementation Plan

| Unit | Design | Implementation Plan | Test/Review 기준 |
| --- | --- | --- | --- |
| U1. 단일 호스트의 Compose 배포 | 기존 FE EC2에서 독립 FE/BE 이미지와 기존 RDS를 연결 | AWS용 Compose, 버전/해시를 기록하는 Docker/Compose 설치, 안전한 소스 context 전달/이미지 빌드, BE secret 시작 wrapper, metadata/FE DB 차단, systemd/실패 복귀 준비 | Compose config 구문, 두 amd64 이미지/실제 health, FE secret 부재/metadata 및 DB 차단, BE TLS/마스터 secret 거부, 비밀값 미출력, 기존 native 서비스와 포트 충돌 없음 |
| U2. Terraform 호스트 통합과 기존 BE 제거 | 인프라의 서비스 목록과 실제 EC2/IAM 목록을 분리하고 단계적으로 축소 | root/application 모드와 전환 중 기존 BE 보존 입력, 호스트별 IAM/SG/출력/테스트/README 갱신. 1차로 기존 BE를 유지하며 FE 호스트에 필요한 권한/DB 경로 적용, U1 전환과 공개 검증 후 2차로 BE/전용 자원만 제거 | 실제 두 Plan에서 의도한 변경/삭제만 발생. 기존 FE/ALB/RDS 교체0, 최종 앱 EC2 1대, 기존 BE terminated와 루트 EBS 삭제 확인, 로그 그룹/DB 데이터 보존 |
| U3. 운영 검증과 기록 | 공개 URL/DB 영속성/단일 호스트 자동 복구와 롤백 검증 | 기존 URL CRUD, BE 컨테이너 재시작과 EC2 재부팅 후 보존, ALB health/CloudWatch 수신, 실제 AWS readback과 최종 Plan, Runbook/AI-DLC 갱신 | 실제 화면/health 200, POST201/DELETE204, 검증 메모 보존, FE/BE healthy와 ALB healthy, 호스트 x86/이미지 amd64, 최종 Plan No changes |

U2의 첫 적용을 먼저 수행해 Compose가 연결할 호스트 권한을 준비하고 U1의 실제 전환을 진행한다. U2의 기존 BE 삭제는 전환 검증 이후다. 위 전체 Inception 및 U1~U3 Design/Implementation Plan을 사용자 승인 후 구현했고, 두 실제 Plan을 적용 직전에 각각 검토했다.

### Acceptance Criteria

1. 앱 EC2는 최종 한 대이며 FE/BE가 실제 Docker Compose 컨테이너로 실행한다. 사양은 x86_64 t3.small이고 공인 IP는 없다.
2. 기존 공개 URL에서 화면/API와 RDS 저장이 정상이다. 시험 메모를 만든 뒤 BE 컨테이너와 EC2를 재시작해 같은 데이터가 남는지 확인한다. 검증용으로 만든 메모만 삭제한다.
3. 기존 FE/ALB/RDS 및 네트워크 ID와 DB 데이터를 보존하고 기존 BE EC2의 terminated/EBS 삭제를 실제 확인한다.
4. FE에는 DB 비밀값이 없으며 컨테이너 metadata와 FE의 DB 직접 접근을 차단한다. BE의 실제 TLS 검증을 유지하고 호스트 역할의 마스터 secret 접근은 거부된다.
5. 재부팅 후 Docker/Compose/방화벽/secret 준비와 ALB가 정상화된다. 서비스별 CloudWatch 로그 수신을 확인하고 실패 시 복귀 절차를 제공한다.
6. 로컬 테스트/실제 Plan/Apply/배포/검증/Git 게시를 구분한다. 변경한 모듈 README와 운영 문서가 실제 코드/검증 결과와 일치한다.

## Operation 계획

- Deployment: 현재 네트워크/IAM/배포 정보를 보관한다. 기존 BE를 유지한 첫 Plan으로 호스트 권한과 DB 경로를 준비하고 Docker/이미지를 설치한다. native FE를 중지해 Compose로 전환하고 공개 CRUD/컨테이너 재시작/호스트 재부팅을 확인한다. 정상화 후 기존 BE 제거 Plan을 검토/적용하고 최종 한 대 상태를 확인한다.
- Observability: `docker compose ps`, 서비스별 health, `docker stats`, 루트 디스크와 Docker 사용량, ALB target health, 기존 두 CloudWatch 로그 그룹 및 RDS 상태를 확인한다. 전체 inspect/env/secret 내용은 출력하지 않는다.
- Rollback: 기존 BE 제거 전에는 Compose를 중지하고 기존 native FE/BE 릴리스로 복귀한다. 제거 뒤에는 같은 호스트의 직전 이미지/Compose 설정으로 복귀한다. 이전 두 EC2 구조가 필요하면 보관한 입력/코드의 새 Plan으로 BE를 재생성해야 한다. DB 삭제/재초기화는 롤백 절차에 포함하지 않는다.
- Runbook: 단일 Compose 배포/개별 재시작, 이미지 버전 복귀, secret 갱신, EC2 재부팅과 장애 진단을 `deploy/workload/README.md` 및 `terraform/workload/RUNBOOK.md`에 구현과 함께 반영한다.

## 확인 기록

| 날짜 | 명령/확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-05 KST | AGENTS.md, docs/README와 AWS AI-DLC, Terraform/배포 도구 읽기 | 현재 두 EC2/native systemd 구성과 단계별 승인 규칙 확인. FE user_data 변경은 교체를 유발할 수 있음 |
| 2026-11-05 KST | 앱 `git status --short --branch`, `git rev-parse HEAD`, Compose와 두 Dockerfile 읽기 | 현재 로컬 main은 fd9a661fdef4d24c5ae7d969677a1daa0c229e88이며 clean. 독립 FE/BE 이미지/health 존재. 기본 Compose에는 PostgreSQL 연결 설정이 없어 배포용 구성 필요. 이 조회에서 최신 원격 SHA는 별도 확인하지 않음 |
| 2026-11-05 KST | `terraform output -json application`와 `aws sts get-caller-identity` | 현재 state 출력과 계정 616723161368 일치. 비밀번호 조회 없음 |
| 2026-11-05 KST | `aws ec2 describe-instances`, `aws rds describe-db-instances`, `aws elbv2 describe-load-balancers` | 기존 FE/BE 모두 running/x86_64/t3.small/공인 IP 없음/IMDSv2 required/hop limit 1. RDS available/18.3/사설/암호화/삭제 보호. 기존 ALB active/같은 DNS |
| 2026-11-05 KST | 기존 EIC 배포 도구로 FE의 `platform.machine`, `shutil.which`, `/proc/meminfo`, `df -h /`, `systemctl is-active delta-sample`, `cloud-init status` 조회 | x86_64, Docker/iptables는 ec2-user PATH에서 발견되지 않음, MemTotal 1,955,412 kB, EBS 20 GiB 중 약18 GiB 여유, 기존 native 서비스 active/cloud-init done. 패키지 설치/서비스 변경 없음 |

위 확인 기록은 설계 당시의 조회다. 이후의 구현/실제 적용 상태는 다음 진행 기록을 따른다.

## 구현과 실제 전환 진행 기록

- 2026-11-05: 사용자 `가자잇`으로 Inception 및 U1~U3의 구현/테스트/실제 전환과 정상화 후 기존 BE EC2 제거를 승인받았다.
- U1/U2 구현: 실제 호스트와 서비스 목록을 분리하고 compose 모드/전환 중 BE 보존 입력, 단일 호스트 IAM/DB SG와 서비스 매핑 출력을 추가했다. 기존 FE user_data는 유지한다. Compose 배포/secret 파일/metadata 및 FE DB 차단/부팅 순서/실패 복귀 도구와 문서를 추가했다.
- `bash terraform/workload/scripts/validate.sh`: 허용된 실행 환경에서 init/validate/root mock28개/child mock9개 및 Python3개 통과. 최초 샌드박스 실행은 provider 프로세스 시작 제한으로 실패했으며 실제 AWS 실패로 기록하지 않는다.
- `python3 -m unittest discover -s deploy/workload/tests -v`: 12개 통과. context 재현성/비밀 파일 제외/dirty 소스 거부, 첫 전환 실패의 native 복귀, 업그레이드 실패/직전 Compose 복귀, 상태 파싱을 확인했다. 첫 테스트의 macOS 임시 경로 별칭 차이는 fixture 경로 정규화로 해결했다.
- `python3 -m py_compile ...`, `sh -n deploy/workload/backend-entrypoint.sh`, `terraform fmt -check -recursive terraform/workload`, `git diff --check`: 현재 변경 검사 통과.
- 실제 `terraform plan -input=false -detailed-exitcode -lock-timeout=30s -out=<prepare.tfplan>`: 종료2. Compose DB ingress/egress 추가2개, FE IAM inline policy 수정1개, 삭제/교체0개. 기존 EC2/ALB/RDS/VPC는 모두 no-op임을 JSON으로 대조했다.
- 검토한 저장 Plan의 `terraform apply -input=false <prepare.tfplan>`: 종료0, 추가2개/수정1개/삭제0개. 전환 중 기존 BE를 유지하는 입력을 사용했다. 당시 실제 앱은 native 구성이었다.
- 기존 공개 URL에서 검증용 메모 POST201을 확인하고 ID를 보관했다. 이후 Compose 전환/컨테이너 및 호스트 재시작 뒤 이 메모의 보존을 확인할 예정이며 기존 사용자 메모를 수정하지 않는다.
- clean 앱 커밋 fd9a661fdef4d24c5ae7d969677a1daa0c229e88의 Docker 이미지 빌드/전환을 완료했다. 해당 커밋은 실행 전 원격 main과 일치함을 확인했다. source_dirty=false이며 앱 저장소 소스는 변경하지 않았다. 이후 실제 결과는 아래 표를 따른다.

실제 입력/Plan/Apply/검증 메모 ID와 실행 로그는 ignored `terraform/workload/.validation/aws-compose-20261105/`, context/manifest는 `.validation/compose-releases/`에 제한된 권한으로 보관한다.

## 실제 전환 완료와 검증

공개 주소: [Delta Notes](http://delta-lab-notes-alb-1242820262.ap-northeast-2.elb.amazonaws.com).

| 대상 | 최종 실제 상태 |
| --- | --- |
| 통합 호스트 | 기존 `i-0637ad326681c7480` / `10.10.1.211`, x86_64 `t3.small`, 공인 IP 없음, IMDSv2 required/hop limit 1 |
| 컨테이너 | frontend/backend 모두 linux/amd64, healthy, 비루트 node, read-only rootfs. FE3000만 publish, BE port binding 없음 |
| 기존 BE | `i-05bc064b10ac062a0` terminated, 루트 `vol-0eb99c5c96c160e17`은 InvalidVolume.NotFound 확인 |
| DB | 기존 `delta-lab-notes`, 사설 PostgreSQL18.3/db.t3.micro, 같은 endpoint/DB/역할/데이터, 암호화/백업7일/삭제 보호 유지 |
| DB 및 IAM | BE의 실제 TLSv1.3/평문 거부, 앱 계정 superuser/createdb/createrole false, 호스트의 마스터 secret AccessDenied |
| 접근 제한 | 두 컨테이너의 metadata PUT 차단, FE의 RDS 직접 TCP 접속 차단. 호스트 재부팅 뒤에도 동일하게 재검증 |
| 자동 시작 | docker/delta-compose-prepare/delta-compose active 및 enabled, 기존 native 서비스 inactive/disabled |
| Secret | `/run` 파일 UID1000/0400, BE에만 mount, FE mount 없음. 두 컨테이너 Config.Env/Config에 실제 비밀번호 없음 |
| 관측 | 기존 두 CloudWatch 그룹 보존, 같은 검증 request ID의 새 Compose stream FE11건/BE8건 확인 |
| 기반 | ALB URL/FE ID/RDS/VPC/state 보존. 기존 GCP 10.32.1.0/24 목적지 두 TGW route active |

| 날짜 | 실행한 명령 또는 확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-05 KST | `compose.py deploy --sample-root ../delta-sample-app` 첫 실행 | Docker 패키지 설치 후 공식 GitHub Compose 다운로드의 HTTP redirect를 따라가지 않아 빈 파일 수신. 고정 SHA-256 검사에서 중단, native FE active와 기존 BE 유지 확인 |
| 2026-11-05 KST | curl에 `--location` 추가 후 같은 deploy 재실행 | 종료0. 공식 x86 Compose 바이너리 SHA-256 일치, 이미지 두 개 빌드와 health 확인 후 native FE를 중지/비활성화하고 Compose로 전환 |
| 2026-11-05 KST | 공개 `/`, `/runtime-config.json`, `/api/health`, `/api/notes` 확인 | 모두200, storage=postgres/커밋 fd9a661f 확인. 전환 전 생성한 메모 보존 |
| 2026-11-05 KST | 실제 docker inspect와 컨테이너 내 metadata/DB 연결 시험, BE pg_stat_ssl/pg_roles 및 비TLS 별도 접속, 호스트 master secret 조회 | amd64/비루트/포트 및 secret 미노출, FE DB/두 metadata 차단, TLSv1.3/평문 거부/역할 제한/마스터 secret 거부 모두 확인 |
| 2026-11-05 KST | `compose.py restart --service backend` 및 공개 메모 조회 | 실제 BE 컨테이너 재시작 뒤 healthy와 동일 메모 보존 |
| 2026-11-05 KST | `aws ec2 reboot-instances`와 boot_id/공개 health/메모/ALB 확인 | 같은 EC2에서 boot_id 변경을 확인. 자동 시작/healthy/공개 API200 및 메모 보존. secret 권한과 metadata/FE DB 차단도 재검증 |
| 2026-11-05 KST | `retain_legacy_backend=false`의 실제 저장 Plan과 JSON 검토 | 종료2. 기존 BE EC2(루트 EBS 포함)와 전용 IAM/SG/규칙 등 Terraform 자원12개 삭제만 계획. 생성/수정/교체0, 기존 FE/ALB/RDS/네트워크/로그 그룹 no-op |
| 2026-11-05 KST | 검토한 `remove-legacy.tfplan` Apply 및 EC2/EBS API 확인 | 종료0, 추가/수정0/삭제12개. 실제 BE terminated와 루트 EBS 부재 확인. 앱의 비종료 EC2는 기존 frontend 한 대 |
| 2026-11-05 KST | 제거 후 공개 메모 보존/같은 Origin POST/다른 Origin POST/DELETE | 기존 검증 메모 보존. 같은 Origin201/다른 Origin403/DELETE204. 직접 만든 검증 메모 두 개만 정리했고 이후 목록에서 부재 확인 |
| 2026-11-05 KST | `aws logs filter-log-events`를 새 Compose stream/검증 request ID로 제한 | FE11건/BE8건 수신 확인. 기존 사용자 메모나 비밀번호를 출력하지 않음 |
| 2026-11-05 KST | `python3 -m unittest discover -s deploy/workload/tests -v` | 최종14개 통과. Compose에서 native 서비스 명령 거부와 운영자 DB 초기화 dispatch도 검사. 이번 운영 DB 초기화는 재실행하지 않음 |
| 2026-11-05 KST | 최종 실제 `terraform plan -input=false -detailed-exitcode -lock-timeout=30s` | No changes/종료0 |

### 배포 식별과 Review

- 소스: `fd9a661fdef4d24c5ae7d969677a1daa0c229e88`, source_dirty=false.
- Context/도구 bundle SHA-256: `b76a0b2519d7ab4d25325c84edba698cf1674d8b5a87e0146bb88f397621fcbe`.
- BE image ID: `sha256:5aff961c83a6fc7bf79d5a455e2441927bbb08933309a01130e21e5c7a39391e`.
- FE image ID: `sha256:82daf698f9f4cc91061e80105499eeb68b2a7a9040a1c8210107e2512066602a`.
- Node base: `node@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43`.
- 설치 패키지: Docker `25.0.18-1.amzn2023.0.2.x86_64`, 실제 CLI 출력 `Docker version 25.0.14, build 0bab007`. 패키지 버전과 CLI 출력은 구분해 기록한다. Compose 실제 출력은 `5.7.0`이고 공식 바이너리 SHA-256은 코드에 고정했다.
- U1~U3 Review: 기존 FE 교체0, ALB/RDS/네트워크/state와 DB 데이터 보존, 단계별 BE 제거, 한 호스트의 보안 경계와 실제 자동 복구를 확인했다. 기존 VM별 IAM 격리와 같다고 설명하지 않는다.
- 실제 직전 Compose 버전 rollback은 첫 성공 릴리스여서 이전 버전이 없어 미실행이다. 첫 설치 실패의 native 복귀와 업그레이드 실패/직전 포인터 복귀는 로컬 테스트로 검증했다. 첫 실제 다운로드 실패는 native 서비스 중지 전에 발생했다.
- 운영 검증 시점에는 코드/문서를 작업 트리에 반영했고 Git 커밋/푸시와 새 원격 CI는 실행하지 않았다. 이후 게시 상태는 Git 이력을 따른다. 별도 GCP 작업 파일은 보존했다.

## 공식 근거

- [Docker Compose의 서비스별 secret file mount](https://docs.docker.com/compose/how-tos/use-secrets/): secret은 지정한 서비스에만 파일로 제공한다. `_FILE` 변수 지원은 앱 구현에 달려 있으므로 현재 BE에는 시작 wrapper가 필요하다.
- [Docker의 iptables와 DOCKER-USER](https://docs.docker.com/engine/network/firewall-iptables/): Docker가 생성하는 규칙보다 먼저 컨테이너 통신을 제한할 위치를 확인한다.
- [EC2 metadata 옵션과 hop limit](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-options.html): hop limit 1인 현재 호스트 조건을 유지하고 컨테이너 metadata 차단을 실제 확인한다.

## 2026-11-05 후속 Operation: Compose만 내리고 EC2 보존

- 사용자 요청: `오키 instance는 남겨두고 docker-compose 올린 것만 내려`.
- 승인된 실행 범위: AWS의 기존 EC2/RDS/ALB/네트워크와 이미지를 유지하면서 delta-notes FE/BE 컨테이너만 내리고 재부팅 시 앱 자동 시작을 해제한다. 신규 구현이나 Terraform 입력/리소스 변경은 하지 않는다.
- 실행 시각: 2026-11-05 21:53 KST. 대상은 기존 `i-0637ad326681c7480` / `10.10.1.211`이다. GCP Compose는 이번 작업 범위가 아니다.

| 날짜 | 실행한 명령 또는 조회 | 실제 결과 |
| --- | --- | --- |
| 2026-11-05 KST | `terraform output -json application`, STS 대조, 현재 릴리스/프로젝트 조회 | 계정616723161368/compose/frontend EC2 한 대와 현재 릴리스 확인. 프로젝트 delta-notes의 FE/BE 두 컨테이너 실행 중 |
| 2026-11-05 KST | `sudo systemctl disable --now delta-compose` | 종료0. 메인 서비스 inactive/disabled, 기존 native 서비스도 inactive/disabled 유지 |
| 2026-11-05 KST | 현재 release의 env/file와 `--project-name delta-notes`를 명시한 `docker compose down` | 종료0. FE/BE 컨테이너 제거, delta-notes-app 네트워크 제거. 프로젝트 컨테이너는 중지 항목을 포함해0개 |
| 2026-11-05 KST | Docker image inspect 및 systemctl 조회 | 두 image ID 보존, Docker daemon은 active/enabled 유지. 볼륨/이미지 삭제 옵션이나 prune 미실행 |
| 2026-11-05 KST | EC2/RDS AWS API readback | 동일 EC2 running/t3.small/10.10.1.211, 기존 RDS available/동일 endpoint |
| 2026-11-05 KST | 공개 `/api/health` 재조회 | HTTP502, 앱 중지 반영. DB/메모를 변경하거나 검증 데이터를 추가하지 않음 |

검증 결과는 ignored `terraform/workload/.validation/aws-compose-20261105/compose-down.json`에 보관한다. 중지 작업 당시에는 원격 런타임만 변경했고 Terraform Plan/Apply나 Git 커밋/푸시는 실행하지 않았다. 앞의 최종 Plan No changes는 배포 완료 시점의 기록이다.

재개는 호스트에서 `sudo systemctl enable --now delta-compose`로 수행한다. 이 명령은 보관한 현재 이미지/설정으로 컨테이너와 네트워크를 다시 만들고 secret/방화벽 준비와 health를 확인한다. 재개 명령은 이번 작업에서 실행하지 않았다.

## 2026-11-05 게시 준비

- 사용자 `커밋 푸시` 요청으로 최신 원격 main 기준 별도 작업 영역에 AWS Compose와 관련 문서만 반영한다. 별도 GCP 작업은 포함하지 않는다.
- `bash terraform/workload/scripts/validate.sh`: root mock28개, child mock9개, bootstrap Python3개 통과. `python3 -m unittest discover -s deploy/workload/tests -v`: 14개 통과. `python3 -m py_compile`과 `git diff --check` 통과. 실제 Terraform Plan/Apply나 앱 재개는 이 게시 준비에서 실행하지 않았다.
- 게시 커밋과 원격 반영은 Git 이력을 따른다. 앱은 중지 상태이며 인프라는 유지한다.
