# 한 페이지 관측 연결

앱은 OTel SDK로 메트릭·로그·trace를 보내고, 같은 실행 환경의 Collector가 플랫폼이 정한 서비스·환경 ID를 붙여 AWS 저장소로 내보낸다. 플랫폼 API는 저장한 환경 연결만 사용해 읽는다. 원본을 업무 PostgreSQL에 저장하지 않는다.

Collector: `otel/opentelemetry-collector-contrib:0.164.0` (`sha256:340c9ca6a36617084c6661bdc4f0fe92b7cda5a805d61673ec8db375fddc522b`). 앱 포트와 Collector 수집 포트를 인터넷에 직접 공개하지 않는다. 앱에는 AWS 자격 증명을 주지 않고 Collector만 수집 권한을 가진다. Docker 고객 앱의 IMDS 접근도 차단해야 한다.

## 앱 계측 계약

- `http.server.request.duration`: 초 단위 OTel histogram, **DELTA** temporality. HTTP 5xx는 `http.response.status_code` 속성으로 구분한다. Python 계측은 `OTEL_SEMCONV_STABILITY_OPT_IN=http`, `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=DELTA`를 사용한다.
- `delta.app.up`, `delta.db.up`: 주기적으로 실제 프로세스·DB 확인 결과를 1/0으로 보고하는 gauge. 보고가 없으면 플랫폼이 정상으로 추측하지 않는다.
- `service.name`, `service.version`, `service.instance.id`를 설정한다. Collector가 `delta_project_id`, `delta_service_id`, `delta_environment_id`를 고정해 덮어쓴다.
- 로그는 경고/오류만 내보낸다. 요청 본문·인증 쿠키·DB 문장·사용자 비밀값을 계측하지 않는다. Collector의 속성 허용 목록·패턴 가리기는 추가 방어이며 모든 비밀값을 자동 발견한다고 보장하지 않는다.
- 대표 HTTP/DB trace를 표본 수집한다. 시연의 작은 트래픽은 전량 수집할 수 있지만 대규모 고객 운영은 별도 표본 정책이 필요하다.

## 저장과 조회

- 메트릭: CloudWatch OTLP → PromQL. p95는 전체 histogram을 합친 후 계산하며 서버별 p95를 평균 내지 않는다. DELTA histogram에 `rate()`를 사용하지 않는다.
- 로그: 서비스/환경별 `/delta-platform/apps/<service_id>/<environment_id>`의 `application` stream, 1일 보관. 화면은 최대 2시간·최근 5건이다.
- trace: OTel Collector의 AWS X-Ray exporter, 서비스/환경 annotation으로 조회. 플랫폼은 요청 정보 중 이름·시간·관계만 반환한다.
- 인프라: AWS EC2/CloudWatch, GCP Compute/Monitoring. CPU·메모리·상태만 조회한다. 메모리는 해당 VM의 CloudWatch Agent/Google Ops Agent가 필요하다. 연결되지 않은 지표는 데이터 없음으로 표시한다.
- GCP 조회는 ADC/연동된 서비스 계정의 조회 권한을 사용한다. GCP Collector의 AWS 쓰기 인증은 연합 역할 등 실행 환경에 맞게 별도로 연결해야 하며 SA JSON 키를 이미지에 넣지 않는다.
- 온프레미스: [Collector 연결 설정](onprem/README.md)과 등록한 호스트 ID로 CPU·메모리·루트 디스크를 조회한다. 앱 지표와 호스트 지표 수신·인증을 분리한다. `수집 중`은 Collector의 보고 상태이며 VM 전원 상태 확인을 뜻하지 않는다. 실제 서버 연결 상태는 [온프레미스 진행 기록](../../docs/ai-dlc/platform-onprem.md)을 따른다.

## 운영자가 환경 등록

검증한 Terraform 출력에 맞춰 JSON을 준비한다. 자격 증명은 JSON에 넣지 않는다.

```json
{"name":"AWS 서울","cloud":"aws","region":"ap-northeast-2","version":"검증한 이미지 digest","account_id":"123456789012","instances":[{"id":"i-0123456789abcdef0","name":"customer-app"}]}
```

```bash
uv run platform-environment --service-id '<플랫폼 서비스 UUID>' --input binding.json
```

등록 결과의 환경 UUID와 고정 workspace UUID `496562be-f67b-5f7e-a16b-929fd347b9ad`를 Collector의 `DELTA_ENVIRONMENT_ID`, `DELTA_PROJECT_ID`로 전달한다. 현재 공통 로그인 작업 공간의 프로젝트이며 고객별 RBAC 구현과 구분한다. 같은 서비스·환경 이름으로 재실행하면 ID를 보존하고 자원 연결을 갱신한다. 다른 리전·CSP는 새 환경으로 등록한다.

## 확인·복구

Collector 설정 검증, 실제 HTTP 요청과 CloudWatch 데이터 대조, 환경 전환·누락·권한 오류를 확인한다. UI에 나타나는 조회 시각과 마지막 수집 시각은 별개다. 수집이 끊기면 오래된 값으로 표시하며 앱 배포 상태를 변경하지 않는다. 수집기 중단/자격 증명 제거로 비활성화할 수 있으며 원본 보관 정책은 각 저장소를 따른다.

구현·실행 상태는 [진행 기록](../../docs/ai-dlc/platform-continuity.md)을 따른다.
