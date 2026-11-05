# FE·BE 이미지 묶음 빌드와 운영 배포

기존 `delta-build`, `delta-deploy` 경로를 유지하고 `delta-release`를 추가한다. 스테이징은 기존 ECS/Cloud Run API를 사용한다. 운영은 서버 인벤토리에 등록된 AWS 또는 GCP 대상 **하나**를 선택한다. 기본 이관 흐름은 GCP이며 AWS 원본 동시 갱신은 없다.

`delta-build`는 full40 commit의 `src/frontend`, `src/backend`를 각각 linux/amd64로 한 번 빌드한다. `FRONTEND_IMAGE_REPO`, `BACKEND_IMAGE_REPO` 기본값은 `IMAGE_REPO/frontend`, `IMAGE_REPO/backend`다. `ECR_IMAGE_REPO`가 있으면 기존 ECR 저장소의 `<RUN_ID>-frontend`, `<RUN_ID>-backend` 태그로 복사한다. `skopeo copy --preserve-digests`는 manifest 변환을 금지하기 위한 도구이며, 복사 후 raw manifest SHA256을 빌드 digest와 대조한다. 한쪽 실패·복사 실패·digest 불일치 시 성공 `result.json`은 없다. 성공 결과만 작업 키별 재사용하며 입력이 바뀌면 거부한다. 결과의 `registry_images.gcp/aws`는 클라우드별 동일 digest ref다. ECR 설정이 없으면 GCP AR만 빌드할 수 있다.

`result.json` schema2의 묶음 해시는 `{schema,commit,platform,image_digests}`를 키 정렬·공백 없는 UTF-8 JSON으로 만든 SHA256이다. `image_ref`, `image_digest`는 frontend 별칭이다. `staging_deploy.py`는 `IMAGES_JSON`, `BUNDLE_SHA256`를 검사해 실제 HTTP body에 전달한다. 반환된 두 이미지·digest·구조화 reset을 검사하고 관측 필드·단계를 보존한다. `databaseVersionNum`은 `database_version_num`으로 정규화한다. 기존 단일 `IMAGE` 호출도 유지한다. 관측되지 않은 commit·DB 버전을 입력값으로 채워서 증거로 만들지 않는다.

운영 파라미터는 `RUN_ID` 또는 `DEPLOYMENT_ID`, `COMMIT`, `TARGET_ID`, `IMAGES_JSON`, `BUNDLE_SHA256`, `APPROVAL`이다. `APPROVAL`은 pipeline이 사람 승인 뒤 발급한 `{"claims":{...},"signature":"base64url-ed25519"}` JSON이다. claims는 deployment_id·commit_sha·bundle_sha256·target_id·environment·config_sha256·exp·nonce다. 공개 target dict 전체의 canonical SHA256이 `config_sha256`이다. Jenkins는 Ed25519 공개 키만 받으며 nonce와 deployment identity를 SQLite 한 transaction에서 예약한다. 서명·만료·대상·묶음 검증이 끝나기 전 클라우드 인증이나 앱 비밀 조회를 하지 않는다.

AWS 실행은 추가로 `BUNDLE_JSON` 전체 빌드 결과의 `registry_images.aws`를 요구하며 `IMAGES_JSON`도 그 AWS refs여야 한다. 레지스트리 주소를 바꿔도 두 digest와 묶음 해시는 같아야 한다. 파이프라인 호출자는 AWS 선택 시 전체 결과 보존·ref 선택을 연결해야 한다. GCP의 기존 파라미터만으로도 release를 요청할 수 있다.

컨트롤러의 서버 소유 파일·설정:

| 항목 | 기본값·역할 |
|---|---|
| `DELTA_RELEASE_TARGETS` | `/run/delta/release-targets.json`, target_id를 키로 하는 비밀 없는 인벤토리 |
| `DELTA_RELEASE_PUBLIC_KEY` | `/run/delta/release-public.pem`, PEM Ed25519 공개 키 |
| `DELTA_RELEASE_DB` | `/var/jenkins_home/delta-release.sqlite3`, 영속 승인 예약 |
| `DELTA_RELEASE_KNOWN_HOSTS` | `/run/delta/known_hosts`, 검증된 호스트 키. 검사 우회 금지 |
| `DELTA_RELEASE_WIF_CONFIG` | `/run/delta/gcp-release.json`, 기존 서버용 WIF 설정. 개인 로그인·프로필 사용 금지 |

공통 인벤토리는 `target_id`, `cloud`, `environment=production`, `connection`, `os_user`, `compose_project`, `install_dir`(기존 `/opt/delta-compose` 재사용), `frontend_url/port`, `backend_port`, `db_name`, `db_major=18`, `db_secret_ref`, 클라우드 로그 설정을 포함한다. GCP는 `connection=iap`, `project`, `zone`, `vm_name`, `log_driver=gcplogs`, `log_project`를 쓴다. AWS는 `connection=eic`, `region`, `instance_id`, `availability_zone`, `endpoint_id`, `private_ip`, `assume_role_arn`, `log_driver=awslogs`, 기업 `/delta/workload/…` 로그 그룹을 쓴다. 호스트·DB·비밀 참조를 Jenkins 요청으로 덮어쓰지 않는다.

원격 연결은 AWS AssumeRole 후 EIC 단기 키·900초 제한 터널 또는 GCP WIF·IAP·OS Login을 사용한다. 같은 호스트 도구를 전달하고 `systemd-run`으로 SSH와 분리해 실행한 뒤 짧은 접속으로 조회한다. SSH 단절·응답 유실은 `unknown`으로 남기며 같은 deployment_id를 조회한다. 원격 launch intent가 이미 있으면 상태를 잃었다고 새 배포를 자동 시작하지 않는다. 기존 `/etc/delta-compose`와 이전 릴리스·로컬 이미지를 삭제하거나 prune하지 않는다.

호스트 `compose_host.py`의 `deploy(request,state_dir,*,runner,probe)`, `query(deployment_id,state_dir)`, `render_compose(target,bundle)` 동작은 독립 테스트 계약 확정 후 구현한다. 2026-11-06 현재 기존 Jenkins 시험 27개 통과·0 skip, 별도 호스트 시험 18개는 구현 부재 오류이며 동작 RED로 집계하지 않는다. 에이전트 이미지와 컨트롤러 런타임은 linux/amd64로 실제 빌드·도구 실행을 확인했다. 최종 컨트롤러 이미지·호스트 동작·실제 Jenkins 시작·클라우드 registry 복사·실배포는 아직 검증하지 않았다.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 services/delta-staging/.venv/bin/python -m pytest -q -c /dev/null --confcutdir=deploy/platform/jenkins/tests deploy/platform/jenkins/tests --tb=line -p no:cacheprovider
```

다른 담당의 테스트는 읽기만 한다. Terraform apply, 운영 DB 변경·배포, commit/push/PR은 수행하지 않았다.
