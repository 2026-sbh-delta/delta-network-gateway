# U3 샌드박스 실행 확인

성공한 빌드의 `repository@sha256:…`를 선택한 환경에서 실행한다. AWS는 ECS·내부 ALB, GCP는 인증된 Cloud Run을 사용하며 실제 이미지와 HTTP 응답을 확인한 뒤 정리한다. 이 결과는 **보안 검사 통과나 운영 배포 승인으로 사용하지 않는다.**

## HTTP

모든 경로는 `/api/v1` 아래이며 기존 고정 로그인 세션을 요구한다. 쓰기는 동일 Origin·`X-Platform-Request: 1`·CSRF 토큰을 확인한다.

| 메서드·경로 | 입력 / 결과 |
| --- | --- |
| POST `/services/{id}/sandbox-checks` | `{"build_id":"UUID","execution_cloud":"aws 또는 gcp"}`와 `Idempotency-Key`. 신규 202, 같은 요청 재전송 200 |
| GET `/services/{id}/sandbox-checks` | `limit`, `cursor`로 실행 이력 조회 |
| GET `/sandbox-checks/{id}` | 상태·고정 커밋·실행 자원(`external_id`)·결과·오류 |
| GET `/sandbox-checks/{id}/events` | `after_sequence`, `limit`으로 진행 기록 조회 |
| POST `/sandbox-checks/{id}/cancel` | `{}`. 대기 작업은 바로 취소, 실행 중에는 정리를 요청 |

`GET /sandbox-targets`는 cloud/label/runtime/configured 목록을 반환한다. 생략한 `execution_cloud`는 기존 요청과 호환되도록 AWS다. 대상이 다른 요청은 같은 멱등 키로 재사용할 수 없다. 온프레미스는 미구성으로 표시하고 요청을 거절한다.

다른 서비스의 빌드, 실패한 빌드, 다른 내용으로 재사용한 요청 키를 거절한다. 전체 플랫폼에 실행 공간은 1개다. 다른 실행이 진행 중이면 409 `sandbox_busy`를 반환한다. 같은 서비스의 소스 확인·빌드와도 겹치지 않는다.

## 결과와 복구

상태는 `queued → running → succeeded / failed`다. 중단은 `failed` + `sandbox_cancelled`로 저장하고 화면에는 **중단 완료**로 표시한다.

성공 결과는 `build_id`, `image`, `image_digest`, `commit_sha`, `task_arn`, `revision`, `container_port`, `healthcheck_path`, 확인 시각과 다음 값을 포함한다.

```json
{"runtime_check":"passed","security_check":"not_run","cleanup":"stopped"}
```

- Worker는 ECS 개정판을 DB에 기록한 뒤 실행한다. 재시작하면 같은 개정판을 이어 확인한다. 등록 응답 자체가 유실되면 미사용 개정판이 남을 수 있지만, 기록 전 태스크를 실행하지 않는다.
- 실행 결과를 먼저 저장하고 정리한다. 정리 응답이 유실돼도 새 태스크를 다시 띄우지 않는다.
- DB 작업 임대와 세션 잠금으로 플랫폼 Worker 간 중복 실행을 막는다. 계정·개정판·실행 ID·커밋·이미지·포트를 확인하고 다른 실행을 덮어쓰거나 종료하지 않는다. 운영자가 같은 ECS 서비스를 동시에 수동 변경하는 사용 방식은 지원하지 않는다.
- 새 태스크의 실제 digest가 일치하고 그 태스크만 healthy여야 통과다. ECS의 RUNNING 표시만으로 성공 처리하지 않는다. HTTP 200~399 응답은 앱 실행 여부의 제한적인 기준이며 업무 기능·DB·보안 검증을 대신하지 않는다.
- 태스크 정리 실패는 `sandbox_cleanup_required`로 남기며 성공 결과를 공개하지 않는다. 후속 실행이 기존 태스크를 강제 종료하지 않는다. [운영 절차](../deploy/sandbox/README.md)를 따른다.

## 설정과 저장

API와 Worker 모두 `PLATFORM_SANDBOX_TARGET`에 Terraform `runtime_env` 출력의 JSON 문자열을 받는다. 설정이 없으면 접수를 503으로 거절한다. 실행 도중 대상 설정을 바꿨다면 기존 태스크 정리를 별도로 확인한다. 설정은 클라우드 리소스 식별자이며 Access Key를 담지 않는다.

DB migration `0003`은 작업 종류·중단 요청 필드·전체 샌드박스의 동시 실행 제한을 추가한다. 기존 빌드와 로그인 기록을 보존한다. U2 앱은 `0002`를 요구하므로 U3 이후 단순 이미지 롤백은 불가능하다. 같은 스키마를 이해하는 수정 릴리스를 사용하거나 배포 전 백업을 검증·복원한다. 샌드박스 기록이 있으면 downgrade를 거절한다.

## GCP 실행

`PLATFORM_GCP_SANDBOX_TARGET`은 실행 대상, `PLATFORM_GCP_CREDENTIALS_JSON`은 AWS 역할의 WIF 설정이다. SA 키를 저장하지 않는다. [Terraform 기반과 배포](../deploy/sandbox-gcp/README.md)를 따른다.

실행 전에 run UUID로 결정한 Cloud Run 자원 ID를 저장한다. 이미지는 ECR에서 Artifact Registry로 동일 digest를 복사한다. 결과에 원본 `image`, 목적지 `runtime_image`, 실제 `revision`, `execution_cloud=gcp`, `environment_check=gcp_sandbox_only`를 기록한다. Cloud Run의 인증·고정 revision·격리 설정을 확인하며 IAM 반영 지연은 최대 120초까지 기다린다. 삭제 실패·소유권 불일치 시 작업을 성공 처리하거나 다른 실행에 공간을 넘기지 않는다.

이 검사는 최종 운영 VM의 DB 연결·방화벽·볼륨 동작을 보장하지 않는다. 환경별 결과를 서로 대체하지 않는다.
