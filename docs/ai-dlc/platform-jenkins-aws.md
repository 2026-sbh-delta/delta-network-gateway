# Jenkins 운영 연결

- 2026-11-03: PR #18 병합을 확인했고 사용자가 운영 서버·플랫폼 연결 진행을 승인했다. `feat/platform-jenkins-aws`에서 구현한다.
- 목적: 공개 플랫폼에서 Jenkins 빌드 → 고정 ECR 이미지 → ECS 실행 확인까지 사용한다.
- 성공 기준: 실제 공개 저장소 빌드·로그·digest·ECS 종료를 공개 플랫폼에서 확인하고 기존 로그인·서비스 이력을 유지한다.
- 범위: 기존 AWS 플랫폼 VPC 안의 Jenkins 컨트롤러/전용 에이전트, 제한된 IAM, 사설 HTTPS 인증서 검증, GitHub OIDC Plan/Apply/Release와 운영 검증. 모니터링·DAST·고객 VM 배포·Jenkins HA는 이번 단위 밖이다.

## 설계·실행 순서

1. 기존 `10.80.1.0/24`에 컨트롤러 `10.80.1.20`(t3.small), 에이전트 `10.80.1.21`(t3.medium)을 둔다. 공인 IP·SSH 수신은 없다. 인터넷 다운로드는 기존 NAT의 HTTPS만 사용한다.
2. 컨트롤러는 executor 0, 에이전트는 1이다. 컨트롤러 데이터는 암호화한 별도 EBS에 보존한다. 에이전트는 고객 빌드 전용이며 앱/DB/컨트롤러와 호스트를 공유하지 않는다.
3. 플랫폼·에이전트 SG만 컨트롤러 443에 접근한다. 사설 CA로 IP 인증서를 발급하고 클라이언트에 CA를 명시적으로 등록한다. `verify=false`는 사용하지 않는다. 관리자 접속은 SSM으로 제한한다.
4. IAM 최초 구성은 기존 관리자 SSO/Terraform 경로를 사용한다. VM 역할은 자신의 Jenkins SecureString·배포 파일·고정 도구 이미지와 SSM 관리 범위만 추가한다. 고객 이미지 게시 권한은 에이전트만 갖는다. 비밀값은 Terraform 입력/state/GitHub 변수/로그에 넣지 않는다.
5. VM·SG·디스크는 기존 플랫폼 Terraform root의 GitHub OIDC Plan/Apply로 생성한다. Jenkins 이미지·설정 배포는 main 전용 별도 GitHub Release가 담당한다. 컨트롤러 배포 → 토큰 초기 구성 → 에이전트 배포 → 플랫폼 설정/Release 순으로 진행한다.
6. 플랫폼 배포 전에 기존 작업을 확인하고 DB 백업·migration을 수행한다. 실제 빌드/실행 정리를 확인한다. 기존 CodeBuild는 삭제하지 않고 명시적 복귀 경로로 남긴다.

## 검증·상태

구현 및 로컬 검증을 완료했다. 실제 VM Plan/Apply, GitHub 배포, 기능 검증과 커밋/PR 상태는 완료 후 별도로 기록한다. 커밋 전에 메시지를 알린다.

- `uv run pytest -q`: 126개 통과, live 1개 선택 제외. 실제 loopback TLS 서버에 대해 미등록 인증서 거부/명시 CA 등록 성공을 검증했다.
- `python3 -m unittest discover -s platform/deploy/tests -q`: 24개 통과.
- `terraform validate/test`: 플랫폼 root 4개, GitHub IAM 7개, Jenkins IAM 3개 통과.
- Ruff와 형식 검사 통과.
- 관리자 IAM Plan: 기존 GitHub 정책 3개 갱신, Jenkins 역할/프로필/정책 6개 추가. 삭제 없음. Access Analyzer의 ERROR/SECURITY_WARNING 없음, 관리형 정책 크기 한도도 확인했다.

- IAM Apply: Jenkins 기반 6개 추가, GitHub 관리형 정책 3개 갱신 완료. 기존 역할/리소스 삭제 없음. VM Apply와 공개 릴리스는 아직이다.
