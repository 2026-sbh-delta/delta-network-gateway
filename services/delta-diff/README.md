# 차분 에이전트 (delta-diff)

AWS/GCP의 S3 Terraform state 또는 온프레미스 k3s YAML을 읽어, 다른 대상 환경(AWS/GCP/k3s)에 무엇을 만들지 매핑표로 제안한다. 제안만 하고 아무것도 만들지 않는다. 적용은 승인 뒤 Jenkins 가 한다.

```text
오케스트레이터 ─ POST /v1/diffs ─▶ delta-diff ─ s3:GetObject(허용 목록) ─▶ Terraform state
                                      │  state → 매핑 입력 추출(정해 둔 속성만)
                                      └─ delta-ai 매핑 판단(같은 프로세스, 자체 LLM 키) ─▶ 매핑표·판정
```

- 매핑 규칙·대응표·판단은 [delta-ai](../delta-ai/README.md) 코드를 라이브러리로 가져다 쓴다(`migration_mapping`). 이 서비스가 더하는 것은 state 읽기와 추출이다.
- state 원본은 메모리에서만 다루고 응답·로그에 남기지 않는다. 응답에는 추출한 매핑 입력과 어떤 state 를 읽었는지(bucket·key·serial·lineage)만 싣는다.
- 허용 목록(`DELTA_DIFF_STATE_SOURCES`) 밖의 위치는 S3 를 부르기 전에 거부한다. 서버 IAM 도 같은 목록만 읽을 수 있다.

## 11/07 IAM 수집과 시연 범위

AWS 인스턴스 프로파일에서 역할·인라인 정책·state에 있는 관리형 정책을 연결해 `servers[].permissions`에 동작 이름만 추출한다. 외부 정책을 읽지 못하거나 경계·Deny·조건이 있으면 부분 허용 목록을 만들지 않고 `unknown`을 유지한다. 정책 원문·리소스 ARN·조건 값은 응답에 싣지 않는다. 이는 설정된 동작의 수집이며 유효 권한이나 최소 권한 동등성의 검증은 아니다. 대응표 밖 동작은 기존처럼 `gap`이다.

`DELTA_DIFF_DEMO_WORKLOAD_SCOPE=1`의 지정된 workload-client state, `delta-sample-app` 서비스, AWS→GCP 방향에서만 Lambda 플랜을 이번 앱 이전 범위에서 제외한다. state에 Lambda 함수가 생기면 제외하지 않는다. 제외 이력은 `source.excluded_capabilities`에 남기며 RDS·S3 보호와 SCC 미확인은 유지한다. 기존 TGW 전용 두 서브넷 제외도 유지한다. [설계·검증·적용 상태](../../docs/ai-dlc/diff-iam-scope.md).

## API

`POST /v1/diffs` (Bearer 토큰)

```json
{
  "diff_id": "run-42-map",
  "service_id": "workload-server",
  "source": {"state": {"bucket": "sbh-delta-workload-tf-616723161368", "key": "delta/lab/workload-server/terraform.tfstate"},
             "region": "ap-northeast-2", "exclude_subnets": ["-tgw"]},
  "target": {"region": "asia-northeast3"},
  "accepted_gaps": [],
  "scc_tier": null
}
```

응답: `{diff_id, source: {bucket, key, version_id, serial, lineage, terraform_version, resource_count, excluded_capabilities}, mapping_input, assessment}`. `assessment` 는 delta-ai `AssessmentResult` 그대로다(`mapping.rows`·`verdict`·`reasons`).

| 코드 | 뜻 |
|---|---|
| 401 | 토큰 없음·틀림 |
| 403 | 허용 목록 밖 state |
| 404 | state 없음 |
| 413 | 요청 64KB 초과, state 20MB 초과 |
| 422 | 요청 형식 오류, state 형식이 4 가 아님, 옮길 서브넷·서버·보안 도구가 없음 |
| 502 | S3 권한·연결 오류 |
| 503 | LLM 일시 오류 |

## 환경 변수

| 이름 | 기본값 | 역할 |
|---|---|---|
| `DELTA_DIFF_TOKEN` | 필수 | 호출 토큰 |
| `DELTA_DIFF_STATE_SOURCES` | 필수 | `버킷/접두어` 쉼표 목록. 접두어 아래 `.tfstate` 만 읽는다 |
| `AWS_REGION` | — | S3 클라이언트 리전 |
| `DELTA_LLM` | `none` | `none`·`fake`·`claude`. `claude` 면 `ANTHROPIC_API_KEY` 필요 |
| `DELTA_LLM_TIMEOUT_SECONDS` | `60` | LLM 호출 제한 시간 |
| `DELTA_RULE_STORE` | `var/rule_candidates.json` | delta-ai 판단이 쓰는 저장 위치 |

## 개발

```bash
uv run --project services/delta-diff --python 3.12 --frozen --extra dev pytest -q services/delta-diff/tests
docker build -f services/delta-diff/Dockerfile services   # 컨텍스트는 services/
```

CLI 로 `terraform show -json` 결과에서 delta-ai 요청을 만들 수도 있다.

```bash
uv run --project services/delta-diff python -m delta_diff.terraform --show show.json --service web --exclude-subnet -tgw --out request.json
```

시연의 실제 서비스와 복구 절차는 [IAM·범위 검증 기록](../../docs/ai-dlc/diff-iam-scope.md)을 따른다. 고객 앱 운영 배포와 DNS 전환은 별도 승인 단계다.

## LB·PostgreSQL·k3s 확장 (2026-11-05)

기존 AWS→GCP 요청과 S3 허용 목록을 유지한다. 클라우드 출발은 `source.provider=aws|gcp`와 `source.state`를 쓴다. GCP state도 S3에서 읽으며 별도 GCS 클라이언트는 없다. 대상은 `target.provider=aws|gcp|onprem`이다. 같은 provider 간 이전은 받지 않는다.

- LB: AWS ALB/NLB/Classic의 리스너와 GCP forwarding rule/proxy를 추출한다. Application LB↔ALB, Network LB↔NLB, k3s Service/Ingress를 제안한다. 모르는 종류·리스너·공개 범위·TLS는 unknown이다.
- DB: RDS/Cloud SQL의 일반 PostgreSQL 버전·할당 스토리지·HA를 추출한다. 비밀번호/사용자/연결 문자열은 복사하지 않는다. Aurora·다른 엔진은 gap이다.
- YAML: `source.provider=onprem`이면 `source.state` 대신 `source.manifests`에 YAML/JSON 문자열을 넣는다. `target.provider=onprem`이면 `target.manifests`로 대상의 선언 구성을 함께 비교할 수 있다. YAML을 apply하거나 kubeconfig/SSH/외부 URL을 받아 실행하지 않는다.
- 지원 kind: Pod/Deployment/StatefulSet/DaemonSet/Job/CronJob, Service/Ingress, PVC/volumeClaimTemplates, NetworkPolicy. Secret/ConfigMap/Namespace/ServiceAccount는 값 추출 없이 제외하고 나머지 미지원 kind는 gap으로 남긴다.
- YAML 원문은 응답·로그·LLM 입력에 남기지 않는다. source 메타데이터는 `{kind: kubernetes_yaml, sha256, resource_count}`다. `mapping_input.kubernetes`에는 허용한 구성 사실만 있다. `target_manifest`에는 대상 YAML 해시가 있다.
- 총 HTTP 본문 64KB, YAML당 60KB, 객체 200개, 중첩 40단계를 제한한다. 별칭/앵커/중복 키와 잘못된 타입은 원문 없는 422다.

온프레미스 출발 예시([입력 YAML](examples/notes-k3s.yaml)):

```python
from pathlib import Path

request = {
    "diff_id": "run-42-map",
    "service_id": "notes",
    "source": {"provider": "onprem", "manifests": Path("notes-k3s.yaml").read_text()},
    "target": {"provider": "gcp", "region": "asia-northeast3"},
}
```

AWS→k3s는 기존 source.state를 유지하고 `target={"provider":"onprem", "manifests": yaml_text}`를 보낸다. 대상 YAML이 없으면 구성 확인이 필요한 항목을 unknown으로 남긴다. PostgreSQL 주 버전 불일치·할당 스토리지 부족·관리형 HA 손실은 gap이다. PVC는 PostgreSQL 컨테이너에 실제 마운트된 선언만 센다. 단순 replicas 증가는 PostgreSQL HA 증거가 아니다.

매핑 결과는 실제 배포/서비스 정상/DB 이전 완료가 아니다. 확장·콜레이션·TLS·권한·외부 진입·CNI·스토리지 실측은 required_tests에 따라 확인한다. 현재 요청당 source state 하나를 읽고, 클라우드 실제 대상 전체를 자동 탐색하지 않는다.

계약: [요청 스키마](schema/diff-request.schema.json) · [응답 스키마](schema/diff-result.schema.json). 갱신: `uv run --project services/delta-diff python services/delta-diff/scripts/export_schema.py`.

## 팀 오케스트레이터 연결

`delta_diff.client.DiffClient`가 HTTPS/CA·토큰·리다이렉트 차단·응답 크기·전체 계약·실행/state/양방향 YAML 해시를 검증한다. 실제 `/healthz`는 `service=delta-diff`, `api_version=v1`이며 임시 200 서버는 readiness를 통과하지 못한다. `python -m delta_diff.check_connection --help`로 호출자 점검 옵션을 확인한다.

서버의 `DELTA_DIFF_STATE_ROLE_ARN`과 `DELTA_DIFF_STATE_EXTERNAL_ID`를 설정하면 운영자가 고정한 역할로 매 read마다 새 STS 세션을 받아 읽는다. `DELTA_DIFF_STATE_SOURCES`는 실제 bucket/key allowlist이며 IAM 정책을 대신하지 않는다. 요청자가 임의 역할을 선택할 수 없다. 이미지 내부 포트 8091과 팀 호스트 포트 8095 매핑을 맞춘다. [통합·배포 조건](../../docs/ai-dlc/diff-orchestrator-integration.md)을 함께 확인한다.
