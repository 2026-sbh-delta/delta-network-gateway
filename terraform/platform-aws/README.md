# 플랫폼 인프라 (AWS)

플랫폼(제어 영역)을 점검·배포 대상과 분리된 새 VPC에 올린다. 서비스마다 EC2 한 대, IAM 역할, 보안 그룹, 이미지 저장소, Parameter Store 경로를 따로 둔다. 인프라는 한 번만 만든다. 각 서비스는 [deploy/platform](../../deploy/platform/README.md)으로 이미지만 바꾼다.

서비스는 7개다: 오케스트레이터, Jenkins 컨트롤러(스테이징 도구 포함), 빌드 에이전트, SAST, DAST, 차분 에이전트, 로그. 모두 같은 모듈(`modules/service-host`)을 쓴다. 서비스 사이 연결은 `main.tf`의 `links` 목록에 적은 것만 열린다. 공개는 CloudFront 배포 4개다: 오케스트레이터·DAST(전체), Jenkins(`/github-webhook/*`만), 로그(`/v1/ingest/*`만). 연결 확인: `deploy/platform/check-links.sh`.

온디맨드 vCPU 한도(24)가 차서 오케스트레이터·Jenkins·빌드 에이전트는 임시로 Spot이다(`spot_services`, 중단되면 정지). 한도 증설(지원 케이스 179380767400001)이 승인되면 빈 목록으로 바꾼다.

```text
브라우저 ─HTTPS─▶ CloudFront(기본 도메인) ─VPC Origin─▶ 내부 ALB ─X-Delta-App 헤더─▶ DAST:8765 ──▶ SAST:8790
                                                                          │
                       서비스 인스턴스 ─▶ NAT 게이트웨이(고정 IP) ─▶ ECR·SSM·LLM API·GitHub·스테이징
```

## 네트워크

| 대역 | 용도 | 인터넷 경로 |
|---|---|---|
| `10.90.0.0/24`, `10.90.1.0/24` | 퍼블릭: NAT 게이트웨이 | IGW |
| `10.90.2.0/24`, `10.90.3.0/24` | 내부 ALB와 CloudFront VPC Origin ENI 전용 | 없음 |
| `10.90.10.0/24` | 서비스 인스턴스 | NAT |

- 사설 이름: `sast.cp.internal`, `dast.cp.internal` (Route 53 사설 영역)
- S3 게이트웨이 엔드포인트: ECR 이미지 레이어를 NAT를 거치지 않고 받는다.

## 보안 설정

- **노출:** 인터넷에 직접 열린 것은 없다. 공개 주소는 CloudFront뿐이고, ALB는 내부용이다.
- **ALB 인바운드:** CloudFront가 VPC에 만드는 관리형 보안 그룹(`CloudFront-VPCOrigins-Service-SG`)에서 오는 요청만 받는다. 서브넷 대역으로 열면 연결되지 않는다(504).
- **앱 라우팅:** CloudFront 배포마다 `X-Delta-App` 헤더를 붙인다. 뷰어가 같은 헤더를 보내도 CloudFront가 덮어쓴다. 헤더가 맞지 않으면 ALB가 404를 준다.
- **인스턴스 인바운드:** 규칙은 ALB → DAST:8765와 DAST → SAST:8790 두 개뿐이다. 기본 보안 그룹에는 규칙이 하나도 없다.
- **아웃바운드:** HTTPS(443)만 연다. DAST는 스테이징 진입점용으로 HTTP(80)를 더 연다. 나가는 연결은 모두 NAT 고정 IP 하나로 나간다.
- **인스턴스 설정:**
  - 공인 IP가 없다.
  - 접속은 SSM으로만 한다(SSH 없음).
  - IMDSv2를 강제하고 홉 제한은 1로 둔다.
  - 디스크는 암호화한다.
- **IAM 역할:** 서비스마다 따로 둔다.
  - 자기 ECR 이미지만 받는다.
  - 파라미터는 자기 경로와 `/delta-cp/shared/`만 읽는다.
  - LLM을 쓰는 서비스는 공용 LLM 역할(`delta-cp-llm`)을 위임받는 권한만 더 가진다. `delta-cp-llm`은 AWS 신원 토큰 발급(`sts:GetWebIdentityToken`)만 할 수 있다. Anthropic OIDC 규칙은 이 ARN 하나에만 일치한다.
- **이미지:**
  - 태그는 덮어쓸 수 없다.
  - 푸시할 때 스캔한다.
  - 최근 10개만 보관한다.
  - 배포는 digest로 고정한다.
- **비밀값:** Terraform state에 넣지 않는다. 처음 배포할 때 배포 스크립트가 만들어 SecureString으로 저장한다.
- **DAST 공개 화면:**
  - 로그인해야 쓸 수 있다.
  - 세션 쿠키는 `__Host-`, `Secure`, `HttpOnly`, `SameSite=Strict`로 발급한다.
  - 10회 실패하면 5분 동안 잠근다.

## 적용 (사람이 실행)

```bash
cp backend.hcl.example backend.hcl   # 최초 1회
printf 'account_id = "616723161368"\n' > terraform.tfvars
AWS_PROFILE=sb terraform init -backend-config=backend.hcl
AWS_PROFILE=sb terraform plan -out=platform.tfplan
```

저장한 Plan(`platform.tfplan`)을 적용한다. CloudFront VPC Origin과 배포를 만드는 데 10~20분 걸린다.

## 출력

| 출력 | 내용 |
|---|---|
| `app_urls` | 공개 앱 주소(DAST 작업실) |
| `nat_egress_ip` | 플랫폼 출구 고정 IP. 스테이징 허용 목록에 넣는다 |
| `instance_ids`, `ecr_repository_urls`, `role_arns`, `internal_names` | 서비스별 값 |
| `parameter_prefix` | `/delta-cp/` |

## 시험

```bash
terraform test
```

mock provider로 plan만 실행해 다음 조건을 확인한다.
- ALB가 내부용이고, 잘못된 헤더를 버린다.
- ALB 서브넷에는 인터넷 경로가 없다.
- 공개 앱은 HTTPS로만 받고, 앱 헤더가 붙는다.
- 기본 보안 그룹은 비어 있다.
- 인스턴스에 공인 IP가 없고, IMDSv2·디스크 암호화·불변 태그가 적용된다.
