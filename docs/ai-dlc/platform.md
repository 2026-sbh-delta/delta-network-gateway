# 개발자용 플랫폼

## 단계와 승인 범위

- 기준일: 2026-11-02 (Asia/Seoul).
- 사용자 지시: `Okay, start the task by making branch for platform. Don't forget to create dir for platform and separate things.`
- 위 지시에 따라 Ideation 방향을 승인된 범위로 기록하고 전용 브랜치·디렉터리 분리(U0)를 수행한다. U1 이후는 세부 설계·구현 계획 단계다.
- 확정: 공개 GitHub 저장소 + Dockerfile, 운영 VM, 기존 ECS 샌드박스, Terraform과 플랫폼 CI 연동, AWS → GCP·온프레미스 DB 이전 준비.
- 구현 전 작업 범위를 공유한다. 추가 지시 `Dont forget to tell me before you commit.`에 따라 커밋 전에 변경 내용·검증 결과를 알린다. 푸시는 별도 지시 범위다.
- 기능 코드·패키지 설치·클라우드 변경은 아직 실행하지 않았다.

## Ideation

- 문제: 개발자가 빌드, 검사, 인프라 준비, 배포, 관측과 이전 상태를 여러 도구에서 따로 확인해야 한다.
- 사용자: 자신의 서비스를 배포하고 운영하는 개발자.
- 성공 기준: 공개 저장소의 특정 커밋을 빌드하고 ECS에서 검사한 동일 이미지를 VM에 배포한다. 플랫폼에서 진행 상황·주소·실제 상태를 확인한다.
- Scope: 서비스 등록·권한·작업 상태, 공개 소스 빌드, 기존 검사 도구/Terraform/VM 배포 연결, 관측 조회, 같은 엔진의 DB 이전 준비.
- Non-goals: 비공개 저장소/GitHub App, 모든 프레임워크 자동 감지, 운영 Kubernetes/serverless, DB 엔진 변환. 복제 엔진·관측 저장소 전체를 새로 만드는 것도 기본 범위가 아니다.

## Inception — 설계 방향

### 요구사항

- 저장소·빌드 경로·앱 포트·헬스 체크를 등록하고 실제 실행을 커밋과 이미지 digest로 식별한다.
- 단계별 상태·실패 이유·검사 증거를 조회한다. Terraform 생성 후 VM 배포 준비 여부를 별도로 확인한다.
- 기존 ECS/검사 도구를 호출하고 통과한 이미지로 배포한다.
- 기존 CSP 모니터링/OTel 데이터를 서비스·환경 기준으로 조회한다.
- DB 이전은 AWS → GCP를 먼저 연결하고 온프레미스로 확장한다. 엔진·버전·DB 유형은 미확정이다.

### 운영 품질 요구사항

- 프로젝트별 권한, 서버 측 자격 증명 관리, 민감정보 로그 제외.
- 실제 DB에 작업 상태·이벤트·외부 실행 ID 저장. 재시작 시 외부 상태와 대조해 재개.
- 중복 요청 방지, 같은 서비스의 충돌하는 배포 직렬화.
- 고객 코드 빌드와 인프라 관리의 권한·실행 환경 분리.
- 미연결 도구를 성공으로 표시하지 않음. 자동 재시작만으로 HA를 주장하지 않음.

### Architecture

```text
개발자 → platform/web → backend/api → 플랫폼 DB
                                     ↕
                              backend/worker
                               ├─ 공개 소스 빌드 CI → 이미지 저장소
                               ├─ ECS / SAST·DAST·AI 도구
                               ├─ Terraform → 전용 출력 → VM 준비 검사
                               ├─ automation → VM 앱 배포·헬스 체크
                               └─ CSP 모니터링 / OTel 저장소 조회
```

웹은 API만 호출한다. API/Worker는 공통 Python 패키지 `delta_platform`을 공유하고 별도 프로세스로 실행한다. 기존 `terraform/`와 `services/`의 도구를 어댑터로 연결한다.

제안 스택은 React + TypeScript, FastAPI + PostgreSQL, Ansible이다. 정확한 버전·인증 공급자·CI 인증·이미지 저장소는 각 구현 단위의 설계에서 확정한다.

### Unit of Work / Acceptance Criteria

| Unit | 내용 | 완료 기준 | 상태 |
| --- | --- | --- | --- |
| U0 | 브랜치·디렉터리·문서 | `feat/platform`, 역할별 경계, 설명 문서·진행 기록 | 준비 완료 |
| U1 | 서비스 등록·권한·작업 상태 | API/Worker의 실제 DB 공유, 재시작 보존·권한 격리·중복 요청 검증 | 세부 설계 예정 |
| U2 | 공개 저장소·빌드 | 특정 커밋의 이미지·결과 저장, 관리 권한 없는 빌드 | 미착수 |
| U3 | ECS 검사 연결 | 검사한 digest·증거 연결, 미통과/미연결 시 배포 차단 | 미착수 |
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

- AWS 시험 VM/ECS는 ARM64, GCP 코드는 x86_64다. amd64 통일은 추천안이며 변경하지 않았다.
- 스테이징 구현은 문서상 `feat/add-staging-tool`에 있으며 현재 작업 트리에는 미병합이다. 최신 공개 ALB 결정과 과거 internal ALB 설명을 구분한다.
- DAST의 로컬 토큰과 플랫폼 사용자 인증은 별개다. 서비스 간 인증을 설계한다.
- 기존 VM의 외부 통신 제한에 맞춰 이미지·로그·비밀값 접근 경로와 권한을 준비해야 한다.
- Terraform 출력 스키마, LB/주소/요청 전환, 관측 조회·보관, 실제 DB 유형은 미확정이다.

## Operation

플랫폼 배포, Terraform Plan/Apply, 클라우드 변경은 미실행이다. 기능별 배포·관측·복구·Runbook과 실제 결과를 구분해 기록한다.

## 검증 기록

| 날짜 | 명령·확인 | 결과 |
| --- | --- | --- |
| 2026-11-02 | `git status --short --branch` (시작 전) | `main...origin/main`, 작업 트리 깨끗함 |
| 2026-11-02 | `git switch -c feat/platform` | 로컬 브랜치 생성·전환 완료 |
| 2026-11-02 | `git diff --check` | 종료 코드 0, 추적 파일 diff 형식 오류 없음 |
| 2026-11-02 | `python3` 구조·문서 검사 | 예정 디렉터리 9개 존재, 로컬 문서 링크 42개 유효, HTML 내부 링크 유효·원본과 동일 |
| 2026-11-02 | `git check-ignore --no-index --stdin` 기반 검사 | 로컬 설정·생성물 경로 6개 제외, 예제·lockfile·문서 경로 5개 유지 확인 |
| 2026-11-02 | `git status --short --branch --untracked-files=all` 및 변경 검토 | `feat/platform`, 변경은 플랫폼 구조·문서와 루트 안내에 한정. 기존 Terraform·서비스 실행 코드 변경 없음 |

애플리케이션 테스트는 미실행이다.

## Git 기록

- 사용자에게 변경 내용·검증 결과와 Conventional Commit 메시지를 공유한 뒤 `Go ahead.`로 로컬 커밋 승인을 받았다.
- 승인된 제목: `chore(platform): scaffold isolated platform workspace`.
- 커밋 기록은 `git log -1 --format=fuller`를 기준으로 확인한다. 원격 푸시는 승인 범위에 포함되지 않으며 미실행이다.
- 후속 커밋 전에도 변경 내용과 검증 결과를 사용자에게 알린다.
