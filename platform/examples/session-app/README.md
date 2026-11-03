# 로그인·데이터 유지 검증용 풀스택 앱

Django가 화면과 API를 함께 제공하고 PostgreSQL이 메모·사용자·로그인 세션을 저장한다. 프레임워크를 모든 고객에게 강제하는 것이 아니라 첫 지원 예제다. 플랫폼 업무 DB와 독립된 DB·컨테이너를 사용한다.

## 로컬 검증

Git에서 제외된 `.env`에 서로 다른 `DB_ADMIN_PASSWORD`, `APP_DB_PASSWORD`, 32자 이상의 `APP_SECRET_KEY`를 준비한다. `.runtime/collector.env`에는 `AWS_REGION`, 플랫폼이 발급한 `DELTA_PROJECT_ID`, `DELTA_SERVICE_ID`, `DELTA_ENVIRONMENT_ID`와 테스트용 단기 AWS 자격 증명을 둔다. 두 파일은 권한 0600으로 관리하며 이미지에 넣지 않는다. 실제 VM의 Collector는 인스턴스/연합 역할을 사용한다.

```bash
docker compose up -d --build --wait
docker compose exec app python manage.py migrate --noinput
```

계정은 Django 관리 명령으로 초기화한다. 고정 운영 비밀번호를 코드에 넣지 않는다. 접속 주소는 `http://127.0.0.1:8096`이다. 로컬 compose만 HTTP 쿠키를 허용하고, 운영은 같은 HTTPS 도메인과 Secure 쿠키를 사용한다. 운영에서는 신뢰하는 LB만 앱에 접근하도록 네트워크를 제한한다.

## 검증할 동작

1. 로그인하고 메모 저장·조회. 같은 요청에서 HTTP와 PostgreSQL trace가 생성되는지 확인한다.
2. 실제 준비 상태와 DB 연결 결과가 `delta.app.up`, `delta.db.up`으로 수집되는지 확인한다.
3. 동일 이미지와 인증 키, 세션 테이블을 포함한 DB로 이전 후 같은 브라우저 쿠키가 계속 유효한지 확인한다.

예제의 메모 쓰기 차단 파일은 보조 장치다. DB 이전 전환 시 로그인·로그아웃·세션 갱신 등 모든 쓰기를 멈추기 위해 기존 앱/작업을 비우고 DB 쓰기 권한·연결을 함께 통제해야 한다. 메모 POST만 막은 것을 전체 쓰기 중단으로 간주하지 않는다.

PostgreSQL logical replication은 DDL·sequence를 자동 이전하지 않으므로 스키마 사전 복사와 최종 sequence 동기화가 필요하다. 대상 DB 쓰기 시작 후에는 단순 DNS 원복으로 데이터가 복구되지 않는다. [이전 설명](../../docs/migration.ko.html)을 따른다.

## 정리

`docker compose stop`은 앱과 수집을 중지하고 DB 볼륨은 보존한다. 테스트 자격 증명 파일은 검증 후 제거하거나 만료시키고, AWS 검증 로그 보관은 해당 로그 그룹 정책을 따른다. 테스트 데이터가 필요 없음을 확인하기 전에는 `down -v`를 실행하지 않는다.
