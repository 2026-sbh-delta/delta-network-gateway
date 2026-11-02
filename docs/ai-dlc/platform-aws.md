# 플랫폼 AWS 배포

## 승인·목표

- 2026-11-03 사용자 `you have to provision the platform on the AWS` 지시로 U1의 AWS 배포를 진행한다.
- 기존 저장소 터미널에 설정한 자격 증명을 사용하라는 지시를 받았다. 별도 실행 프로세스에 export가 전달되지 않아 로컬 전달 도구를 사용했으며, 만료된 세션 갱신 후 STS로 **업무 계정 616723161368**과 서울 리전을 확인했다.
- API·Worker는 Python이다. Node.js는 웹 빌드 단계에서만 사용하며 운영 이미지에는 Node.js를 포함하지 않는다.
- 팀원이 HTTPS에서 고정 계정으로 접속해 등록·소스 확인을 수행하는 것이 완료 기준이다. 고객 앱 배포·중앙 관측·DB 이전 기능은 후속 단위다.

## 설계와 구현 순서

1. `platform/deploy/terraform`의 독립 VPC·S3 state로 기존 서비스 인프라와 분리한다.
2. CloudFront VPC Origin으로 사설 EC2 API에 연결한다. 로그인 응답 캐시를 비활성화하고 쿠키·CSRF 헤더를 전달한다.
3. 작은 ARM64 EC2에 API·Worker·PostgreSQL을 별도 컨테이너로 실행한다. VM 역할을 통한 SSM·ECR·백업 접근을 사용하며 관리 키를 앱에 전달하지 않는다.
4. 고객 앱 네트워크와 연결하지 않은 상태에서 NAT 인스턴스로 GitHub·이미지·관리 서비스에 HTTPS 송신한다.
5. 별도 암호화 EBS에 DB를 저장하고, S3 일일 백업과 복원 시험을 준비한다.
6. 실제 Plan을 검토하고 Apply한 뒤 이미지를 digest로 고정해 배포한다. HTTPS 로그인·GitHub 조회·재시작·백업을 실제 검증한다.

이 배포는 예산을 줄인 단일 AZ 구성이다. 앱·DB·NAT의 자동 장애 전환은 제공하지 않는다. 운영 확장 시 관리형 DB·다중 AZ·다중 API/Worker로 분리할 수 있도록 작업 상태는 PostgreSQL에 보존한다.

## 현재 배포

- HTTPS: https://d3udcz0zch0on.cloudfront.net
- 업무 계정 `616723161368`, 서울 `ap-northeast-2`, AZ `ap-northeast-2a`.
- 앱 `i-0e5df7881a7de8fbd`: `t4g.small`, 사설 `10.80.1.70`, 공인 IP 없음.
- NAT `i-0e64f0f45eef52cb5`: `t4g.micro`. 앱·DB와 별도 실행한다.
- 데이터 디스크 `vol-06539af28c9dca914`: 암호화 gp3 20 GiB, Terraform 삭제 보호.
- CloudFront `E3EO68SK5IW63J`, VPC Origin `vo_BkjrgZuceMFD8YVLI9rtup`.
- state `s3://delta-platform-tf-616723161368/platform/terraform.tfstate`, lockfile 사용.
- 릴리스·백업 `delta-platform-artifacts-616723161368`, 비공개·암호화·버전 관리.
- 기존 고객용 VPC `10.10.0.0/16`, 서버 VPC `10.20.0.0/16`와 분리한 `10.80.0.0/16`이다. 기존 서비스 인프라는 수정하지 않았다.
- 고정 ID는 `delta`, 생성한 비밀번호는 Git에서 제외한 로컬 `platform/.runtime/platform-login.txt`에만 보관한다. 서버에는 비밀번호 해시를 저장한다.

NAT를 처음 `t4g.nano`로 만들었지만 초기 패키지 설치가 메모리 부족으로 중단됐다. 초기 Apply의 CloudFront 대기 중 NAT를 중지·`t4g.micro`로 변경·재기동했고, Terraform 코드도 일치시켰다. 이후 NAT 설정·앱 bootstrap이 완료됐다. 최종 Plan에서 리소스 교체가 없음을 확인했다. EIP가 연결된 NAT의 public-IP 속성 차이로 발생한 불필요한 교체 Plan도 적용 전에 수정했다.

## 실제 검증 기록

2026-11-03 (Asia/Seoul)에 실행했다. 로컬 상세 검증은 [U1 기록](platform-u1.md)을 따른다.

| 명령·검증 | 실제 결과 |
| --- | --- |
| `terraform fmt -check -recursive`, `validate`, `test` | 통과, mock 3개 통과 |
| STS 조회 → 저장 Terraform Plan 검토 → `apply` | 별도 플랫폼 기반 생성 완료 |
| ARM64 Docker 빌드·실행 | API·Worker 실제 GitHub 검사 성공, 컨테이너 재시작 후 세션·결과 보존. Python 런타임에 Node 없음 |
| `publish-images.py`, `deploy-release.py` | ECR digest 게시·SSM 배포 Success. 명령 ID `4d70b75f-dfd7-4842-98a8-f3d3e0a75f74` |
| HTTPS API smoke | 웹·readiness 200, 미로그인 401, 고정 로그인, Secure/HttpOnly/SameSite 쿠키, 실제 GitHub 검사 성공, 다른 Origin 403, 로그아웃 후 401 |
| 실제 GitHub 대상 | `docker/welcome-to-docker`의 `68c1b9f87c41fb3fef2667e27149865c2f42d1eb`, run `8cf812d7-0c7e-4c69-ba1f-c5605bd0a742`, 이벤트 8개 |
| `systemctl restart delta-platform`, 백업 service 실행 | API·Worker·DB 재시작 후 readiness 성공, 기존 결과 보존, 백업 timer active |
| S3 백업 다운로드 → 별도 임시 DB `pg_restore` | services 1, runs 1, run_events 8이 원본과 일치. 시험 DB만 삭제 |
| 실제 HTTPS Playwright | 로그인·기존 결과·새로고침·로그아웃 성공, JS 오류 없음, 모바일 가로 넘침 없음 |
| 임시 Origin ingress 제거 Plan → `apply` | bootstrap 규칙 1개만 제거. 최종 ingress는 CloudFront 서비스 SG 허용 |
| 최종 `terraform plan -detailed-exitcode` | 종료 코드 0, **No changes** |

실제 결과는 ignored `platform/.runtime/https-smoke.json`, `operations-smoke.json`, `aws-browser.json`, `no-changes.log`에 보관한다. 비밀값을 일반 문서·Git에 기록하지 않는다. 전체 VM 재부팅·다중 AZ 장애 전환·부하 시험은 수행하지 않았다.

## GitHub 배포 전환

사용자의 `Isn't it more appropriate to provision the infra using GitHub?`에 따라 로컬 최초 배포와 팀의 정상 배포 경로를 분리했다.

- `platform-check.yml`: PR에서 AWS 권한 없는 실제 DB·브라우저·Terraform mock 검증.
- `platform-infra.yml`: main에서 Plan을 생성하고 검토한 run ID·같은 커밋의 Plan만 별도 적용.
- `platform-release.yml`: 테스트 후 ARM64 이미지·ECR·SSM 배포, 기존 SSM 비밀값·PostgreSQL digest 유지.
- 인증은 OIDC 임시 역할을 전제로 한다. 터미널의 세션 키를 GitHub에 복사하지 않는다.
- `actionlint` 통과, `python3 -m unittest discover -s platform/deploy/tests -v` 8개 통과. 잘못된 계정·다른 커밋 Plan 거부와 CI의 비밀값 생성·조회 금지를 확인했다. 배포 Python lint·format 통과.
- PR 테스트 워크플로는 원격 실행을 시작했다. **GitHub OIDC 역할·Environment 연결, GitHub를 통한 실제 인프라·앱 배포는 미실행**이다. 설정 방법과 권한 범위는 [GitHub 배포 안내](../../platform/deploy/GITHUB.md)에 기록했다.
- 2026-11-03 Conventional Commit 메시지를 제시하고 사용자 `commit and open PR` 지시로 구현을 `5390c63`에 커밋했다. `git push -u origin feat/platform` 성공 후 `gh pr create`로 [PR #7](https://github.com/2026-sbh-delta/delta-network-gateway/pull/7)을 열었다.
- GitHub PR 검사 실행은 [41479405704](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41479405704)에서 시작됐다. 이 기록은 시작 확인이며 최종 성공 여부는 PR Checks를 따른다.

[인프라 입력·출력](../../platform/deploy/terraform/README.md)
