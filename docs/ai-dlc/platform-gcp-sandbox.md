# 대상 환경별 샌드박스와 GCP 실행기

- 기준일: 2026-11-04.
- 승인: 사용자가 GCP 대상이면 GCP에서 검사하라는 기존 요구를 재확인했고, GCP 실행기·대상 선택·DAST 연결 구현 제안에 `Go for it.`으로 진행을 승인했다. 기존 플랫폼 구현·검증·커밋·PR 병합 위임을 유지한다. 커밋 전에 메시지를 알린다.
- 브랜치: `feat/platform-gcp-sandbox`, 기준 main `0a3ce37`.
- 현재: PR #29 병합과 AWS 플랫폼·DAST 릴리스 완료. 실제 GCP 실행·인증·삭제 및 플랫폼 DAST 연결 검증 완료. 지원 XSS 후보가 없는 예제이므로 실제 취약점 재현 성공을 뜻하지 않는다.

## 목적과 범위

개발자가 AWS 또는 GCP를 선택해 같은 빌드 이미지를 해당 환경에서 실행하고 DAST 결과를 그 환경에 연결한다. AWS 검사 결과를 GCP 검증으로 재사용하지 않는다. 기존 ECS 검사·기존 서비스와 공용 도구 VM을 보존한다.

GCP 실행은 Cloud Run **서비스**를 사용한다. HTTP 대상이 필요한 DAST를 위해 검사마다 서비스를 만들고, 성공·실패·취소 후 삭제를 확인한다. 최소 인스턴스 0, 최대 1, 작은 CPU/메모리로 제한한다. 온프레미스 실행기는 현재 연결 정보가 없으므로 선택지에 미구성으로 표시하고 요청을 거절한다.

## 설계와 구현 단위

1. API와 화면에 `execution_cloud`를 추가한다. 과거 요청·결과는 AWS로 해석한다. 요청 키의 대상 변경을 거부하고 실행 입력에 설정과 클라우드를 고정한다. 미구성 대상으로 요청하면 다른 클라우드로 우회하지 않는다.
2. 실행마다 고유한 Cloud Run 이름을 사용한다. ECR의 단일 linux/amd64 이미지를 Artifact Registry로 복사하고 manifest digest를 대조한다. 재빌드하지 않는다. 실행 ID·커밋·실제 revision·이미지를 대조하고 인증된 HTTP 응답을 확인한다.
3. 고객 컨테이너는 별도 역할 없는 SA와 샌드박스 VPC를 사용한다. Direct VPC egress로 나가는 요청을 제한하고 운영 DB·플랫폼 네트워크를 연결하지 않는다. Cloud Run은 IAM 인증이 필요하며 공개 익명 호출을 허용하지 않는다.
4. DAST는 플랫폼이 소유한 GCP 실행인지 Cloud Run API로 검증한 뒤 해당 origin만 검사한다. 인증 헤더는 검사기의 서버 측 요청에만 넣고 브라우저·근거·로그·다른 origin에 노출하지 않는다. 검사 중단·실행 소유권·정리 확인 원칙은 ECS와 같다.
5. Terraform은 전용 VPC·방화벽·Artifact Registry·SA·WIF/IAM을 관리한다. 수명이 짧은 Cloud Run 앱 서비스는 실행기가 생성·삭제한다. 동일 서비스를 Terraform과 실행기가 함께 소유하지 않는다. AWS 역할에서 단기 GCP 인증을 얻고 SA 키를 배포하지 않는다.
6. 실제 운영 VM의 네트워크·권한·DB 검사는 Cloud Run 샌드박스 검사와 별도다. GCP에서 실행됐다는 사실만으로 최종 VM의 모든 동작을 보장하지 않는다.

## 완료 기준

- AWS 회귀, 대상 변경 멱등성, GCP 설정·소유권·digest 불일치, 취소·응답 유실·재시작과 삭제 실패를 검증한다.
- 화면에서 선택 대상과 실제 실행 결과를 구분한다. 기존 AWS 이력을 GCP로 바꾸어 표시하지 않는다.
- 실제 Plan/Apply, 이미지 복사, 인증 없는 접근 거부, Cloud Run 실행·HTTP 검사·삭제, DAST 연결을 각각 기록한다. 미실행 항목은 완료로 쓰지 않는다.
- 서버와 앱 배포 전 실제 입력·변경 목록을 검토한다. 서비스 중단/삭제는 이 실행이 소유한 검사 자원에 한정한다.

## 근거

- [Cloud Run 서비스 간 인증과 WIF](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)
- [Cloud Run Direct VPC egress](https://docs.cloud.google.com/run/docs/configuring/vpc-direct-vpc)

## 검증·운영 기록

- 2026-11-04: AWS SSO 재로그인과 업무 계정 `616723161368` 확인 완료.
- `uv run pytest -q` (platform/backend): 177 passed, 1 deselected. AWS 회귀와 GCP 소유권·복구·취소 포함.
- DAST 계약/기존 SARIF/LLM 테스트 22개, GCP origin·인증 경계 테스트 7개, 배포 도구 테스트 32개 통과.
- `terraform validate`, `terraform test`: 검증 및 mock 1개 통과. linux_amd64/darwin_arm64 Provider lock 생성.
- 실제 Plan 28 add/0 change/0 destroy 검토 후 Apply 완료. GCS state `platform/sandbox-gcp`, 격리 대역 `10.33.0.0/24`.
- AWS 역할→GCP WIF 인증과 API 조회는 실제 확인했다. 이미지 복사 과정에서 작은 tmpfs 용량 제한을 확인해 Worker의 레이어 임시 저장을 디스크 볼륨으로 분리했다. 실제 실행·인증 HTTP·익명403·삭제 확인은 통과했다. 서비스 IAM 부여 직후 403은 최대120초의 제한된 재검사로 처리한다. DAST·최종 릴리스 검증은 진행 중.
- 아래 최종 운영 기록이 최신 상태다.

- 실제 검증 SSM `9f92cb45-e79e-4e78-9fc0-d7593de93477`: 동일 digest 복사·생성·인증 HTTP·익명403·삭제 성공.
- 추가 IAM 이름 조건 검증에서는 `run.services.get` 403이 발생했다. 조건을 지원한다고 가정하지 않고 기존 최소 custom role로 복원했다. 남은 검사 서비스는 소유권 확인 후 삭제했다. 코드의 소유권 검사는 유지한다. 전용 프로젝트 분리는 운영 확장 항목이다.
- 브라우저 전체16개와 build/format 검사 통과. 팀 DAST 모듈 통합1개 통과.

## 병합·릴리스·공개 검증 완료

- 구현 커밋 `0cb26f0`, [PR #29](https://github.com/2026-sbh-delta/delta-network-gateway/pull/29)는 전체 CI 통과 후 main `e944412`로 병합했다.
- [GitHub Platform Release 41814681634](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41814681634) **success**. 앱·DB·브라우저·기존 Terraform와 GCP root 검사를 통과하고 AWS ARM64 플랫폼 이미지를 배포했다.
- 기존 SSM `/delta-platform/runtime`에 GCP 두 값만 병합했다(버전7). 로그인·DB·Jenkins·SAST·DAST 설정은 보존했다. 새로운 SA 비밀 키는 만들지 않았다.
- 공용 도구 VM에서 DAST만 digest `sha256:f28ec15b2374468096a1a82a0d136908638f0a225b9cceaf66d7c5d5086a59d5`로 교체했다. 기존 환경·마운트·모델 설정·실행 기록과 SAST 프로세스는 유지했다. 이전 DAST 컨테이너는 중지 상태로 복구용 보존했다.
- DAST의 실제 AWS 역할 → scanner SA WIF → Cloud Run 자원·이미지 검증 → 인증 HTTP **200** 확인. 이 검사에 사용한 Cloud Run 서비스도 삭제했다.
- 공개 플랫폼 <https://d3udcz0zch0on.cloudfront.net>에서 GCP 선택 가능과 온프레미스 미구성을 확인했다. 기존 로그인과 서비스 2개를 보존했다.
- 실제 플랫폼 DAST 실행 `1ac12941-e346-4f30-9cad-9193ffeb855c`: `execution_cloud=gcp`, `runtime=cloud_run`, 원본 ECR/목적지 Artifact Registry digest 동일, 실제 revision, `runtime_check=passed`, `cleanup=stopped` 확인. Cloud Run 서비스가 남지 않았음을 별도 조회했다.
- 위 예제의 SAST 지원 후보는 **0건**이다. `dast_check=not_run`, `dast_reason=no_supported_xss_candidates`, `deployment_approved=false`로 기록했다. 실제 XSS를 재현했다고 주장하지 않는다.
- 실제 공개 웹의 GCP 선택·실행 결과와 데스크톱/모바일 화면을 확인했다. JavaScript 오류 0, 가로 넘침 0. 기존 전체 아키텍처 HTML/PNG의 GCP 실행기 미구현 표시도 현재 상태로 갱신했다.
- IAM 복원 후 최종 실제 Terraform Plan **No changes**. 구현과 기준 네트워크는 일치한다. 단기 Cloud Run 서비스는 Worker 소유이므로 Terraform state에 넣지 않는다.

## 남은 범위와 운영 한계

- 온프레미스 실행기는 미연결이며 다른 클라우드로 자동 우회하지 않는다.
- Cloud Run 검사는 최종 운영 VM의 DB·볼륨·사설망·권한 검증을 대신하지 않는다. 실제 고객 VM 배포·앱/DB 전환은 별도 작업이다.
- 이번 실제 예제에서는 취약점 재현 브라우저 시나리오를 실행하지 않았다. 지원 후보가 있는 통제된 앱으로 추가 검증한다.
- 해커톤 비용을 위해 검사 실행은 전체 플랫폼에 한 번에 1개다. Cloud Run 최소 0/최대 1, 격리된 10.33.0.0/24 대역, 이미지 저장소1일 정리 정책을 사용한다.
- Cloud Run 관리 custom role은 현재 공유 프로젝트 범위다. 코드의 소유권 검사를 우회할 수 없는 운영 권한 관리가 필요하며, 서비스 확장 시 검사 전용 프로젝트를 분리한다.

증거 원본은 ignored `platform/.runtime/gcp/`의 공개 API 결과·브라우저 증거와 SSM 실행 기록에 보관한다. 인증 값·실제 tfvars·state·Plan은 커밋하지 않는다.
