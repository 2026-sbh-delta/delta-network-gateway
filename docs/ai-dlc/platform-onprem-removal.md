# 온프레미스 실행 앱 삭제

2026-11-05 사용자가 플랫폼에서 온프레미스 실행 서비스를 삭제하는 기능을 요청·승인했다. 등록 정보 삭제와 실행 앱 제거를 구분한다. 구현은 `feat/platform-onprem-removal`에서 진행하며 커밋·PR 전에 내용을 보고한다.

## 설계와 완료 기준

- 환경 카드에서 삭제 대상 Deployment·Service를 보여주고 서비스 이름을 입력받는다. DB·PVC·VM·k3s·namespace·공유 인증과 다른 앱은 보존한다. 메모리 안의 데이터는 앱 종료로 사라짐을 알린다.
- 운영자가 등록한 환경의 자원 UID와 서비스/환경 소유 라벨만 사용한다. 사용자는 임의 주소·namespace·명령을 입력할 수 없다. 오래된 확인 화면, 소유권 변경, 같은 이름으로 재생성된 자원은 거부한다.
- 기존 인증·CSRF·작업 큐·Worker·lease·이력을 재사용한다. 삭제 완료 전에는 성공으로 표시하지 않고 연결 실패·부분 삭제는 재시도 가능 상태로 남긴다. 작업 중 환경 재연결·동시 배포를 막는다.
- 삭제 권한은 등록 namespace의 지정 Deployment·Service에만 준다. Foreground 삭제로 하위 실행 자원 정리를 확인한다. DB와 PVC 삭제 권한은 주지 않는다.
- 운영 앱을 검증 목적으로 삭제하지 않는다. 별도 임시 앱으로 실제 제거를 확인하고, DB 기반 테스트·브라우저에서 확인/진행/실패/재조회와 보호 경계를 검증한다.
- 중앙 연결이나 실제 배포가 준비되지 않으면 상태를 명확히 기록한다. 삭제된 환경과 이력은 남기고 기존 프로젝트 등록 삭제 API는 유지한다.

현재 단계: 구현·별도 임시 앱 실검증 완료. 변경 내용을 보고한 뒤 사용자의 `Commit and merge your changes` 지시로 커밋·PR·병합을 승인받았다. 운영 플랫폼 릴리스와 운영 앱 삭제는 별도다.

## 구현

- `runtime_remove` 작업을 기존 큐에 추가했다. Migration `0008`은 작업 종류/완료 제약만 확장하며 기존 데이터·테이블을 제거하지 않는다.
- 삭제 확인 대상 해시와 소유 서비스·환경·Kubernetes UID를 고정한다. 실제 GET의 resourceVersion까지 DELETE precondition으로 전달한다. 모든 자원을 먼저 점검한 뒤 제거하며 같은 이름의 새 자원은 보존한다.
- API는 로그인·Origin·CSRF·이름 확인·멱등 키를 적용한다. 대상 주소와 자원은 운영자 등록 정보만 사용한다.
- 삭제 진행/부분 실패 동안 환경 재연결과 새 실행을 막는다. 승인/실행 중 계획이 있으면 삭제를 거부하고, 삭제된 환경을 기존 계획에서 다시 실행하지 못하게 한다.
- `prepare-removal.py`는 기존 앱의 소유 메타데이터와 지정 이름의 `get/delete` 권한만 준비한다. 앱 Pod 템플릿을 변경하지 않으며 Secret·DB·PVC·namespace 삭제 권한은 주지 않는다.
- 기존 프로젝트 기록 삭제 버튼과 별도로 환경 카드에서 실행 앱을 제거한다. DB/볼륨/서버와 플랫폼 이력은 남는다.

## 2026-11-05 검증 결과

| 확인 | 결과 |
|---|---|
| PostgreSQL 기반 백엔드 전체 `uv run --frozen pytest -q` | #28·#47·#49가 병합된 main과 통합한 뒤 최종 215개 통과, live 1개 제외. 기존 샌드박스와 FE·BE 묶음 실행을 포함 |
| `tests/test_runtime_removal.py` | 최종 10개 통과. API 확인·멱등성·이력, 다른 서비스/재생성 UID 보호, 부분 삭제 재시도, 종료 대기, 만료된 Worker 차단, 진행 작업/승인 계획 차단, 대상 변조·PVC 연결 앱 삭제 거부 확인 |
| 웹 `npm run build`, Prettier, Python Ruff | 통과 |
| Playwright 삭제·환경 화면 15개 | 이름·취소·대기·실패 재시도·완료·새로고침·오래된 확인 대상·모바일·미연결 표시 및 기존 프로젝트 삭제 회귀 통과 |
| 실제 온프레미스 임시 앱 | `delta-removal-check-7b69cdc6`에서 실행/Ready 후 API 요청→Worker→실제 Kubernetes API로 Deployment·Service·하위 Pod 제거 확인 |
| 삭제 전용 권한 | PVC 삭제, 기존 `demo` 앱 삭제, 미등록 이름의 앱 삭제 모두 거부 |
| 기존 자원 | `delta-notes`의 앱 Pod·PVC UID가 전후 동일. 기존 발표 앱·DB 데이터 삭제 없음 |
| AWS 플랫폼 사설 연결 | SSM으로 플랫폼 VM에서 `10.40.0.10:6443`, `:32081`에 연결 시도했으나 시간 초과 |

실제 삭제는 로컬 인증 API/Worker와 테스트 DB를 사용했고, 전송만 SSH 관리 경로로 중계했다. 진짜 namespace 제한 토큰·TLS·Kubernetes API를 사용했으며 AWS Worker의 직접 접근 성공으로 표시하지 않는다. 검증 기록은 Git 제외 `platform/.runtime/removal/proof.json`에 보관한다. 검증용 namespace 삭제 완료·미존재를 확인해 임시 자격 증명도 폐기했고 기존 runtime은 유지했다. 마지막에는 상태 변경 중 확인창의 승인 대상을 고정하는 브라우저 검사3개를 재실행해 통과했다.

## 남은 연결

팀의 VPN/TGW 왕복 경로와 플랫폼 VPC 연결 → 새 API·Worker 릴리스 → 올바른 서비스/환경의 실제 자원 UID 등록 → 전용 토큰 설정 순서로 활성화한다. 기존 환경 등록의 VM 정보만 보고 앱 소유 관계를 추측하지 않는다. 실제 운영 앱 삭제는 사용자가 화면에서 대상을 확인해 요청할 때 수행한다.

2026-11-05 AWS의 `describe-transit-gateway-attachments`와 플랫폼 VPC `describe-route-tables`를 읽기 전용으로 확인했다. 플랫폼 VPC `10.80.0.0/16`에는 TGW attachment와 원격 사설망 목적지 경로가 없었다. 팀이 검증한 AWS 업무 VPC↔GCP 연결과 플랫폼의 연결 상태를 구분한다. 이 변경에서 VPN·TGW·라우팅과 S3 state는 수정하지 않는다.

## Git 게시 상태

2026-11-05 사용자 승인 후 `feat(platform): add on-premise application removal`을 로컬 커밋했다. `git push`는 반복해서 GitHub의 `fatal error in commit_refs`로 거부됐고, GitHub API의 브랜치 생성도 HTTP 422 `Reference update failed`로 실패했다. 다른 브랜치 이름으로도 같은 결과였으며 저장소의 쓰기 권한은 확인했다. 원인은 미확정이다. PR 생성은 원격 브랜치 부재로 실패해 CI와 병합은 아직 진행하지 못했다. 원격 main과 기존 PR은 변경하지 않았다.

후속: 사용자의 PR 순서별 병합 지시로 #28 → #47 → #49를 각각 `4556c08`, `f23de04`, `fbaf4af`에 병합했다. #49는 #47의 후속이므로 기준 브랜치를 main으로 변경했다. 세 PR의 CI는 모두 통과했고 초안 #39는 유지했다. 이후 앱 삭제 브랜치 `dd7824d`의 push 재시도도 성공했다. 새 main을 통합하면서 API의 두 입력 타입 import와 문서 2개의 충돌을 양쪽 기능을 보존하는 방식으로 해결했다. 병합 자체는 운영 배포나 실제 앱 삭제를 실행하지 않는다.

통합 검증: 백엔드 전체 `uv run --frozen pytest -q` 215개 통과·live 1개 제외, `npm run build` TypeScript/웹 빌드 통과, 백엔드 Ruff 검사/형식과 `git diff --check` 통과. 추가 실환경 변경은 하지 않았다.
