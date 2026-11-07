# 웹 검사와 병렬로 실행하는 워크로드 차분

2026-11-07 KST. ADR 번호 미할당. 사용자 승인 범위는 기존 웹 검사·수정 루프를 보존하고, AWS → GCP Diff 결과를 운영 반영 전에 확인하는 구현과 후속 실제 시연 오케스트레이터 배포다. 기준 소스는 GitHub `main`의 `328268e`다.

## 흐름과 결정

입력 확인 후 읽기 전용 Diff 호출을 시작한다. 빌드·SAST, 스테이징·DAST와 기존 수정 후 재검증이 별도로 진행된다. 웹 흐름이 운영 배포 또는 종료로 진입할 때 같은 실행의 Diff 결과를 받는다. 기존 웹 검사와 승인 조건을 그대로 적용한다.

| Diff 결과 | 그래프 동작 |
| --- | --- |
| `pass` | 웹 검사 결과와 합류한다. 운영 배포와 도메인 전환의 기존 승인이 각각 필요하다. |
| `needs_human` | 매핑표를 보여 주고 해당 실행의 현재 결정 ID로 사람 확인을 받는다. 승인 후에도 차분 판정은 `needs_human`으로 남는다. 거절·시간 초과 시 중단한다. |
| `fix`, HTTP 오류, 시간 초과, 잘못된 응답 | 기존 delta-ai 예외 진단과 보고서 저장 경로로 보낸다. 실패 보고서를 확인한 뒤 종료한다. 운영 배포와 DNS 변경은 실행되지 않는다. |

승인 기록은 그래프 체크포인트에 남는다. 승인 대기에서 재시작해도 이미 수집한 Diff는 다시 호출하지 않는다. 프로세스가 Diff 결과 수집 전에 종료되면, 재개한 프로세스가 같은 실행 ID로 읽기 전용 비교를 다시 요청할 수 있다. 승인 범위는 해당 실행에서 받은 state serial과 고정된 요청이다. Diff 엔진의 서버 측 TGW 서브넷 제외 규칙을 그대로 사용한다.

## 오케스트레이터 설정

설정은 운영자가 지정한다. 브라우저 요청은 Terraform state 위치, 연결 토큰 또는 차분 승인 조건을 고를 수 없다. 서비스 ID가 일치하는 GCP 웹 실행에 차분을 연결한다. 차분을 요구하는 배포에서 이 입력과 일치하지 않는 도메인 전환 요청은 중단한다.

| 설정 | 값 |
| --- | --- |
| `DELTA_DIFF_REQUIRED` | `1`로 활성화. 생략 시 기존 웹 흐름을 사용한다. |
| `DELTA_DIFF_REQUEST_FILE` | 검토된 JSON 파일의 컨테이너 경로. 읽기 전용 마운트. |
| `DELTA_DIFF_CONNECTION_PARAMETER` | 기존 SecureString `/delta-cp/shared/diff_connection`을 사용한다. 대안으로 `DELTA_DIFF_URL`, `DELTA_DIFF_TOKEN`, 선택적 `DELTA_DIFF_CA_BASE64`를 런타임에 주입한다. |
| `DELTA_DIFF_ALLOW_PRIVATE_HTTP` | 현재 사설 `http://diff.cp.internal:8095`를 사용할 때만 `1`. 그 외 연결에는 HTTPS가 필요하다. |
| `DELTA_DIAGNOSTIC_URL`, `DELTA_DIAGNOSTIC_TOKEN` | 기존 delta-ai 예외 진단 연결을 사용한다. |

요청 파일 예시이며 실제 배포의 승인된 워크로드 객체로 대체한다.

```json
{
  "service_id": "delta-sample-app",
  "source": {
    "provider": "aws",
    "state": {"bucket": "demo-workload-state", "key": "workload/terraform.tfstate"},
    "region": "ap-northeast-2"
  },
  "target": {"provider": "gcp", "region": "asia-northeast3"}
}
```

실행 ID는 오케스트레이터가 넣는다. 승인된 요청 해시는 실행과 함께 저장한다. 오케스트레이터 교체 및 설정 활성화 전에는 진행 중인 도메인 전환을 마무리하고, 기존 state 디렉터리를 보존한다.

## 검증

2026-11-07에 `pytest -q services/delta-pipeline/tests services/delta-orchestrator/tests`를 실행해 251개가 통과했다. 웹·Jenkins·Diff·delta-ai·Cloudflare를 대역으로 사용해 병렬 실행, 승인·거절, 예외 진단과 전환 차단, 웹 수정 재검증, 재시작 후 승인, 다른 실행의 Diff 응답 거부를 확인했다. 이전 Diff API 코드의 로컬 `TestClient`가 생성한 `needs_human` 응답도 새 클라이언트가 검증했다. `node --check`와 `git diff --check`가 통과했다.

## 실제 시연 오케스트레이터 배포

후속 사용자 승인으로 코드 `e0c3d3a`의 변경 파일을 실행 중인 이미지 위에 적용했다. 미완료 실행 0건과 기존 소스 해시를 확인한 뒤, 별도 상태 디렉터리의 후보를 검증하고 포트 8080을 전환했다. 기존 환경 설정과 실행 기록 60개를 보존했다.

- 새 이미지 digest는 `sha256:135df51d81d83fe7e8a5b21a8abc4b8d4f9bc064e2f4d0c399fa04ad3e90b120`이다.
- 이전 컨테이너는 `delta-orchestrator-before-parallel-diff-e0c3d3a5964b`로 정지 보존했다. 상태 사본은 호스트 `/opt/delta/parallel-diff/e0c3d3a5964b/state-before`에 있다.
- 호스트 `/opt/delta/parallel-diff/e0c3d3a5964b/rollback.py`는 현재 이미지와 미완료 실행을 확인한 뒤 이전 컨테이너를 복구한다. 배포 검증 중 이 스크립트는 설치만 했다.
- 실제 검증 실행 `dfc73e0513544f5342262ba27cf63fb133d68b45009712a441ae0e2181101836`은 기존 성공한 수정 커밋 `45a6aee5`를 사용했다. 운영 배포와 DNS 전환 옵션은 껐다. 빌드·SAST·스테이징 통과 후 DAST 후보 0건을 실제 delta-ai가 분석했고, 사람 확인 대기에 도달했다. 후속 검증 결과는 해당 실행 기록을 따른다.

DNS는 별도 세션이 담당한다. 이 배포는 기존 `DELTA_CUTOVER` 값을 그대로 보존했다.
