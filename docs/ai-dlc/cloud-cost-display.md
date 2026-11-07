# 실행별 클라우드 매핑·가격 비교 표시

기준: 2026-11-07 Asia/Seoul. 사용자가 설명한 `실제 매핑 호출 → 실행 기록 저장 → UI 비교표` 구현을 승인했다. 별도 `codex/cloud-cost` 체크아웃에서 최신 origin/main과 기존 사례 콘솔 변경을 보존 통합했다. 운영 배포·원격 게시 상태와 로컬 검증은 구분한다.

## Ideation · 승인된 범위

- 문제: delta-ai는 `mapping.cost`를 반환하지만 현재 웹 그래프는 매핑을 호출하지 않고 가격을 표시하지 않는다.
- 사용자: 실행 전 클라우드 서버 비용을 비교하고 실행 후에도 같은 근거를 다시 보는 운영자.
- 성공 기준: 새 실행에서 정규화한 Terraform 추출 JSON을 선택하면 실제 delta-ai 매핑 응답의 서버별 월 비용·합계·차액을 저장하고 화면에서 재조회한다.
- 범위: 매핑 호출·실행별 영속 기록·기존 조회 API·오케스트레이터 UI·검증.
- 제외: 실시간 가격 수집, 전체 청구액 계산, Terraform state 자동 다운로드, Terraform 생성·Apply, 운영 서비스 변경, 팀 platform/SAST/DAST 코드 수정.

## Inception · 설계와 결정

사용자가 승인한 기존 연결 방법을 구현한다. 새 실행에 선택적 `mapping_request`를 추가하고, 기존 `prepare` 뒤·빌드/SAST 전에 읽기 전용 `map_resources` 노드를 실행한다. 이 결정은 기존 ADR-AI-39의 서버 비용 추정 계약을 확장해 소비하는 것이며 새로운 가격 모델을 만들지 않는다.

1. 추출기 출력(`kind=migration_mapping`, `service`, `evidence.mapping`)만 받는다. 원본 state, 다른 kind/근거, 알 수 없는 필드는 거부한다. 최대 60KB, 서버·서브넷 각각 100개다.
2. 실행 요청의 저장소·커밋으로 입력을 결합한다. 업로드가 다른 저장소·커밋을 지정하면 거부한다. 매핑 assessment ID는 서버가 `map-{run_id}`로 정한다.
3. 서버 설정의 HTTPS 주소·토큰으로 `POST /v1/assessments`를 호출한다. 기존 진단용 안전한 HTTP 어댑터를 재사용한다. URL은 브라우저가 지정할 수 없고 토큰은 실행 기록에 저장하지 않는다.
4. 응답 스키마·kind·assessment ID·mapping 존재 여부를 검증한다. 검증된 응답만 `nodes[id=map].detail.mapping`에 저장한다. 입력 해시와 방향·판정도 보존한다.
5. Board의 기존 atomic replace와 기존 `GET /api/pipeline/runs/{run_id}`를 사용한다. 그래프 체크포인트 전 강제 종료돼도 같은 해시의 저장된 결과는 재사용한다.
6. UI 상단의 ‘VM 예상 비용’을 누르거나 ‘매핑 · 가격 비교’ 노드를 선택하면 상세 표가 나온다. 서버별 출발·대상 타입/월 비용/증감, 합계, 리전, 통화, 월 시간, 단가 기준일, 제외 항목과 기본값 가정을 표시한다. `null`을 0으로 바꾸지 않는다.

가격은 참고용이다. 매핑 `needs_human`/`fix`는 노드 경고와 원래 판정으로 보존하며 인프라 적용을 승인하지 않는다. 기존 웹 검증과 배포 승인 게이트는 그대로다. 매핑 호출/응답 실패는 가격 없음이나 0원이 아닌 실패로 기록하고 빌드 전에 중단한다. 매핑 입력을 선택하지 않은 실행과 과거 실행은 기존 흐름을 유지한다.

## Construction · 사용 방법

1. 출발 Terraform root의 `terraform show -json`을 기존 추출기에 전달한다. 원본 show/state에는 비밀이 있을 수 있으므로 UI에 올리지 않는다.

```bash
python services/delta-ai/scripts/extract_terraform.py \
  --show /secure/path/terraform-show.json \
  --service my-service --out /secure/path/mapping-request.json
```

2. 추출 JSON의 `evidence.mapping.source_provider`, `target_provider`, `source_region`, `region`과 서버 목록을 확인한다. 생략한 리전은 기존 delta-ai 기본값을 사용하고 화면의 가정에 표시한다. 추출기가 지원하지 않는 출발 환경은 기존 `MappingInput` 계약으로 정규화해 제공한다.
3. 서버에 `DELTA_MAPPING_URL`, `DELTA_MAPPING_TOKEN`을 설정한다. 사설 CA를 사용하면 `DELTA_MAPPING_CA_BASE64`도 설정한다. 생략 시 각각 기존 `DELTA_DIAGNOSTIC_URL`, `DELTA_DIAGNOSTIC_TOKEN`, `DELTA_DIAGNOSTIC_CA_BASE64`를 재사용한다. 원격 주소는 HTTPS만 허용하며 로컬 검증은 루프백 HTTP가 가능하다. 인증서 검증과 redirect 거부를 유지한다.
4. ‘새 실행’에서 저장소·커밋과 ‘가격 비교 (선택)’의 추출 JSON을 선택하고 실행한다. 계산이 끝나면 상단 비용 버튼에서 상세를 연다. API를 직접 쓰면 기존 실행 생성 JSON에 `mapping_request`를 포함한다.
5. 과거 실행은 자동으로 다시 계산하지 않는다. 입력을 선택해 새 실행을 만들면 새로운 assessment와 기록을 생성한다. 입력을 바꾸고 같은 idempotency key를 재사용하면 409다.

예상 비용은 코드에 저장된 2026-11-02 단가와 월 730시간·USD 기준이다. 최신 가격이나 VM 성능 동등성을 보증하지 않으며 디스크·네트워크·NAT·공인 IP·보안 도구·할인은 제외한다.

## Test · Review

검증 명령:

```bash
DIAGNOSTICS_PYTHON=/path/to/validated/python bash deploy/orch-diagnostics/check.sh
```

검사기는 AI, pipeline, orchestrator 회귀와 JS 문법 검사·가격 렌더러의 Node 테스트를 수행한다. 주요 수용 기준은 실제 AI API의 정방향/역방향 비용, 기록 재조회와 호출 재사용, 누락 가격, 잘못된 응답/연결 실패, 원본 state·크기 제한, 인증·교차 출처 거부, 요청 키와 입력 결합, HTML escaping이다.

실제 브라우저 검증과 최종 실행 결과는 아래 검증 기록에 추가한다. 로컬 검증은 실제 delta-ai 로직과 로컬 HTTP를 사용하되 빌드·SAST·DAST·배포는 검증용 대역으로 운영 자원을 변경하지 않는다.

## Operation

현재 작업은 소스 연결과 로컬 검증이다. AWS 서비스 교체·클라우드 리소스 변경·main 반영·push/PR·팀 Notion 수정·Slack 전송은 수행하지 않았다. 운영에 표시하려면 이 변경을 포함하는 pipeline/orchestrator 이미지와 위 서버 설정을 함께 릴리스해야 한다. 기존 실행 JSON은 보존한다. 되돌릴 때는 기능을 사용한 미완료 실행이 없는지 확인하고 기존 버전/설정으로 복귀한다. 새 옵션이 포함된 실행은 이전 버전의 unknown-options 보호로 자동 재개하지 않는다.


### 2026-11-07 로컬 검증 결과

- `deploy/orch-diagnostics/check.sh`: AI **346 passed**, pipeline **211 passed**, orchestrator **20 passed**. 가격 렌더러의 Node 검사 **4 passed**, JS 구문·컴파일·`git diff --check` 통과.
- 최종 경고 상태 표시 변경 뒤 `test_mapping_flow.py` **12 passed**와 렌더러 **4 passed**를 재확인했다. 공개 인증/CSRF 거부, 잘못된 입력/원본 state 400, 크기 초과 413, 변경된 요청 키 409, 기록 재조회는 회귀에 포함된다.
- Playwright + 실제 Chrome에서 추출 JSON 파일 선택 → 실제 루프백 HTTP `POST /v1/assessments` → Board 저장 → 기존 조회 API → 상단 비용 버튼/상세 비교표를 확인했다. 검증 실행 `ba9e8fe4cfe144e6a0532fb5ccb19856`. 로컬 delta-ai는 실제 규칙·가격 계산을 사용했고 LLM은 끈 상태다. 나머지 파이프라인 외부 도구는 대역이다.
- 이 입력의 저장된 단가 기준 출력은 AWS `t4g.nano` **USD 3.80/월** → GCP `e2-micro` **USD 7.84/월**, 차액 **USD 4.04 증가**였다. 성능·메모리 차이 주석, 단가 기준일, 제외 비용을 함께 표시했다. 이 값은 최신 클라우드 가격 조회 결과가 아니다.
- 새로고침 전후 입력 해시·매핑 결과가 동일했다. 모바일 **390px**에서 페이지 폭 **390px**, 표 내부 스크롤 가능, JS 오류 **0건**. 데스크톱은 **1600×1050**에서 표의 전체 열을 확인했다. 검증 스크립트/JSON/스크린샷은 로컬 `.validation/cloud-cost/`에 보관한다.
- 운영 AWS/GCP API·리소스·팀 서비스와 CI는 이번 기능 검증에서 호출하거나 변경하지 않았다. 원격 main fetch와 로컬 base 통합만 수행했고 기능 커밋·push·PR·운영 배포는 수행하지 않았다.
