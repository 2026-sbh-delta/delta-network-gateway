# 콘솔 사례 검토 · 평가셋 추출

기존 오케스트레이터의 **사례·평가셋** 버튼에서 사례를 선택한다. 원래 진단과 재검사 근거를 확인하고 검토자·근거를 작성한 뒤 검색을 승인하거나 거절/취소한다. 검증 결과가 success/failure이고 환경 정보가 있어야 승인된다. 승인된 최신 버전에 사람이 작성한 기대 확인 항목이 있으면 **평가셋 JSON 다운로드**가 활성화된다. 현재 다운로드는 선택한 사례 1개를 포함하며 기존 `EvaluationDataset`/평가기와 호환된다. 실제 모델 평가는 이 버튼으로 실행하지 않는다.

## 운영 연결

두 서비스에 기존 그래프/진단 생산자 토큰과 **다른** `DELTA_CASE_REVIEW_TOKEN`을 같은 값으로 설정한다. 비밀값은 서버 비밀 저장소에서 주입하고 브라우저·URL·로그·저장소에 넣지 않는다. AI 설정에 이 값이 없으면 새 관리 API는 모두401, 오케스트레이터에 없으면 콘솔에 연결 설정 오류503을 표시한다. 기존 사례 DB 경로와 볼륨을 그대로 사용하며 새 DB 마이그레이션은 없다.

오케스트레이터는 기존 `DELTA_DIAGNOSTIC_URL`, `DELTA_DIAGNOSTIC_CA_BASE64`를 사용한다. AI 앞단 프록시를 경로 허용 목록으로 운영하면 아래 경로도 기존 HTTPS upstream으로 전달해야 한다. 기존 catch-all404는 유지한다.

- `GET /v1/case-reviews?limit=30&offset=0`
- `GET /v1/case-reviews/{case_id}`
- `POST /v1/case-reviews/{case_id}/review`
- `POST /v1/case-reviews/{case_id}/export`

이 경로는 생산자 토큰을 받지 않는다. 검토 토큰도 진단/사례 게시용 API를 호출할 수 없다. 기존 공개 관리자 로그인과 같은 Origin의 JSON 요청만 콘솔 쓰기를 수행할 수 있다. loopback도 검토 기능은 관리자 세션이 필요하다. 공유 관리자 계정이므로 검토자 이름은 직접 작성하는 감사 표기이며 개별 계정의 인증된 실명을 뜻하지 않는다.

새 조치 결과 버전은 이전 승인을 상속하지 않는다. 화면에 표시한 버전과 최신 버전이 다르면409가 발생하며 화면은 최신 근거를 다시 불러온다. 승인 취소는 향후 검색과 추출에서 즉시 제외하지만 이미 다운로드한 파일을 회수하지 않는다. 검토 내용의 미저장 변경이 있으면 다운로드를 차단한다. 보고서 확인·재시도 접수만으로 success를 만들지 않는다.

API는 목록 최대50개, 버전/감사 최근20개, 콘솔 입력16KB/응답1MB 및10초 upstream 제한을 적용한다. 저장 오류를 통과/승인으로 해석하지 않는다. 새 UI와 API는 모델 호출·클라우드 작업·파이프라인 게이트 변경을 수행하지 않는다.

## 로컬 브라우저 검증

AI/pipeline/orchestrator와 테스트 의존성이 설치된 Python 환경 및 Playwright와 Chrome을 사용한다. `fixture.py`는 **127.0.0.1**에서만 서비스하며 별도의 임시 DB에 합성 사례32개를 생성한다. 다음 시험용 비밀번호/토큰은 fixture 코드 전용이며 운영에 사용하지 않는다.

1. `/private/tmp/delta-case-console-fixture`에 하루짜리 자체 서명 인증서를 만든다.
2. 저장소 루트에서 `PYTHONPATH=services/delta-ai:services/delta-pipeline:services/delta-orchestrator python deploy/case-review-console/fixture.py --root /private/tmp/delta-case-console-fixture/data --cert /private/tmp/delta-case-console-fixture/cert.pem --key /private/tmp/delta-case-console-fixture/key.pem`를 실행한다.
3. `PLAYWRIGHT_MODULE`을 설치된 playwright 모듈 경로, `CASE_CONSOLE_EVIDENCE`를 시험 결과 전용 폴더, `CASE_CONSOLE_PYTHON`을 위 Python으로 지정하고 같은 `PYTHONPATH`로 `node deploy/case-review-console/browser.cjs`를 실행한다.

검증은 관리자 로그인→목록 페이지 이동→근거 확인→승인→정확한 버전의 JSON 다운로드→미저장 편집 차단→승인 취소/감사→unknown 승인 차단→동시 새 버전 충돌→모바일/오류 복구를 수행한다. 자체 서명 인증서 허용은 테스트 브라우저에만 적용한다. 재실행 시 fixture 서버를 종료하고 이 전용 임시 DB만 새로 생성한다. 시험 종료 후 두 로컬 서버를 종료한다. 실제 Claude/AWS 고객 작업은 호출하지 않는다.

## 배포·원복

AI와 오케스트레이터의 새 이미지를 함께 검증한 후 기존 환경/인증/TLS/볼륨과 사례 DB를 보존해 교체한다. 프록시 허용 경로와 별도 검토 비밀 설정이 추가로 필요하다. 이 기능 구현에서 실제 AWS 배포나 해당 설정 변경은 수행하지 않았다. 이미지/설정 원복 시 사례 DB·감사·기존 보고서를 삭제하지 않는다. 기존 CLI는 계속 사용할 수 있다.
