# 보안 도구 호스트 (AWS)

SAST·DAST 상시 서비스를 올리는 EC2 1대와 이미지 저장소를 만든다. 보안 도구는 점검 대상(스테이징·운영)과 분리된 독립 서비스다. 컨테이너와 표준 인터페이스(HTTP API, SARIF, OIDC)만 쓰므로, 호스팅 위치를 바꿔도 이미지는 그대로다.

- 인스턴스: Amazon Linux 2023 arm64(Graviton). SSM 에이전트가 기본 설치돼 있다.
- 접속: 보안 그룹 인바운드 0개. 작업실은 SSM 포트 포워딩으로 연다(SSH 미사용).
- 출구 IP: 탄력적 IP로 고정한다. 스테이징 ALB에 DAST 실행기 /32로 허용한다.
- 권한(인스턴스 역할):
  - SSM 관리(`AmazonSSMManagedInstanceCore`)
  - 두 ECR 저장소 이미지 받기
  - `parameter_path` 아래 Parameter Store 읽기
  - (선택) `sts:GetWebIdentityToken`: AWS 아웃바운드 페더레이션으로 LLM 단기 자격 증명을 받을 때 쓴다
- 이미지 저장소: `delta-tooling/sast`, `delta-tooling/dast`. 태그 변경 불가, 푸시 시 스캔, 최근 10개를 보존한다.
- 컨테이너 실행과 배포는 [deploy/tooling](../../deploy/tooling/README.md)을 따른다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `name` | `string` | `delta` | 리소스 이름 접두사 |
| `environment` | `string` | `lab` | 이름과 환경 태그 |
| `region` | `string` | `ap-northeast-2` | Region |
| `account_id` | `string` | 필수 | Provider가 허용할 계정 ID(12자리) |
| `vpc_id` | `string` | 필수 | 호스트를 둘 기존 VPC |
| `subnet_id` | `string` | 필수 | 인터넷 게이트웨이로 나가는 공개 서브넷. 출구는 탄력적 IP |
| `instance_type` | `string` | `t4g.large` | Graviton(arm64) 인스턴스 타입만 허용 |
| `root_volume_gb` | `number` | `40` | 루트 EBS(gp3, 암호화) 크기. 이미지·점검 상태·DAST 증거 |
| `parameter_path` | `string` | `/delta/tooling/` | 호스트가 읽을 Parameter Store 경로(슬래시로 시작·끝) |
| `enable_outbound_federation` | `bool` | `true` | 인스턴스 역할에 `sts:GetWebIdentityToken` 허용 |
| `tags` | `map(string)` | `{}` | 공통 태그 추가/재정의 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `instance_id` | 호스트 인스턴스 ID(SSM 대상) |
| `egress_ip` | 고정 출구 IP. 스테이징 ALB에 DAST 실행기 /32로 허용 |
| `role_arn` | 호스트 IAM 역할. LLM API 페더레이션 규칙의 주체 |
| `ecr_repository_urls` | `sast`, `dast` 이미지 저장소 주소 |
| `port_forward_command` | 노트북에서 작업실(127.0.0.1:8765)을 여는 SSM 명령 |

## 상태와 Plan

실제 tfvars, backend 설정, 상태 파일과 Plan은 gitignore 대상이다. 스테이징과 같은 상태 버킷을 쓰고, 상태 경로만 `delta/lab/tooling-aws/`로 따로 둔다.
