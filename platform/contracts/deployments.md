# 사용자 배포 API 계약

인증·CSRF·멱등 키는 기존 플랫폼 API와 같다. 배포 자격 증명과 계정/네트워크 연결은 서버에서만 관리한다.

- `GET /api/v1/deployment-options`: `items` 배열. 항목은 `cloud`, `label`, `runtime`, `region`, `configured`, `reason`, `profiles`(`id`, `label`, `cpu`, `memory_mb`, `description`), `hosts`(`id`, `label`). 카탈로그의 지원 사양만 선택한다.
- 서비스 등록의 선택 속성 `deployment_target`: `{cloud, region, profile_id, host_id?}`. 최초 등록 화면에서는 필수, 기존 API 고객은 생략 가능하다. 등록은 자원을 생성하지 않는다.
- `GET /api/v1/services/{id}/deployment-plans`: `{items:[plan]}`.
- `POST /api/v1/services/{id}/deployment-plans`: `{kind:'deployment'|'migration', build_id, backend_build_id?, source_environment_id?, targets:[선택 속성]}`. 최초 배포 대상은 1개, 이전 대상은 서로 다른 클라우드 1~2개이며 출발 환경을 포함할 수 없다. 서버가 고정 이미지·검사·대상을 검증해 계획과 `input_hash`를 반환한다.
- `GET /api/v1/deployment-plans/{id}`: 상세 계획. `id`, `service_id`, `kind`, `status`, `input_hash`, `created_at`, `approved_at`, `input_snapshot`(`image`, `commit_sha`, `source`, `targets`, `components`), `targets`(대상별 `target`, `environment_id`, `run`, `status`, `reason`, `service_url`). 상태 `planned`, `queued`, `running`, `succeeded`, `partial`, `failed`. `succeeded`는 대상 앱 배포 완료이며 트래픽 이전 완료가 아니다.
- `POST /api/v1/deployment-plans/{id}/execute`: `{input_hash}`. 계획 검토 후 배포 실행. 대상별 새 실행을 한 번만 접수한다. 설정/검사 변경 시 새 계획을 요구한다.
- `GET /api/v1/deployment-runs/{id}/events`: 기존 실행 이벤트 형식.

화면에서는 등록 환경만 선택하게 하지 않고 카탈로그에서 새 대상을 선택한다. 실제 환경은 실행 결과가 검증된 뒤 자동 연결한다. 연결되지 않은 환경도 설정 선택·계획 확인은 가능하지만 실행은 차단하고 이유를 표시한다. 원본 환경과 DB·트래픽은 자동 변경하지 않는다.
