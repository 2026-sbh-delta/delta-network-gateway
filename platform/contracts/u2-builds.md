# U2 이미지 빌드 계약

새 기본 실행기는 **Jenkins**다. 기존 CodeBuild 이력은 해당 실행기의 조회 경로로 보존한다. 운영 전환 상태는 [Jenkins 작업 기록](../../docs/ai-dlc/platform-jenkins.md)을 따른다.

U1의 인증·CSRF·요청 크기·Idempotency-Key 규칙을 그대로 사용한다. 모든 경로는 `/api/v1` 기준이다. 공개 GitHub 소스의 성공한 검사 결과가 있어야 한다.

| 메서드·경로 | 입력 | 결과 |
| --- | --- | --- |
| POST `/services/{id}/builds` | `{ "source_check_id": "UUID" }`, Idempotency-Key | 최초 202, 동일 요청 재전송 200 |
| GET `/services/{id}/builds` | limit·cursor | 이 서비스의 빌드 이력만 반환 |
| GET `/builds/{id}` | 없음 | 상태·단계·고정 커밋·외부 실행 ID·결과/오류 |
| GET `/builds/{id}/events` | after_sequence·limit | 접수·시작·빌드 단계·종료 이력 |
| GET `/builds/{id}/logs` | 없음 | 최근 빌드 로그 텍스트·available·truncated |

기존 `/source-checks`는 소스 검사만 반환한다. 서로 다른 종류의 ID로 조회하면 404다. `GET /services/{id}`에는 `latest_build`가 추가된다.

## Jenkins 저장과 복구

- `build_target` snapshot은 `provider=jenkins`, controller URL, Job, ECR repository/region을 고정한다. 사용자/토큰은 snapshot이나 HTTP 응답에 넣지 않는다.
- `submission`은 POST 전 요청 파라미터와 접수 의도를 저장한다. 고객 입력은 `DELTA_RUN_ID`, `DELTA_SOURCE`, `DELTA_COMMIT`, `DELTA_CONTEXT`, `DELTA_DOCKERFILE`로 전달하고 관리자 소유 Job만 실행한다.
- `external_id`는 `jenkins:queue:<번호>`에서 `jenkins:build:<번호>`로 한 번 전환한다. 같은 Worker 임대 소유자만 전환할 수 있다.
- 응답 유실 시 같은 요청 ID의 최근 200개 빌드/큐를 검색한다. 결과를 확정하지 못하면 `build_submission_uncertain`으로 실패하며 자동 재접수하지 않는다. 기록 보존 기간을 넘긴 실행도 같은 원칙을 따른다.
- Job 성공 뒤 `result.json`의 schema_version/run_id/commit_sha/platform/image/image_digest와 ECR의 `run-<UUID>` tag를 비교한다. 결과가 다르면 성공으로 저장하지 않는다.
- 로그는 크기를 제한한 텍스트이며 redirect를 따르지 않는다. HTTP API에는 private snapshot·접수 intent·인증 정보가 반환되지 않는다.
- Job 30분, Worker 접수부터 35분 제한. 제한 초과 시 요청 소유권을 검증한 실행만 중단 요청한다. 실제 중단 완료까지 검증했다는 의미는 아니다.
- Jenkins 연결 설정이 없는 새 요청은 503이며 CodeBuild로 자동 전환하지 않는다. `PLATFORM_BUILD_PROVIDER=codebuild`는 명시적인 이전 실행기 선택용이다.

## 기존 CodeBuild 저장과 복구

- `runs.kind`: 기존 `source_preflight` + 새 `image_build`. 동일 서비스의 활성 작업은 종류에 관계없이 하나다.
- 빌드 입력 snapshot: 성공한 소스 검사의 등록 정보·source_check_id·실행기 대상(project/repository/region). `resolved_commit_sha`는 그 검사에서 복사한다.
- `submission`: 외부 호출 직전에 저장한 정확한 StartBuild 요청·준비 시각. 재시작 후 같은 요청·같은 토큰을 사용한다. 사용자에게는 이 내부 필드를 반환하지 않는다.
- `external_id`: CodeBuild 실행 ID. 이후 Worker가 죽어도 이 ID로 결과를 조회한다.
- 시작 결과가 불명확하고 준비 후 4분이 지나면 `build_submission_uncertain`으로 멈춘다. AWS의 5분 idempotency 기간이 끝난 뒤 중복 작업을 만드는 것보다 운영자 확인을 우선한다.
- Worker lease는 60초, heartbeat는 15초다. 소스 확인은 시도당 120초, 빌드는 1,800초가 상한이다. 복구 시도는 3회까지다. AWS 빌드는 실행 15분·대기 10분 제한을 별도로 갖는다.

## 성공 결과

```json
{
  "image": "<account>.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds@sha256:<digest>",
  "image_digest": "sha256:<digest>",
  "commit_sha": "40자리 Git SHA",
  "platform": "linux/amd64",
  "source_check_id": "소스 검사 UUID",
  "build_id": "프로젝트:실행 UUID",
  "completed_at": "ISO 8601 UTC"
}
```

CodeBuild의 성공 상태·실제 source SHA·플랫폼 작업 ID를 확인하고, ECR의 run 태그에서 단일 manifest digest를 읽은 후에만 저장한다. Docker build 출력에서 digest를 추출하지 않는다. 이 성공은 ECS 검사나 배포 승인이 아니다.

주요 실패: `source_check_required`(409), `build_in_progress`(409), `builder_not_configured`(503), `build_submission_uncertain`, `build_commit_mismatch`, `build_failed`, `build_timed_out`. 실행 중 통신이 반복 실패하면 기존 lease 복구 정책을 따른다.

로그는 로그인한 공통 작업 공간에서만 읽는다. 최근 100개 이벤트·20,000자 상한이며 HTML로 실행하지 않는다. 이 로그 API는 고객 서비스의 중앙 로깅 기능이 아니다.
