# AWS Jenkins 운영 연결

`공개 플랫폼 → 사설 HTTPS Jenkins → 전용 에이전트 → ECR → ECS 실행 확인` 구성이다. 플랫폼의 인프라/릴리스 CI는 GitHub Actions를 계속 사용한다. 고객 Dockerfile은 전용 빌드 VM에서만 실행한다.

| 구성 | 주소·규모 | 접근 |
| --- | --- | --- |
| 컨트롤러 | `10.80.1.20`, t3.small, executor 0 | 플랫폼·에이전트 SG의 443만 허용 |
| 에이전트 | `10.80.1.21`, t3.medium, executor 1 | 수신 없음. 컨트롤러에 WebSocket 연결 |
| 이력 디스크 | 암호화 gp3 20 GiB | 컨트롤러 VM과 수명 분리, 삭제 보호 |

공인 IP나 공개 관리자 화면은 없다. 기존 플랫폼 NAT의 HTTPS 송신 경로를 재사용한다. 에이전트는 전용 VM에서 privileged Docker-in-Docker를 사용하며 호스트/컨트롤러 Docker socket을 공유하지 않는다. Docker 빌드 네트워크에서 사설망·IMDS 신규 연결을 차단한다. 관리자 코드를 실행하는 에이전트 컨테이너만 IMDSv2로 제한된 역할을 사용한다.

## 배포 순서

1. 관리자 SSO로 `platform/deploy/jenkins/identity`와 `platform/deploy/identity`의 저장 Plan을 검토·적용한다. 일반 GitHub CI는 IAM을 수정하지 못한다.
2. 코드가 main에 반영된 뒤 **Platform infrastructure**의 Plan을 검토하고 같은 커밋의 저장 Plan을 Apply한다. 기존 VPC·앱·DB의 교체/삭제가 없는지 확인한다. 새 GitHub 변수는 필요 없다.
3. 플랫폼 root의 `terraform output -json`을 ignored 파일에 저장한다. 관리자 SSO로 최초 인증서를 준비한다.

```bash
uv run --project platform/deploy python platform/deploy/jenkins/aws/bootstrap-runtime.py prepare --profile delta-platform --outputs platform/.runtime/jenkins-outputs.json
```

4. main의 **Platform Jenkins release**를 `component=controller`로 실행한다. 컨트롤러가 준비되면 아래 명령으로 플랫폼 API/에이전트 토큰을 구성한다. 토큰은 출력하지 않는다.

```bash
uv run --project platform/deploy python platform/deploy/jenkins/aws/bootstrap-runtime.py connect --profile delta-platform --outputs platform/.runtime/jenkins-outputs.json
```

5. 같은 워크플로를 `component=agent`로 실행한다. 컨트롤러의 에이전트 online/executor 1을 확인한다.
6. 진행 중인 기존 플랫폼 작업이 없는지 확인한 뒤 **Platform release**를 실행한다. 기존 로그인/DB 설정을 유지하며 배포 전 백업과 migration `0004`를 수행한다.
7. 공개 플랫폼에서 실제 저장소 빌드·Jenkins 로그·digest·ECS 응답과 태스크 종료를 확인한다. 설치 명령 성공만으로 빌드 가능한 상태라고 판정하지 않는다.

## 인증서·비밀값

Jenkins 자체 HTTPS 포트를 사용한다([공식 설정](https://www.jenkins.io/doc/book/installing/initial-settings/)). 사설 CA와 IP SAN 인증서로 서버를 확인한다. 플랫폼과 Java 에이전트는 이 CA를 명시적으로 신뢰하며 검증을 끄지 않는다. CA가 바뀌면 기존 작업을 다른 신뢰 대상으로 재연결하지 않는다.

컨트롤러 비밀번호/PKCS12는 `/delta-platform/jenkins/controller`, 에이전트 토큰은 `/delta-platform/jenkins/agent`, 플랫폼 연결은 기존 `/delta-platform/runtime`의 SecureString에 저장한다. 컨트롤러 구성은 4KB를 넘어 Advanced tier를 사용하며 플랫폼 구성도 크기에 따라 Advanced가 될 수 있다. GitHub에는 비밀값을 저장하지 않는다. SSM 명령 실행/조회 권한은 운영 권한이며 토큰 초기 구성 명령의 응답을 팀 채팅에 복사하지 않는다.

서버 인증서는 **30일**, CA는 90일이다. 해커톤 기간용이며 자동 갱신은 아직 없다. 만료 전 작업을 비우고 인증서·클라이언트 신뢰를 함께 교체해야 한다. `prepare`는 기존 비밀값을 덮어쓰지 않는다. 초기 CA 개인키는 발급 후 로컬 임시 디렉터리와 함께 삭제한다.

## 운영·복구

- Jenkins 빌드가 진행 중일 때 컨트롤러/에이전트를 재배포하지 않는다. 현재 동시에 빌드 하나만 처리한다.
- 컨트롤러 이력은 별도 EBS에 남고 재부팅 시 Docker보다 먼저 마운트한다. EBS 스냅샷 자동 백업·HA는 아직 구성하지 않았다.
- 실패 시 새 이미지 digest와 SSM 명령 ID를 확인한다. 토큰 초기 구성은 컨트롤러의 root 전용 연결 파일을 재사용하므로 재시도해도 토큰을 계속 만들지 않는다.
- 플랫폼 전환을 되돌릴 때는 기존 `/delta-platform/runtime`에서 `PLATFORM_BUILD_PROVIDER=codebuild`를 명시하고 검증된 플랫폼 이미지를 다시 배포한다. 기존 CodeBuild 자원을 먼저 삭제하지 않는다. DB `0004`의 보안 검사 기록이 있으면 downgrade하지 않는다.
- 비용 중단 시 두 Jenkins VM을 중지할 수 있다. 이력 EBS와 이미지 저장 비용은 남는다. 기존 앱/NAT나 팀 ECS 서비스를 함께 내리지 않는다.

실제 Plan/Apply/Release와 기능 검증 상태는 [진행 기록](../../../../docs/ai-dlc/platform-jenkins-aws.md)을 따른다.
