# ADR-AI-39: 1:1 로 맞지 않는 AWS 자원을 GCP 로 어떻게 판단할 것인가

> 상태: 제안 · 오너: 김연재 · 작성: 2026-11-02. 2026-11-02 로컬 구현(미커밋). 대응 값은 팀 합의 전이다. 범위와 시험 기록은 [AI-DLC 문서](../../../../docs/ai-dlc/migration-mapping.md)에 있다.

## Context

- 결정할 질문: 서브넷, 서버 스펙, OS, 보안 도구처럼 CSP 간에 1:1 로 맞지 않는 자원을 판단 서비스가 어떻게 제안하고 판정할 것인가?
- 배경: 11/02 회의에서 다음이 나왔다. 서브넷부터 올릴지 네트워크를 미리 둘지, 스펙이 안 맞으면 근사치와 매핑표, 타이트하게 잡을지 여유 있게 보낼지, CSP 베스트 프랙티스를 RAG 로 쓸지, GuardDuty 대응 도구를 하나만 쓸지 둘 다 쓸지.
- 현재 코드: `migration_parity` 는 이미 만든 환경의 WAF·권한·로그 등을 비교한다. 만들기 전에 무엇을 만들지 정하는 판단은 없다. WAF 범주 매핑(ADR-AI-16)만 비슷한 구조다.
- 실제 차이: 지금 레포의 AWS 서버는 `t4g.nano`, arm64, Amazon Linux 2023 이고 GCP 서버는 `e2-micro`, x86, Ubuntu 24.04 다. CPU 구조와 OS 가 이미 다르다.
- 범위: 이전 전 매핑 판단을 다룬다. Terraform 생성과 Apply, 입력 출처([ADR-AI-38](ADR-AI-38-terraform-vs-cartography.md))는 다루지 않는다.

## Decision Drivers

1. 판정은 재현할 수 있어야 하고 LLM 이 정하지 않는다(ADR-AI-04).
2. 맞지 않는 부분을 숨기지 않는다. 대응이 없으면 통과가 아니다.
3. 임의로 정한 기본값과 실제 값을 구분해 보여 준다.
4. 표로 보여 줄 수 있고 심사 질문에 근거를 댈 수 있다.
5. 본선까지 구현할 수 있다.

## Considered Options

### Option A: 새 `migration_mapping` kind, 코드 대응표와 등급 (권고)

- 설명: 정규화한 출발 자원을 받아 코드 대응표로 후보를 고르고 등급을 매긴다. LLM 은 근거 문서를 인용해 이유만 설명한다.
- 장점: 결과가 재현되고 매핑표가 그대로 산출물이 된다. `migration_parity` 는 그대로 둔다.
- 단점 및 위험: 대응표 범위 밖 자원은 전부 사람 확인으로 간다.

### Option B: LLM 이 베스트 프랙티스 문서를 RAG 로 읽어 설계

- 장점: 범위가 넓고 처음 보는 자원도 답한다.
- 단점 및 위험: 같은 입력에 다른 결과가 나오고 없는 기능을 말할 수 있다. Driver 1 과 맞지 않는다.

### Option C: `migration_parity` 에 입력을 추가

- 장점: kind 가 늘지 않는다.
- 단점 및 위험: "만들기 전 제안"과 "만든 뒤 검증"이 섞인다.

## Decision

- 선택: Option A 권고.
- 순서: `migration_mapping`(무엇을 만들지) → Terraform Apply → `migration_parity`와 공격 시험(실제로 같은지). 매핑 결과만으로 같다고 말하지 않는다.

### 등급

| 등급 | 뜻 | 판정 |
|---|---|---|
| `exact` | 뜻이 같은 대응이 있다 | 통과 |
| `approximate` | 근사 대응이다. 차이와 확인할 시험을 함께 낸다 | 통과. 단, 차이를 표에 남긴다 |
| `split` | 하나가 여러 자원으로 나뉜다 | 전부 포함되면 통과 |
| `gap` | 대응이 없거나 일부 기능을 잃는다 | `needs_human`. 잃는 기능을 적는다 |
| `unknown` | 값이 없거나 대응표에 없다 | `needs_human` |

수정 가능한 충돌(대역 겹침 등)은 `fix`로 낸다.

### 자원별 판단

| 자원 | 판단 |
|---|---|
| 서브넷 | AWS 는 서브넷이 한 AZ 에 묶이고 인터넷 게이트웨이 경로로 공개·사설을 나눈다. GCP 는 VPC 가 전역, 서브넷이 리전 단위이고 공개·사설 구분이 없다. 그래서 구분은 경로, 방화벽, 외부 IP, Cloud NAT 로 옮긴다. 등급은 `approximate` |
| 대역 | 대상 대역이 출발, 온프레미스, VPN 상대와 겹치면 `fix` |
| 구성 방식 | `network_mode`: `create` 는 서브넷부터 만든다. `reuse` 는 미리 만든 네트워크를 쓰고 만들어야 할 값과 대조만 한다. **기본은 `create`.** 시간이 모자라면 사람이 `reuse` 로 바꾼다. 시간을 재기 전에는 코드가 시간으로 고르지 않는다 |
| 서버 스펙 | 메모리와 vCPU 를 비교해 **원본 이상인 가장 작은 타입**을 고른다. 공유 코어는 vCPU 수만으로 같다고 보지 않고 부하 시험을 확인 항목으로 낸다 |
| 크기 정책 | 원본 수준을 유지한다. 별도 옵션은 두지 않고, 입력에 최대 메모리 사용률이 있으면 작은 후보를 차이 칸에 제안만 한다. 줄일지는 사람이 정한다 |
| CPU 구조 | arm64 ↔ x86 이면 `approximate` 에 "이미지 재빌드 필요"를 붙인다. 멀티 아키텍처 이미지가 확인되지 않으면 `fix` |
| OS | Amazon Linux 는 같은 이미지가 없다. 기본 대응은 RHEL 계열로 두고 패키지와 부팅 스크립트 시험을 낸다. 이미 Ubuntu 를 쓰는 서버는 그대로 간다 |
| 권한 | 인스턴스 프로파일 → 서비스 계정으로 `approximate`. 정책 내용은 자동 변환하지 않는다. 대응표 밖 권한은 `gap` |

### 보안 도구

- **기준은 제품 개수가 아니라 능력이다.** 출발 도구를 능력 목록으로 풀고, 켜져 있는 능력을 **빠짐없이 덮는 가장 작은 조합**을 고른다. 하나로 덮이면 하나, 안 되면 필요한 만큼 쓴다.
- 비용 등으로 줄이면 사람이 승인하고, 잃는 능력을 `gap` 으로 남긴다.
- GuardDuty 는 CloudTrail 관리 이벤트, VPC 흐름, DNS 로그를 기본으로 쓰고 Runtime Monitoring 같은 보호 플랜은 따로 켠다. 그래서 어떤 플랜이 켜져 있는지부터 입력으로 받는다.
- GCP 대응 후보는 Event Threat Detection(로그), VM Threat Detection, Container Threat Detection 이다. 셋 다 Security Command Center **Premium 이상**이다. 회의의 "도구 2개"는 별도 제품이라기보다 SCC 안의 서비스 조합에 가깝다.
- 일부 Data Access 감사 로그는 기본으로 꺼져 있어, 탐지가 쓰는 로그를 켜는 항목도 함께 낸다.
- **미확인:** 현재 GCP 프로젝트에서 Premium 을 쓸 수 있는지, 비용이 얼마인지. 못 쓰면 이 항목은 `gap` 이다.

### 기본값

- 입력이 비어도 진행할 수 있게 기본값 파일을 둔다.
- 결과의 모든 값에 출처를 붙인다: `terraform`, `default`, `operator`. 기본값으로 정한 행은 표에 "기본값"으로 드러나고, 실제 확인으로 치지 않는다.

### 베스트 프랙티스 RAG

- CSP 공식 문서를 규칙별 근거 문서로 넣는다. 지금처럼 규칙 ID 로 찾으며 벡터 검색은 넣지 않는다(ADR-AI-13). 문서는 설명과 인용에 쓰고 판정에는 쓰지 않는다.
- 근거 문서가 없는 규칙은 지금 체계대로 사람 확인으로 간다.
- 발표에서는 "베스트 프랙티스를 참고해 대응표를 만들었고 차이는 표에 남겼다"까지만 말한다. "완전히 같다"고 말하지 않는다.

### 월 예상 비용

- 11/02 호원 님 제안. 결과의 `mapping.cost` 로 서버별 출발·대상 월 비용과 차액을 낸다. **참고 값이며 finding 과 판정을 만들지 않는다.** 판정이 가격 변동에 흔들리지 않게 하기 위해서다.
- 기준: 온디맨드 시간당 단가 × 730 시간. 원 단위가 아니라 USD 이고, 표에 보이는 값끼리 더하고 빼서 합계가 맞게 한다.
- 범위: 서버 비용만 넣는다. 디스크, 네트워크 전송·NAT·공인 IP, OS 라이선스, 보안 도구, 약정·Spot·무료 등급 할인은 빼고 `excludes` 에 적는다. 그래서 전체 이전 비용으로 말하면 안 된다.
- 단가는 서울 리전(`ap-northeast-2`, `asia-northeast3`)만 넣었다. 다른 리전이나 표에 없는 타입은 추정하지 않고 `null` 로 두며, 모르는 줄이 있으면 합계도 `null` 이다.
- 대상 메모리가 원본보다 크면 차액에 사양 차이가 섞인다고 줄에 적는다. 예: `t4g.nano`(0.5 GiB) → `e2-micro`(1 GiB).
- 단가는 사람이 관리하는 표다(2026-11-02 확인). 실시간 조회는 넣지 않았다. 판단 서비스가 외부 가격 API 에 묶이지 않게 하기 위해서다.
- `source_region` 을 주지 않으면 `ap-northeast-2` 를 쓰고 `assumptions` 에 적는다. Terraform 추출기는 아직 이 값을 넣지 않는다.

### 시각화

- 결과는 표 데이터로 내므로 준우 님 11/01 다이어그램에 얹을 수 있다. 화면 담당과 형식은 합의가 필요하다.

### 구현하며 정한 것

- 규칙 8개: `MAP-NET-001~003`, `MAP-CMP-001~002`, `MAP-OS-001`, `MAP-IAM-001`, `MAP-SEC-001`.
- `approximate`·`split` 은 막지 않는 low finding 으로 내서 LLM 이 근거 문서로 설명할 수 있게 했다. 판정은 통과다.
- Terraform 에 GuardDuty 가 없으면 꺼졌다고 쓰지 않고 `unknown` 으로 둔다. 콘솔에서 켰을 수 있어서다. 사람이나 호출자가 실제 값을 넣어야 한다.
- 능력 손실 승인은 `accepted_gaps` 로 받는다. finding 은 dismissed 가 되지만 표에는 `gap` 으로 남고 출처는 `operator` 다. 지금은 보안 도구에만 적용한다.
- **알려진 한계:** CPU 구조 `fix` 의 ops 는 `multi_arch_image=true` 다. 호출자가 빌드 없이 이 값만 넣어 다시 보내면 통과한다. 이 값은 신고이지 이미지 확인이 아니다. 호출자를 정한 뒤 이미지 매니페스트 확인으로 바꿀지 다시 본다.

### 다시 볼 조건

- 대응표 밖 자원이 시연 대상에 많아질 때, 또는 Premium 을 쓸 수 없을 때.

## Consequences

- 긍정: 회의에서 나온 질문마다 판단 규칙과 답이 생긴다.
- 부정: 대응표는 사람이 관리한다. 시연 대상 자원만 먼저 다루며 AWS 전체를 다룬다고 말할 수 없다.
- 부정: 등급은 이전 전 판단일 뿐이다. 실제 성능과 탐지 품질은 시험 전까지 모른다.
- 불확실성: 근사 타입의 실제 성능은 재지 않았다.

## 출처

- https://docs.cloud.google.com/compute/docs/general-purpose-machines
- https://docs.aws.amazon.com/ec2/latest/instancetypes/gp.html
- https://docs.cloud.google.com/vpc/docs/vpc
- https://docs.aws.amazon.com/vpc/latest/userguide/configure-subnets.html
- https://docs.aws.amazon.com/guardduty/latest/ug/what-is-guardduty.html
- https://docs.cloud.google.com/security-command-center/docs/concepts-security-sources
- https://docs.cloud.google.com/security-command-center/docs/overview-threats
- https://aws.amazon.com/ec2/pricing/on-demand/
- https://cloud.google.com/products/compute/pricing/general-purpose
