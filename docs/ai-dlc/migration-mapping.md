# AWS → GCP 매핑 판단

2026-11-05 보안 기반 후속: Workload와 Network/Security 두 계정의 서울 GuardDuty를 활성화하고 root별 Terraform 등록을 완료했다. 기존 workload 보호 설정은 보존했다. 아래 2026-11-02의 GuardDuty 없음/state 대조는 당시 기록이며 현재 인프라 상태로 재사용하지 않는다. [실제 활성화와 검증](aws-guardduty.md)을 따른다. 이전 판단 서비스의 새 인벤토리 재수집/평가는 이번 활성화 범위에 포함하지 않았다.

2026-11-05 후속: AWS 기본 대상 풀을 공통 `10.10.0.0/16`로 바꾸고 기존 네 서브넷을 예약한다. AWS 공통 root는 네트워크만 관리한다. 아래 Server state 대조 결과는 과거 기록이며 현재 대상 root나 주소로 재사용하지 않는다. [워크로드 통합 기록](aws-workload-consolidation.md)을 따른다. `mapping-workload-server.json` 파일명은 평가 사례와의 호환을 위해 유지하되 내부 주소와 출처는 운영자 제공 예제로 갱신한다.

- 기준일: 2026-11-02 (Asia/Seoul)
- 현재 단계: Construction 로컬 구현 (2026-11-02 사용자 승인). **커밋·푸시 안 함. GCP 생성과 Terraform Plan·Apply 도 안 함.**
- 결정 근거: [ADR-AI-37](../../services/delta-ai/docs/adr/ADR-AI-37-aws-front-door.md), [38](../../services/delta-ai/docs/adr/ADR-AI-38-terraform-vs-cartography.md), [39](../../services/delta-ai/docs/adr/ADR-AI-39-migration-mapping.md)

## Ideation

- 문제: 11/02 회의에서 세부 값 전달과 1:1 이 안 되는 자원이 가장 큰 이슈로 나왔다. 판단 서비스에는 이전 전에 무엇을 만들지 정하는 기능이 없다.
- 사용자: 배포 파이프라인 또는 관리 에이전트(호출자), 결과를 승인하는 사람, 발표 청중.
- 성공 기준: 시연 대상 AWS 서버 구성을 넣으면 GCP 대응과 등급, 차이, 확인할 시험, 값의 출처가 표로 나온다. 대응이 없으면 통과하지 않는다.
- Scope: 서브넷, 대역, 서버 스펙, CPU 구조, OS, 권한, 보안 도구. Terraform 추출기. 기본값 파일. 근거 문서.
- Non-goals: GCP Terraform 생성·Apply, DB 이전, Cartography 구현, 벡터 검색, AWS 전체 자원 지원, GCP → AWS 방향.

## Inception

### Functional Requirements

1. `migration_mapping` kind 를 추가한다. 입력은 정규화한 출발 자원, `network_mode`(기본 `create`), 대상 대역 목록이다. 크기는 원본 수준을 유지하고, 사용률이 있으면 작은 후보를 제안만 한다.
2. 자원별로 대상 후보, 등급(`exact`·`approximate`·`split`·`gap`·`unknown`), 차이, 확인할 시험, 값 출처(`terraform`·`default`·`operator`)를 낸다.
3. `gap`·`unknown` 은 `needs_human`, 고칠 수 있는 충돌은 `fix` 로 판정한다.
4. 보안 도구는 켜진 능력을 빠짐없이 덮는 가장 작은 조합을 고르고, 못 덮는 능력을 적는다.
5. 추출기는 `terraform show -json` 결과에서 정해 둔 속성만 꺼낸다.

### Non-Functional Requirements

- 판정은 LLM 없이 같은 입력에 같은 결과를 낸다. LLM 장애에도 판정은 나온다.
- 기존 kind 의 동작과 테스트를 바꾸지 않는다.
- 민감 표시 값과 state 원본을 입력·로그에 넣지 않는다.
- 기본값으로 정한 값은 실제 확인으로 표시하지 않는다.

### Architecture

```txt
terraform show -json → 추출기(scripts) → 정규화 입력
기본값 파일(YAML)  ───────────────────┘      │
                                             ▼
             POST /v1/assessments  kind=migration_mapping
                                             │
             mapping.py: 대응표 → 후보·등급 ─┴→ 결과 표 + 판정
             knowledge/rules/mapping: 근거 문서 → LLM 설명(판정에 관여 안 함)
```

호출 순서: `migration_mapping` → 사람 확인 → Terraform Apply → `migration_parity`·공격 시험.

### Unit of Work

| Unit | 내용 | 산출물 |
|---|---|---|
| U1 | 계약, 등급, 판정 | `contract.py`, 스키마, 테스트 |
| U2 | 네트워크·스펙·구조·OS·권한 판단 | `mapping.py`, 대응표, 기본값 파일 |
| U3 | 보안 도구 능력 매핑 | 능력 대응표, 테스트 |
| U4 | Terraform 추출기 | `scripts`, 레포 Terraform 기반 픽스처 |
| U5 | 근거 문서, 평가 사례, 문서 갱신 | `knowledge/rules/mapping`, `eval/cases.yaml`, README |

### Acceptance Criteria

- `t4g.nano`, arm64, Amazon Linux 2023 입력 → `e2-micro`, `approximate`, 이미지 재빌드·OS 시험 표시.
- `t4g.medium` 입력 → `e2-medium`, 메모리 원본 이상.
- 대상 대역이 온프레미스 `10.40.0.0/24` 와 겹치면 `fix`.
- 대응표에 없는 타입 → `unknown`, `needs_human`.
- GuardDuty Runtime Monitoring 이 켜져 있는데 대상에서 못 덮으면 `gap`, `needs_human`. SCC Premium 불가 입력도 같다.
- 기본값으로 채운 행의 출처가 `default` 로 나온다.
- `network_mode=reuse` 에서 기존 네트워크가 요구와 다르면 차이가 나온다.
- LLM 없이 같은 입력에 같은 판정이 나온다. 기존 테스트가 전부 통과한다.

### Unit 범위 밖의 선행 조건

| 항목 | 상태 (2026-11-02 확인) |
|---|---|
| AWS `delta-finals`, `delta-finals-ro`, `delta-security` | 로그인 후 확인. 앞의 둘은 계정 616723161368 (ReadOnly·Administrator), `delta-security` 는 482840608926. 조회·정책 시뮬레이션·`create-vpc --dry-run` 통과. 실제 생성은 안 함 |
| AWS 실제 값 | `delta-lab-server`·`delta-lab-client` 는 `t4g.nano`, arm64, 프로파일 없음. ap-northeast-2 에 GuardDuty detector·VPN 연결·WAF web ACL 0개 |
| GCP `project-5bb650b3-9c21-4c8b-a16` | 이 Mac 계정에 권한 없음. `gcloud projects describe` 거부 |
| Terraform CLI | 이 Mac 에 미설치 |
| GCP 담당 | 동규 님 |

## Test

```bash
cd services/delta-ai && .venv/bin/pytest -q
```

결과: 233 passed (2026-11-02, 기존 테스트 포함). `tests/test_mapping.py`, `tests/test_extract_terraform.py` 를 추가했다.

실제 state 대조 (2026-11-02): `terraform/workload-server` 와 팀 state(읽기 전용)로 Docker `hashicorp/terraform:1.16.4` 의 `show -json` 을 돌려 추출기에 넣었다. 복사한 state 는 바로 지웠다. `--exclude-subnet tgw` 기준 결과:

| 자원 | 출발 | 대상 후보 | 등급 | 출처 |
|---|---|---|---|---|
| 서브넷 2개 | `10.20.1.0/24`, `10.20.3.0/24`, 사설 | `10.60.0.0/24`, `10.60.1.0/24`, 외부 IP 없음 | approximate | default |
| 서버 | `t4g.nano` | `e2-micro` | approximate | terraform |
| CPU 구조 | arm64 | x86_64 | approximate | terraform |
| OS | Amazon Linux 2023 | RHEL 계열 | approximate | terraform |
| 권한 | 프로파일 없음 | 권한 부여 없음 | exact | terraform |
| GuardDuty | Terraform 에 없음 | 없음 | unknown | terraform |

판정은 `needs_human`(`EVIDENCE_UNKNOWN`)이고 ops 로 이미지 확인 값을 요구한다. 위의 AWS 조회값(GuardDuty 없음)과 이미지 확인을 넣은 `fixtures/mapping-workload-server.json` 은 `pass` 다. 이미지 확인 값은 픽스처 가정이고 실제 x86 이미지는 아직 만들지 않았다.

## Evaluation

- 가짜 LLM 평가 사례 2건 추가 (`mapping-workload-server`, `mapping-guardduty-without-scc-tier`). 실제 Claude 응답으로는 돌리지 않았다.
- 시험 안 한 것: GCP 서버 실제 생성, 부하 시험, SCC 탐지, 실제 x86 이미지 빌드, Terraform 생성.
- 알려진 한계: [ADR-AI-39](../../services/delta-ai/docs/adr/ADR-AI-39-migration-mapping.md) `구현하며 정한 것` 참고.
