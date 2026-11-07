# 웹 검사와 병렬로 실행하는 워크로드 차분

2026-11-07 KST. 로컬 ADR, 번호 미할당. 사용자 승인 범위는 기존 웹 검사·수정 루프를 보존하고, AWS → GCP Diff 결과를 운영 반영 전에 확인하는 로컬 구현이다. 기준 소스는 GitHub `main`의 `4e5c2a5`다.

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

실행 ID는 오케스트레이터가 넣는다. 승인된 요청 해시는 실행과 함께 저장한다. 오케스트레이터 교체 및 설정 활성화 전에는 진행 중인 도메인 전환을 마무리하고, 기존 state 디렉터리를 보존한다. AWS·DNS 변경과 공용 런타임 배포는 이 로컬 작업의 범위 밖이다.

## 검증

2026-11-07에 `pytest -q services/delta-pipeline/tests services/delta-orchestrator/tests`를 실행해 239개가 통과했다. 웹·Jenkins·Diff·delta-ai·Cloudflare를 대역으로 사용해 병렬 실행, 승인·거절, 예외 진단과 전환 차단, 웹 수정 재검증, 재시작 후 승인, 다른 실행의 Diff 응답 거부를 확인했다. 이전 Diff API 코드의 로컬 `TestClient`가 생성한 `needs_human` 응답도 새 클라이언트가 검증했다. `node --check`와 `git diff --check`가 통과했다. 배포, 실제 파이프라인 실행, DNS 변경은 검증 범위에 포함하지 않았다.
