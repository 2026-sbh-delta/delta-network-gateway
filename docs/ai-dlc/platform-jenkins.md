# 플랫폼 Jenkins 빌드 연결

- 기준일: 2026-11-03. `feat/platform-jenkins`, 별도 worktree, main `fa05807` 기준.
- 승인: 사용자가 빌드 실행기를 Jenkins로 정정하고 다음 작업 구현을 지시했다. Jenkins가 아직 없어 구성도 준비하도록 확인했다.
- 상태: 구현·로컬 실제 Jenkins/ECR/ECS 검증 완료. 빌드 전용 IAM 기반 적용 완료. GCP 네트워크 작업은 기존 worktree에 보존했다. Jenkins 운영 EC2·HTTPS·공개 플랫폼 전환·푸시는 미실행이다. 사용자가 `feat(platform): integrate Jenkins builds and SAST checks` 메시지의 로컬 커밋을 승인했다.

## 목적과 범위

개발자는 기존 플랫폼에서 공개 저장소의 확인된 커밋을 Jenkins로 빌드하고, 고정 이미지 digest와 로그를 확인한 뒤 기존 ECS 실행 확인으로 이어간다. 플랫폼 CI의 GitHub Actions와 고객 앱 빌드 실행기는 별개다.

이번 단위는 Jenkins 어댑터·관리자 소유 Pipeline·컨트롤러/에이전트 구성·결과와 로그 표시·복구 검증이다. 이전 CodeBuild 기록과 ECR 이미지를 보존하며 CodeBuild로 자동 대체하지 않는다. 이어서 [SAST 결과 연결](platform-security.md)을 구현했다. 기존 ECS 실행 확인은 태스크를 종료하므로 DAST에는 별도 실행 수명·접근 계약이 필요하다.

## 설계와 구현 순서

1. 새 빌드는 Jenkins가 기본이다. 실행기·Job·저장소를 요청 snapshot에 고정하고 과거 기록은 해당 실행기의 어댑터로 읽는다.
2. 소스의 Jenkinsfile을 실행하지 않는다. 플랫폼이 관리하는 Job이 GitHub의 고정 commit을 내려받아 Dockerfile을 전용 에이전트에서 빌드한다. 컨트롤러 executor는 0이다.
3. Jenkins POST는 자체 중복 방지 토큰이 없으므로 요청 intent를 먼저 저장한다. 응답 유실 시 queue/build의 실행 ID 파라미터로 기존 실행을 찾는다. 찾지 못하거나 중복이면 재접수하지 않고 확인 필요로 종료한다.
4. Job 성공만 믿지 않고 요청 ID·커밋·이미지 메타데이터와 ECR digest를 비교한다. 결과 없는 성공은 실패다.
5. 인증 정보는 서버 설정의 SecretStr로만 보관한다. 설정한 Jenkins 주소 밖의 URL/redirect를 따라가지 않는다. 로그는 크기가 제한된 텍스트로 반환한다.
6. HTTP API/DB/Worker 회귀, 응답 유실·재시작·다른 실행/이미지 거부, 빌드 스크립트 입력 검증과 브라우저를 확인한다. 실제 Jenkins 시험과 운영 서버 배포 상태는 구분한다.

## 운영 전환

Jenkins와 전용 빌드 에이전트를 준비하고 제한된 API 계정/토큰을 플랫폼 서버 설정에 넣는다. 기존 빌드가 없는 시점에 플랫폼을 릴리스한다. 과거 CodeBuild 자원 삭제는 이 작업에 포함하지 않는다. 커밋 전 Conventional Commit 메시지를 공유한다.

공식 근거: [Jenkins Remote API](https://www.jenkins.io/doc/book/using/remote-access-api/), [Controller isolation](https://www.jenkins.io/doc/book/security/controller-isolation/).

## 검증 기록

2026-11-03, 현재 worktree에서 실행했다.

| 검증 | 결과 |
| --- | --- |
| `cd platform/backend && uv run pytest -q` | SAST 포함 122개 통과, live GitHub 1개 선택 제외. 추가 migration 검증은 SAST 기록 참조 |
| `python3 -m unittest discover -s platform/deploy/tests -v` | 24개 통과: 배포 경계·Jenkins 입력·SSM 병합 포함 |
| `cd platform/web && npm run build && npm run test:e2e` | 빌드 성공, 브라우저 9개 통과. 외부 응답 fixture, 실제 API/Worker/PostgreSQL |
| Ruff·Prettier | 코드 검사·형식 검사 통과 |
| `cd platform/deploy/jenkins/identity && terraform validate && terraform test` | validate 및 mock 2개 통과 |
| IAM 실제 Plan/Apply | 역할·정책·instance profile 3개 생성. 임시 SSO 검증 신뢰를 제거한 최종 Plan은 No changes |

### 실제 흐름

- 로컬 컨트롤러 `127.0.0.1:18081`, executor 0. 별도 Docker-in-Docker 에이전트 executor 1. 플랫폼 API 계정은 Job 조회 200, 관리자 `/script`는 403을 확인했다.
- 공개 `docker/welcome-to-docker`, 커밋 `68c1b9f87c41fb3fef2667e27149865c2f42d1eb`을 실제 Jenkins #1에서 빌드했다. 요청 ID는 `c11607b2-32ef-4246-985b-b3ed9ec5739e`다.
- `Content-Encoding` 중복 해석 오류를 수정한 뒤 Worker를 재실행했다. 저장된 큐에서 동일 #1을 회수했고 두 번째 빌드를 접수하지 않았다.
- ECR 결과 `sha256:ebbfface3da28b1532f830626f807fae021ad997714cddb12a4dfb5ffdae935d`를 실제 조회해 대조했다. 제한된 빌드 역할의 단기 세션만 에이전트에 전달했다.
- 같은 digest를 ECS `delta-platform-sandbox:4`에서 실행했다. 검사 ID `74184aae-0350-42ac-80d2-66d9333d7052`, 앱 3000 포트 `/` 응답과 실행 digest를 확인하고 태스크를 종료했다. 최종 desired/running/pending은 모두 0이다. 기존 팀 서비스 `delta-lab-staging:8`은 desired/running 1을 유지했다.
- 실제 로컬 웹의 Jenkins 이름·로그·digest·ECS 결과를 데스크톱과 모바일에서 확인했다. 운영 공개 웹에서 새 Jenkins 기능을 검증한 것은 아니다.
- 검증 JSON·화면은 ignored `platform/.runtime/jenkins-live-result.json`, `jenkins-desktop.png`, `jenkins-mobile.png`에 있다. 테스트 후 로컬 컨트롤러/에이전트를 종료했고 임시 AWS 인증 파일을 삭제했다. 이력 볼륨과 ECR 이미지는 보존했다.

### 운영에 남은 작업

IAM `delta-platform-jenkins-agent`는 적용했지만 VM에 연결하지 않았다. 최종 trust는 `ec2.amazonaws.com`만 허용한다. 운영 EC2/전용 에이전트·HTTPS·토큰 설정, 플랫폼 Release와 공개 웹 실제 검증은 남아 있다. 기존 `/delta-platform/runtime`, CodeBuild, 운영 DB는 이번 작업에서 전환하지 않았다. 커밋/PR 후 운영 연결 순서와 복구는 [실행 안내](../../platform/deploy/jenkins/README.md)를 따른다.
