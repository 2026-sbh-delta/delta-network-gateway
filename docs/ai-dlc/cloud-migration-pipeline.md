# Jenkins 클라우드 이전 파이프라인과 서비스별 가중치 화면

- 기준일: 2026-11-04 (Asia/Seoul)
- 상태: 설계·로컬 구현. **클라우드 자원은 만들지 않는다.** AWS·GCP 는 로컬 컨테이너와 HAProxy 대역으로 시험한다.
- 실행 방법: [deploy/jenkins-local](../../deploy/jenkins-local/README.md) 의 "클라우드 이전" 절
- 앞선 문서: [Jenkins 로컬 검증](jenkins-local.md), [AWS → GCP 매핑 판단](migration-mapping.md)

## Ideation

- 문제: 판단 서비스는 이전 전(`migration_mapping`)·이전 후(`migration_parity`) 판정과 수정안을 내지만, 수정안을 적용해 대상에 배포하고 트래픽을 옮기는 실행 단계가 없다. 카나리 비율도 파이프라인이 정한 10→50→100 만 쓴다.
- 사용자: 이전을 진행하는 배포 담당(김연재), 시연을 보는 심사위원.
- 성공 기준
  1. Jenkins 한 번 실행으로 판단 → 갭 메우기 → 대상 배포 → 동등성 확인 → 서비스별 단계 전환까지 간다.
  2. 자동으로 메울 수 있는 갭은 파이프라인이 메우고 다시 판단받는다. 나머지는 사람이 정하기 전에는 진행하지 않는다.
  3. 사람이 화면에서 서비스마다 대상 비율을 바꿀 수 있고, 바꿀 때마다 지표와 판단을 거친다. 기준을 넘으면 그 서비스만 출발 환경으로 되돌린다.
  4. 판단 서비스·가중치 화면이 죽어도 통과로 보지 않고, 롤백은 화면 없이도 된다.
- Scope: AWS → GCP 를 먼저 하고, 시험이 통과하면 GCP → AWS 를 같은 흐름에 붙인다. **두 방향 모두 로컬 시험 통과(2026-11-04).**
- Non-goals: 실제 Terraform apply·GCP 자원 생성, DB 이전 실행(동규 님 `platform-db-move` 연결 전), 도메인 전환, 실제 Cloud Armor·AWS WAF.

## Inception

### 실행과 판단의 분리

판단 서비스는 판정과 수정안만 낸다(ADR-AI-05). 수정안을 적용하는 것은 파이프라인 스크립트다. 적용 뒤에는 반드시 다시 판단받고, `pass` 를 받아야만 다음 단계로 간다.

### 갭을 누가 메우는가

| 판단 결과 | 예 | 메우는 방법 |
|---|---|---|
| `MAP-CMP-002` fix: `/mapping/servers/i/multi_arch_image` | arm64 → x86_64 | 대상 구조로 이미지를 빌드하고 스모크 시험이 통과한 뒤에만 값을 `true` 로 바꿔 다시 판단 |
| `MAP-NET-002` fix: `/mapping/subnets/i/target_cidr` | 대상 대역 충돌 | 판단이 고른 빈 대역을 그대로 넣고 다시 판단 |
| `MAP-SEC-001` unknown: SCC 등급 모름 (GCP 대상만) | GuardDuty → SCC | 사람이 등급을 고른다(`SCC_TIER` 또는 입력 단계). AWS 대상은 GuardDuty 에 등급이 없어 묻지 않는다 |
| `MAP-SEC-001` gap | Standard 라 VM 위협 탐지를 잃음, SCC Security Health Analytics 는 AWS 대응 없음 | 사람이 잃어도 된다고 승인하면 `accepted_gaps` 에 넣고 다시 판단. 표에는 gap 으로 남는다 |
| `MAP-IAM-001` gap | SSM·ECR 권한, `roles/pubsub.publisher` | 자동으로 옮기지 않는다. 사람이 승인해야 진행하고 승인 기록을 남긴다 |
| `PAR-WAF-001` fix: ops + `waf_rules` | 대상에 WAF 없음 | 대상 계획의 WAF 값과 규칙(GCP 대상은 Cloud Armor, AWS 대상은 AWS 관리형 그룹)에 넣고, 대상 입구에 적용한 뒤 공격 시험 |
| `PAR-WAF-002` unknown: 공격 시험 없음 | WAF 를 켠 직후 | 공격 시험을 돌려 결과를 넣고 다시 판단 |
| 그 밖의 `autofix=forbidden` | 시크릿·권한 방식 | 사람 확인 |

같은 판단을 세 번 넘게 돌려도 `pass` 가 안 되면 멈춘다. 적용할 수 없는 수정안 경로를 받으면 추측하지 않고 사람에게 넘긴다.

### 파이프라인

```text
준비 → 판단 서비스 확인 → 출발 환경 확인
  → 이전 판단 ⟲ 갭 메우기(대상 구조 빌드·스모크, 대역) ⟲ 사람 결정(SCC 등급, 승인할 갭)
  → 대상 계획(tfvars) → 대상 이미지 빌드 → 대상 배포
  → 동등성 확인 ⟲ 갭 메우기(WAF 규칙 적용, 공격 시험) ⟲ 사람 확인
  → DB 이전(대상 있으면 사람 확인, 미연결) → 서비스별 단계 전환(auto | manual) → 마무리
```

Jenkins 파라미터: `DIRECTION`(`aws-to-gcp` | `gcp-to-aws`), `SCENARIO`(`good`, `scc-standard`, `iam-gap`, `security-gap`, `bad-target`, `gate-down`), `SCC_TIER`(GCP 대상만), `ACCEPT_GAPS`, `CUTOVER_MODE`, `CUTOVER_STEPS`(예: `web=10,50,100;api=25,100`).

방향은 출발 인벤토리(`pipeline/migration/inventory-<출발>.json`)와 판단 서비스의 방향별 대응표가 정한다. 파이프라인 단계는 같고, 비율·롤백·공격 시험은 `SOURCE`·`TARGET` 으로 양쪽을 바꿔 쓴다.

### 로컬 대역

```text
                   ┌ gateway :81 ─ backend mig_<svc> ─┬ server aws (weight) → aws edge :8201 ─┬ mig-web-aws (arm64)
브라우저·traffic.py ┤                                  └ server gcp (weight) → gcp edge :8202 ─┤ mig-api-aws
                   └ (공격 시험은 각 edge 에 바로 건다)                                           ├ mig-web-gcp (amd64)
                                                                                                └ mig-api-gcp
```

- **gateway**: 서비스마다 backend 가 하나씩 있고 `aws`·`gcp` 두 서버의 가중치 합을 100 으로 둔다. 가중치가 곧 비율이다.
- **edge**: 각 클라우드 입구(ALB + AWS WAF, 부하 분산기 + Cloud Armor)의 대역이다. 경로로 서비스를 고르고, WAF 대역은 HAProxy ACL 파일에 런타임으로 패턴을 넣는다. **실제 Cloud Armor·AWS WAF 가 아니다.** 판단 서비스가 낸 규칙의 범주(sqli, xss …)만 같은 범주의 시험 패턴으로 바꿔 막는다.
- 출발 컨테이너는 인벤토리의 구조(AWS 출발 t4g 는 `linux/arm64`, GCP 출발 E2 는 `linux/amd64`)로, 대상은 판단 결과의 CPU 구조(E2·t3 모두 `x86_64` → `linux/amd64`)로 빌드한다. GCP → AWS 는 구조가 같아 재빌드 fix 가 없다. Apple Silicon 에서 amd64 는 에뮬레이션으로 돈다.
- 출발 컨테이너가 출발 버전(`<출발>-src`)이 아니면 다시 띄운다. 반대 방향 빌드가 대상으로 띄운 컨테이너(실패율 포함)를 출발로 쓰지 않으려는 것이다.
- 실제 환경에서는 대상 계획을 Terraform 으로 적용한 뒤 대상 환경 값을 **다시 수집**해 동등성 판단에 넣어야 한다. 로컬 실행기는 적용한 계획을 대상 환경 값으로 쓴다(대역).

### 서비스별 가중치 화면 (traffic-control)

- 위치: `deploy/jenkins-local/traffic-control/`, `127.0.0.1:18082`. Jenkins 와 같은 compose 네트워크에서 HAProxy 런타임 API 로 가중치를 바꾼다. HAProxy 관리 포트는 밖으로 열지 않는다.
- 쓰기 권한
  - 파이프라인: `Authorization: Bearer <TRAFFIC_CONTROL_TOKEN>` (`.env`)
  - 화면: 페이지가 받은 CSRF 토큰(`X-Control-Token`). 다른 사이트의 페이지는 이 값을 읽지 못한다.
- 상태: 진행 중인 전환(run)과 서비스별 상태만 메모리에 둔다. 실제 가중치는 매번 HAProxy 에서 읽는다.

| 모드 | 누가 비율을 바꾸나 | 화면 |
|---|---|---|
| `idle` | 아무도 | 읽기 전용 |
| `auto` | 파이프라인(`CUTOVER_STEPS` 순서) | 비율은 읽기 전용, 롤백 버튼만 |
| `manual` | 사람(화면) | 슬라이더·프리셋·롤백·확인·전환 마침 |

서비스 상태: `source`(대상 0%) → `measuring`(측정 중, 잠김) → `passed` | `needs_ack`(표본 부족 등, 사람이 확인해야 다시 올릴 수 있음) | `rolled_back`(이번 실행에서 잠김) → `completed`(100% 통과).

수동 모드 흐름

1. 사람이 서비스 비율을 바꾼다 → traffic-control 이 가중치를 바꾸고 그 서비스를 `measuring` 으로 잠근 뒤 이벤트를 남긴다.
2. 파이프라인(`cutover.py manual`)이 이벤트를 받아 그 서비스만 `traffic.py` 로 재고 `deployment_gate`(대상 = 이번 실행의 대상 클라우드) 판단을 받는다.
3. 지표 초과·`fix`·판단 불가 → 그 서비스만 0% 로 되돌리고 `rolled_back`. `needs_human` → `needs_ack`. `pass` → `passed`(100% 면 `completed`).
4. 사람이 "전환 마침"을 누르면 끝난다. 0~100 사이에 걸친 서비스가 있으면 거절한다. 전부 `completed` 면 SUCCESS, 일부만 옮겼으면 UNSTABLE(부분 이전).

가중치 순서: 새 값이 큰 쪽 서버를 먼저 올린다. 그래야 중간에 두 서버가 모두 0 이 되지 않는다. 기존 `haproxy_ctl.py split` 은 100% 에서 0% 로 되돌릴 때 잠깐 0/0 이 되는 문제가 있어 같은 규칙으로 고친다.

### 실패 처리

- 롤백은 traffic-control 을 거치지 않고 HAProxy 에 바로 한다(`cutover.py rollback`). 화면이 죽어도 되돌릴 수 있어야 한다. 화면에는 가능하면 알린다.
- 파이프라인이 실패·중단되면 `completed` 가 아닌 서비스를 전부 출발 환경 100% 로 되돌린다. 이미 100% 판단을 통과한 서비스는 그대로 둔다.
- 판단 서비스·traffic-control 응답이 없으면 통과로 보지 않는다.

### Requirements traceability

| 요구 | 구현 | 시험 |
|---|---|---|
| 판단 → 갭 메우기 → 재판단 | `migration_plan.py`, Jenkinsfile.migration | 단위 + `good` 실행 |
| 사람 결정 없이 gap 을 넘지 않음 | 입력 단계, `accepted_gaps` | `scc-standard`, `iam-gap` 실행 |
| WAF 수정안 적용과 공격 시험 | `edge_waf.py`, `attack_probe.py` | 단위 + `good` 실행 |
| 서비스별 자동 단계 전환 | `cutover.py auto` | `good`, `bad-target` 실행 |
| 화면에서 서비스별 비율 조절 | `traffic-control`, `cutover.py manual` | 단위 + 수동 실행(브라우저) |
| 롤백이 화면에 의존하지 않음 | `cutover.py rollback` | 단위 + traffic-control 중지 시험 |
| GCP → AWS 같은 흐름 | 방향별 대응표(delta-ai), `inventory-gcp.json` | 단위 + `good`·`bad-target`·`security-gap`·`iam-gap`·수동 실행 |

### 로컬 시험 결과 (2026-11-04, 스택 `delta-migration-test`)

| 빌드 | 방향 | 상황 | 결과 |
|---|---|---|---|
| #2 | aws-to-gcp | good / auto | SUCCESS. 출발 arm64 → 대상 amd64, 게이트웨이가 `gcp-b2` 응답 |
| #3·#4 | aws-to-gcp | bad-target | 중단 시 롤백 / auto 에서 web 완료·api 10% 롤백(오류율 0.375) |
| #5 | aws-to-gcp | scc-standard | 세 능력 승인 후 SUCCESS, 사용자 단계 `web=50,100;api=100` |
| #6·#8 | aws-to-gcp | iam-gap | 미승인 FAILURE / 승인 SUCCESS |
| #7 | aws-to-gcp | gate-down | 시작 전 FAILURE |
| #9 | aws-to-gcp | bad-target / manual(브라우저) | UNSTABLE: web 완료, api 10% 롤백 |
| #10 | aws-to-gcp | traffic-control 중지 | FAILURE(code 30), 트래픽 이동 없음 |
| #11 | gcp-to-aws | good / auto | SUCCESS. 재빌드 없음, WAF 수정안 AWS 관리형 3개 그룹·범주 6개, `aws-b11` 응답 |
| #12 | gcp-to-aws | bad-target / auto | FAILURE: web 완료, api 10% 롤백(오류율 0.375) |
| #13→#14 | gcp-to-aws | security-gap | #13 은 입력 답 매핑 버그로 FAILURE → 고친 뒤 #14 승인 SUCCESS |
| #15 | gcp-to-aws | iam-gap 승인 | SUCCESS, `remaining_gaps` 에 `roles/pubsub.publisher` 기록 |
| #16 | gcp-to-aws | bad-target / manual(브라우저) | UNSTABLE: web 50→100 완료, api 25% 롤백(오류율 0.500) |
| #17·#18 | aws-to-gcp | good·scc-standard 재실행 | 변경 뒤 회귀 없음, 세 능력 승인이 `ACCEPT_<능력>` 으로 기록 |

## 남는 일

- 실제 실행기: 대상 root 에 tfvars 적용, 대상 배포, 적용 후 대상 환경 재수집, 실제 LB 에 공격 시험. 대상 root 에 없는 변수는 빌드마다 `terraform-gaps.json` 으로 남는다.
  - GCP 대상(`terraform/gcp-server`): 머신 타입·보안 정책 변수가 없다.
  - AWS 대상(`terraform/workload-server`): `t4g.nano` 와 arm64 AMI 검사가 박혀 있어 **t3(x86) 대상은 지금 root 로 만들 수 없다.** WAF(WebACL)·GuardDuty 기능 변수도 없다. `azs` 는 AZ 두 개와 TGW 서브넷을 받아 서브넷 하나로는 채우지 않는다.
- DB 이전 단계를 `platform-db-move` 와 연결.
- GCP 출발 인벤토리는 실제 GCP 프로젝트를 조회하지 않고 AWS 인벤토리를 거꾸로 둔 시험 값이다(GCP 는 상시무료만 쓴다). GCP 수집기를 만들면 바꾼다.
