# Delta Platform

개발자가 공개 GitHub 저장소의 앱을 빌드하고, ECS 샌드박스 검사 후 VM에 배포하며, 서비스 상태와 이전 작업을 확인하는 플랫폼 작업 영역이다.

현재는 **브랜치·디렉터리·설계 문서를 준비한 상태**다. 웹/API/Worker의 실행 코드는 아직 없다.

- 작업 브랜치: `feat/platform`
- [한국어 구성 설명](docs/overview.ko.html): 브라우저에서 파일을 직접 열 수 있다.
- [설계·구현 단위·검증 기록](../docs/ai-dlc/platform.md)

## 구조

```text
platform/
├── web/                         # 개발자용 웹 화면
├── backend/
│   ├── src/delta_platform/
│   │   ├── api/                 # 인증·입력 검증·조회·작업 접수
│   │   ├── worker/              # 오래 걸리는 작업 실행·재개
│   │   ├── core/                # 서비스·작업 상태·저장 규칙
│   │   └── adapters/            # GitHub·CI·CSP·검사 도구 연동
│   └── tests/                  # API·작업 상태·저장소 검증
├── contracts/                  # 팀 간 입력·출력 계약
├── automation/                 # 고객 VM의 앱 배포 자동화
└── docs/                       # 사람이 읽는 플랫폼 설명
```

Python 패키지명은 표준 라이브러리 `platform`과 충돌하지 않도록 `delta_platform`을 사용한다. API와 Worker는 별도 프로세스이며 공통 모델과 저장 규칙을 같은 패키지에서 공유한다.

## 역할 경계

- 웹은 플랫폼 API만 호출한다. 브라우저에 클라우드 관리 자격 증명을 전달하지 않는다.
- 백엔드는 사용자 권한, 작업 상태, 외부 도구 호출과 결과 기록을 담당한다.
- 기존 `terraform/`는 VM·네트워크·권한 등 인프라를 관리한다.
- 기존 `services/`는 AI·SAST·DAST·스테이징 도구의 독립 구현 위치다. 플랫폼은 어댑터로 호출한다.
- `automation/`은 준비된 VM의 고객 앱 배포를 담당한다. 플랫폼 자체 배포와 구분한다.

## 합의와 다음 작업

공개 GitHub 저장소와 Dockerfile을 지원하고, 운영은 VM, 샌드박스는 기존 ECS로 구성한다. Terraform을 플랫폼 CI에 연결한다. DB 이전 방향은 AWS → GCP 또는 온프레미스이며 엔진·버전은 미확정이다.

다음 단위는 서비스 등록·권한·영속적인 작업 상태를 다루는 API/Worker 기반이다. 구체적인 기능 구현은 [AI-DLC 문서](../docs/ai-dlc/platform.md)의 단위별 설계·구현 계획에 따라 진행한다.

애플리케이션 테스트, Terraform Plan/Apply, 플랫폼 배포는 이번 작업에서 실행하지 않았다. 사용자가 변경 내용과 Conventional Commit 메시지를 확인한 뒤 로컬 커밋을 승인했다. 커밋 기록은 Git 이력으로 확인하며 원격 푸시는 수행하지 않는다. **후속 커밋도 실행 전에 변경 내용과 검증 결과를 사용자에게 알린다.**
