# Sample App의 Compose 및 개별 EC2 배포

## 단일 EC2 Docker Compose

`application.deployment_mode="compose"`는 기존 frontend EC2 한 대에 FE/BE 컨테이너를 실행한다. ALB와 RDS는 유지한다. 실제 승인/진행 상태는 [Compose 전환 기록](../../docs/ai-dlc/aws-compose-consolidation.md)을 따른다.

기존 두 EC2에서 이전할 때 먼저 `retain_legacy_backend=true`로 Plan/Apply해 기존 BE를 보존하면서 FE 호스트의 앱 secret/이미지/로그 권한과 RDS SG 경로를 준비한다. EC2 user_data는 변경하지 않는다. 아래 배포와 공개 CRUD/재시작 검증이 끝난 뒤 `retain_legacy_backend=false`로 별도 Plan/Apply해 기존 BE EC2/전용 역할/SG를 제거한다. 신규 배포는 false를 사용한다. 새 RDS를 처음 만드는 경우에만 배포 전 `release.py init-db --sample-root ../delta-sample-app`을 실행한다. 이 명령은 Compose에서도 운영자 인증과 frontend 호스트의 SSH 터널을 사용한다. 기존 RDS를 재사용하는 이번 전환에서는 재실행하지 않는다.

```sh
python3 deploy/workload/compose.py deploy --sample-root ../delta-sample-app
python3 deploy/workload/compose.py status
python3 deploy/workload/compose.py restart --service backend
python3 deploy/workload/compose.py restart --service frontend
python3 deploy/workload/compose.py rollback
```

소스는 clean Git 커밋만 허용한다. 두 서비스의 Dockerfile/패키지/소스 경로만 검증한 context와 운영 도구를 `releases/compose/{commit}/{sha256}.tar.gz`에 저장한다. `.env`, node_modules와 비밀값을 context에 포함하지 않는다. 호스트는 SHA-256/압축 경로를 검사하고 각 이미지를 순서대로 빌드한다. Node 24.19.0 Alpine의 실제 digest를 고정하며 최종 amd64 image ID를 Compose에 사용한다. Docker는 AL2023 `25.0.18-1.amzn2023.0.2`, Compose는 공식 x86 바이너리 `v5.7.0`과 고정 SHA-256으로 설치한다. 설치/빌드/배포는 EIC SSH와 기존 NAT를 사용한다.

호스트는 기존 앱 secret을 조회해 `/run/delta-compose/db-password`에 UID1000/0400으로 저장한다. 해당 파일을 BE에만 mount하고 시작 wrapper가 PGPASSWORD를 앱 프로세스에 전달한다. Docker inspect의 Config.Env/Compose 환경/이미지에는 비밀번호를 저장하지 않는다. RDS CA/호스트 이름 검증을 유지한다. 호스트 IAM 역할에는 마스터 secret 조회를 허용하지 않는다.

FE 3000만 publish하며 BE 8080은 내부에서 사용한다. `172.30.240.0/24` bridge와 실제 VPC/호스트 경로가 겹치면 배포를 중단한다. FE는 BE8080과 응답 트래픽만 허용하고 컨테이너 metadata/호스트 신규 접속을 차단한다. 앱은 비루트, read-only rootfs, cap_drop ALL로 실행하며 Docker socket/host network를 사용하지 않는다. 두 역할별 기존 CloudWatch 그룹에 Docker daemon이 로그를 전송한다.

`delta-compose-prepare.service`가 비밀번호/방화벽을 준비한 뒤 Docker와 `delta-compose.service`가 시작된다. native `delta-sample.service`는 전환 때 비활성화한다. EC2 부팅/전체 secret 갱신에는 `sudo systemctl restart delta-compose`를 사용하며 새 파일을 mount하도록 컨테이너를 재생성한다. 개별 restart는 기존 비밀번호를 그대로 쓴다.

호스트의 `/opt/delta-compose/current`, `previous`와 각 `releases/{sha256}/release.json`에 이미지 ID/소스/설치 버전을 기록한다. 설치 실패 시 이전 Compose 포인터로 복귀하며, 최초 전환 실패는 native FE를 다시 시작한다. 처음 성공한 Compose에는 직전 Compose 릴리스가 없어 rollback이 거부된다. 기존 BE 제거 이후 두 EC2 구조로 복구하려면 이전 입력/코드의 Plan으로 BE 재생성이 필요하다. DB를 재초기화하지 않는다.

상태 확인은 위 status, `systemctl status delta-compose docker delta-compose-prepare`, 공개 `/api/health`와 ALB target health를 사용한다. 전체 `docker inspect`, Compose 환경 출력이나 비밀번호 파일은 일반 로그에 출력하지 않는다. 활성/직전 이미지를 prune하지 않는다.

### EC2를 유지한 채 Compose만 내리기

호스트에서 메인 앱 자동 시작을 해제하고 현재 프로젝트만 내린다. EC2/RDS/ALB/이미지/릴리스는 유지한다.

```sh
sudo systemctl disable --now delta-compose
sudo docker compose --project-name delta-notes \
  --env-file /opt/delta-compose/current/.env \
  --file /opt/delta-compose/current/compose.yaml down
```

`docker ps -a --filter label=com.docker.compose.project=delta-notes`가 비어 있고 `delta-compose`가 inactive/disabled인지 확인한다. Docker daemon과 기존 native 서비스 상태는 별도로 확인한다. 앱이 내려가면 공개 URL은 더 이상 정상 서비스하지 않으며, 실제 중지 때 ALB는502를 반환했다. `-v`, 이미지 삭제 옵션, prune이나 Terraform 앱 비활성화는 이 절차에 사용하지 않는다.

재개할 때는 `sudo systemctl enable --now delta-compose`를 실행하고 공개 `/api/health`와 두 컨테이너 health를 확인한다. 현재 이미지/설정으로 secret/방화벽을 준비하고 네트워크/컨테이너를 재생성한다. 현재 중지/재개 실행 여부는 [실제 운영 기록](../../docs/ai-dlc/aws-compose-consolidation.md)을 따른다.

## 기존 native 모드

아래 `release.py` 설치/재시작/상태/rollback 절차는 `deployment_mode="native"`에서만 사용한다. Compose 모드에서는 잘못된 호스트/서비스 제어를 막기 위해 거부한다. 운영자 DB 초기화는 두 모드를 지원하고 service_hosts의 BE 배치를 따라 SSH 터널 호스트를 선택한다.

`terraform/workload`의 선택적 앱 모드는 공개 ALB, 사설 x86 FE/BE EC2 각 1대와 사설 RDS PostgreSQL을 준비한다. 이 디렉터리는 FE/BE 산출물 생성, 비공개 S3 게시, EIC를 통한 설치, DB 초기화와 rollback을 담당한다. 앱 소스는 별도 `delta-sample-app` 저장소에서 관리한다.

현재 승인/구현/테스트/Plan/Apply/배포 상태는 [AI-DLC](../../docs/ai-dlc/aws-sample-app.md)를 따른다. 아래 명령은 운영 절차이며 실행 완료 기록이 아니다.

## 준비

- 로컬: Python 3, Node.js 24/npm, AWS CLI v2, Terraform 1.16.4, OpenSSH와 curl이 필요하다.
- AWS 프로필 기본값은 `sbh-delta-workload`다. 모든 외부 작업 전 STS 계정을 Terraform 인벤토리의 account_id와 대조한다.
- 실제 tfvars/backend/Plan/산출물과 known_hosts는 ignored 경로에 보관한다. DB 마스터/앱 비밀번호는 tfvars와 사용자 데이터에 넣지 않는다.
- 실제 Plan을 검토하고 승인된 범위로 적용한 뒤 아래 배포를 실행한다. `application` 출력이 null이면 아직 앱을 활성화/적용하지 않은 상태다.

```sh
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload output -json application
npm ci --prefix ../delta-sample-app/backend --ignore-scripts
```

## FE와 BE를 독립 빌드

```sh
python3 deploy/workload/release.py build --sample-root ../delta-sample-app --service frontend
python3 deploy/workload/release.py build --sample-root ../delta-sample-app --service backend
```

각 서비스의 자체 `npm run build`만 실행하고 `dist/`를 SHA-256으로 식별한 tar.gz와 manifest로 만든다. 기본 출력은 `.validation/sample-releases/`다. FE/BE manifest에는 소스 커밋, 서비스, 산출물 해시/경로가 있다. `.env`, 숨김 파일과 심볼릭 링크를 산출물에 넣지 않는다.

기본은 clean 소스만 허용한다. 검토 중인 로컬 스냅샷에는 `build --allow-dirty`를 명시할 수 있고 manifest의 `source_dirty=true`로 기록한다. 해당 manifest의 소스 커밋은 기준 커밋이며 미커밋 변경의 동일성을 주장하지 않는다. 설치도 별도 `--allow-dirty`가 필요하다. 산출물의 실제 내용은 전체 SHA-256으로 식별한다.

## S3 게시

```sh
python3 deploy/workload/release.py publish --manifest .validation/sample-releases/backend.json
python3 deploy/workload/release.py publish --manifest .validation/sample-releases/frontend.json
```

전용 버킷의 `releases/{service}/{commit}/{sha256}.tar.gz`에만 게시한다. EC2 역할은 자기 서비스 경로의 GetObject만 허용한다. 암호화/버전 관리/공개 차단과 TLS 전용 버킷 정책을 적용한다.

## DB 초기화

```sh
python3 deploy/workload/release.py init-db --sample-root ../delta-sample-app
```

운영자 프로필로 RDS 마스터 secret을 읽고 BE의 EIC SSH 터널을 통해 `delta_notes` DB에 접속한다. 터널의 연결 주소는 loopback이어도 실제 RDS endpoint 이름과 CA를 검증한다. 스키마와 최소 권한 앱 역할을 준비하고 별도 앱 secret에 username/password를 저장한다.

초기화 도구는 비밀번호를 표준 입력/프로세스 환경과 메모리로만 전달한다. 명령 인자, 로그와 Terraform state에 기록하지 않는다. 재실행 시 기존 앱 secret의 비밀번호를 재사용하고 기존 메모를 보존한다. 앱 EC2에는 마스터 secret 읽기 권한이 없고, BE만 앱 secret을 읽는다.

## BE와 FE 설치

```sh
python3 deploy/workload/release.py install --manifest .validation/sample-releases/backend.json
python3 deploy/workload/release.py install --manifest .validation/sample-releases/frontend.json
python3 deploy/workload/release.py check --service backend
python3 deploy/workload/release.py check --service frontend
```

BE를 먼저 설치한 후 FE를 설치한다. FE의 내부 API_HOST는 실제 BE private IP로 주입하며 브라우저에는 전달하지 않는다. EIC는 일회성 SSH 공개 키와 IAM 인증을 사용한다. instance ID별 SSH host key는 `.validation/workload-known-hosts`에 보존하고 임시 개인 키는 세션 종료 시 삭제한다.

EC2는 cloud-init 완료 후 자체 설치 도구로 S3 객체의 해시, 서비스/커밋/경로, 압축 내부 파일을 검증한다. BE는 lockfile로 production 의존성을 설치한다. 새 release 디렉터리를 완성한 뒤 current 링크를 교체하고 systemd를 재시작한다. 역할별 health의 버전이 일치하지 않으면 이전 release로 복귀한다.

DB/AMI/설치 도구 변경 없이 앱 버전만 바꾸면 Terraform Apply 없이 위 개별 산출물로 배포한다. 앱 실행 계정은 소스/런타임을 수정하지 못하고 로그 경로만 쓴다. 기본 공개 접속은 ALB DNS의 HTTP이며 HTTPS 사용 시 ACM 인증서와 해당 hostname을 함께 지정한다.

## 검증과 관측

1. `application.url`에서 화면과 `/api/health`를 확인한다. BE 상태의 storage는 postgres여야 한다.
2. 메모를 생성하고 페이지를 다시 열어 조회한다. BE를 실제 재시작한 뒤 같은 메모가 남는지 확인하고 테스트 메모를 삭제한다.
3. AWS API에서 EC2 공인 IP 부재, x86 AMI/유형, RDS PubliclyAccessible=false, ALB target health, SG/IAM/RT와 기존 GCP route를 확인한다.
4. 역할별 CloudWatch 로그와 systemd 상태를 확인한다. 로그는 14일 보관하고 비밀번호/DB 연결 문자열을 출력하지 않는다.
5. 실제 적용과 앱 검증 후 최종 Terraform Plan을 다시 확인하고 AI-DLC에 날짜/명령/결과를 남긴다.

```sh
python3 deploy/workload/release.py restart --service backend
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload plan -input=false -detailed-exitcode
```

## 개별 rollback

```sh
python3 deploy/workload/release.py rollback --service backend
python3 deploy/workload/release.py rollback --service frontend
```

직전 release로 current 링크를 바꿔 재시작하고 health를 확인한다. 첫 설치에는 이전 release가 없다. 실패한 첫 설치는 서비스를 중지하며 성공한 배포로 기록하지 않는다. rollback은 DB/네트워크를 삭제하지 않는다. 스키마 파괴적 변경/DB 복원/전체 자원 제거는 별도로 검토한다. `application_enabled=false`는 전체 앱 삭제 계획이므로 앱 버전 rollback에 사용하지 않는다.

## 실제 실행하는 로컬 검사

```sh
bash terraform/workload/scripts/validate.sh
python3 -m unittest discover -s deploy/workload/tests -v
```

Terraform 스크립트는 격리 init/validate와 mock Plan만 실행한다. 실제 AWS Plan/Apply를 실행하지 않는다. Python 검사는 manifest/압축 경로와 파일 형식, 현재 release 교체 실패/복귀, 첫 설치 실패를 확인한다. 실제 EC2 부팅, systemd/CloudWatch 수신, S3 게시와 AWS HTTP 결과는 별도로 기록한다.
