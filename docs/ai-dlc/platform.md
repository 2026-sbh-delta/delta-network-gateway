# 개발자용 플랫폼

## 단계와 승인 범위

- 기준일: 2026-11-03 (Asia/Seoul).
- 사용자 지시: `Okay, start the task by making branch for platform. Don't forget to create dir for platform and separate things.`
- 위 지시에 따라 Ideation 방향을 승인된 범위로 기록하고 전용 브랜치·디렉터리 분리(U0)를 수행했다. U1은 사용자 구현 승인 후 [구현·검증](platform-u1.md)과 [AWS 배포](platform-aws.md)를 완료했다.
- 확정: 공개 GitHub 저장소 + Dockerfile, 운영 VM, 기존 ECS 샌드박스, Terraform과 플랫폼 CI 연동, AWS → GCP·온프레미스 DB 이전 준비.
- 로그인은 사용자 정정에 따라 고정 ID·비밀번호 한 쌍과 공통 작업 공간으로 시작한다. U1에 회원가입·계정 관리·소셜 로그인·개인별 권한은 없다.
- 문서는 한국어로 작성하고 코드 주석은 필요하면 영어로 작성한다.
- 구현 전 작업 범위를 공유한다. 추가 지시 `Dont forget to tell me before you commit.`에 따라 커밋 전에 변경 내용·검증 결과를 알린다. 푸시는 별도 지시 범위다.
- U1과 GitHub OIDC 연결·실제 Plan/Release 검증을 완료했다. 사용자가 U2 [PR #14](https://github.com/2026-sbh-delta/delta-network-gateway/pull/14)를 병합한 뒤 GitHub Infra Plan/Apply와 Release로 운영 배포했다. 운영 역할의 CodeBuild 조회 권한을 보완하고 공개 HTTPS에서 실제 빌드·결과·로그·브라우저를 검증했다. 해당 권한 수정 PR #15도 main에 병합됐다. [U2 기록](platform-u2.md)을 따른다.

## Ideation

- 문제: 개발자가 빌드, 검사, 인프라 준비, 배포, 관측과 이전 상태를 여러 도구에서 따로 확인해야 한다.
- 사용자: 자신의 서비스를 배포하고 운영하는 개발자.
- 성공 기준: 공개 저장소의 특정 커밋을 빌드하고 ECS에서 검사한 동일 이미지를 VM에 배포한다. 플랫폼에서 진행 상황·주소·실제 상태를 확인한다.
- Scope: 고정 로그인·서비스 등록·작업 상태, 공개 소스 확인·빌드, 기존 검사 도구/Terraform/VM 배포 연결, 관측 조회, 같은 엔진의 DB 이전 준비.
- Non-goals: 비공개 저장소/GitHub App, 모든 프레임워크 자동 감지, 운영 Kubernetes/serverless, DB 엔진 변환. 복제 엔진·관측 저장소 전체를 새로 만드는 것도 기본 범위가 아니다.

## Inception — 설계 방향

### 요구사항

- 저장소·빌드 경로·앱 포트·헬스 체크를 등록하고 실제 실행을 커밋과 이미지 digest로 식별한다.
- 단계별 상태·실패 이유·검사 증거를 조회한다. Terraform 생성 후 VM 배포 준비 여부를 별도로 확인한다.
- 기존 ECS/검사 도구를 호출하고 통과한 이미지로 배포한다.
- 기존 CSP 모니터링/OTel 데이터를 서비스·환경 기준으로 조회한다.
- DB 이전은 AWS → GCP를 먼저 연결하고 온프레미스로 확장한다. 엔진·버전·DB 유형은 미확정이다.

### 운영 품질 요구사항

- 고정 계정의 로그인 보호, 서버 측 자격 증명 관리, 민감정보 로그 제외. U1은 공통 작업 공간이며 개인별 권한·감사를 제공한다고 표현하지 않는다.
- 실제 DB에 작업 상태·이벤트·외부 실행 ID 저장. 재시작 시 외부 상태와 대조해 재개.
- 중복 요청 방지, 같은 서비스의 충돌하는 배포 직렬화.
- 고객 코드 빌드와 인프라 관리의 권한·실행 환경 분리.
- 미연결 도구를 성공으로 표시하지 않음. 자동 재시작만으로 HA를 주장하지 않음.

### Architecture

```text
개발자 → platform/web → backend/api → 플랫폼 DB
                                     ↕
                              backend/worker
                               ├─ CodeBuild → 고객 이미지 전용 ECR
                               ├─ ECS / SAST·DAST·AI 도구
                               ├─ Terraform → 전용 출력 → VM 준비 검사
                               ├─ automation → VM 앱 배포·헬스 체크
                               └─ CSP 모니터링 / OTel 저장소 조회
```

웹은 API만 호출한다. API/Worker는 공통 Python 패키지 `delta_platform`을 공유하고 별도 프로세스로 실행한다. 기존 `terraform/`와 `services/`의 도구를 어댑터로 연결한다.

제안 스택은 React + TypeScript, FastAPI + PostgreSQL, Ansible이다. U1 로그인은 고정 ID·비밀번호와 서버 세션이다. 라이브러리 버전·CI 인증·이미지 저장소는 각 구현 단위에서 확정한다.

### Unit of Work / Acceptance Criteria

| Unit | 내용 | 완료 기준 | 상태 |
| --- | --- | --- | --- |
| U0 | 브랜치·디렉터리·문서 | `feat/platform`, 역할별 경계, 설명 문서·진행 기록 | 준비 완료 |
| U1 | 고정 로그인·서비스 등록·소스 사전 검사 | 실제 DB 공유, 인증 보호·재시작 보존·중복 요청·GitHub 확인 검증 | [구현·검증 완료](platform-u1.md), AWS 배포 완료 |
| U2 | 공개 저장소·빌드 | 특정 커밋의 이미지·결과 저장, 관리 권한 없는 빌드 | [운영 배포·공개 HTTPS 실제 빌드 검증 완료](platform-u2.md), PR #15 병합 완료 |
| U3 | ECS 검사 연결 | 검사한 digest·증거 연결, 미통과/미연결 시 배포 차단 | [실행 확인 구현·실제 AWS 검증 완료](platform-u3.md), 공개 웹 Release·보안 검사 연결은 후속 |
| U4 | Terraform·VM 배포 | handoff 검증·준비 검사·배포·헬스 체크·실패 처리 | 미착수 |
| U5 | 관측 조회 | 실제 지표·로그·트레이스, 조회 실패·수집 시각 표시 | 미착수 |
| U6 | DB 이전 | 호환성·복제·최종 동기화·단일 쓰기 전환·검증, 복구 경계 명시 | 미착수 |

## Construction — U0

- Design: `platform/` 아래 웹, 백엔드 API/Worker, 계약, VM 배포 자동화, 설명 문서를 분리한다.
- Plan: 브랜치 생성 → 디렉터리/역할 문서 → HTML 포함 → 루트 문서 연결 → 구조·링크·diff 검증.
- Approval: 현재 사용자의 브랜치 생성·디렉터리 분리 지시에 포함된다.
- Implementation: 구조와 문서를 준비했다. 실행 코드와 클라우드 자원은 추가하지 않았다.
- Test / Review: 아래 구조·문서 검증과 애플리케이션 테스트를 구분한다.

## 연결 전에 확인할 사항

- 최신 스테이징 도구의 고객 이미지는 x86_64다. U2는 `linux/amd64` 단일 manifest를 만든다. 플랫폼 자체 VM/이미지의 ARM64와 구분한다.
- 스테이징 도구 PR #6은 main에 병합됐고 AWS 실배포 검증 기록이 있다. U3에서 고정 digest·인증·접속 계약을 확인해 연결한다.
- DAST의 로컬 토큰과 플랫폼 사용자 인증은 별개다. 서비스 간 인증을 설계한다.
- 기존 VM의 외부 통신 제한에 맞춰 이미지·로그·비밀값 접근 경로와 권한을 준비해야 한다.
- Terraform 출력 스키마, LB/주소/요청 전환, 관측 조회·보관, 실제 DB 유형은 미확정이다.

## Construction — U1

- 사용자의 `Proceed.`에 따라 API·DB·로그인·검증 계획을 구체화했다. 후속 정정으로 운영자 계정 생성 방식도 제외하고 고정 계정 한 쌍으로 확정했다.
- [U1 설계와 순서](platform-u1.md), [HTTP 계약](../../platform/contracts/u1-http.md), [DB 구조](../../platform/contracts/u1-data.md)를 함께 검토한다.
- 후속 `You can start implementation.`으로 구현 승인을 받았다. Python API·Worker·PostgreSQL·React를 구현하고 실제 DB 56개, 실제 GitHub 1개, 브라우저 3개 시험을 통과했다.

## Operation

사용자 지시로 AWS 사설 EC2·CloudFront 기반에 배포했다. 실제 HTTPS·재시작·백업 복원과 최종 No changes를 확인했다. [AWS 결과](platform-aws.md)와 [GitHub 전환 안내](../../platform/deploy/GITHUB.md)를 따른다. PR #7·#8·#9는 병합됐고 GitHub OIDC Plan/Release도 성공했다. Free/private 저장소이므로 GitHub Environment 없이 main 전용 역할을 사용한다. 고객 앱 배포 U4와 플랫폼 자체 배포를 구분한다.

## 검증 기록

| 날짜 | 명령·확인 | 결과 |
| --- | --- | --- |
| 2026-11-02 | `git status --short --branch` (시작 전) | `main...origin/main`, 작업 트리 깨끗함 |
| 2026-11-02 | `git switch -c feat/platform` | 로컬 브랜치 생성·전환 완료 |
| 2026-11-02 | `git diff --check` | 종료 코드 0, 추적 파일 diff 형식 오류 없음 |
| 2026-11-02 | `python3` 구조·문서 검사 | 예정 디렉터리 9개 존재, 로컬 문서 링크 42개 유효, HTML 내부 링크 유효·원본과 동일 |
| 2026-11-02 | `git check-ignore --no-index --stdin` 기반 검사 | 로컬 설정·생성물 경로 6개 제외, 예제·lockfile·문서 경로 5개 유지 확인 |
| 2026-11-02 | `git status --short --branch --untracked-files=all` 및 변경 검토 | `feat/platform`, 변경은 플랫폼 구조·문서와 루트 안내에 한정. 기존 Terraform·서비스 실행 코드 변경 없음 |

위 표는 U0 당시 검증이다. 이후 애플리케이션 검증은 [U1 기록](platform-u1.md), 클라우드 검증은 [AWS 기록](platform-aws.md)을 따른다.

## Git 기록

- U0 변경 내용·검증 결과와 Conventional Commit 메시지를 공유한 뒤 `Go ahead.`로 로컬 커밋 승인을 받았다.
- U0 커밋: `0567762` — `chore(platform): scaffold isolated platform workspace`.
- 2026-11-03 사용자 `commit and open PR` 지시로 U1 구현·AWS 배포·GitHub 워크플로를 `5390c63` (`feat(platform): add developer workspace and AWS deployment workflows`)으로 커밋하고 `git push -u origin feat/platform` 성공을 확인했다.
- `gh pr create --base main --head feat/platform`으로 [PR #7](https://github.com/2026-sbh-delta/delta-network-gateway/pull/7)을 생성했다. GitHub 조회 결과 OPEN, MERGEABLE이며 PR 테스트 실행이 시작됐다. 병합은 실행하지 않았다.
- 후속 커밋 전에도 변경 내용과 검증 결과를 사용자에게 알린다.
