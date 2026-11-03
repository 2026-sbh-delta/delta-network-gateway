# 고객 앱·DB 이전

구현 중이다. 현재 클라우드 이전 완료 상태가 아니다. 첫 지원 대상은 같은 스키마의 PostgreSQL 17과 DB에 세션을 저장하는 풀스택 앱이다. 앱 VM은 Terraform/CI가 준비하고 검증한 이미지 digest를 실행한다. DB 이전 중 엔진·스키마를 바꾸지 않는다.

## 이번 구현 단위

1. 독립된 DB 두 개에서 스키마·기본 키·쓰기 계정·복제 권한을 확인한다. 복제 대상 스키마는 미리 같게 만들고 대상 데이터는 비어 있어야 한다. 운영 DB를 자동으로 비우지 않는다.
2. PostgreSQL publication/subscription으로 초기 데이터와 이후 변경을 함께 복제한다. 준비 상태와 WAL 보존량을 확인한다.
3. 전환 승인 이후 기존 DB의 **모든 앱 쓰기 계정**을 잠그고 진행 중 요청을 기다린다. 기존 연결을 정리한 뒤 마지막 마커가 대상 DB에 도착했는지 확인한다. 로그인·로그아웃·세션 갱신도 같은 DB 계정이므로 함께 통제한다.
4. 데이터·스키마를 대조하고 sequence 값을 옮긴다. 복제를 멈췄는지 확인한 뒤 새 DB의 앱 계정만 연다. 전환 의도를 먼저 영속 기록해 도중에 프로세스가 죽어도 두 DB를 자동으로 쓰기 가능하게 만들지 않는다.
5. 앱은 같은 도메인·쿠키·서명 키를 사용해 새 DB로 연결한다. 프론트·백엔드가 포함된 같은 이미지로 실행하고, 기존 로그인·기존 데이터·새 저장을 확인한 후 트래픽을 전환한다. DAST와 변경 테스트는 별도 테스트 DB에서 수행한다.

한 개 DB만 쓰기 대상으로 선택한다. 앱을 GCP와 온프레미스 양쪽에서 실행해도 두 DB에 독립적으로 쓰지 않는다. 대상 DB에서 쓰기가 시작될 가능성이 생긴 이후에는 자동 원복을 거부한다. 네트워크/DNS만 되돌리는 것은 DB 복구가 아니다.

## 제한과 운영 경계

- 초기 지원은 전용 PostgreSQL 17 DB·전용 앱 계정·기본 키가 있는 일반 public 테이블이다. 대규모 DB용 고속 검증, 엔진 변환, 파티션·RLS·large object·외부 파일·외부 세션 저장소는 별도 설계다. 미지원 구조는 전환 전에 거부한다.
- 실제 인프라 공급자 연결은 사설 DB 도달 경로·인증·TLS와 해당 DB의 논리 복제 권한을 준비해야 한다. 로컬 Docker 성공을 AWS→GCP 이전 성공으로 표시하지 않는다.
- 스키마 변경과 배치/관리자의 외부 쓰기는 이전 중 중지한다. 자동 점검은 이 운영 통제를 대신하지 않는다. 앱 쓰기 계정은 해당 서비스 전용이어야 한다.
- 테스트 트래픽을 복제 중인 운영 전환 대상 DB에 쓰지 않는다. 실제 target 앱 readiness는 승격 이후 확인하고, 사전 실행·보안 검사는 별도 DB를 쓴다.
- DB 접근 비밀값은 실행 시 운영 설정으로만 전달하고 계획·화면·로그에 저장하지 않는다. DB 저장 계획은 DB 신원·스키마·환경을 고정하고, 플랫폼 workflow 계획은 별도로 이미지 digest와 승인한 실행 입력을 연결한다.
- 각 실행의 진행 기록과 승인 근거는 2시간 관측 삭제 대상이 아니다. WAL은 서버의 보존 상한과 복제 슬롯 상태를 함께 관리한다.

[PostgreSQL CREATE SUBSCRIPTION](https://www.postgresql.org/docs/17/sql-createsubscription.html)은 초기 복사와 변경 동기화를 지원한다. [논리 복제 제한](https://www.postgresql.org/docs/17/logical-replication-restrictions.html)에 따라 스키마·sequence·large object를 별도로 다뤄야 한다. [테이블 준비 상태](https://www.postgresql.org/docs/17/catalog-pg-subscription-rel.html)와 마지막 마커를 모두 확인하며, 지연 숫자 하나로 전환 가능 여부를 판단하지 않는다.

## 도구 사용

DB 접속값은 `DELTA_MIGRATION_SOURCE_DSN`, `DELTA_MIGRATION_TARGET_DSN`, `DELTA_MIGRATION_REPLICATION_DSN` 환경변수로 전달한다. 마지막 값은 출발 DB의 전용 읽기·복제 계정이다. 실환경에서는 세 연결 모두 `sslmode=verify-full`과 신뢰할 CA를 준비한다. `--local-testing`은 격리된 로컬 실험에서만 사용한다.

```bash
# 플랫폼 backend 환경에서 실행. 계획 파일은 접속 비밀값을 포함하지 않는다.
platform-db-move plan --plan /private/move/plan.json \
  --source-environment-id <출발-환경-UUID> --target-environment-id <대상-환경-UUID> \
  --writer-role app

# 계획·DB·대상을 검토한 뒤 출력된 plan_hash를 전달한다.
platform-db-move start --plan /private/move/plan.json --journal /private/move/journal.json \
  --workflow-id <workflow-UUID> --run-id <실행-UUID> --approved-hash <plan_hash>
```

같은 `--plan`, `--journal`, workflow/run ID로 `status`·`wait`를 호출해 복사 상태를 확인한다. 트래픽 진입을 대기 상태로 전환하고 모든 앱 쓰기를 통제할 준비가 되면 `promote --approved-hash <plan_hash>`를 실행한다. 승격한 DB로 새 앱을 시작하고 **실제 진입 주소의 헬스와 로그인 유지**를 확인한 뒤 트래픽을 연다. `cleanup`으로 복제 구독·슬롯·publication을 정리한다. 전환 전 중단은 `abort`이며, 새 DB가 쓰기를 받기 시작했거나 그 여부가 불확실한 `promotion_pending` 이후에는 거부한다.

도구가 진행 중인 트랜잭션이나 데이터 차이를 발견하면 쓰기 차단을 유지한다. 임의로 두 DB 계정을 모두 열지 않는다. 문제를 해결해 같은 실행을 재개하거나, 승격 전인 경우에만 명시적으로 중단·원복한다. journal은 영속 디스크에 보존한다. 현재 이 CLI는 플랫폼의 웹 Worker에 자동 연결되지 않았으며, 웹의 계획 승인 버튼으로 DB 전환을 실행하지 않는다.

`lag_bytes`는 출발 WAL과 복제 확인 위치 사이의 차이이며 정확한 업무 데이터 지연 시간은 아니다. 최종 판단에는 테이블 준비 상태, 마지막 복제 마커, 데이터 해시 대조를 함께 사용한다. 전체 대조는 첫 실행기에서 테이블별 100만 행까지 지원하며, 큰 DB에서는 별도 검증 전략이 필요하다.

## 실행한 검증

저장소 루트에서 아래 명령을 사용한다. 테스트 DB는 별도 Docker 네트워크의 `migration_test`이며 호스트 포트를 열지 않는다. 다른 개발 DB를 비우지 않는다.

```bash
docker compose -f platform/migration/compose.test.yaml build test
docker compose -f platform/migration/compose.test.yaml run --rm test
docker compose -f platform/migration/compose.test.yaml down -v
```

2026-11-04 실제 PostgreSQL 두 개에서 **6개 통합 테스트 통과**: 초기 복사·CDC·프로세스 재개·세션 레코드·sequence·단일 쓰기, 승인 불일치, 데이터 불일치 시 차단/승격 전 원복, 잘못된 대상/스키마/쓰기 계정 거부, prepared transaction 차단, 복제 계정 쓰기 권한 거부.

풀스택 앱과 실제 Chromium 검증은 다음과 같다. 먼저 `platform/web`의 npm 의존성과 Playwright Chromium을 설치한다.

```bash
uv run --project platform/backend python platform/migration/demo.py
```

이 데모는 자체 source/target DB·동일 앱 이미지·loopback Nginx만 생성한다. Nginx는 로컬에서 같은 주소를 유지하기 위한 시험용 진입점이며 운영 CSP LB 구조를 대체하는 제안이 아니다. `http://127.0.0.1:18096`에서 확인할 수 있다. 임의 생성된 로컬 전용 로그인/설정은 ignored `platform/.runtime/migration-demo/.env`에 0600으로 저장하고, 비밀값을 채팅·문서·CI에 올리지 않는다. 기존 계획이 있으면 덮어쓰지 않고 중단한다.

실제 Chromium에서 로그인 1회, URL·세션 쿠키 유지, 이전 전/CDC 중 메모 보존, 대상 앱의 새 메모 저장과 같은 이미지 ID를 확인했다. 마지막 검증의 제어된 대기 시간은 **4.332초**, 헬스 요청은 **503 17건 / 200 12건**이었다. 재검증 중 Nginx reload 명령이 끝나도 경로 전환은 아직 진행 중인 경우를 발견해, 공개 진입 주소에서 새 환경의 정상 응답을 연속 확인한 뒤 전환 완료로 표시하도록 수정했다. 무중단이나 AWS→GCP 실환경 검증 결과가 아니다.

실행 후 대상 앱·DB는 로컬 확인용으로 남는다. 아래 명령은 이 데모의 임시 DB 데이터를 지운다. 실제 앱 DB나 플랫폼 DB에는 사용하지 않는다.

```bash
docker compose --env-file platform/.runtime/migration-demo/.env \
  -f platform/migration/compose.demo.yaml --profile target-app --profile runner down -v
```

AWS/GCP VM 배포·VPN 연결·관리형 DB 권한과 웹 실행기 연결은 후속이다. 2026-11-04 조회 기준 `term2-delta`의 고객 서브넷은 `10.30.1.0/24`이며 해당 프로젝트의 VM과 서울 VPN 터널은 0개였다. 준비된 고객 환경으로 연결하기 전에는 클라우드 이전 완료로 표시하지 않는다.
