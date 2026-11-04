# 온프레미스 실행과 핵심 관측

- 기준일: 2026-11-04 (Asia/Seoul).
- 승인: 사용자가 계획·아키텍처 확인 후 `Proceed. What about monitoring?`으로 구현을 승인했다. 기존 커밋·PR 위임을 유지하며 커밋 전에 메시지를 알린다.
- 브랜치: `feat/platform-onprem`, 기준 main `cfa68b3`.
- 현재: 실행기·팀 DAST 연결·관측 조회/화면 구현과 실제 VM 내부 검증 완료. 전용 namespace와 Collector 파일을 설치했다. **VPN/TGW는 팀에서 작업 중**이며 AWS 플랫폼 활성화·중앙 관측 전송은 아직 하지 않았다.

## 목적과 범위

온프레미스를 선택하면 동일 ECR digest를 기존 k3s 전용 namespace에서 실행한다. 실행 ID·commit·Pod UID·실제 imageID 확인 → HTTP → 팀 DAST → Pod/pull Secret 정리로 연결한다. 재시도는 같은 실행을 복구하며 소유권이나 정리 여부를 확인하지 못하면 잠금을 유지한다.

AWS 플랫폼·Jenkins·SAST/DAST와 기존 온프레미스 앱·DB는 보존한다. 운영 앱·DB 이전 완료를 의미하지 않는다. 기존 VM 내부는 Kubernetes manifest로 관리하고 신규 VM·VPN·TGW Terraform은 추가하지 않았다.

## 실행 경계

- Tailscale SSH는 관리용이며 AWS Worker/DAST의 실제 사설 경로와 구분한다.
- 전용 `delta-platform-sandbox`: restricted Pod Security, controller/scanner/runtime 분리, API 토큰 자동 마운트 금지.
- Pod1개, 500m CPU, 256MiB 메모리·임시 저장소, /tmp64MiB,15분 제한. 비root·읽기 전용 루트·호스트 마운트·PVC 금지.
- Pod 외부 연결 차단, Worker·DAST 출발지만 허용. NodePort32080·API6443은 VPN 내부에서 사용한다. shared kernel이므로 불특정 고객용 강한 격리나 이중화를 보장하지 않는다. 노드 이미지 캐시는 별도 용량 관리가 필요하다.
- namespace에 제한된 토큰으로7일 발급을 요청했다. 실제 만료는2026-11-11 11:35 UTC경이다. 토큰·키·CA 개인 키는 Git에 저장하지 않는다.

## 모니터링

앱 OTel → 앱 Collector → AWS gateway4318 → CloudWatch/X-Ray, VM hostmetrics → 호스트 Collector → gateway4319 → CloudWatch 경로를 준비했다. 별도 client CA를 사용하고 환경/호스트 ID를 서버에서 고정한다. 앱 입력의 호스트 지표를 필터링한다. 온프레미스에는 AWS 장기 키를 배포하지 않는다.

VM은 CPU·메모리·루트 디스크 사용률만 추가한다.60초 수집, 기본15분·최대2시간 조회,5분 이상 지난 값은 수집 지연 표시. 누락·실패를0으로 바꾸지 않으며 Collector 수신 상태와 VM 전원 상태를 구분한다. 앱의 기존 요청 수·5xx·p95·오류 로그·trace 화면을 재사용하고 원본은 업무 DB에 저장하지 않는다.

## 실제 적용·검증

2026-11-04 실행 결과다.

| 항목 | 실행·결과 |
|---|---|
| 서버 확인 | Tailscale 재인증 후 SSH 성공. Ubuntu24.04, amd64/4core/약5GB RAM, k3s1.36.5. 기존 demo DB·웹 Pod·PVC 보존 |
| 실행 공간 | `prepare.py` dry-run/apply. 제한된 Role·SA·Quota·NetworkPolicy·NodePort 적용. 관리자 kubeconfig를 플랫폼에 전달하지 않음 |
| 실제 앱 | 기존 Jenkins digest `sha256:1ee930a1a62209e2e2b273ac1f2c0244fa02b02290a1e4029ca0b9dff6b0c5a7`를 재빌드 없이 실행. imageID 일치·HTTP200·Pod UID 확인 |
| 실제 접근 경계 | controller의 노드 조회, scanner의 Secret 조회·Pod 삭제는403. Pod 외부 TCP와 미허용 Tailscale 출발지의 앱 접근 차단 |
| 실제 팀 DAST 등록 | 실제 scanner 토큰으로 팀 모듈이 Pod UID·commit·imageID·서비스를 확인하고 등록/해제. 브라우저 공격 재현 스캔은 미실행 |
| 정리 | 소유한 Pod·pull Secret 삭제. 기존 demo Pod/PVC·재시작 상태 보존 |
| 실제 호스트 지표 | SHA256 확인한 Collector0.164.0을 비root 사용자로 실행해 CPU·메모리·루트 디스크 값 확인. 검증 프로세스 종료 |
| Collector 설치 | native binary·설정·systemd2개 설치. 인증서·gateway 연결 전이므로 inactive/disabled |
| AWS 사설 경로 | Worker/DAST→온프레미스와 역방향 경로 timeout. 사용자가 팀 연결 작업 중임을 확인. 공유 VPN/TGW/라우터 변경 없음 |

실제 실행은 Mac SSH 관리 터널로 확인했다. AWS 공개 플랫폼부터 온프레미스까지의 end-to-end 성공이나 AWS 관측 저장 성공으로 기록하지 않는다. 접속 정보와 상세 증거는 무시된 `platform/.runtime/onprem/`에 보관한다.

## 코드 검증

- `uv run pytest -q` (platform/backend):192 passed,1 deselected. AWS/GCP 회귀, 온프레미스 성공·취소·복구·정리 실패·타 자원 보존·DAST 바인딩 포함.
- 팀 DAST `test_platform_api.py`, `test_cloud_run.py`, `test_onprem.py`:24 passed. Pod 교체·다른 이미지·origin·미준비 상태 거부 포함.
- `python3 platform/deploy/onprem/verify-observability.py`: 실제 Collector 설정3개, 익명/다른 CA 거부·식별자 강제·앱의 호스트 지표 주입 차단 통과. 임시 인증서·컨테이너 삭제. AWS 쓰기 없음.
- `npm run build`: 통과. 로컬 Node20.18 Vite 버전 경고가 있어 CI는Node24를 사용한다.
- Playwright `tests/observability.spec.ts`:2 passed. 모바일 가로 넘침 없음. 수치는 fixture이며 실제 AWS 관측 데이터가 아님.
- Python3.12로 CI와 같은 팀 DAST 계약5개 파일 실행:37 passed. 플랫폼↔실제 팀 DAST API 통합 테스트도1 passed.
- `python3 -m unittest discover -s platform/deploy/tests -v`:35 passed. 온프레미스 권한·출발지·자원 제한3개 포함.
- Ruff·웹 Prettier·셸 문법·`git diff --check` 통과. 새 DAST·배포 테스트와 실제 Collector 경계 검증을 GitHub CI에 추가했다.

## 다음 작업과 운영

1. 팀 VPN 연결 후 Worker와 DAST 각각의 왕복 사설 경로 확인. VPC peering의 전이 연결을 가정하지 않는다.
2. 만료 확인한 scoped 토큰·target만 기존 비밀 설정에 병합하고 DAST/플랫폼 릴리스. 로그인·DB·Jenkins·AWS/GCP 값 보존.
3. 공개 웹에서 실제 온프레미스 검사·취소·정리, 실제 DAST 실행 확인.
4. AWS 관측 gateway의 IAM·mTLS·사설 포트를 적용하고 온프레미스 Collector 시작. 실제 수치·앱 요청/로그/trace·수집 중단 시 표시 확인.

본문은 로컬 검증과 VM 준비 기록이다. 구현 코드는 `feat/platform-onprem` PR로 게시하고 원격 CI·병합 결과를 별도로 기록한다. AWS 플랫폼 활성화·릴리스는 사설 연결 확인 후 진행한다. [준비·포트·활성화·롤백](../../platform/deploy/onprem/README.md)을 따른다.
