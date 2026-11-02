# U1 HTTP 인터페이스

U1에서 구현한 계약이다. 기본 경로는 `/api/v1`이며 고정 계정 하나로 로그인한 공통 작업 공간을 다룬다. 사용자·프로젝트 관리 API는 없다. 모든 예시는 설계용이며 실제 실행 결과가 아니다.

## 인증·오류·공통 규칙

- 인증: HttpOnly 세션 쿠키. 로그인 외 업무 endpoint는 인증 필수다.
- 같은 origin의 JSON 요청만 지원한다. 모든 POST는 `Content-Type: application/json`, `X-Platform-Request: 1`, 일치하는 Origin을 요구한다.
- 로그인 이후 POST는 `X-CSRF-Token`도 요구한다. 토큰은 로그인/세션 확인 응답에서 받고 로그에 기록하지 않는다.
- UUID ID, UTC RFC3339 시각, snake_case JSON을 사용한다.
- 업무 오류: `{"error":{"code":"service_not_found","message":"서비스를 찾을 수 없습니다.","request_id":"..."}}`. 내부 예외·자격 증명·상류 응답 원문은 노출하지 않는다.
- 미인증은 401, 요청 보호 실패는 403, 없는 ID는 404, 중복 충돌은 409, 입력 오류는 422, 로그인 시도 제한은 429다.
- 목록은 `limit` 기본 50/최대 100과 불투명 cursor를 사용한다. 응답은 `items`, `next_cursor`다.

## API 목록

| 메서드·경로 | 역할 | 성공 응답 |
| --- | --- | --- |
| `POST /auth/login` | 고정 ID·비밀번호 검증, 새 세션 발급 | 200, 쿠키 + 세션 요약 |
| `GET /auth/session` | 로그인 상태 확인 | 200, `login_id`, `expires_at`, `csrf_token` |
| `POST /auth/logout` | 현재 세션 폐기 | 204 |
| `POST /services` | 서비스 등록 | 201 신규 / 200 같은 요청 재사용 |
| `GET /services` | 서비스 목록 | 200, 페이지 |
| `GET /services/{service_id}` | 서비스 설정·최근 사전 검사 요약 | 200 |
| `POST /services/{service_id}/source-checks` | 설정 snapshot으로 사전 검사 접수 | 202 신규 / 200 같은 요청 재사용 |
| `GET /services/{service_id}/source-checks` | 해당 서비스 검사 이력 | 200, 페이지 |
| `GET /source-checks/{run_id}` | 단계·커밋·결과·실패 이유 | 200 |
| `GET /source-checks/{run_id}/events?after_sequence=0` | 저장된 이벤트 조회 | 200, `items`, `next_sequence`, `has_more` |

`GET /health/live`는 프로세스 생존, `GET /health/ready`는 DB 연결·migration 버전 준비 여부만 반환한다. 두 endpoint는 인증 없이 최소 상태만 응답한다. readiness 실패는 503이며 Worker 정상 동작까지 보장하지 않는다.

## 로그인 요청

`POST /auth/login`은 문자열 `login_id`(1~128자)와 `password`(1~1024자)를 받는다. 두 값의 대소문자나 앞뒤 공백을 자동 변경하지 않으며 추가 필드는 거부한다. 요청 본문은 최대 16 KiB다. 성공 시 세션 확인과 같은 요약을 반환하고, 실패 시 입력값을 응답이나 로그에 남기지 않는다.

## 서비스 등록

`POST /services`는 `Idempotency-Key`를 필수로 받는다. 키는 1~128자의 ASCII 식별자다. 다음 요청은 형식 예시다.

```json
{
  "name": "orders-api",
  "repository_url": "https://github.com/example/orders-api",
  "source_ref": "main",
  "build_context": ".",
  "dockerfile_path": "Dockerfile",
  "container_port": 8080,
  "healthcheck_path": "/health"
}
```

- `name`: 1~80자, `source_ref`: 1~255자. ref를 shell 명령에 삽입하지 않는다.
- repository URL은 github.com의 공개 저장소 형식만 허용한다. 실제 공개 여부는 Worker가 확인한다.
- build context와 Dockerfile은 저장소 상대 경로다. build context 안에 Dockerfile이 있어야 한다.
- port는 1~65535. healthcheck path는 `/`로 시작하는 1~256자 상대 HTTP 경로로, query·fragment·제어문자를 거부한다.
- 추가 필드·환경변수·비밀값은 U1 요청에서 거부한다. 성공 응답은 저장된 필드와 `id`, `created_at`, `latest_source_check: null`을 포함한다.
- 같은 키·같은 정규화된 요청은 같은 서비스다. 같은 키·다른 요청은 `idempotency_conflict`다. 이름은 작업 공간에서 고유하며 같은 이름·새 키는 `service_name_conflict`다.
- U1에는 수정·삭제 endpoint를 추가하지 않는다.

## 사전 검사 접수와 상태

`POST /services/{service_id}/source-checks`는 body `{}`와 `Idempotency-Key`를 받는다. 클라이언트가 status·commit SHA·검사 결과를 지정할 수 없다.

신규 접수 응답:

```json
{
  "id": "11111111-1111-4111-8111-111111111111",
  "service_id": "22222222-2222-4222-8222-222222222222",
  "kind": "source_preflight",
  "status": "queued",
  "current_step": null,
  "resolved_commit_sha": null,
  "attempt": 0,
  "result": null,
  "error": null
}
```

- `status`: `queued`, `running`, `succeeded`, `failed`.
- `current_step`: `repository`, `revision`, `build_context`, `dockerfile` 중 하나 또는 null.
- 성공 결과는 `resolved_commit_sha`, 검증한 경로·Dockerfile blob SHA·확인 시각이다. 이미지 digest나 서비스 URL은 아직 없다.
- 오류 예: `repository_unavailable`, `ref_not_found`, `path_not_found`, `unsupported_path_type`, `source_limit_exceeded`, `github_rate_limited`, `github_unavailable`, `attempts_exhausted`.
- 같은 서비스에 활성 작업이 있으면 다른 키의 요청은 `source_check_in_progress` 409와 기존 `run_id`를 반환한다.
- 같은 키의 재요청은 먼저 기존 작업을 반환한다. 완료된 작업의 같은 키를 다시 보내도 새 작업을 만들지 않는다. 새 검사는 새 키를 사용한다.
- 이벤트는 기본 100/최대 200개를 순서대로 조회한다. `sequence`, `run_id`, `step`, `event_type`, `message`, `created_at`을 반환하며 중복 이벤트는 sequence로 구분한다.

## 화면 연결

로그인 → 서비스 목록 → 등록 → 상세 → **소스 확인**을 누르는 흐름이다. 활성 작업은 3초 간격으로 DB에 저장된 상태를 조회하고, 끝나거나 화면을 떠나면 polling을 중지한다. API 조회가 실패하면 마지막 결과와 조회 실패를 구분한다.

서비스 상세에서 커밋 링크는 검증된 GitHub owner/repo와 고정 SHA로 구성한다. 외부 오류 문구를 HTML로 삽입하지 않는다. 테스트 데이터는 개발 fixture로만 사용하고 실제 결과 화면에 주입하지 않는다.
