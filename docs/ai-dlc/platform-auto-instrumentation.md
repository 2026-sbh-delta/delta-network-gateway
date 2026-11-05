# 플랫폼 배포 시 자동 계측

2026-11-05 사용자의 `Implement it` 지시로 구현을 승인받았다. 샘플 앱의 소스·Dockerfile·의존성을 수정하지 않고 Oneport와 같은 배포 시 계측 방식을 적용한다.

## 범위와 설계

- 현재 발표 앱의 Node.js 24를 첫 지원 런타임으로 검증한다. 지원하지 않는 언어를 자동 지원으로 표시하지 않는다.
- 플랫폼이 digest로 고정한 공식 OTel 계측 이미지의 파일을 전용 볼륨에 준비한다. 앱 프로세스는 플랫폼이 넣은 시작 옵션으로 계측을 불러온다. 앱 이미지·명령은 유지한다.
- 동일한 프로필을 Docker VM과 Kubernetes 배포 구성에 사용할 수 있게 한다. 온프레미스 운영 후보 렌더러·적용 도구에 연결한다. 계측은 명시적으로 켜며 중복 계측은 거부한다.
- 요청 trace와 FE의 HTTP 지표를 수집한다. BE 지표를 중복 집계하지 않는다. 로그·호스트 관측은 기존 수집 경로를 사용한다.
- 초기화 컨테이너도 비root, 읽기 전용 루트, 제한된 자원으로 실행한다. 계측 endpoint는 운영자가 등록한 사설 수집기로 제한한다.
- 테스트는 원본 앱의 실제 FE→BE 요청으로 trace ID·부모 관계·HTTP 지표를 확인하고, 계측 OFF·수집 실패 시 앱 실행도 확인한다. 샘플 앱 저장소에 테스트 코드를 추가하지 않는다.
- 기존 팀 `demo`, 데이터 PVC, VPN/TGW, S3 state는 보존한다. 중앙 사설 경로가 미완료이면 실제 중앙 저장·대시보드 검증도 대기로 기록한다.

## 완료 기준

계측 ON/OFF 배포 구성을 생성하고 같은 앱 이미지에서 요청 추적이 실제로 달라져야 한다. 온프레미스에서 자동 계측 프로세스와 Collector의 수신을 확인한다. 패키지 초기화 오류·미지원 환경을 조용히 성공 처리하지 않는다.

현재 단계: 구현·온프레미스 수신 검증 완료. 2026-11-05 사용자가 사전 보고한 커밋 메시지와 PR 내용의 게시를 승인했다. 대상은 플랫폼 저장소의 `feat/platform-auto-instrumentation`이며 기존 온프레미스 PR #47 브랜치를 기준으로 한다. 게시·CI·병합 상태는 해당 GitHub PR을 따른다. 플랫폼 웹의 배포 버튼이나 AWS/GCP 실행 환경에 적용한 상태는 아니다.

## 2026-11-05 검증

| 실행 | 결과 |
|---|---|
| `uv run --project platform/deploy --frozen python -m unittest discover -s platform/deploy/tests` | 배포 테스트44개 통과 |
| 변경 Python 파일의 Ruff check / format | 통과 |
| `prepare-agent.py` + `verify-node.py` | 고정 공식 agent 준비, 소스 수정 없이 FE→BE 부모 관계·DELTA HTTP histogram·OFF 시 미전송·Collector 장애 시 앱 응답 확인 |
| 고정 Collector 이미지의 `validate --config=...` | 임시 수신 설정 통과 |
| Jenkins 원본 앱 빌드와 k3s 샌드박스 | FE `1cef3783-b3a4-4226-a6e8-bb11d886721e`, BE `836da860-86d1-4408-85f6-ea5ca72a532e` 성공. 실제 digest·FE→BE 통신 확인 후 Pod·pull Secret 정리 |
| k3s 후보에 자동 계측 적용 | 두 앱 Ready, 재시작0, init 성공. Jenkins digest와 실제 imageID 일치 |
| 실제 `/api/health`, `/api/notes` 요청 → 임시 Collector | FE server → FE client → BE server의 trace ID와 부모 관계 일치, HTTP histogram 수신 |
| 동일 이미지로 계측 OFF 재배포 | 두 앱 Ready·HTTP200, 주입 init/env 없음, 전용 egress 허용 해제, 임시 Collector 제거, DB 중지·PVC 보존 확인 |

샘플 기준 커밋은 `e29af5449dbccc7e6c9bff11e4cb2f0ce5812ee8`이며 샘플 저장소 변경은 없다. 실제 연결된 trace는 `cfd595cc15206dc4d24876412f02c3d0`이다. 원본 결과는 Git 제외 경로 `platform/.runtime/autoinstrumentation/{node-proof,k3s-proof}.json`에 저장했다. CI에는 고정 샘플 커밋으로 같은 Node 실행 검사를 추가했다. 위 결과는 로컬·온프레미스 실행 기록이며 GitHub CI 결과와 구분한다.

## 운영과 남은 연결

- 후보 FE·BE는 원본 앱 이미지로 실행한다. 중앙 연결 전까지 계측은 OFF로 되돌렸고 임시 Collector만 제거했다. DB는 계속 replicas0이며 PVC와 기존 `demo`는 보존했다.
- k3s 확인은 SSH 관리 경로다. AWS Worker의 사설 경로 성공·중앙 저장·플랫폼 대시보드 성공을 의미하지 않는다.
- `/etc/delta-onprem/otel/app.env`와 앱 수집 인증서가 아직 없어 native Collector의 중앙 전송을 켜지 않았다. 팀의 사설 경로와 함께 준비해야 한다.
- 기존 샌드박스 검사는 원본 앱 기준이다. 계측 ON 구성의 검증은 별도 후보에서 수행했으며 모든 배포의 자동 승인 흐름으로 연결하지 않았다. DAST API는 미준비로 실행하지 않았다.
- Docker VM은 동일 프로필의 Compose 렌더링을 지원하고 단위 검사했다. 이번 실제 클라우드 배포는 온프레미스 k3s만 확인했다.

실행·OFF 원복은 [자동 계측 안내](../../platform/deploy/instrumentation/README.md)를 따른다. 커밋 메시지와 PR 제목·본문은 사용자에게 먼저 보고한다.

## 근거

- [OTel JavaScript 자동 계측](https://opentelemetry.io/docs/zero-code/js/)
- [공식 ESM 지원 안내](https://github.com/open-telemetry/opentelemetry-js/blob/main/doc/esm-support.md)
- Oneport의 `controlplane/instrumentation.py`, 환경별 Collector 연결 구조를 참고했다.
