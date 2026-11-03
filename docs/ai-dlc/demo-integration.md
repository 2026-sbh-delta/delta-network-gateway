# 온프레미스·DAST·AI 판단·배포 통합 시연

- 기준일: 2026-11-02 (Asia/Seoul)
- 상태: 로컬 구현·리허설 완료. **브랜치는 푸시했고(PR 전) 서버 배포는 안 함.** 사용자 요청 "전체 임시 시연, 보여 줄 UI" 에 따른 작업이며 팀 합의 전이다.
- 브랜치: `feat/demo-integration` (`feat/jenkins-on-main` + `feat/onprem-integration` 병합)
- 실행 방법: [deploy/demo-console](../../deploy/demo-console/README.md)
- 결정 근거: [ADR-AI-27](../../services/delta-ai/docs/adr/ADR-AI-27-dast-verdict.md) (제안)

## Ideation

- 문제: 세 축이 서로 다른 브랜치에 있고 서비스끼리 호출하지 않아 전체 흐름을 보여 줄 수 없었다.
- 사용자: 시연자(김연재), 팀원, 심사위원.
- 성공 기준: 한 화면에서 시나리오를 고르면 실제 서비스들이 차례로 돌고, 위험하면 배포 전에 멈추며, 어디까지가 실측이고 대역인지 드러난다.
- Non-goals: 서버 배포, AWS·GCP 실제 이전, VPN, `/fix` 패치 검토.

## 범위

| 구성 요소 | 상태 | 비고 |
|---|---|---|
| 온프레미스 증거 | 실측 | VM 200. 수집 약 2분, 저장본 재사용 |
| AI 판단 | 실제 | 규칙 판정, Claude 설명 |
| DAST | 실제 | Chrome 재현, 대상은 실습 게시판 |
| 출발 환경 값 | 선언값 | AWS 인벤토리는 설정 파일 값 |
| 배포·트래픽 | 대역 | Mac Docker 의 Jenkins, HAProxy 대역 |
| AWS·GCP·VPN | 미연결 | 포함하지 않음 |

## 구현

- `services/delta-ai`: 두 브랜치 병합(`deployment_gate` + `runtime_security`), `dast_verification` 추가, 규칙 문서 3개.
- `deploy/demo-console`: 흐름 구동·화면·상태 API. 표준 라이브러리만 쓴다.
- `deploy/jenkins-local`: delta-ai 를 `127.0.0.1:18090` 에 공개, 카나리 단계마다 8초 유지.

## 검증 (2026-11-02)

| 명령 | 결과 |
|---|---|
| `services/delta-ai` `.venv/bin/python -m pytest -q` | 196개 통과 |
| `.venv/bin/python -m pytest -q deploy/demo-console/tests deploy/jenkins-local/tests` | 67개 통과 |

콘솔로 실제 스택을 돌린 결과다(온프레미스·DAST·배포 판단에 Claude `claude-sonnet-5-5`).

| 시나리오 | 빌드 | 결과 |
|---|---|---|
| 정상 | #68, #72, #73, #75, #78, #79 | 10→50→100% 승격. 게이트웨이 시험 요청 실패 0 |
| 취약 | #69, #80 | `REPRODUCED` → `needs_human` 으로 정지 → 재검증 `NOT_REPRODUCED` → 승격, 실패 0 |
| 장애 | #70, #81 | 재발 기록으로 사람 확인 → 승인 → 10% 에서 오류율 초과 → 자동 롤백, 안정 버전 유지 (#81 시험 요청 실패 3/397) |
| 정상(오탐) | #76 | 스택을 새로 띄운 직후 p95 544ms 로 100% 에서 롤백. 아래 알려진 문제 참고 |

`down.sh` 후 `up.sh` 로 처음부터 띄워 재현했다(#75·#76). 그 뒤 Jenkins 파라미터 선언과 p95 기준을 바꿔 Jenkins 만 재시작하고 #78~#81 을 돌렸다.

온프레미스 수집: 오류 0(`collect_onprem.py` 종료 코드 0), 런타임 판단 finding 33건·실측 통제 8건, 이전 판단 finding 8건·실측 통제 6건, 모두 `needs_human`.

## 알려진 문제

- **VM 201 → VM 200 연결이 안 된다.** 양성 대조(포트 80 기대 open)가 `closed` 라 노드 계층과 전체 태세가 `unknown` 이다. 같은 VLAN 인데 ping 도 실패하고 VM 200 tcpdump 에 패킷이 안 보인다. 원인은 못 찾았다. 앞선 `isolated` 실측과 다르다.
- 판단 서비스가 뜨지 않은 상태의 실제 스택 시험은 이번 콘솔로 하지 않았다. 단위 테스트로만 확인했다.
- 게이트웨이 시험 요청은 초당 약 10건이고 카나리 측정 부하와 별개다.
- `planner: agent`(AI 에이전트 DAST)와 `/api/collect` 버튼은 화면으로 돌려 보지 않았다.
- Mac 메모리 여유가 적다(스왑 약 9.6GB 사용). 정상 빌드 p95 가 544ms(#76)·824ms(#78)까지 튀어 시험대 기준을 2000ms 로 올렸다. 서버 기준이 아니며, 원인을 고친 것이 아니다. 시연 전에 다른 앱을 닫고 한 번 돌려 둔다.

## 다음 작업

- 준우 님과 ADR-AI-27 합의(Jenkins 구동에 맞춘 호출 방식, `/fix` 패치 흐름, DAST 대상을 실제 릴리스로 맞추기).
- 배포 대상을 온프레미스 k3s 로 옮겨 증거를 낸 곳과 배포하는 곳을 일치시키기.
- 동규 님 게이트웨이 결정 뒤 `haproxy_ctl.py` 를 비율 변경·상태 조회·즉시 원복 계약으로 교체.
