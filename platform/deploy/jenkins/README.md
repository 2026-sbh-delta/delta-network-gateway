# 플랫폼 Jenkins 빌드

고객 앱은 **플랫폼 → Jenkins 전용 Job → 빌드 에이전트 → ECR → ECS 실행 확인** 순서로 처리한다. 플랫폼 자체의 Terraform·앱 Release는 기존 GitHub Actions를 사용한다. Cloud Build는 사용하지 않는다. 이전 AWS CodeBuild 기록은 보존한다.

## 무엇을 준비했나

| 구성 | 역할 |
| --- | --- |
| `controller/` | 버전 고정 Jenkins와 설정. 컨트롤러 executor 0, 가입/익명 접근 비활성화 |
| `pipeline/Jenkinsfile` | 플랫폼 소유 Job. 고객 저장소의 Jenkinsfile은 실행하지 않음 |
| `agent/` | 전용 Docker daemon에서 확인한 커밋의 Dockerfile 빌드. ECR 로그인은 빌드가 끝난 뒤 수행 |
| `identity/` | 기존 고객 이미지 ECR 한 곳에 읽기·게시하는 IAM 역할. VM 생성·ECS 배포 권한 없음 |
| `configure-runtime.py` | 기존 로그인·DB·샌드박스 설정을 보존하며 SSM에 Jenkins 연결값 병합 |

Jenkins API 계정은 읽기·빌드·중단만 허용한다. Job 수정·관리자 스크립트 권한은 없다. 현재 전용 컨트롤러에는 플랫폼 Job 하나만 둔다. 다른 용도 Job과 섞을 때는 Job별 권한으로 더 제한한다.

## 플랫폼 설정

| 환경변수 | 값/용도 |
| --- | --- |
| `PLATFORM_BUILD_PROVIDER` | `jenkins` — 새 기본값. 설정 누락 시 CodeBuild로 자동 대체하지 않음 |
| `PLATFORM_JENKINS_URL` | 운영 서버의 HTTPS 주소. 로컬 모드만 loopback HTTP 허용 |
| `PLATFORM_JENKINS_JOB` | `delta-platform-image` |
| `PLATFORM_JENKINS_USER` | `platform` |
| `PLATFORM_JENKINS_TOKEN` | 해당 사용자 API 토큰. 채팅·Git·GitHub 일반 variable에 넣지 않음 |
| `PLATFORM_JENKINS_CA_BASE64` | 선택. 사설 CA PEM을 base64로 저장. 미설정 시 기본 신뢰 저장소 사용 |
| `PLATFORM_BUILD_REPOSITORY` | `616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds` |
| `PLATFORM_BUILD_REGION` | `ap-northeast-2` |

이전 CodeBuild 로그를 읽으려면 기존 `PLATFORM_BUILD_PROJECT`와 조회 권한을 유지한다. 실행기 정보가 없는 과거 snapshot은 CodeBuild로 해석한다. 새 Jenkins 설정과 저장된 실행 대상이 다르면 해당 실행을 다른 서버로 넘기지 않는다.

## 로컬 실제 검증

저장소 루트에서 아래 디렉터리로 이동한다. 기존 `deploy/jenkins-local` 시연용 스택과 분리된다. 로컬 Compose는 `127.0.0.1:18081`만 공개하며 호스트 Docker socket이나 Jenkins 홈을 에이전트에 마운트하지 않는다.

```bash
cd platform/deploy/jenkins
python3 prepare-local.py --repository 616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-platform-builds
docker compose up -d --build controller
# 컨트롤러가 준비된 뒤 실행. 최초 한 번만 토큰을 생성한다.
uv run --project ../../backend python connect-local.py
docker compose --profile agent up -d --build agent
```

- `.env`는 관리자/플랫폼 계정 비밀번호를, `.runtime/platform.env`는 API 연결값을, `.runtime/agent-secret`은 inbound agent 토큰을 담는다. 모두 ignored·0600이며 생성 도구가 덮어쓰지 않는다.
- 실제 ECR 게시에는 에이전트의 AWS 인증이 필요하다. 로컬 검증에서만 빌드 전용 역할의 단기 세션을 `.runtime/agent-aws.env`에 넣었다. 관리자 자격 증명은 에이전트에 전달하지 않았다. 운영에서는 EC2 instance profile을 사용한다.
- 로컬 에이전트는 Docker-in-Docker 때문에 privileged 컨테이너다. **운영에서는 전용/폐기 가능한 빌드 VM에 배치**한다. 플랫폼 DB나 Jenkins 컨트롤러와 같은 호스트에 고객 빌드 권한을 주지 않는다.
- 컨트롤러와 에이전트 이미지의 Docker context는 allowlist로 제한해 `.env`와 `.runtime`이 전달되지 않는다.
- 검증 후 `docker compose --profile agent down`으로 컨테이너만 종료한다. 이력 볼륨 삭제는 별도다. 단기 AWS 인증 파일은 삭제하고, 임시 IAM 검증 주체는 `null`로 되돌린다.

## 운영에 올릴 때

1. 사설 Jenkins 컨트롤러와 전용 빌드 에이전트를 분리한다. 컨트롤러는 플랫폼에서 접근 가능한 HTTPS 주소로 제공하고 관리자 접속은 SSM 등 관리 경로를 사용한다. 공개 Docker API·공개 Jenkins 관리자 화면을 만들지 않는다.
2. 에이전트 EC2에 `identity/`의 instance profile을 연결한다. IMDSv2를 사용하고 컨테이너의 metadata/사설망 접근을 차단한다. 빌드 스크립트도 Docker의 사설망·link-local 신규 연결을 거부하며 필요한 DNS/기존 연결 응답은 허용한다. 일반 인터넷 다운로드와 ECR 접근은 별도 네트워크 경로가 필요하다.
3. 이 저장소에서 만든 고정 컨트롤러/에이전트 이미지와 Job을 설치한다. 고객에게 Job 수정 권한을 주지 않는다. ARM 에이전트에서도 결과는 `linux/amd64`이므로 운영은 x86 에이전트를 권장한다. 현재 Job은 한 번에 빌드 하나만 처리한다.
4. 위 7개 설정을 담은 ignored JSON 파일로 `configure-runtime.py --profile delta-platform --account-id 616723161368 --config <로컬 파일>`을 실행한다. 기존 작업을 마친 후 한 운영자가 순서대로 갱신한다. 도구는 감지한 동시 갱신을 거부하지만 SSM의 원자적 compare-and-swap은 아니다.
5. 플랫폼 릴리스 후 실제 공개 저장소 빌드·digest·로그·ECS 실행 확인을 검증한다. Jenkins가 준비되기 전에 새 기본 설정만 배포하면 새 빌드는 503으로 차단된다.

로컬 구현/검증은 [이전 기록](../../../docs/ai-dlc/platform-jenkins.md), 후속 AWS 운영 배포는 [운영 안내](aws/README.md)와 [최신 기록](../../../docs/ai-dlc/platform-jenkins-aws.md)을 따른다. 구현 완료와 실제 운영 전환을 구분한다.

## 실패와 복구

- 요청 ID·커밋을 접수 전에 DB에 저장한다. 응답을 잃으면 Jenkins의 최근 200개 빌드와 큐에서 같은 요청을 찾는다. 찾지 못해도 자동 재접수하지 않으며, 운영자가 Job 이력과 요청 ID를 확인한다.
- Job 성공만으로 배포 가능한 이미지를 만들지 않는다. 플랫폼 요청·커밋·`result.json`·ECR tag의 digest가 일치해야 한다. 태그는 기존 ECR의 immutable 정책을 유지한다.
- Job에는 30분 제한, Worker에는 접수부터 35분 제한이 있다. Worker 제한 초과 시 소유권을 확인한 큐/빌드만 중단 요청한다. 중단 요청 응답을 프로세스 종료 완료로 표현하지 않는다.
- 재시작 후 저장한 큐 ID/빌드 번호로 조회를 계속한다. 성공한 결과와 기존 서비스 이력은 DB에 남는다.
- 로그는 제한된 크기의 텍스트다. 콘솔 Range 지원 여부에 따라 마지막 구간 또는 앞부분을 반환할 수 있고 잘림 여부를 표시한다. HTML로 렌더링하지 않는다.
- Jenkins 연결 장애 시 새 빌드를 중지한다. CodeBuild 복귀가 필요하면 진행 중 Jenkins 실행을 확인·정리한 후 관리자가 `PLATFORM_BUILD_PROVIDER=codebuild`를 명시한다. 자동 복귀는 없다.

## 보안 검사와의 경계

빌드 성공은 **이미지 생성**이며 ECS 실행 확인과 SAST 검사는 별도 작업이다. [SAST 연결 코드](../../contracts/security-checks.md)는 고정 커밋의 발견 사항·근거를 빌드 digest와 연결한다. 운영 SAST 연결은 아직이다. DAST에는 검사 동안 ECS 앱을 유지하고 검사 서비스만 접근시키는 별도 수명·네트워크 계약이 필요하다.

공식 문서: [Remote API](https://www.jenkins.io/doc/book/using/remote-access-api/), [컨트롤러 격리](https://www.jenkins.io/doc/book/security/controller-isolation/), [Docker 실행](https://www.jenkins.io/doc/book/installing/docker/), [권한 설정](https://plugins.jenkins.io/matrix-auth/).
