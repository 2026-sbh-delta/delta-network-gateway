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
| `DELTA_RELEASE_CONFIG` / `DELTA_DEPLOY_CONFIG` | `/run/delta/release-config.json`, 배포기 설정(S3 state 버킷, 대상 계정 위임, VM·DB 네트워크, DB 프로필). 비밀 값 없음 |
| `GOOGLE_APPLICATION_CREDENTIALS` | `/run/delta/gcp-release-credentials.json`, GCP `prod-deploy` WIF 설정(키 파일 아님) |

운영 배포는 동규님 방식이다. `release_deploy.py`가 승인·예약·묶음을 검사한 뒤 `vm_release.py`가 요청을 동규님 `DELTA_DEPLOYMENT` 형식으로 바꿔 `/opt/delta/release/deploy.py`를 별도 프로세스로 실행한다. 배포기는 환경마다 Terraform으로 사설 VM을 만들고(state는 S3 `delta-cp/release/<environment_id>/…`), DB(new/existing, PostgreSQL 18)를 준비하고, VM에서 Docker Compose(awslogs/gcplogs, 하드닝)를 띄운 뒤 VM 안에서 실행 이미지 digest와 `/api/health`를 관측한다. `vm_release`는 그 관측값으로만 결과 계약을 만든다. 기존 VM을 EIC/IAP로 갱신하던 `compose_host.py`·`release_transport.py`는 삭제했다.

인벤토리 항목은 `target_id`, `environment=production`, `cloud`(gcp/aws), `region`, `profile_id`(e2-small/t3.small), `database{profile_id, mode=new|existing}`, 선택 `environment_id`(UUID)다. 호스트·DB·비밀 참조는 인벤토리가 아니라 배포 설정에만 둔다. 파이프라인 `release_config`는 이 항목과 같아야 한다(`config_sha256`). 실패 결과는 `stage`·`code`만 남긴다. 상세·인프라 전제는 [운영 배포기](../release/README.md).

컨트롤러 이미지는 `deploy/platform/release`를 이름 있는 빌드 컨텍스트로 받는다(`--build-context release=deploy/platform/release`). 2026-11-06 현재 Jenkins·배포기 시험 128개 통과(0 skip). 실제 컨트롤러 이미지 빌드·Jenkins 시작·클라우드 실배포는 아직 검증하지 않았다.

```sh
(cd deploy/platform/jenkins && uv run --quiet --with pytest --with pyyaml --with cryptography --with boto3 --with google-auth --with "psycopg[binary]" python -m pytest -q tests ../release/tests -p no:cacheprovider)
```

다른 담당의 테스트는 읽기만 한다. Terraform apply, 운영 DB 변경·배포, commit/push/PR은 수행하지 않았다.
