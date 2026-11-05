# 온프레미스 발표 앱 실행

기존 `demo`와 별도의 `delta-notes` 공간을 사용한다. 현재 샘플 앱은 메모리 저장 FE·BE이며 DB와 직접 OTel 계측 코드는 없다. 수정 버전 후보는 중지 상태다. 임시 검사는 `delta-platform-sandbox`에서 한 번에 하나만 실행한다. VM·VPN·팀의 SAST/DAST 서버·S3 state는 이 도구가 관리하지 않는다.

## 구성 파일과 책임

| 경로 | 관리 대상 |
|---|---|
| `manifests/base/` | 샌드박스 namespace, 계정, RBAC, Quota |
| `manifests/sandbox/` | 고정 Service, 기본 차단, FE→BE·DNS 허용 |
| `manifests/runtime/` | 운영 후보의 FE·BE, 접근 정책, 선택 PostgreSQL |
| `inventory/` | 비밀값 없는 환경 구성 예시 |
| 플랫폼 Worker | 실행별 FE·BE Pod와 ECR 인증 생성·정리 |

YAML의 `RELEASE_*`/`POSTGRES_IMAGE_DIGEST`는 템플릿이다. 원본을 그대로 apply하지 않고 아래 렌더러를 사용한다. DB 비밀번호와 ECR 인증은 실행할 때 생성하며 Git에 넣지 않는다. 고정 YAML과 임시 실행기가 같은 Pod를 관리하지 않는다.

## FE·BE 빌드와 검사 요청

서비스를 FE(`frontend/`, `frontend/Dockerfile`, 3000)와 BE(`backend/`, `backend/Dockerfile`, 8080)로 등록해 Jenkins에서 빌드한다. 같은 프로젝트·공개 저장소·전체 소스 커밋·amd64 빌드여야 한다. 첫 묶음은 Delta Notes의 `/api/health` 계약을 사용한다.

새 플랫폼 코드 배포 후 로그인/CSRF/Idempotency-Key가 있는 기존 검사 API에 다음 본문을 보낸다.

```json
{
  "build_id": "FE의 성공한 빌드 UUID",
  "backend_build_id": "BE의 성공한 빌드 UUID",
  "execution_cloud": "onprem"
}
```

주소는 `POST /api/v1/services/{FE service_id}/sandbox-checks`다. 결과는 두 이미지, Pod UID, 개별 빌드/서비스 ID, 프로젝트 ID, `release_sha256`, FE→BE 검사, 정리 결과를 포함한다. 워크플로·환경 ID는 기존 워크플로 연결을 통해 이어야 하며 이 API가 이전·승인을 자동 완료하지 않는다. 프론트 화면의 묶음 선택은 별도 UI 작업이다.

FE NodePort는 32080이다. BE는 샌드박스 내부 Service만 가진다. 현재 AWS 사설 경로가 준비되지 않아 실검증은 SSH 관리 경로를 사용했다. 이를 AWS Worker→온프레미스 경로 검증으로 표시하지 않는다.

## 정책 변경과 AI 입력

```bash
uv run --project platform/deploy python platform/deploy/onprem/collect-state.py \
  --host '<user>@<host>' --key ~/.ssh/teammate_vm \
  --desired '<렌더링한 YAML>' --output '<상태 결과.json>'
```

원하는 YAML 해시, 실제 Pod/Deployment/Service/정책/권한, 노드 용량, 지원·미지원 기능을 수집한다. 기존 `delta_ai.adapters.cluster_facts`를 재사용하며 Secret·환경변수 값·마지막 apply 주석은 보내지 않는다. `--source-inventory <출발 환경 JSON> --service-id <UUID>`를 함께 주면 팀 AI의 `migration_parity` 요청 계약으로도 출력한다. 출발 환경을 모르면 추측해서 채우지 않는다. 이 명령은 AI API를 호출하거나 판정을 자동 승인하지 않는다.

`data.md`에 안내된 차분 에이전트 연결 정보는 `/delta/diff/connection`, AI 판단 호스트는 `/delta/ai/connection`에 있다. 기존 AWS→GCP 매핑을 온프레미스 지원이라고 표시하지 않는다. 이번 정책 실행기는 팀의 AI가 만든 YAML도 아래 제한을 만족할 때만 받는다.

```bash
uv run --project platform/deploy python platform/deploy/onprem/policy-change.py plan \
  --host '<user>@<host>' --key ~/.ssh/teammate_vm --plan '<변경안.json>'
# 사람이 내용·대상·영향을 검토한 뒤 출력된 SHA256으로 승인한다.
uv run --project platform/deploy python platform/deploy/onprem/policy-change.py apply \
  --host '<user>@<host>' --key ~/.ssh/teammate_vm --plan '<변경안.json>' \
  --approved-sha256 '<승인한 변경안 SHA256>'
```

`--candidate <YAML>`은 외부 제안 입력이다. 이번 실행기는 지정 샌드박스의 FE→BE:8080 규칙만 허용하며 다른 namespace·전체 공개·범위 확대를 거부한다. 승인 후 실제 정책의 resourceVersion이 바뀌면 다시 검토해야 한다. 실검증은 검토된 템플릿으로 수행했고, AI가 생성했다고 기록하지 않았다.

## 운영 후보 준비

아래 DB 옵션과 복원 도구는 DB 지원 앱을 별도로 확정한 경우에만 사용한다. 현재 샘플 앱에 PostgreSQL을 연결하면 데이터가 유지된다고 가정하지 않는다.

DAST·승인 통합이 준비되지 않은 상태에서는 `--preview`를 사용하며 보안 상태를 `pending`으로 표시한다. 보안 검사를 생략한 결과를 통과로 바꾸지 않는다. 이 CLI는 서버 등록 자동화와 실제 서비스 도메인 전환을 수행하지 않는다.

```bash
uv run --project platform/deploy python platform/deploy/onprem/registry-auth.py \
  --profile delta-platform --instance '<플랫폼 VM ID>' --output '<비공개 ECR 인증.json>'
uv run --project platform/deploy python platform/deploy/onprem/prepare-runtime.py \
  --host '<user>@<host>' --key ~/.ssh/teammate_vm --registry-auth '<비공개 ECR 인증.json>' \
  --result '<완료된 샌드박스 결과.json>' --output '<운영 후보.yaml>' --preview \
  --postgres-image 'postgres@sha256:<검증한 PostgreSQL 16 Alpine digest>'
```

PostgreSQL을 선택하면 전용 DB·PVC 2GiB와 제한된 앱 DB 계정을 준비하고 BE를 중지한다. DB 연결이 설정된 BE나 기존 DB 테이블이 있으면 초기 준비를 거부한다. 이후 앱 갱신은 DB 연결을 유지한 저장된 운영 YAML로 수행한다. `demo` DB를 재사용하거나 삭제하지 않는다. 운영 이미지의 인증은 ECR이므로 새 노드 pull·재배포 전 `registry` Secret을 갱신한다. `registry-auth.py`는 플랫폼 VM의 읽기 역할에서 토큰을 발급하고 SSM에는 암호문만 남긴다. 관리자 프로필에서 발급한 광범위한 ECR 토큰을 저장하지 않는다. 갱신 시 같은 명령에 `--sync-host <SSH 주소> --key <키 경로>`를 추가하면 해당 Secret만 교체한다. 자동 갱신은 사설 Worker 경로 연결 후 작업이며 지금은 배포 전 명시적으로 실행한다. 기존 실행 Pod는 ECR 토큰 만료 때문에 자동 중지되지 않는다.

소스 앱 쓰기를 중단하고 **같은 PostgreSQL 16**에서 `pg_dump -Fc --no-owner --no-acl`로 덤프한다. 새 데이터가 들어오지 않음을 확인한 후 덤프 SHA256을 승인한다.

```bash
uv run --project platform/deploy python platform/deploy/onprem/restore-db.py \
  --host '<user>@<host>' --key ~/.ssh/teammate_vm --dump '<덤프 파일>' \
  --approved-sha256 '<덤프 SHA256>' --output '<복원 결과.json>'
```

도구는 전용 빈 `notes` DB, 중지된 대상 BE, PostgreSQL 16을 확인한다. 실패 시 트랜잭션을 되돌리고 기존 데이터는 덮어쓰지 않는다. 이후 BE replicas를 1로 반영하고 FE의 `/api/health`, 기존 메모, 새 쓰기, BE 재시작 후 데이터를 확인한다. 승인한 YAML에도 replicas=1을 기록한다. 백업·데이터 정책은 별도 검토 대상이며 새 DB 쓰기 시작 후 단순 주소 원복을 데이터 복구라고 부르지 않는다.

## 관측과 접속

앱 계측은 Oneport처럼 플랫폼이 배포 시 지원되는 런타임에 자동 계측 라이브러리를 연결하는 방향으로 전환한다. 자동 주입은 아직 구현하지 않았다. 현재 샘플 앱에 `OTEL_EXPORTER_OTLP_ENDPOINT`만 설정해도 trace가 생성되는 것은 아니다. 호스트 지표는 기존 native Collector 설정을 사용한다. 인증된 AWS gateway와 실제 왕복 경로 확인 전에는 중앙 전송 성공·대시보드 표시 완료로 기록하지 않는다. 설정은 [관측 문서](../../observability/onprem/README.md)를 따른다. 앱과 호스트 수집 인증을 공유하지 않는다.

예약된 후보 주소는 사설 `10.40.0.10:32081`이며 현재 앱은 중지되어 응답하지 않는다. 원복된 이미지로 다시 배포한 뒤 Tailscale에 연결된 개발 PC에서 아래 SSH 터널로 확인할 수 있다.

```bash
ssh -i ~/.ssh/teammate_vm -N -L 18081:10.40.0.10:32081 onprem@delta-onprem.tail6bd796.ts.net
# 브라우저: http://localhost:18081
```

인터넷 공개나 서비스 도메인 전환은 수행하지 않았다. 최종 활성 쓰기 대상은 하나를 정하고 GCP·온프레미스 독립 DB에 동시 쓰기를 분배하지 않는다.

## 실패 시 처리

- 샌드박스 삭제 확인이 실패하면 실행 잠금을 유지한다. 연결/권한을 복구한 뒤 동일 실행의 정리를 재시도한다.
- 운영 앱 문제는 이전 승인 YAML·이미지로 복구하되 DB 호환성과 신규 쓰기를 먼저 확인한다.
- DB/PVC/namespace 전체 삭제를 복구 방법으로 사용하지 않는다.
- 단일 VM의 완전 장애, 무중단 이전, 악성 고객 코드의 강한 격리는 보장하지 않는다.
