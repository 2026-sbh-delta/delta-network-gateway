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

구현·로컬 검증·PR #19 병합과 실제 VM Apply를 완료했다. 아래 기록에서 서버 설치와 공개 플랫폼의 기능 검증을 구분한다. 추가 수정은 커밋 전에 메시지를 알린다.

- `uv run pytest -q`: 126개 통과, live 1개 선택 제외. 실제 loopback TLS 서버에 대해 미등록 인증서 거부/명시 CA 등록 성공을 검증했다.
- `python3 -m unittest discover -s platform/deploy/tests -q`: 24개 통과.
- `terraform validate/test`: 플랫폼 root 4개, GitHub IAM 7개, Jenkins IAM 3개 통과.
- Ruff와 형식 검사 통과.
- 관리자 IAM Plan: 기존 GitHub 정책 3개 갱신, Jenkins 역할/프로필/정책 6개 추가. 삭제 없음. Access Analyzer의 ERROR/SECURITY_WARNING 없음, 관리형 정책 크기 한도도 확인했다.

- 최초 IAM Apply: Jenkins 기반 6개 추가, GitHub 관리형 정책 3개 갱신 완료. 기존 역할/리소스 삭제 없음. 당시 VM Apply와 공개 릴리스는 미실행이었다.

## 배포 준비 확인

- 구현 `8263c5b`을 커밋·푸시하고 [PR #19](https://github.com/2026-sbh-delta/delta-network-gateway/pull/19)를 열었다. [GitHub CI](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41651639104)의 앱/브라우저·Terraform 5개 root 검사가 모두 성공했다. 사용자가 PR #19를 병합했고 main `cc1b1c3`에서 운영 배포를 진행한다.
- 실제 VM Terraform Plan은 Jenkins 관련 10개 추가만 포함했다. 기존 앱·DB·네트워크 변경/교체/삭제는 없다. 실제 Apply는 main 병합 후 GitHub에서 새 Plan을 만들고 수행한다.
- 두 IAM root를 다시 Plan했고 모두 No changes, 상세 종료 코드 0이었다.
- 실제 로컬 Jenkins 컨테이너를 PKCS12로 기동해 CA 검증한 `/login` 200, 익명 `/api/json` 403을 확인했다. 컨트롤러 SecureString 내용 크기는 6,264바이트로 Advanced 한도 이내다. 검증 컨테이너/임시 키 파일은 제거했다.
- 검토한 `10.80.1.20` 주소로 컨트롤러의 30일 인증서와 최초 비밀번호를 SSM SecureString에 준비했다. 준비 단계에서는 VM/운영 플랫폼을 변경하지 않았다. main 전용 OIDC 경계를 유지하며 GitHub에서 배포한다.

## 운영 배포와 권한 보완

- 2026-11-03: main `cc1b1c3`의 GitHub Plan [41653399393](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41653399393)은 10개 추가만 포함했다. SHA/체크섬과 기존 리소스 변경·삭제 없음 확인 후 Apply했다.
- Apply [41653854446](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41653854446)은 SG 2개·EBS 1개 생성 후 새 SG 규칙 생성 권한에서 실패했다. 기존 플랫폼에는 변경이 없었다.
- 원인: 새 `security-group-rule`에는 생성 전에 리소스 태그가 없다. 기존 부모 SG의 `ec2:ResourceTag/Project` 조건은 유지하고, 새 규칙 ARN에만 `aws:RequestTag/Project`를 검사하는 inline policy를 추가했다. [EC2 권한 명세](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ec2.html)의 리소스/요청 태그 구분을 따른다.
- `terraform -chdir=platform/deploy/identity test`: 8개 통과. Access Analyzer ERROR/SECURITY_WARNING 없음. 신규 규칙·부모 SG 각각의 프로젝트 태그 허용/다른 태그 거부를 두 API에 대해 시뮬레이션해 8개 통과했다.
- 관리자 SSO의 검토한 IAM Plan으로 inline policy 1개만 적용했다. 부분 Apply 후에는 오래된 Plan을 재사용하지 않고 main에서 새 Plan을 생성한다. 코드 보완은 `fix/platform-jenkins-deploy`에 있으며 아직 커밋·푸시 전이다.

- 재생성 Plan [41655022890](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41655022890): 남은 Jenkins 7개 추가만 검토했다. Apply [41655264279](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41655264279) 성공. 컨트롤러 `i-0d397ad0c9f39bcfd`/`10.80.1.20`, 에이전트 `i-0ebc19741a54e507b`/`10.80.1.21`은 공인 IP 없이 실행 중이고 SSM Online이다.
- 컨트롤러 Release [41655539656](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41655539656) 성공. 이력 EBS 마운트·Docker/SSM 기동을 확인했다. 기존 플랫폼은 DB `0003`, 진행 중인 작업 0개였고 기존 서비스 ID와 설정 해시를 보존했다.

- 컨트롤러 준비 확인: CA 검증한 `/login` 200, 익명 `/api/json` 403. 운영자 bootstrap으로 API/에이전트 토큰을 SecureString에 저장했다. 플랫폼 런타임 버전 4에서 Jenkins를 선택했고 기존 로그인·DB·샌드박스 등 9개 필드와 DB 비밀번호 해시 일치를 확인했다. 비밀값은 로그/문서에 남기지 않았다.
- `AWS_PROFILE=delta-platform terraform plan -detailed-exitcode`: 플랫폼·GitHub IAM·Jenkins IAM 세 root 모두 No changes, 종료 코드 0.

- 에이전트 Release [41656136687](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41656136687) 성공. 실제 Jenkins API로 컨트롤러 executor 0 / 에이전트 online·executor 1을 확인했다. 플랫폼 API 토큰은 일반 API 200, 관리자 `/script` 403이었다.
- 전환 전 ECS 상태: 팀 `delta-lab-staging` desired/running 1, 플랫폼 sandbox desired/running/pending 0. 팀 스테이징은 변경하지 않는다.

- 플랫폼 Release [41656506066](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41656506066) 성공. CI: 백엔드 126개/live 제외 1개, SAST 36개, 배포 도구 24개, 브라우저 9개 통과. 인프라 5개 root 검사도 통과했다. VM에서 DB `0004`, systemd active, readiness ready, 빌드 실행기 `jenkins`를 확인했다.

## 공개 플랫폼 실제 검증 완료

- 2026-11-03 23:55 KST: [공개 플랫폼](https://d3udcz0zch0on.cloudfront.net)에서 기존 ID/PW로 로그인하고 **Jenkins AWS 연결 검증** 서비스를 등록했다. 저장소는 `docker/welcome-to-docker`, 고정 커밋은 `68c1b9f87c41fb3fef2667e27149865c2f42d1eb`이다.
- 소스 확인 `e18373cb-85ca-4d8d-819b-4770f66dc40e` 성공. 빌드 `0f95caa8-ae33-4159-84e9-20190f255311`은 실제 Jenkins `build:1`에서 약 91초에 성공했고 플랫폼의 로그 조회에서 `Finished: SUCCESS`를 확인했다.
- 결과 ECR digest `sha256:1ee930a1a62209e2e2b273ac1f2c0244fa02b02290a1e4029ca0b9dff6b0c5a7`를 실제 ECR API와 대조했다. 같은 digest의 샌드박스 실행 `f1957632-52de-4664-9d05-3c1e8de97916`이 HTTP 검사·이미지 일치·정리까지 성공했다.
- 최종 ECS 확인: 플랫폼 sandbox desired/running/pending 모두 0. 팀 `delta-lab-staging`은 desired/running 1 유지. 기존 서비스 ID와 로그인/DB 설정도 보존됐다.
- 실제 공개 웹의 데스크톱·모바일에서 Jenkins 실행 표시·빌드 로그·실행 검사 결과·새로고침 후 이력 유지·가로 넘침 없음·JS 오류 없음을 확인했다. 초기 검증 스크립트의 Jenkins 문구 선택 위치를 실제 화면 구조에 맞춘 후 통과했다. 제품 코드는 변경하지 않았다.
- 배포 전/후 DB 백업은 S3 `database/2026/11/03/144959-database-Bdwhn3rv.dump`, `database/2026/11/03/145026-database-qm3Z746i.dump`에 저장됐다. 이번 단위에서는 별도 복원 시험을 반복하지 않았다.
- ignored 근거: `platform/.runtime/jenkins-public-run.json`, `jenkins-aws-proof.json`, `jenkins-public-desktop.png`, `jenkins-public-mobile.png`, `jenkins-platform-release/`. 인증값은 근거 문서나 Git에 포함하지 않는다.

현재 **Jenkins 서버·에이전트·공개 플랫폼 전환과 실제 빌드/실행 검증 완료**다. 권한 보완은 사용자 승인 후 `42bbe5b`로 커밋했다. 운영 문서는 후속 `feat/platform-sast-aws` PR에 함께 포함한다. PR #19로 배포한 코드는 main `cc1b1c3`이다.

다음 작업은 운영 SAST 연결이다. 플랫폼 UI/API와 DB `0004`는 배포됐지만 `PLATFORM_SAST_URL`/토큰 및 실제 운영 검증은 남아 있다. 이후 DAST의 ECS 검사 수명/접근 계약, 고정 이미지의 Terraform·VM 배포로 이어간다. Jenkins HA·자동 확장·인증서 자동 갱신은 현재 구성에 포함하지 않는다.
