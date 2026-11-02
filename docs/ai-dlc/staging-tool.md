# 파이프라인 스테이징 도구

## 단계와 승인 상태

- 기준일: 2026-11-02 (Asia/Seoul).
- Ideation·Inception 승인: 사용자(정준우) `스테이징 쪽은 우리 관리 에이전트가 호출가능한 하나의 컴포넌트 툴로 개발 시작해` 지시를 아래 범위의 승인으로 기록한다. 파이프라인의 각 단계를 관리 에이전트가 호출하는 도구로 만든다는 구조도 같은 지시에서 확인했다.
- 현재 단계: AWS U1·U2 구현과 단위/mock 검증 완료. 실제 계정 Plan/Apply, 실제 ECS 배포는 미실행.
- 범위: AWS ECS 스테이징 기반(Terraform), 이미지 교체·상태 조회·중지를 하는 도구 서비스(HTTP API, CLI, LLM 도구 정의).
- 범위 밖: GCP 구현, 실제 배포, 앱 데이터 초기화, DAST 대상 자동 등록, 빌드 단계.

## Ideation

- 문제: SAST·빌드 뒤 DAST가 점검할 스테이징을 클라우드마다 손으로 띄울 수 없다. 관리 에이전트가 파이프라인 단계에서 호출만 하면 스테이징이 떠야 한다.
- 사용자: 상위 관리 AI 에이전트(호출자), 파이프라인 운영자.
- 성공 기준: 이미지와 실행 ID를 넘기면 스캐너만 닿는 내부 주소가 돌아온다. 같은 실행 ID 재호출은 중복 배포하지 않는다. 실패는 사유와 함께 돌아오고 서비스는 내려간다.
- Non-goals: 운영 배포, 트래픽 전환, WAF 경유 시험.

## Inception

### 구조

```text
관리 에이전트 ─ staging_deploy(run_id, image) ─▶ delta-staging ─▶ ECS 새 태스크 정의 개정판 → 서비스 1개 → healthy
            ◀─ state=deploying ─────────────
            ─ staging_status(run_id) ───────▶ state=ready, url(내부 ALB) ─▶ DAST
            ─ staging_stop(run_id) ─────────▶ 서비스 0개
```

- 기반(클러스터, 서비스, 내부 ALB, 보안 그룹, VPC 엔드포인트)은 Terraform `terraform/staging-aws`가 만든다. 실행마다 바뀌는 이미지는 도구가 SDK로 바꾼다. Terraform은 `task_definition`·`desired_count`를 무시한다.
- 클라우드 공통 계약: `deploy(run_id, image) → url`. AWS는 ECS, GCP는 같은 계약으로 Cloud Run을 추가한다.
- DAST는 앱 자체를 점검하므로 스테이징은 WAF를 거치지 않는다. 대신 인터넷에 열지 않고 스캐너 CIDR/보안 그룹만 허용한다.

### 요구사항

1. Terraform 1.16.4/AWS Provider 6.71.0과 기존 lock을 쓴다. 기존 VPC와 서로 다른 AZ의 사설 Subnet 2개 이상을 입력으로 받는다. VPC·Route·TGW는 만들지 않는다.
2. ALB는 internal, 리스너 80은 스캐너 출처만 받는다. 스캐너 CIDR은 정규화된 RFC1918만 허용하고 출처가 하나도 없으면 실패한다.
3. 태스크는 공인 IP 없이 Fargate로 돌고, ALB 보안 그룹에서만 컨테이너 포트를 받는다. 태스크 역할은 정책이 없다.
4. 인터넷 출구 없는 Subnet을 위해 ECR API/DKR·Logs·S3 엔드포인트를 기본 생성한다.
5. 도구는 허용 저장소의 이미지만 받고, 배포 전 STS 계정을 확인한다. 비밀처럼 보이는 env 이름은 거부한다.
6. 도구는 바로 `deploying`을 돌려주고, ECS 배포 완료와 대상 그룹 healthy 뒤 `ready`로 바꾼다. 실패하면 서비스를 0개로 내린다.
7. 같은 `run_id`는 멱등이다. 다른 실행이 쓰는 중이면 409, `replace=true`면 교체한다.
8. `GET /v1/tools`로 LLM 도구 정의와 HTTP 경로를 함께 준다.

### Unit of Work

| Unit | 내용 | 상태 |
| --- | --- | --- |
| U1 | `terraform/staging-aws` 기반 모듈, mock 테스트, README 입력/출력 표 | 구현·검증 완료 |
| U2 | `services/delta-staging` 도구(API, CLI, 도구 정의), 단위 테스트 | 구현·검증 완료 |
| U3 | 실제 계정 Plan/Apply와 ECS 배포·DAST 접속 확인 | 미실행 |
| U4 | GCP Cloud Run 구현 | 미착수 |

## 검증 기록

| 날짜 | 명령 | 결과 |
| --- | --- | --- |
| 2026-11-02 | `TERRAFORM_BIN=<1.16.4> terraform/staging-aws/scripts/validate.sh` | fmt/validate 통과, mock `terraform test` 6개 통과 |
| 2026-11-02 | `services/delta-staging`: `.venv/bin/pytest -q` | 21개 통과(ECS/ELB/STS 응답 흉내) |
| 2026-11-02 | `.venv/bin/python -m delta_staging tools` / `deploy`(설정 없음, 허용 밖 이미지) | 도구 정의 출력, 설정 없음·이미지 거부를 종료 코드 2로 보고 |

실제 AWS Plan/Apply, ECS 배포, 내부 ALB 접속, DAST 연동은 실행하지 않았다.

## 남은 일

- 스테이징을 둘 VPC/Subnet과 DAST 실행기 위치를 네트워크 담당과 정한다.
- 실행마다 앱 데이터 초기화(저장형 XSS 점검 잔여물 제거).
- DAST에 스테이징 주소를 실행마다 대상으로 등록하는 API.
- GCP Cloud Run 구현.
