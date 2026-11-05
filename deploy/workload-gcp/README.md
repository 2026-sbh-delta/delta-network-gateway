# GCP x86 Sample App 배포

FE/BE는 각각 독립 빌드하며 Node.js Linux x64와 Cloud SQL Proxy Linux AMD64의 공식 SHA-256을 확인해 self-contained tar에 포함한다. 소스 commit, 변경된 로컬 snapshot 여부, 산출물 해시/아키텍처/런타임 버전을 manifest로 기록한다. VM에서는 npm/apt/GitHub에 접근하지 않고 Private Google Access와 자기 서비스 계정으로 GCS 산출물을 내려받는다.

현재 운영은 아래 [단일 VM Compose](#단일-vm-compose)를 따른다. `release.py`의 native 설치/서비스 명령은 이전 배포와 복귀용이다.

## 선행 조건

- 승인된 `terraform/network-security-gcp`와 `terraform/workload-gcp` 적용 결과.
- S3 backend 접근용 AWS 세션과 GCP ADC. 배포 도구는 `gcloud auth application-default print-access-token`을 사용한다.
- 로컬 Node.js24/npm, Python3, curl, ssh-keygen과 gcloud.
- `terraform output -json application`의 비밀값 없는 inventory를 `.validation/gcp-sample-app-20261105/application.json`에 저장한다. 다른 위치라면 `--inventory`를 action 앞에 지정한다.
- SQL의 PSC endpoint ACCEPTED, private DNS와 API 전용 route, OS Login/IAP 권한.

## 이전 native 배포

```sh
python3 deploy/workload-gcp/release.py build --sample-root ../delta-sample-app
python3 deploy/workload-gcp/release.py publish --manifest .validation/gcp-sample-app-20261105/releases/backend.json
python3 deploy/workload-gcp/release.py publish --manifest .validation/gcp-sample-app-20261105/releases/frontend.json
python3 deploy/workload-gcp/release.py install --manifest .validation/gcp-sample-app-20261105/releases/backend.json --prepare
python3 deploy/workload-gcp/release.py init-db
python3 deploy/workload-gcp/release.py activate --service backend
python3 deploy/workload-gcp/release.py install --manifest .validation/gcp-sample-app-20261105/releases/frontend.json
```

검토한 로컬 미커밋 변경을 시험 배포할 때만 build의 `--allow-dirty`를 지정한다. 이 경우 HTTP 버전에도 `snapshot`과 산출물 해시를 표시하며 게시된 commit으로 오해하지 않도록 한다. 산출물은 hash 기반 경로와 GCS generation 조건으로 게시하고 덮어쓰지 않는다.

`--prepare`는 backend 산출물과 로컬 Proxy만 준비한다. `init-db`는 운영자 ADC로 관리자 비밀번호를 설정/보관하고 IAP SSH의 암호화된 stdin으로 초기화한다. schema와 제한된 `delta_app` 계정을 만들고 기존 메모를 보존한다. BE VM에는 관리자 secret 권한이 없으며 앱용 secret 하나만 읽는다. 비밀번호는 CLI 인자, Terraform, metadata 또는 출력에 넣지 않는다.

## 확인과 복귀

```sh
python3 deploy/workload-gcp/release.py check --service backend
python3 deploy/workload-gcp/release.py check --service frontend
python3 deploy/workload-gcp/release.py restart --service backend
python3 deploy/workload-gcp/release.py rollback --service backend
```

rollback은 해당 VM의 previous 산출물을 사용하며 DB를 삭제하거나 되돌리지 않는다. 최초 배포에는 previous가 없다. 기존 NLB/시험 VM의 인프라 복구는 보호된 이전 코드/입력의 새 Plan이 필요하고 같은 공인 IP 복원을 보장하지 않는다.

공개 URL의 화면/health/CRUD, BE 재시작 후 같은 메모 조회, FE의 PSC 3307/5432 차단과 BE의 정상 연결, 실제 `uname -m=x86_64`/Node `process.arch=x64`, ALB backend health/SQL 상태를 확인한다. BGP UP만으로 TGW 사설 HTTP 완료를 기록하지 않는다. AWS 실행 VM이 없는 경우 실제 요청을 보내지 않은 방향은 미검증으로 남긴다.

## 운영

- `delta-sample`, BE의 `delta-sql-proxy` systemd 상태와 journal을 확인한다. 원래 요청 본문이나 비밀번호를 진단 로그에 추가하지 않는다.
- Proxy는 loopback5432에서 수신하고 PSC3307로 TLS 연결한다. 앱의 `PGSSLMODE=disable`은 loopback 구간에만 적용하며 원격 평문 DB 연결이 아니다.
- Google API private DNS/route와 GCS object-read IAM, Secret Manager/Cloud SQL 권한을 각각 확인한다. 저장소 장애 때 memory 모드로 자동 전환하지 않는다.
- Cloud SQL 자동 백업과 삭제 보호를 유지한다. 데이터 복구는 별도 인스턴스 복원과 실제 데이터 대조를 거쳐 진행한다.
- OS 업데이트는 현재 일반 인터넷 경로에 의존하지 않는다. 고정 이미지와 런타임을 재검증한 교체/릴리스로 갱신한다.

승인/구현/테스트/실제 Plan/Apply/배포/게시 상태는 [AI-DLC](../../docs/ai-dlc/gcp-sample-app.md)를 따른다.

## 단일 VM Compose

2026-11-05 게시판 배포와 검증 후 사용자 후속 요청으로 Compose를 다시 내리고 자동 시작을 해제했다. 현재 FE/BE/Proxy와 프로젝트 네트워크 잔여0개, delta-compose inactive/disabled다. VM/Cloud SQL/이미지/설정은 보존하며 최신 상태는 [Compose 기록](../../docs/ai-dlc/gcp-compose-consolidation.md)의 마지막 중지 절을 따른다.

현재 전환 도구는 `compose.py`, 호스트 도구는 `compose-host.py`다. 기존 FE VM/ALB/SQL/PSC를 보존하고 FE/BE 이미지를 개별 생성한다. 로컬 Docker나 VM의 외부 registry/npm/apt 접근은 필요 없다. 검증된 기존 Node x64 산출물과 VM의 Ubuntu 런타임 라이브러리를 rootfs로 구성해 `docker image import --platform linux/amd64`로 만든다. 각 산출물 hash/source snapshot 및 실제 Docker image ID는 `/etc/delta-compose/images.json`과 보호된 배포 결과에 기록한다. 일반 앱 Dockerfile 빌드와 동일한 이미지라고 표현하지 않는다.

- Docker Engine 29.9.3: [공식 static 배포본](https://download.docker.com/linux/static/stable/x86_64/docker-29.9.3.tgz), SHA-256 `3fb5d1f13014c18cc54945bc177c58a9056a482dada031e17d12cf2e6922c973`. 공식 HTTPS에서 받은 바이트의 배포 고정 hash다. 별도 공개 서명을 검증했다고 주장하지 않는다.
- Compose 5.7.0: [공식 release](https://github.com/docker/compose/releases/tag/v5.7.0), Linux x86_64 SHA-256 `7758048ad38e580f22559603312874fec8bb638d021a9e64182385fc17cc12a2`, GitHub release asset의 digest와 대조한다.
- Node/Proxy의 고정 버전/공식 hash는 `release.py`와 기존 manifest를 따른다.

운영자는 공식 URL에서 위 두 파일을 `.validation/gcp-compose-20261105/binaries/docker-29.9.3.tgz`, `binaries/docker-compose`에 준비한다. 기존 FE/BE manifest와 tar를 `release.py build`로 개별 준비한 뒤 다음을 실행한다. 기존 데이터가 있는 DB에 `init-db`를 다시 실행할 필요는 없다.

```sh
python3 deploy/workload-gcp/compose.py bundle
python3 deploy/workload-gcp/compose.py publish
python3 deploy/workload-gcp/compose.py prepare
python3 deploy/workload-gcp/compose.py activate
python3 deploy/workload-gcp/compose.py status
python3 deploy/workload-gcp/compose.py restart
```

통합 bundle은 기존 FE 계정이 읽을 수 있는 전용 `releases/frontend/compose/<sha>.tar.gz`에 immutable 게시한다. host는 bundle과 Docker/Compose 및 FE/BE tar의 hash를 검증한 뒤 실행한다. Compose는 `/etc/delta-compose/compose.json`에서 관리하며 JSON은 Compose가 지원하는 YAML subset이다. `prepare`는 native 앱을 정지하지 않는다. `activate`는 native FE를 disable/stop하고 Compose를 시작한다. 시작 실패 시 marker를 제거하고 native FE를 다시 시작한다.

FE는 `172.28.91.2`, BE는 `.3`을 사용하고, host `10.32.1.20:3000`만 publish한다. BE/Proxy의 8080/5432는 host에 publish하지 않는다. Proxy는 BE namespace의 127.0.0.1:5432에서 듣고 PSC3307 TLS로 연결한다. 두 앱은 nonroot/read-only/cap_drop ALL이며 secret file은 BE에만 mount한다. 호스트 권한을 컨테이너 격리와 혼동하지 않는다.

`delta-compose-firewall`이 Docker보다 먼저 출발지 제한을 만들고 `delta-compose`가 secret 조회와 규칙 확인 후 컨테이너를 기동한다. FE는 BE8080 연결과 기존 응답만 허용하며 metadata/DB/host 새 연결을 차단한다. DB secret은 Secret Manager에서 host가 조회해 0400 파일로 저장하며 앱 관리자 비밀번호는 읽지 않는다. password는 이미지/Compose env/metadata에 포함하지 않는다.

호스트에서 sudo로 실행하는 운영 명령:

```sh
systemctl status docker delta-compose delta-compose-firewall --no-pager
docker compose -p delta-sample -f /etc/delta-compose/compose.json ps
systemctl restart delta-compose
```

BE 단독 restart는 namespace가 바뀌어 기존 Proxy와 loopback이 분리될 수 있다. `restart backend proxy` 역시 병렬 재시작 중 Proxy가 아직 내려간 BE namespace에 합류하려다 실패할 수 있다. 운영에서는 위 `systemctl restart delta-compose` 또는 로컬 `compose.py restart`를 사용한다. 이 절차는 전체를 완전히 stop한 후 의존성 순서대로 start하고 healthy를 기다린다.

secret 갱신은 `systemctl restart delta-compose`로 조회와 컨테이너 시작을 다시 수행한다. 민감값을 출력하는 전체 inspect/config/env는 진단 결과로 공유하지 않는다. 제한된 서비스별 로그와 health만 조회한다. 재부팅 후 Compose/systemd, ALB health 및 같은 시험 메모를 대조한다. Compose unit은 network-online/Docker 뒤에 시작한다. multi-user.target 이후의 Google startup scripts를 After에 추가하면 부팅 순환 의존성이 생기므로 추가하지 않는다.

BE VM 제거 전 rollback은 `systemctl stop delta-compose`, `/etc/delta-compose/active` 삭제, `systemctl enable --now delta-sample` 순서로 기존 native FE를 복구한다. BE VM 제거 후에는 `single_vm_compose=false`인 새 Terraform Plan을 검토/적용해 BE를 재생성하고 기존 BE native artifact를 설치/활성화해야 한다. DB는 재초기화하지 않는다. 이미지 복귀는 실제 healthy를 검증한 이전 compose.json/images.json과 로컬 image ID를 대조한 뒤 해당 구성을 재기동한다. 최초 nonroot 권한 실패로 남은 이미지/최초 bundle을 복귀 대상으로 사용하지 않는다.

실제 Plan/Apply/health/컨테이너 및 VM 재부팅 결과는 [단일 VM AI-DLC](../../docs/ai-dlc/gcp-compose-consolidation.md)에 기록한다. Git 커밋/푸시는 배포와 별개다.

## Compose만 중지하고 VM 유지

2026-11-05 실제 중지를 완료했다. FE/BE/Proxy와 프로젝트 network만 제거하고 VM/boot disk/SQL/ALB, 이미지와 설정은 보존한다. Docker daemon과 native fallback 방지 marker도 유지한다.

호스트에서 실행한 중지 명령:

```sh
sudo systemctl disable delta-compose
sudo systemctl stop delta-compose
sudo docker compose -p delta-sample -f /etc/delta-compose/compose.json down
```

`--volumes`와 `--rmi`는 사용하지 않는다. 중지 상태는 Compose inactive/disabled, 프로젝트 컨테이너0개/전용 network0개다. VM은 계속 RUNNING이며 공개 API503은 앱 중지에 따른 결과다.

사용자가 재개를 지시하면 호스트에서 다음을 실행한다. 현재 중지 작업에서는 실행하지 않았다.

```sh
sudo systemctl enable --now delta-compose
sudo docker compose -p delta-sample -f /etc/delta-compose/compose.json ps
```

보존한 이미지를 사용해 세 컨테이너를 새로 만들고 FE/BE healthy 및 공개 API/DB 저장을 확인한다. 서비스가 disabled여도 기존 `compose.py restart` 명령은 앱을 시작할 수 있으므로, 의도적으로 중지한 동안에는 실행하지 않는다. 실제 중지 시각과 자원 보존 근거는 [Operation 기록](../../docs/ai-dlc/gcp-compose-consolidation.md#후속-operation-compose만-내리고-vm-보존)을 따른다.
