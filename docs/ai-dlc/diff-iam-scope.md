# Diff IAM 수집 및 앱 이전 보안 범위

2026-11-07 KST. 사용자 승인: IAM 권한 수집 누락 수정, Lambda만 이번 앱 이전 범위에서 제외, RDS·S3 검사 유지, SCC 읽기 전용 추가 확인. 고객 앱 운영 배포와 DNS 전환은 금지한다. ADR 번호 미할당.

## 문제와 설계

실제 Diff 서비스의 Terraform 추출기는 EC2의 프로파일 부착 여부만 읽고 정책 동작을 넘기지 않았다. AWS GuardDuty의 사용·플랜은 확인됐으나 대상 `scc_tier`가 없어 기본 탐지 행이 unknown이었다. 계정 전체 Lambda 보호도 앱 이전에 포함됐다.

현재 브랜치에 없던 `services/delta-diff`를 기존 `516d7f9`에서 도입된 패키지를 보존한 `b91b97c`의 Git 스냅샷으로 복원했다. 실제 실행 중인 이미지 `07931d6e…`의 모듈 해시와 대조했고, 기존 YAML-only API와 TGW 제외 소스도 보존했다. 다른 작업 폴더의 미커밋 작업은 변경하지 않았다.

- 프로파일→역할→인라인 정책·관리형 정책을 state에서 연결하고 동작 이름만 수집한다. 해당 역할 외 정책은 섞지 않는다. 관리형 정책 문서 미확인, permissions boundary, Deny·Condition·NotAction 등은 부분 Allow 목록으로 축약하지 않는다. 정책 원문·리소스 ARN·조건 값은 출력하지 않는다. 역할 간 대응은 기존 approximate/gap 규칙과 실제 호출 확인을 유지한다.
- Lambda 제외는 기존 시연 환경 변수, 정확한 workload-client state, `delta-sample-app`, AWS→GCP에 묶는다. state에 Lambda가 없을 때 `LAMBDA_NETWORK_LOGS`만 제거하고 `source.excluded_capabilities`에 기록한다. Lambda가 있으면 기존 검사를 유지한다. TGW 제외 규칙은 보존한다.
- RDS_LOGIN_EVENTS·S3_DATA_EVENTS와 SCC 미확인은 그대로다. `accepted_gaps`나 `scc_tier=premium`을 자동 주입하지 않는다. 이전 연결 검증의 사람 승인은 재사용하지 않는다.

## SCC 조회

대상 `term2-delta`의 ETD와 상위 조직의 VM Threat Detection을 각각 다시 조회했으나 `securitycentermanagement.googleapis.com`의 SERVICE_DISABLED로 실패했다. 이전 securitycenter API 조회도 같은 제한이 있고 조직 IAM 조회 권한도 없다. API·유료 등급·권한은 변경하지 않았다. API 비활성화가 조직 전체 SCC 미사용을 뜻하지 않으므로 등급과 유효 탐지는 unknown으로 유지한다.

공식 ETD에는 Cloud SQL 로그인·유출 탐지와 Data Access/IP 로그 요건이 있다. 대상 Cloud SQL은 일반 PostgreSQL 로그를 수신하지만 `databaseFlags`에 감사·IP 로그 활성화 선언이 없고, 조직 상속 감사 정책은 읽지 못했다. 설정과 샘플 탐지 확인 전 동등성 완료로 보지 않는다. S3 객체 접근 탐지의 GCS 대응도 시나리오별로 확인해야 한다.

## 검증·적용

IAM 정상 수집·다른 역할 격리·정책 제한·관리형 정책 해석·비밀 값 비출력과 Lambda 제외 범위 및 RDS/S3 유지 회귀를 검증했다. Diff 107개(후보 실패·전환 후 실패 복구 3개 포함), AI 346개, 파이프라인·오케스트레이터 251개가 통과했다. 초기 기본 샌드박스에서 루프백 포트가 차단된 HTTPS 테스트는 포트 바인딩을 허용한 실행으로 재검증했다. 스키마를 재생성했고 Python 컴파일·셸 구문·`git diff --check`가 통과했다.

시연 Diff에만 적용한 SSM `cdc77231-2d50-4da5-923b-cb03db066f63`이 Success였다. 릴리스 ID는 `c38d667b1b27`이며 후보 18095와 실제 8095에서 인증 HTTP200/익명401/다른 state403, 실제 Claude 사용을 각각 확인했다.

- state serial 21, IAM 동작 5개 수집. IAM은 unknown에서 미대응 `logs:CreateLogStream`·`logs:DescribeLogStreams`의 gap으로 바뀌었다. `logs:PutLogEvents`·S3 읽기·Secret 읽기 대응은 제안이며 정책 범위 동등성 완료를 뜻하지 않는다.
- Lambda 플랜만 제외되고 `source.excluded_capabilities=["LAMBDA_NETWORK_LOGS"]`가 남았다. RDS·S3 gap과 SCC unknown은 유지됐다. 전체 판정은 needs_human이다.
- TGW 두 서브넷 제외·나머지 서브넷 8개 유지, 기존 환경·토큰·역할·WIF·로그·상태 마운트를 보존했다. 모듈 해시는 `deploy/diff-demo-scope/manifest.json`과 일치했다.
- 이전 컨테이너 `delta-diff-before-iam-lambda-c38d667b1b27`와 `/opt/delta/diff-cp/iam-scope/c38d667b1b27/state-before`를 보존했다. 복구 스크립트 `/opt/delta/diff-cp/iam-scope/c38d667b1b27/rollback.py`를 설치했고 실행하지 않았다. 복구가 승인되면 진행 중인 비교가 없음을 확인한 뒤 `python3 /opt/delta/diff-cp/iam-scope/c38d667b1b27/rollback.py /opt/delta/diff-cp/iam-scope/c38d667b1b27`을 사용한다.

SSM `743d3f17-48c7-40c6-b646-4dfb9ae6a7b0`에서는 다른 세션의 최신 오케스트레이터에 설치된 실제 Diff 클라이언트로 응답 검증까지 통과했다. 직접 읽기 전용 비교만 했으며 전후 파이프라인 목록이 완전히 같고 활성 실행과 실제 스테이징 점유는 각각 빈 목록이었다. 도메인은 기존 `demo.delta.mungji.com`→GCP, revert idle로 보존됐다. 고객 앱 운영 배포·DNS 전환·새 파이프라인 실행은 하지 않았다. 오케스트레이터는 다른 세션이 갱신한 `895705a3…` 이미지를 그대로 두었다.
