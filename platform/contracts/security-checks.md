# SAST 코드 검사

성공한 빌드의 **같은 저장소·커밋**을 검사하고 결과를 해당 이미지 digest에 연결한다. 이미지 자체의 취약점 스캔이나 실행 중인 앱의 DAST가 아니다. 기존 `services/delta-sast` HTTP API를 사용한다.

## API

기본 경로는 `/api/v1`이다. 로그인 세션이 필요하며 POST는 기존 CSRF와 `Idempotency-Key` 규칙을 따른다.

| 요청 | 역할 |
| --- | --- |
| `POST /services/{service_id}/security-checks` | `{ "build_id": "UUID" }`. 새 작업 202, 동일 요청 재조회 200 |
| `GET /services/{service_id}/security-checks` | 최신순 이력. 기존 `limit`·`cursor` 페이지 규칙 |
| `GET /security-checks/{run_id}` | 상태·결과·실패 이유 |
| `GET /security-checks/{run_id}/events` | 진행 이력. 기존 `after_sequence`·`limit` 규칙 |

다른 서비스의 빌드, 실패한 빌드, 같은 서비스의 진행 중 작업과 충돌하면 409다. 검사 서비스 미설정은 503이며 새 작업을 만들지 않는다.

## 실행과 결과

`queued → running → succeeded/failed`. `succeeded`는 **검사 실행과 근거 확인 완료**다. 취약점이 있어도 검사 실행은 성공할 수 있다.

| 결과 | 의미 |
| --- | --- |
| `build_id`, `commit_sha`, `image`, `image_digest` | 검사 소스와 연결된 빌드·이미지 |
| `scanner_run_id` | SAST 실행 ID. 플랫폼 검사 ID와 동일 |
| `counts.fail`, `counts.review` | 조치 대상·확인 필요 수 |
| `findings` | 최대 100건의 규칙·파일·줄. 원문 메시지/코드/비밀값은 제외 |
| `findings_truncated` | 표시 한도를 넘었는지 여부 |
| `evidence_sha256` | 받은 SARIF를 키 정렬·공백 없는 JSON으로 정규화한 SHA-256 |
| `coverage` | `configured_scanner_rules_only`: 연결된 검사기의 지원 언어·규칙 범위 |
| `deployment_approved` | 항상 `false`. 현재는 자동 운영 배포 승인 기능 없음 |
| `dast_check` | `not_run` |

SAST의 `fast`·`preset` 모드를 사용한다. 저장소·40자리 커밋·요청 ID·모드가 일치하고 유효한 SARIF 2.1.0이 있어야 결과를 받는다. 발견 사항 0건도 검사하지 못한 언어·경로까지 안전하다는 뜻은 아니다. 원본 SARIF는 SAST 서버에 남고 플랫폼에는 요약·해시를 저장한다.

응답을 잃으면 같은 검사 ID와 입력으로 재접수한다. 기존 SAST의 중복 방지 계약을 이용한다. Worker 복구 시에도 같은 ID를 사용한다. 대기는 접수 준비 시점부터 15분으로 제한하며, 초과 시 원격 검사를 취소했다고 가정하지 않는다. 검사의 최종 정리는 SAST 실행 기록에서 확인한다.

## 연결과 배포

| 서버 설정 | 설명 |
| --- | --- |
| `PLATFORM_SAST_URL` | 지정 검사 서버의 HTTPS 주소. 로컬 모드만 loopback HTTP 허용 |
| `PLATFORM_SAST_TOKEN` | SAST 서버의 `DELTA_SAST_TOKEN`과 같은 Bearer 토큰 |
| `PLATFORM_SAST_CA_BASE64` | 사설 검사 서버 CA의 PEM을 Base64로 인코딩. 미설정은 기본 신뢰 저장소 사용 |

토큰은 서버의 비밀 설정에만 보관한다. 브라우저나 Git에 넣지 않는다. 운영 SSM `/delta-platform/runtime`에 기존 로그인·DB·빌드 값을 보존하며 연결 값을 추가해야 한다. 사설 CA를 쓰는 현재 구성은 위 세 값을 모두 설정한다. Jenkins의 `configure-runtime.py`는 Jenkins 설정 전용이므로 SAST 값을 받지 않는다. SAST 쪽 `SAST_REPO_PREFIXES`도 실제 검사할 공개 저장소 범위에 맞춘다. 설정 URL의 리다이렉트는 따라가지 않는다.

DB migration `0004`를 앱 시작 전에 적용한다. 기존 소스 확인·빌드·샌드박스 이력은 유지한다. SAST 미설정 환경에서는 다른 기능이 계속 동작한다. 보안 검사 이력이 생긴 뒤에는 `0003`으로 자동 downgrade하지 않는다. 롤백은 배포 전 백업 또는 스키마 호환 코드로 진행한다.

2026-11-04: PR #20 병합 후 사설 HTTPS·CA 설정을 활성화하고 GitHub Release를 완료했다. 공개 웹에서 실제 SAST 요청·결과·빌드 연결을 검증했다. DB `0004`와 기존 기록을 유지했다. [적용 안내](../deploy/sast/README.md)와 [검증 기록](../../docs/ai-dlc/platform-security.md)을 따른다. DAST는 아직 플랫폼에 연결하지 않았다.

후속 DAST 연결은 [별도 계약](dast-checks.md)을 따른다. 같은 빌드의 원본 SARIF 해시를 확인하며, SAST 완료와 DAST 결과·운영 배포 승인을 구분한다.
