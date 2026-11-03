# 플랫폼 AWS 배포와 복구

고객 앱을 배포하는 `automation/`과 별개로 **플랫폼 자체**를 AWS에 올리는 영역이다. API·Worker는 Python 이미지, 웹은 이미지 안의 정적 파일이다. 런타임에는 Node.js를 설치하지 않는다.

[인프라 구성·입출력](terraform/README.md) · [현재 배포·검증 기록](../../docs/ai-dlc/platform-aws.md)

팀의 정상 변경 경로는 [GitHub Actions 배포](GITHUB.md)다. 아래 로컬 명령은 최초 기반 준비·복구용으로 남긴다. 현재 최초 AWS 배포와 PR #7 병합은 완료됐고 CI도 통과했다. Environment 없이 main 전용 OIDC 역할 생성을 완료했다. 새 워크플로도 PR #8에서 main에 반영했고 GitHub OIDC Plan·Release 및 배포 후 기능 검증까지 완료했다. 사용자가 입력할 변수는 [GitHub 안내](GITHUB.md), 실제 IAM 적용·GitHub 배포 상태는 [OIDC 기록](../../docs/ai-dlc/platform-oidc.md)을 따른다.

## 배포 순서

AWS 인증은 해당 터미널의 환경변수 또는 AWS 프로필에서 받는다. 다른 터미널의 export가 자동 전달된다고 가정하지 않는다. AWS 키·세션 토큰을 Docker 이미지, user data, Terraform 변수, 앱 설정에 넣지 않는다.

1. STS로 계정·역할을 확인하고 `terraform/terraform.tfvars.json`에 실제 계정, 리전, ARM64 AMI, AZ를 기록한다. 첫 Origin 생성 때만 `bootstrap_origin_access=true`다.
2. `uv sync --project platform/deploy --frozen` 후 `scripts/bootstrap-state.py --account-id ...`로 전용 상태 버킷과 `backend.hcl`을 준비한다.
3. Terraform `init -backend-config=backend.hcl`, `validate`, 저장 Plan 검토, `apply` 순서로 실행한다. 실제 값을 넣기 전 mock 시험을 먼저 수행한다. 기존 state·리소스를 가져오지 않는다.
4. 저장소 루트에서 `docker build --platform linux/arm64 -t delta-platform:u1 -f platform/Dockerfile .`으로 이미지를 만든다. 저장소 루트가 빌드 컨텍스트이며 Dockerfile.dockerignore로 필요한 코드만 포함한다. 플랫폼 Worker는 VM 역할로 CodeBuild·ECS를 호출한다.
5. `scripts/publish-images.py --account-id ... --output platform/.runtime/published-images.json`으로 플랫폼·PostgreSQL 이미지를 ECR에 게시한다. 실제 명령은 배포 가상환경의 `uv run --project platform/deploy python ...`으로 실행한다.
6. `terraform output -json` 결과를 Git에서 제외된 로컬 파일로 저장하고, `scripts/deploy-release.py --outputs ... --images ... --runtime-dir platform/.runtime`를 실행한다. VM의 SSM 접속과 `/opt/delta-platform/bootstrap-ready`가 먼저 준비돼야 한다.
7. `bootstrap_origin_access=false`로 바꾸고 저장 Plan을 검토해 재적용한다. 최종 수신은 CloudFront 서비스 SG만 허용한다.
8. HTTPS 로그인·서비스 등록·실제 GitHub 확인, API·Worker 재시작, 백업·복원을 검증한다. 마지막 Plan이 No changes인지 확인한다.

`deploy-release.py`는 기존 SSM 설정이 있으면 고정 계정과 DB 비밀번호를 유지한다. 최초 실행에만 ID `delta`와 임의 비밀번호를 생성하고 로컬 `platform-login.txt`에 권한 0600으로 저장한다. 비밀번호 원문은 서버에 저장하지 않고 해시만 SSM SecureString에 넣는다. DB 비밀번호는 같은 암호화 파라미터로 관리한다. 로그인 정보는 일반 문서나 Git에 복사하지 않는다.

배포 도구는 S3 릴리스 파일을 가져오는 명령만 SSM에 전달한다. 비밀값 자체를 SSM 명령 인수에 넣지 않는다. VM은 자기 역할로 특정 파라미터를 읽어 root 전용 `/etc/delta-platform/`에 저장한다. 해시의 `$` 문자를 Compose가 변경하지 않도록 앱 env 파일은 `format: raw`로 읽는다.

## 서버 구성

| 위치 | 용도 |
| --- | --- |
| `/opt/delta-platform/releases/` | 릴리스별 Compose·실행 도구 |
| `/opt/delta-platform/current` | 현재 릴리스 링크 |
| `/etc/delta-platform/app.env` | API·Worker 설정, root 전용 |
| `/etc/delta-platform/deploy.env` | 고정 이미지 digest·DB 설정, root 전용 |
| `/srv/delta-platform/postgres` | 별도 암호화 EBS의 PostgreSQL 데이터 |
| `delta-platform.service` | 부팅 시 컨테이너 자동 시작, 데이터 디스크 마운트 필수 |
| `delta-platform-backup.timer` | UTC 03시 일일 DB 백업 |

API만 컨테이너의 8088 포트를 VM에 연결한다. DB는 Docker 내부 네트워크에서만 접근한다. API·Worker는 root 권한 없이 실행하며 파일 시스템 읽기 전용·권한 축소를 적용한다. 로그는 컨테이너별 10 MiB × 3개로 제한한다. 중앙 로그 수집은 후속 단계다.

## 확인과 복구

SSM에서 `systemctl status delta-platform`, `docker compose ... ps`, `/health/ready`를 확인한다. 서비스의 Worker 상태는 실제 검사 작업이 진행되는지 함께 확인한다. 상세 SSM 로그에 민감값을 출력하는 진단 명령은 사용하지 않는다.

- **앱 재시작:** `systemctl restart delta-platform`으로 실행한다. 영속 디스크·DB 볼륨은 삭제하지 않는다.
- **코드 롤백:** `/etc/delta-platform/previous-deploy.env`의 이전 이미지 digest와 DB 스키마 호환성을 확인한 뒤 적용한다. migration이 데이터를 바꿨다면 먼저 백업·복원 경로를 판단한다.
- **DB 백업:** `systemctl start delta-platform-backup`으로 실행한다. `database/` 경로의 S3 객체 생성을 확인한다. 버킷은 비공개·암호화·버전 관리와 7일 수명 주기를 적용한다.
- **복원 확인:** 백업을 별도 시험 DB에 `pg_restore`하고 테이블·서비스·작업 수를 원본과 비교한다. 확인 후 시험 DB만 제거한다. 실제 플랫폼 DB를 덮어쓰는 복원은 별도 작업이다.
- **NAT 장애:** SSM·GitHub 접근이 실패하면 NAT EC2, source/destination check 해제, 전달 설정·사설 기본 경로를 확인한다. NAT는 사용자 수신 경로에 포함되지 않는다.
- **종료:** 앱을 중지해도 EBS·공인 IPv4·저장소 등 비용은 남는다. 해체는 데이터 보존 범위를 먼저 정하고 별도로 수행한다.

현재 구성은 단일 AZ·단일 앱/DB VM이다. 다중 AZ HA와 자동 복구를 검증한 배포로 표현하지 않는다.

## 고객 이미지 빌드

플랫폼 자체 배포 CI와 고객 Dockerfile 빌드를 분리한다. 고객 빌드 기반·초기 SSO 구성·runtime 연결·비용·실패 복구는 [CodeBuild 안내](builds/README.md)를 따른다.
