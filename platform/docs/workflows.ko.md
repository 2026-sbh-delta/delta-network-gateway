# 배포·이전 작업의 식별과 승인

하나의 서비스가 AWS에서 GCP로 옮겨져도 `service_id`는 유지한다. 실행 위치는 `environment_id`, 한 번의 배포·이전 계획은 `workflow_id`, 각 도구 호출은 `run_id`로 구분한다. AI의 `assessment_id`는 해당 도구 호출의 `run_id`와 같다.

| 식별자 | 범위 |
| --- | --- |
| `project_id` | 현재 고정 로그인으로 사용하는 팀 공통 작업 공간 |
| `service_id` | GitHub 저장소로 등록한 고객 서비스 |
| `environment_id` | 운영자가 등록한 서비스의 AWS·GCP·온프레미스 환경 |
| `workflow_id` | 빌드·출발 환경·대상 환경을 고정한 한 번의 계획 |
| `run_id` / `assessment_id` | 계획 아래의 개별 검사·배포·이전 실행 |

기존 빌드·SAST·DAST의 `runs` 이력은 보존한다. 새 workflow가 성공한 빌드 ID를 참조하며, AI 등 후속 실행은 `workflow_executions`에 저장한다. UUID를 아는 것 자체가 권한은 아니다. 현재는 공통 작업 공간이며 고객별 계정·RBAC가 완성된 상태는 아니다.

## 사용 흐름

1. 운영자가 `platform-environment`로 실제 자원과 서비스·환경을 연결한다. 웹에서 임의 계정·VM을 등록할 수 없다.
2. 개발자가 서비스 상세에서 성공한 빌드와 출발·대상 환경을 선택해 계획을 만든다. 서버가 정확한 커밋·이미지 digest·환경 연결 해시를 저장한다.
3. 같은 요청 키와 같은 입력은 같은 계획/실행을 반환한다. 입력을 바꾸고 같은 키를 재사용하면 409 오류가 난다. 변경된 코드나 근거의 검사는 새 키·실행 ID로 만든다.
4. 계획 승인은 로그인한 사용자의 ID와 검토한 `input_hash`를 저장한다. 승인 후 대상 자원 연결이 바뀌면 실행을 거부한다.
5. 변경 실행은 계획의 `actions`에 등록한 대상·종류·입력 해시와 일치해야 한다. AI가 통과라고 답했다고 새로운 인프라 변경이 승인되지는 않는다.

**현재 웹은 위치·빌드 계획을 작성하고 승인 기록을 남긴다. 실제 배포·DB 전환 실행기는 아직 연결되지 않았다.** 웹에서 만드는 계획에는 변경 `actions`가 없어 인프라 변경 권한이 생기지 않는다. 실제 Terraform Apply를 연결할 때에는 검토할 Plan 원문/변경 목록과 저장 Plan의 SHA256을 제공하고, 그 입력을 승인한 action에 연결해야 한다. `input_hash`를 Terraform 바이너리 Plan의 해시와 혼동하지 않는다.

## 팀 AI 도구 연결

`AssessmentClient.execute(workflow_store, workflow_id, environment_id, evidence, request_key)`를 Worker/운영 코드에서 호출한다. 운영자가 설정하는 `PLATFORM_AI_URL`, `PLATFORM_AI_TOKEN`, 필요 시 `PLATFORM_AI_CA_BASE64`를 사용한다. 토큰은 SecureString/환경 설정으로 전달하며 고객 입력으로 URL·토큰을 받지 않는다. 실제 AI 서버 설정 활성화는 아직 하지 않았다.

플랫폼은 서비스/환경 소유 관계와 커밋·이미지·계획을 먼저 확인한다. 서비스·저장소·커밋은 저장한 빌드에서 채우고, 관측 근거의 공급자도 계획과 대조한다. 요청에 다음 `execution_context`를 보낸다.

- `project_id`, `service_id`, `workflow_id`, `run_id`, `environment_id`, `source_environment_id`
- `source_provider`, `target_provider`, `repository_url`, `commit_sha`, `image_digest`
- `plan_hash`: 플랫폼이 저장한 전체 계획의 해시
- `payload_hash`: `assessment_id`와 `execution_context`를 제외한 JSON 요청 본문의 SHA256. 키 정렬·ASCII 이스케이프·공백 없는 JSON을 사용한다.

AI API는 ID·저장소·커밋·공급자·입력 해시를 검증하고 결과에 같은 context를 돌려준다. 플랫폼은 호출 전후에 연결을 다시 검증한 뒤 결과를 저장한다. 응답을 다른 workflow에 붙이거나 완료된 결과를 다른 내용으로 덮어쓸 수 없다. 이 검증이 실제 VM의 이미지나 Terraform 적용 결과를 대신 검증하는 것은 아니다. 각 실행기가 실제 결과를 추가 대조해야 한다.

AI API의 기존 context 없는 호출은 팀의 기존 통합을 위해 유지한다. context가 있는 호출은 규칙 저장 파일 옆 `*-assessments/`에 요청 해시와 완료 결과를 보존한다. API를 다시 시작해도 같은 입력·ID의 결과를 재사용하며, 변경한 입력의 같은 ID는 거부한다. 원본 요청과 비밀값은 이 ledger에 저장하지 않는다. 결과에는 검사 근거가 있을 수 있으므로 디렉터리를 비공개 영속 볼륨에 둔다. **현재 AI API는 프로세스 하나를 전제로 한다.** 여러 프로세스로 확장할 때는 이 파일 ledger와 규칙 저장소를 공통 DB로 바꿔야 한다.

## 확인한 범위

실제 PostgreSQL을 사용하는 플랫폼 테스트, 실제 팀 AI FastAPI와 플랫폼 어댑터를 연결한 통합 테스트, 브라우저 계획·승인 테스트를 실행했다. AI 통합 테스트는 외부 LLM 없이 규칙 판단을 사용하며, 미확인 인벤토리는 `needs_human`으로 남는다. 실제 고객 서비스 이전·Terraform Apply·AI 수정 코드 적용까지 완료했다는 뜻은 아니다.
