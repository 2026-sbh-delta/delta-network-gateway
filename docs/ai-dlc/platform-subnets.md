# 플랫폼 역할별 서브넷 분리

## 승인과 범위

2026-11-04 사용자가 다이어그램뿐 아니라 실제 AWS 구성을 변경하도록 승인했다. 이어 AI 서버의 Terraform도 `platform/` 안에서 관리하도록 요청했다. 기존 서비스와 데이터를 보존하며 역할별 네트워크를 분리한다.

## 설계

플랫폼 VPC `10.80.0.0/16`을 유지한다. 플랫폼 API·Worker·DB는 기존 `10.80.1.0/24`에 두고 CloudFront 주소를 유지한다. Jenkins Controller/Agent와 AI/SAST/DAST에는 아래 전용 서브넷을 배정한다. 기존 NAT를 재사용하고 역할별 SG로 수신을 제한한다. 서브넷 분리만으로 통신을 차단했다고 주장하지 않는다.

| 역할 | 목표 CIDR | 목표 주소 |
| --- | --- | --- |
| Jenkins Controller | 10.80.10.0/24 | 10.80.10.20 |
| Jenkins Agent | 10.80.11.0/24 | 10.80.11.21 |
| AI 판단 API | 10.80.20.0/24 | 10.80.20.10 |
| SAST | 10.80.30.0/24 | 10.80.30.10 |
| DAST | 10.80.40.0/24 | 10.80.40.10 |

기존 `10.80.0/1/2/3.0/24`와 겹치지 않음을 AWS 조회로 확인했다. NAT/플랫폼/샌드박스 서브넷, 고객 VPC, TGW/VPN은 유지한다. 중앙 로그는 VPC 밖의 관리형 서비스이므로 별도 로그 EC2를 만들지 않으며 수신 Destination ARN도 유지한다. 단일 AZ의 비용 제한 구성이며 HA 확장을 의미하지 않는다.

## 이전 순서와 안전 조건

1. Terraform으로 역할별 서브넷·SG·새 AI/SAST/DAST VM을 준비한다. 기존 AI·공용 도구 VM은 다른 작업이 사용할 수 있어 삭제하지 않는다.
2. 기존 고정 이미지와 설정·검사 기록을 보존해 새 VM에 각 서비스만 배포한다. 비밀값은 Git·Plan·명령 출력에 넣지 않는다. 새 사설 주소의 인증서 검증을 유지한다.
3. Jenkins의 진행 중 작업을 확인하고, 이력 EBS 스냅샷을 만든 뒤 컨트롤러·에이전트를 전용 서브넷에 재생성한다. 이력 EBS와 로그인/API 자격 증명은 보존한다. 주소와 CA를 플랫폼·에이전트에서 함께 갱신한다.
4. 플랫폼→각 API, Jenkins Agent→Controller, DAST→SAST/등록 샌드박스만 필요한 포트로 연결한다. SAST·DAST의 모델 인증과 GCP WIF도 재검증한다.
5. 기능·데이터 검증 후 플랫폼 연결을 전환한다. 실패하면 기존 연결과 이미지를 복원한다. 다른 팀의 Terraform 소유권이 확인되지 않은 서버를 가져오거나 제거하지 않는다.

## 성공 기준

- 역할별 실제 Subnet ID/CIDR과 실행 중인 서비스가 일치한다.
- 플랫폼 공개 URL·로그인·기존 서비스/DB·Jenkins 이력이 보존된다.
- Jenkins 실제 빌드와 SAST/DAST/AI 연결을 확인한다. 수행하지 않은 AI 분석이나 DAST 공격 검사를 성공으로 쓰지 않는다.
- 허용된 호출 성공과 불필요한 역할 간 접근 차단을 확인한다.
- 중앙 로그 AWS Destination/GCP PubSub 수신 설정이 유지된다.

## 진행 기록

- 2026-11-04: AWS EC2/Subnet/SSM 조회 완료. 플랫폼·Jenkins·AI는 기존 플랫폼 subnet, SAST/DAST는 별도 VPC 공용 VM에서 실행 중임을 확인했다.
- AI 기존 VM `i-0aa3b07234755ae3f`는 `delta-ai` 컨테이너를 실행한다. PR #28은 같은 서비스의 기능 변경이나 해당 EC2의 Terraform은 포함하지 않는다. 새 플랫폼 관리 인스턴스를 준비하고 기존 VM은 보존한다.
- 구현: 진행 중. Terraform Plan/Apply·이전·기능 검증·커밋·게시: 아직 실행 전.
- 2026-11-04: 플랫폼 Terraform validate 및 mock test 5개 통과. 실제 저장 Plan은 네트워크 25개 추가, 기존 리소스 변경/삭제 0개. 아직 Apply 전이다.
- AI Terraform state는 `s3://sbh-delta-workload-tf-616723161368/delta/lab/ai-aws/terraform.tfstate`에서 확인했다. 사용자가 AI 인프라를 플랫폼 쪽에서 관리하도록 재확인했다. `platform/deploy/ai/`에서 같은 state를 이어 쓰며 기존 state와의 차이를 먼저 검증한다.
