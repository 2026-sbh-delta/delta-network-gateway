# 플랫폼 U3: 샌드박스 실행 확인

- 기준일: 2026-11-03. 브랜치 `feat/platform-sandbox`, 기준 main `febabd8`(PR #15 병합).
- 승인: 공개 플랫폼에서 성공한 빌드 → ECS 실행 → 실제 이미지·헬스 확인 → 결과 표시와 충돌·정리를 제안했고 사용자가 `Go ahead.`로 구현을 승인했다. 구현 시작과 커밋 전 메시지 공유를 안내했다.
- 상태: 실행 확인 API·Worker·화면과 전용 AWS 기반 구현·검증 완료. 로컬 플랫폼 API/Worker에서 실제 GitHub → CodeBuild → ECS 실행·정리까지 확인했다. AWS 기반 Apply·SSM 설정 병합 완료. 기능 커밋 `652fb93`을 origin에 푸시하고 [PR #16](https://github.com/2026-sbh-delta/delta-network-gateway/pull/16)을 열었다. 공개 웹 U3 Release와 main 병합은 아직 수행하지 않았다.

## 목적과 범위

개발자가 성공한 빌드의 고정 이미지를 샌드박스에서 실행하고 실제 실행된 digest와 헬스 체크 결과를 플랫폼에서 확인한다. 이 단위는 **실행 확인**이며 SAST·DAST·AI 보안 검사 통과나 운영 배포 승인이 아니다. 고객 VM 배포·트래픽 전환·관측 통합·DB 이전은 후속 범위다.

## 설계와 순서

1. 플랫폼의 성공한 빌드를 참조하는 `sandbox_check` 작업을 저장한다. 다른 서비스의 빌드·변경된 이미지·중복 접수는 거부하거나 동일 작업으로 돌려준다.
2. 기존 `services/delta-staging`의 AWS ECS 실행 코드를 라이브러리로 재사용한다. 플랫폼 Worker는 VM 역할로 호출하며, 고객 태스크에 플랫폼 권한을 전달하지 않는다. 별도 공개 관리 API나 장기 키를 추가하지 않는다.
3. 작업 DB의 임대·외부 개정판 기록과 전용 실행 공간 잠금으로 중복 실행을 막는다. 외부 서비스 소유권도 확인하고 다른 실행을 강제 교체하지 않는다. Worker 재시작은 저장된 개정판을 조회해 이어 확인한다.
4. 실제 실행 태스크의 digest와 ALB 헬스 체크를 확인한다. 성공·실패·중단 때 자기 실행만 정리하고 정리 실패를 성공으로 숨기지 않는다.
5. 웹에 실행 요청·진행 단계·중단 요청·결과·이력을 표시한다. `실행 확인 통과 / 보안 검사 미실행`을 명시한다.
6. 실제 PostgreSQL, AWS SDK 계약, 중복·복구·충돌·취소·정리 실패, 브라우저를 검증한 뒤 실제 AWS 실행을 대조한다.

## 기존 환경과 분리

- 읽기 전용 AWS 확인: 616723161368/ap-northeast-2의 `delta-lab-staging`은 desired/running=1, revision 8, `DELTA_RUN_ID=t8-board`, 앱 포트 8080이다. 이 실행을 교체하거나 중지하지 않는다.
- 플랫폼 샌드박스는 별도 ECS 서비스·대상 그룹·실행 역할로 분리한다. 플랫폼 VPC `10.80.0.0/16`과 NAT를 재사용하고, ALB용 `10.80.2.0/24`·태스크용 `10.80.3.0/24`를 별도로 생성했다. 최소 Fargate 크기와 동시 실행 1개로 제한하며 실행 후 태스크는 0개로 정리한다.
- 기존 도구의 JSON Registry는 단일 프로세스 전제이고 진행 중 재시작을 실패로 처리한다. 플랫폼에서는 작업 DB를 상태 기준으로 쓰고 기존 AWS 실행 코드를 재사용한다.
- 기존 도구의 고정 앱 포트·헬스 경로와 플랫폼 등록값의 연결을 명시적으로 검증한다. 잘못된 포트에서 확인한 결과를 성공으로 기록하지 않는다.

## 완료 기준

- 빌드 결과와 실제 ECS 태스크 digest가 일치하고 새 태스크의 헬스 체크가 통과해야 실행 확인 성공이다.
- 재시도·프로세스 재시작이 별도 실행이나 타 서비스 종료를 만들지 않는다.
- 미설정·외부 실행 중·digest 불일치·실행 실패·정리 실패가 각각 확인 가능한 상태로 남는다.
- 플랫폼 로그인·API 보호·소스 확인·빌드 기능을 유지한다.
- 실제 검증 결과, 비용이 남는 자원, 배포·Git 상태를 아래에 구분해 기록한다.

## 검증·운영 기록

2026-11-03에 다음 검증을 실제 수행했다.

| 구분 | 명령·방법 | 결과 |
| --- | --- | --- |
| 플랫폼 API/DB/Worker | `cd platform/backend && uv run pytest -q` | 실제 PostgreSQL에서 88 passed, 실제 GitHub 별도 테스트 1 deselected |
| ECS 라이브러리 회귀 | `uv run --project platform/backend --with pytest-asyncio pytest -q -c services/delta-staging/pyproject.toml services/delta-staging/tests` | 36 passed |
| 배포 설정 경계 | `python3 -m unittest discover -s platform/deploy/tests -q` | 20 passed |
| 웹 | `cd platform/web && npm run build && npm run test:e2e` | TypeScript/번들 성공, 브라우저 7 passed. 데스크톱·390px 모바일 캡처를 확인 |
| 형식·정적 검사 | backend/deploy `ruff check`, `ruff format --check`, web `npm run format:check`, `git diff --check` | 통과 |
| 컨테이너 | `docker buildx build --platform linux/arm64 --load -t delta-platform:u3-check -f platform/Dockerfile .` 및 컨테이너 안 두 패키지 import | 성공. 저장소 루트 컨텍스트를 allowlist로 제한 |
| 샌드박스 Terraform | `init -backend=false -lockfile=readonly`, `validate`, `test` | mock 2개 run 통과. 태스크 0개 대기·사설망·권한·다른 AZ 거부 검사 |
| 실제 Plan/Apply | 별도 S3 key `platform/sandbox.tfstate`, SSO `delta-platform`, 계정 616723161368/서울 | 24개 생성, 기존 자원 교체·삭제 없음 |
| IAM | Access Analyzer·역할 시뮬레이터·플랫폼 VM SSM 조회 | findings 0, 허용/범위 밖 거부 12건, 실제 VM 역할의 ECS·헬스 조회 통과 |
| 실제 실행 | ignored `.runtime/u3-live-check.py`의 로컬 API/Worker와 실제 AWS | 공개 소스 확인·CodeBuild 성공·실제 task digest/ALB healthy·종료 모두 확인 |
| 최종 기반 상태 | 두 root의 `plan -detailed-exitcode` | 플랫폼·샌드박스 모두 No changes, 종료 코드 0 |

실제 검증 세부:

- 공개 `docker/welcome-to-docker`, 고정 커밋 `68c1b9f87c41fb3fef2667e27149865c2f42d1eb`, 앱 포트 3000, 경로 `/`.
- 로컬 검증용 빌드 `8e49d84a-87fd-4426-b8b6-f7425b8d9876`, 실행 `bc961128-2fdc-4756-9481-174f185276d2`.
- 빌드 및 실제 실행 digest `sha256:0f623be411b117a6cc01190d4891e0aed24c52989ab46d5b84cd62442e0c24df` 일치.
- ECS `delta-platform-sandbox:2`, 태스크 `c549cb06ed8744909a2864b45ab61f7f`. 20:33:25 KST 헬스 확인, 20:34:14 KST 정리 완료. `runtime_check=passed`, `security_check=not_run`, `cleanup=stopped`.
- 종료 후 desired/running/pending 모두 0. 기존 `delta-lab-staging:8`은 desired/running=1 유지.
- AWS에 새 내부 ALB·전용 태스크 공간이 남는다. 태스크 비용은 종료됐으나 ALB·저장 비용은 남는다. 플랫폼 VM·NAT의 크기는 변경하지 않았다.
- SSM `/delta-platform/runtime` 버전 3에 실행기 공개 설정만 병합했다. 로그인·DB·기존 빌드 설정은 보존했다. 실행 중인 공개 웹은 아직 U2 버전이다.

검증 중 수정:

- 첫 백엔드 테스트는 저장소 루트에서 실행해 `alembic.ini` 위치를 찾지 못했다. 올바른 `platform/backend`에서 재실행해 위 결과를 확인했다.
- 새 Terraform data source의 NAT ENI 속성을 provider 스키마와 맞췄다. 태스크 정의의 AWS 반환 기본값(`capabilities.add=[]`)을 명시해 불필요한 교체 Plan을 없앴다.
- `ecs:DescribeTaskDefinition`, `elasticloadbalancing:DescribeTargetHealth`는 AWS에서 리소스 단위 권한을 지원하지 않아 조회만 `Resource=*`로 분리했다. 변경 권한·PassRole은 전용 자원에 제한한다. 역할 정책 1개 수정 Plan을 확인해 적용하고 다시 검증했다.

검증 한계와 다음 단계:

- 실제 워크플로는 **로컬 시험 DB/API/Worker + SSO 권한으로 AWS 호출**했다. 공개 웹의 U3 동작과 VM 역할로 전체 실행하는 Release 검증은 PR 병합 후 수행한다. 역할 시뮬레이터와 VM의 읽기 호출 검증을 전체 운영 흐름 검증으로 표현하지 않는다.
- SAST·DAST·AI 보안 판정, 고객 운영 VM 배포, 다중 고객 격리·고가용성·부하 검증은 미실행이다.
- DB schema는 `0003`이며 기존 U2 앱으로 이미지 롤백만 하면 readiness에 실패한다. 배포 전 백업 또는 스키마 호환 수정 릴리스가 필요하다.
- `feat(platform): add ECS sandbox runtime checks` 메시지를 제시했고 사용자의 `Go ahead.`로 커밋·푸시·PR 생성·CI 확인을 승인받았다. 게시 결과는 아래에 기록하며 사용자가 PR을 병합한다.

## Git 게시

2026-11-03에 `git fetch origin main`으로 기준 main `febabd8`과 차이가 없음을 확인했다. `git diff --check`와 커밋 파일 검사 후 기능 커밋 `652fb93`을 생성하고 `git push -u origin feat/platform-sandbox`를 완료했다. 자격 증명·실제 tfvars/backend/state/Plan·검증용 실행 파일은 제외했다.

`gh pr create`로 [PR #16](https://github.com/2026-sbh-delta/delta-network-gateway/pull/16)을 열었으며 충돌 없이 병합 가능한 상태를 확인했다. GitHub Platform checks의 앱 시험과 네 Terraform root 검증 결과는 PR Checks를 따른다. `docs(platform): record sandbox PR publication` 후속 커밋은 이 게시 기록을 추가한다.

사용자가 병합한 뒤 GitHub Release와 공개 웹의 전체 흐름을 검증한다. PR 생성·기반 Apply를 공개 웹 U3 배포 완료로 표현하지 않는다.
