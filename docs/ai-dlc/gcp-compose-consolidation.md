# GCP FE/BE의 단일 x86 VM Compose 통합

기준일: 2026-11-05 (Asia/Seoul).

## 승인과 현재 상태

현재 운영 상태: 사용자 후속 요청으로 Compose를 내리고 자동 시작을 해제했다. VM은 RUNNING이며 Cloud SQL/ALB/이미지와 설정을 보존한다. 아래 구축/통신 검증은 중지 전 이력이며 최신 상태는 마지막 Operation 절을 따른다.

- 사용자 변경 요청: `상황이 바뀜.. VM 한대에 FE/BE docker-compose로 그냥 말아서 올려줘.`
- 변경 방향: 사용자가 직접 지정한 VM 1대와 FE/BE Docker Compose를 기준으로 한다. 이전 FE/BE 별도 VM 조건은 이번 변경 방향으로 대체한다.
- Inception 및 U1~U3 Design/Implementation Plan: 사용자의 `레스기릿`, `기릿` 지시로 승인 완료다.
- 완료: U1~U3 구현/배포/검증/Review를 마쳤다. 기존 FE VM 한 대의 Compose로 FE/BE와 DB Proxy를 운영하고, 기존 BE VM/디스크 제거와 최종 두 GCP root No changes를 확인했다. Git 게시 상태는 Git 이력을 따른다.
- 이전 2대 구성의 실제 배포와 검증은 [기존 기록](gcp-sample-app.md)을 따른다. 아래 Inception은 승인된 설계이며 현재 배포 상태는 최종 운영/검증 절을 기준으로 한다.

## Ideation

- 문제 정의: 작은 데모 앱의 FE/BE VM 2대를 운영하는 구성을 한 대의 Compose로 단순화한다.
- 사용자: Delta 팀 운영자와 기존 공개 데모 사용자.
- 성공 기준: x86 VM 1대에서 FE/BE 컨테이너가 healthy이고 공개 URL의 CRUD와 컨테이너/VM 재시작 후 Cloud SQL 데이터 보존을 확인한다.
- Scope: 기존 FE VM의 통합 호스트 재사용, x86 Docker/Compose 및 FE/BE 이미지, Cloud SQL 연결, 비밀값/IAM/네트워크 조정, Compose 자동 시작/복귀, 기존 BE VM 제거와 Terraform/문서 동기화.
- Non-goals: ALB/Cloud SQL 교체나 DB 재초기화, AWS 앱 변경, 신규 도메인/HTTPS, NAT/공개 VM 추가, 불필요한 빈 subnet/기존 VPN 정리, 고가용성 구성.

## Inception

### Functional Requirements

1. 기존 FE VM `delta-board-gcp-frontend` / `10.32.1.20`을 통합 호스트로 재사용한다. x86 e2-small과 사설 배치를 유지한다.
2. FE/BE를 각각 linux/amd64 이미지로 독립 빌드하고 같은 Compose 프로젝트에서 실행한다. FE는 기존 ALB의 3000 포트로 제공하고 BE는 Compose 내부 통신으로 연결한다.
3. 기존 Cloud SQL PostgreSQL/DB/앱 계정/데이터와 PSC를 사용한다. DB를 컨테이너로 옮기거나 DB 컨테이너를 추가하지 않는다.
4. Cloud SQL Auth Proxy의 TLS 연결을 새 호스트에서 제공한다. BE만 DB 비밀값을 받도록 하고 FE에는 DB secret을 전달하지 않는다.
5. Google API/GCS를 이용한 이미지/설치물 전달을 준비해 VM의 일반 인터넷/NAT 접근에 의존하지 않는다. Docker와 Compose의 x86 버전/해시를 고정한다.
6. 기존 native FE 서비스를 멈추고 Compose FE로 전환한다. Compose가 정상화된 뒤 기존 BE VM의 삭제 보호 해제와 삭제를 실제 Plan으로 검토/적용한다.
7. 재부팅 시 Docker/Compose가 자동 시작하고, 실패 시 이전 native FE/BE로 되돌릴 수 있게 이전 배포 정보와 산출물을 보존한다.

### Non-Functional Requirements

- 기존 ALB URL `http://34.64.134.28`, Cloud SQL/PSC, VPC/NCC/VPN과 저장 데이터를 보존한다. 이 요청으로 AWS 리소스를 배포하거나 삭제하지 않는다.
- 이전의 서로 다른 VM/서비스 계정에 의한 FE/BE 격리는 단일 호스트의 컨테이너 경계로 바뀐다. 같은 VM이므로 기존 VM 수준의 격리가 유지된다고 표시하지 않는다.
- DB 앱 secret은 host의 제한된 런타임 파일/Compose secret으로 BE에만 제공한다. 비밀번호를 이미지, Terraform state/metadata, Compose YAML과 명령 출력에 넣지 않는다.
- FE 컨테이너가 VM metadata의 서비스 계정 토큰이나 DB 연결을 우회 이용하지 않도록 컨테이너 출발지 기준 제한을 적용하고 실제 차단을 확인한다. Docker socket/host PID/privileged 모드를 앱에 주지 않는다.
- ALB는 FE만 공개한다. BE와 DB Proxy는 외부 포트를 publish하지 않고, Proxy의 DB 연결은 BE와 공유한 namespace의 loopback 등 제한된 경로로 제공한다.
- 기존 FE VM에는 Docker가 없고 iptables는 있다. Docker 설치와 기존 native systemd 서비스 정지는 명시적으로 관리하며, 재부팅 시 옛 서비스가 포트를 차지하지 않도록 한다.
- `docker compose config`와 inspect 출력에 secret 값이 포함될 수 있으므로 비밀값을 주입한 전체 설정/환경을 일반 출력에 남기지 않는다.
- 실제 Plan에서 기존 FE VM 교체가 나오면 metadata/startup 변경의 영향을 먼저 해결한다. 의도하지 않은 ALB/SQL/VPC/state 교체를 적용하지 않는다.

### Architecture

```text
Internet -> 기존 ALB :80
                -> x86 VM 1대 / 사설 10.32.1.20
                     Docker Compose
                       frontend :3000 -> backend :8080
                                             -> Cloud SQL Auth Proxy
                                                  -> 기존 PSC -> Cloud SQL
```

초기 호스트는 기존 e2-small 2GB로 제안한다. Compose의 실제 메모리/컨테이너 health를 확인하며 임의로 상위 사양으로 바꾸지 않는다. FE/BE 이미지를 별도로 식별하고 기존 source commit/dirty snapshot과 각 이미지 digest를 기록한다.

### Unit of Work, Design와 Implementation Plan

| Unit | Design | Implementation Plan | Test/Review 기준 |
| --- | --- | --- | --- |
| U1. 단일 호스트의 Compose 기반 | 기존 FE 호스트와 ALB를 이용하고 Compose/IAM/DB 경계를 구성 | GCP 배포용 Compose/호스트 설치/secret 조회 도구, 고정 x86 이미지와 offline 전달, DB Proxy와 최소 컨테이너 네트워크, systemd 자동 시작/복귀 준비 | Compose 정적 검사와 실제 컨테이너 health, FE metadata/DB 접근 거부, 앱 secret 미노출, amd64 실행과 기존 native 포트 충돌 없음 |
| U2. 교체와 BE VM 제거 | 정상 앱 전환 뒤 BE VM만 제거 | GCP root의 VM 생성 조건/출력/README와 정책 대상을 통합 호스트에 맞춤, 삭제 보호 단계 Plan, 기존 FE/ALB/SQL ID와 데이터 대조, BE VM 제거 | 실제 Plan은 의도한 VM/정책 변경만 포함. FE/ALB/SQL 교체0, 최종 VM1대, 이전 BE VM404 및 DB 데이터 보존 |
| U3. 실제 운영 검증과 문서 | 공개 앱과 DB 영속성, 한 대 구성의 재시작/복귀 확인 | 공개 CRUD, BE 컨테이너 재시작 후 보존, Compose/VM 재부팅 후 자동 복구, ALB health, 관련 최종 Plan과 실행 기록 갱신 | 실제 HTTP200/POST201/DELETE204, DB 메모 보존, FE/BE healthy/ALB HEALTHY, host x86/컨테이너 amd64, 최종 GCP Plan No changes |

### Acceptance Criteria

1. GCP 앱 VM은 최종 1대이며 FE/BE는 실제 Docker Compose 컨테이너로 실행한다.
2. 기존 ALB URL의 화면/API와 Cloud SQL 저장이 정상이다. 시험 메모를 만든 뒤 BE 컨테이너 및 VM 재시작 후 같은 데이터가 남는다.
3. FE/BE 컨테이너, Proxy와 Docker 서비스가 재부팅 후 정상화되고 이전 native FE와 포트가 충돌하지 않는다.
4. FE에는 DB secret이 없으며 metadata/DB 우회 접근은 실제 제한한다. 한 호스트로 바뀐 보안 경계를 문서에 반영한다.
5. 기존 SQL/PSC/ALB 및 네트워크 ID/데이터를 보존하고 제거한 BE VM이 실제 API404다.
6. 실행한 테스트/Plan/Apply/배포/게시를 구분하고 변경 모듈 README의 전체 입력/출력 표 및 관련 Runbook을 갱신한다.

## Operation

- Deployment: 기존 BE를 유지한 상태에서 호스트/이미지/DB 연결을 준비하고 FE 전환을 검증한다. 정상 동작을 확인한 뒤 기존 BE VM을 제거한다. 같은 호스트의 FE 포트 전환 때 짧은 중단이 발생할 수 있다.
- Observability: `docker compose ps`/서비스별 health와 비밀값 없는 로그, ALB health와 Cloud SQL 상태를 확인한다.
- Rollback: BE VM 제거 전에는 기존 native FE/BE 릴리스로 복귀한다. 제거 뒤에는 이전 입력/코드의 새 Plan으로 BE를 재생성하는 복구가 필요하다. DB를 삭제하거나 재초기화하지 않는다.
- Runbook: [Compose 운영 문서](../../deploy/workload-gcp/README.md#단일-vm-compose)에 순서가 보장된 전체 재시작/재배포, secret 갱신, Docker/VM 재부팅과 검증된 이전 이미지 복귀 절차를 기록했다.

## 확인 기록

| 날짜 | 명령/확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-05 KST | docs/README와 GCP AI-DLC, AGENTS.md 읽기 | 기존 2대 배포 완료와 단계별 승인 규칙 확인 |
| 2026-11-05 KST | 앱 compose.yaml과 FE/BE Dockerfile 읽기 | FE/BE 개별 이미지/health 구성 존재. 현재 Compose에는 PostgreSQL 환경 연결이 없어 GCP용 설정 필요 |
| 2026-11-05 KST | 기존 배포 도구의 ADC/IAP로 FE의 uname, shutil.which와 /proc/meminfo 조회 | x86_64, Docker 미설치, iptables 존재, MemTotal 2,007,080 KiB. 실행 서비스/자원 변경 없음 |

사용자의 상세 교체안 승인 후 Construction에 진입했다. 실제 실행 결과는 명령과 함께 추가한다.

## 2026-11-05 Construction 진행

- 상세 교체안 승인: 사용자의 `레스기릿`, `기릿`. 구현과 실제 전환/BE 제거까지 승인 범위다.
- `compose-access` 실제 Plan/Apply: 앱 secret accessor와 조건부 Cloud SQL client IAM2개 추가, VM/SQL/ALB 변경0.
- `compose-db-egress` 실제 Plan/Apply: database_allow의 통합 호스트 대상 추가1개, 다른 네트워크 변경0.
- Terraform workload validate/mock7개, network 격리 검증 mock14개/Python10개, 배포 Python5개 통과. 최초 네트워크 직접 test는 실제 tfvars의 project number와 mock 값 불일치로 실패했고, 기존 isolated validation script로 재실행해 통과했다.
- Docker29.9.3/Compose5.7.0 공식 x86 설치와 offline image import 준비를 완료했다.
- 최초 전환 실패: umask077이 rootfs parent directory에 남아 nonroot Node가 Permission denied(exit126)로 실행되지 않았다. systemctl enable --now의 성공 응답만으로 완료를 판단한 오류도 확인했다. 실제 컨테이너/API 검증은 실패로 기록한다. native FE를 복구했고 이전 BE/DB는 유지했다.
- 수정: image parent directory755, app UID10001, host script hash를 image tag에 포함해 같은 app tar의 잘못된 이미지를 재사용하지 않는다. 명시적 systemctl restart/is-active와 compose --wait를 실제 기동 gate로 추가하고 BE가 loopback proxy를 기다리게 한다. 수정한 새 bundle로 재검증한다.

위 항목은 완료한 단계만 기록하며 최종 Compose 전환/BE 제거와 재부팅 성공은 아직 기록하지 않는다.

### 수정 후 실제 전환

- 최종 offline bundle SHA-256: `475c598798774fae8e93833d45cb3c4f6e2cd3d608977dc70df9bc4d60aef9dd`. host recipe SHA-256: `5065f42edc26712546f7818a9e5b2f495ec61bd9a57bd4bf9f6cb2e080c440fb`.
- `python3 deploy/workload-gcp/compose.py prepare`: Docker29.9.3/Compose5.7.0과 FE/BE nonroot Node의 실제 `process.arch=x64`, Proxy `--version` 실행 성공 후 준비 완료.
- `python3 deploy/workload-gcp/compose.py activate`: 명시적 systemctl restart/is-active와 Compose --wait 통과. native FE disabled, Compose active.
- `verify_host.py cutover`: FE/BE running/healthy, Proxy running, 세 image Architecture=amd64. FE만10.32.1.20:3000 publish, BE/Proxy host publish0. FE secret 없음, metadata/PSC3307/PSC5432/host22 접근 차단을 실제 확인. Docker/Compose/방화벽 unit active.
- 공개 `verify_http.py after-cutover`: HTTP200, storage=postgres, native 배포에서 만든 같은 메모 보존. `cloud_check.py cutover`: 기존 FE/ALB/PSC ID 및 SQL createTime 보존, ALB HEALTHY/SQL RUNNABLE.
- 당시 메모리: VM 총1960MiB/사용595MiB/available1364MiB, FE27.34MiB/BE19.21MiB/Proxy9.164MiB. e2-small 증설 없이 진행한다.

| 이미지 | 실제 image ID |
| --- | --- |
| frontend | `sha256:37ba5d8f0555d25d54b0c62e73535def446a64cfa45012f9b447432baf485069` |
| backend | `sha256:35b8f4a244fb95a2195b218c73f9ec7eedf70f42812fe66b59fee0ed9a241827` |
| proxy | `sha256:7341d40a0c8b41f92c2a866ad48f876124d8701b5ad3a612c8782e3329869e78` |

### 컨테이너 재시작 검증

- BE 단독 `docker compose restart backend`는 namespace를 공유한 Proxy를 이전 공간에 남겨 일시적인 API502를 일으켰다. Proxy를 재시작하여 복구했고 BE healthy/같은 메모 보존을 확인했다.
- `restart backend proxy`도 Proxy가 내려가는 BE namespace에 동시에 합류하려 해 실패했다. 운영 Runbook을 실제 stop/start 순서를 보장하는 `systemctl restart delta-compose`와 `compose.py restart`로 수정했다. 이 두 실패를 PASS로 표시하지 않는다. 서비스 전체 `systemctl restart delta-compose`는 두 컨테이너를 포함한 Compose 전체를 stop/start한다.
- BE 삭제 보호 실제 Plan은 기존 BE의 `deletion_protection` 한 필드 update1개뿐이다. FE/SQL 보호와 ALB/PSC는 변경0이다. Apply 전 재부팅 검증을 수행한다.

- `restart_stack.py`의 `systemctl restart delta-compose` 실제 실행은 전체 stop/start 후 FE/BE healthy와 Proxy running을 확인했다. 운영에서 이 순서를 사용하며 BE/Proxy 동시 restart 실패와 구분한다.

### 첫 VM 재부팅에서 확인한 시작 순서 문제

- 실제 boot ID는 바뀌었지만 Compose 자동 기동은 실패했다. FE/BE/Proxy가 stopped이고 Docker/방화벽만 active인 것을 확인했다. 이를 재부팅 PASS로 표시하지 않는다.
- Compute serial 로그: `google-startup-scripts.service: Found ordering cycle on multi-user.target/start`, `Job delta-compose.service/start deleted to break ordering cycle`.
- 원인: multi-user.target으로 시작하는 Compose unit에 Google 시작 스크립트 이후 조건을 추가하여, multi-user.target 다음에 실행되는 Google 스크립트와 순환 의존성이 생겼다.
- 수정: 불필요한 `After=google-startup-scripts.service`를 제거한다. native unit/Compose unit은 별도이며 native가 disabled이고 marker 조건도 있어 이 순서는 필요 없다. 수정 source/실제 unit을 동기화하고 두 번째 실제 재부팅으로 검증한다. 첫 재부팅 뒤 수동 복구와 두 번째 자동 복구를 구분한다.

- 시작 순서 수정의 실제 host source SHA-256 `fc5b29122520eafa22b7ad989e5ce6912e7daa46398f58450dca3c53a64f4955`, unit SHA-256 `1571ea8298b968103c21b05d011a0c3a5cfb17fef727e02ea99d9f73fa955b4d`. 기존 검증 이미지는 그대로 사용하고 host/unit만 동기화했다.
- 다음 설치에도 같은 수정을 사용하도록 최종 bundle `fb9f3d8a566a85571438d85f24c1b9da2e99892d64615d0beb6e24c2b9f5d52a`를 별도 게시한다. 앞서 기록한475c bundle은 첫 이미지 생성/전환 때의 역사적 산출물이다.

### 두 번째 VM 재부팅 검증 완료

- `reboot_vm.py` 요청 전 boot ID `9d922909-99b1-4d98-b247-4bf4adc611a4`, 재부팅 후 `eddf6421-2dd2-417d-afe4-f9fd4e6642d3`로 바뀌었다. 두 번째 재부팅 뒤 별도 수동 start 없이 Compose active/native inactive를 확인했다.
- `wait_boot.py`, `verify_http.py after-vm-reboot`, `verify_host.py reboot`: 자동 기동, FE/BE healthy/Proxy running, 공개 HTTP200/PostgreSQL 및 동일 시험 메모 보존, FE metadata/PSC3307/PSC5432/host22 차단과 secret 부재를 모두 확인했다. IAP SSH와 ALB health가 재부팅 동안 잠시 미응답/503이었고 이후 정상화됐다.
- 재부팅 직후 VM 메모리 총1960MiB/사용504MiB/available1455MiB. FE68.41MiB/BE70.59MiB/Proxy31.36MiB이며 OOM 없음.
- 이 검증 후 U2의 기존 BE 삭제 보호 해제 Apply와 BE 제거 Plan/Apply로 진행한다.

## 최종 운영 상태와 검증 (2026-11-05 KST)

- 프로젝트 `term2-delta`, `asia-northeast3-a`, VM `delta-board-gcp-frontend` 한 대다. instance ID `5224499913819284415`, private IP `10.32.1.20`, x86 e2-small/shared vCPU2/메모리2GB/공인 IP 없음/삭제 보호 true다. 기존 FE VM 이름/ID를 유지하며 이 VM이 FE/BE를 함께 실행한다.
- 공개 URL: `http://34.64.134.28`. ALB ID `4852268953839461236`와 PSC ID `5891712603532682146`를 보존했다. ALB backend HEALTHY, Cloud SQL `delta-board-gcp-db` RUNNABLE, SQL createTime `2026-11-05T10:37:28.319Z` 동일이다.
- FE/BE Compose healthy, Proxy running, host x86_64/image amd64. 서비스 전체 재시작과 수정 후 실제 VM 재부팅의 자동 기동/데이터 보존을 확인했다. FE metadata/DB/host 차단과 secret 부재가 재부팅 뒤에도 유지된다.
- 기존 BE VM `delta-board-gcp-backend`와 boot disk는 API404다. 이전 시험 VM `delta-lab-server`/NLB `delta-lab-demo`도 API404를 다시 확인했다. unrelated `delta-lab-staging-db` RUNNABLE을 보존했다.
- BE 빈 subnet과 복귀용 서비스 계정/릴리스는 보존했다. VPC/NCC/VPN을 교체하지 않았고 BGP4개 UP이다. 이번 Compose 작업에서 AWS 리소스를 변경하지 않았다.
- 배포 도구/템플릿은 `deploy/workload-gcp/`, 인프라는 `terraform/workload-gcp/`, 네트워크 소유권은 `terraform/network-security-gcp/`다. 실제 host Compose 파일은 `/etc/delta-compose/compose.json`, 운영 unit은 `delta-compose`다.
- 재시작은 [Runbook](../../deploy/workload-gcp/README.md#단일-vm-compose)의 `python3 deploy/workload-gcp/compose.py restart` 또는 host `systemctl restart delta-compose`를 사용한다. BE/Proxy namespace 때문에 단독/동시 Docker restart 명령을 운영 절차로 사용하지 않는다.

### 실제 실행 기록

| 날짜 | 명령/단계 | 실제 결과 |
| --- | --- | --- |
| 2026-11-05 KST | `ops.py compose-access terraform/workload-gcp plan/apply` | IAM2개 추가, 다른 인프라 변경/삭제0, Apply 종료0 |
| 2026-11-05 KST | `ops.py compose-db-egress terraform/network-security-gcp plan/apply` | DB allow 대상 추가1개, Apply 종료0 |
| 2026-11-05 KST | `compose.py prepare/activate`, `verify_host.py cutover` | 초기 실패 수정 후 nonroot x64 실행/실제 컨테이너 health/FE 차단 검증 성공 |
| 2026-11-05 KST | `restart_stack.py`, `verify_http.py after-stack-restart` | 전체 Compose stop/start, FE/BE healthy/Proxy running, 같은 DB 메모 보존 |
| 2026-11-05 KST | 두 번째 `reboot_vm.py`, `wait_boot.py`, `verify_host.py reboot` | 수정 후 실제 boot ID 변경, 수동 start 없이 자동 복구, FE 경계 유지 |
| 2026-11-05 KST | `ops.py be-unprotect terraform/workload-gcp plan/apply` | 기존 BE의 삭제 보호 필드만 update1개, Apply 종료0 |
| 2026-11-05 KST | `ops.py be-retire terraform/workload-gcp plan/apply` | 기존 BE VM1개 삭제, 생성0/FE/ALB/SQL 변경0, Apply 종료0 |
| 2026-11-05 KST | `cloud_check.py final` | 앱 VM1대, BE VM/디스크404, 기존 FE/ALB/PSC/SQL 보존, ALB HEALTHY/SQL RUNNABLE/BGP4개 UP |
| 2026-11-05 KST | `final_crud.py`, `verify_http.py cleanup` | 단일 VM에서 POST201/GET200/DELETE204, 재부팅 전 메모 보존 확인 후 시험 메모 모두 삭제 |
| 2026-11-05 KST | `terraform -chdir=terraform/workload-gcp test` | mock7개 통과. 최종 single_vm_compose 입력으로 실행 |
| 2026-11-05 KST | `bash terraform/network-security-gcp/scripts/validate.sh` | 격리된 mock14개/Python10개 통과 |
| 2026-11-05 KST | `python3 -m unittest discover -s deploy/workload-gcp -p 'test_*.py' -v` | Python5개 통과 |
| 2026-11-05 KST | `ops.py final-workload terraform/workload-gcp plan` | 실제 No changes/종료0 |
| 2026-11-05 KST | `ops.py final-network terraform/network-security-gcp plan` | 실제 No changes/종료0 |

저장 Plan SHA-256: BE 보호 해제 `16647c1f0682a756c60227208687ca9c762be2491e7c25db4d868761ceb133a3`, BE 제거 `a7394bb72f185ca15f6e7749e61bcb9e6008cbe74486f4bd0a3414d162288383`. 실제 변경 목록과 대상 instance ID를 검토한 뒤 해당 저장 Plan을 Apply했다.

민감 원본/state/로그는 ignored `.validation/gcp-compose-20261105/`의 제한된 권한으로 보관한다. 공개 문서/코드에 password를 넣지 않는다. 배포 당시 게시 commit/원격 CI 확인은 수행하지 않았다. 현재 Git 게시 상태는 Git 이력을 따른다.

## 후속 FE -> BE -> DB 통신 실측

- 실행 시각: 2026-11-05 21:54:34 KST. 사용자 `fe -> be -> db 통신 테스트 ㄱㄱ` 요청으로 실제 운영 URL에서 시험했다.
- 실행 명령: `python3 .validation/gcp-compose-20261105/verify_chain.py`. 종료0이며 시험 전용 메모만 생성/삭제했다.
- 경로: 공개 ALB `http://34.64.134.28` -> FE3000 -> Compose BE `172.28.91.3:8080` -> 같은 namespace의 Proxy `127.0.0.1:5432` -> 기존 Cloud SQL PostgreSQL. FE/BE healthy와 실제 Proxy namespace를 조회해 연결 대상을 대조했다.
- HTTP: FE 화면200/health200, BE의 postgres health200, 메모 생성201/재조회200/삭제204/삭제 후 재조회200이다.
- DB 직접 검증: BE 컨테이너에서 기존 앱 계정 `delta_app`으로 `BEGIN READ ONLY` 후 `current_database()`와 `current_user`를 확인했다. `delta_notes.notes WHERE id=$1`의 생성 후1행과 title/content 일치를 확인했고 API 삭제 후 같은 ID가0행임을 확인했다. 관리자 계정과 비밀번호 출력은 사용하지 않았다.
- 로그 대조: `chain-33742d6061e6486c9a328608a626e537` 접두사의 API 요청5개가 FE/BE 두 실제 access log에서 같은 requestId/method/status로 일치한다. 단순 포트 연결이나 health 응답만으로 성공을 판단하지 않았다.
- 결과: FE -> BE -> DB 실제 생성/조회/삭제 통신 성공. 시험 메모 정리 완료. 서버 재시작/인프라 변경 없이 수행했다.
- 상세 근거는 ignored `.validation/gcp-compose-20261105/chain-33742d6061e6486c9a328608a626e537-result.json`에 보관한다.

## 후속 Operation: Compose만 내리고 VM 보존

- 사용자 요청: `그럼 이제 compose만 내려 인스턴스는 유지하고`. 운영 앱 중지/VM 보존 지시로 실행했으며 신규 인프라/앱 구현은 하지 않았다.
- 실제 확인 시각: 2026-11-05 21:57:44 KST. 명령: `python3 .validation/gcp-compose-20261105/stop_compose_only.py`. 종료0.
- host 실행: `systemctl disable delta-compose`, `systemctl stop delta-compose`, `docker compose -p delta-sample -f /etc/delta-compose/compose.json down`. `--volumes`/`--rmi` 없이 수행했다.
- 결과: FE/BE/Proxy 컨테이너3개와 Compose 전용 network 제거, 프로젝트 컨테이너/네트워크 잔여0개. Compose inactive/disabled, native FE inactive, Docker active다. 3000/8080/5432 host listener 없음도 확인했다.
- VM `delta-board-gcp-frontend`: RUNNING, ID `5224499913819284415`, private IP `10.32.1.20`과 boot disk ID/URL 동일. VM 중지/삭제/교체를 하지 않았다.
- Cloud SQL `delta-board-gcp-db`: RUNNABLE, createTime 동일. ALB ID `4852268953839461236`/IP `34.64.134.28`도 동일하다. 앱이 내려간 공개 API는 HTTP503이다.
- FE/BE/Proxy의 검증된 image ID3개와 Compose/DB secret 설정 파일을 보존했다. native fallback 방지 marker도 유지하여 재부팅 시 native 앱이 다시 실행되지 않게 한다. Compose unit이 disabled이고 컨테이너도 제거하여 재부팅 자동 기동을 중단했다.
- Terraform 코드/Plan/Apply는 이번 중지 작업에서 실행하지 않았다. Git 게시 상태는 Git 이력을 따른다. 이전 No changes와 앱 정상 통신 기록은 당시 검증 이력이다.
- 재개는 VM에서 `sudo systemctl enable --now delta-compose`로 수행한다. 이 unit은 secret/방화벽을 확인하고 보존한 이미지로 FE/BE/Proxy를 의존성 순서에 따라 재생성한다. 재개 지시 전에는 실행하지 않는다.
- 증거: ignored `.validation/gcp-compose-20261105/compose-stop/before.json`, `host.json`, `after.json`, `result.json`.
