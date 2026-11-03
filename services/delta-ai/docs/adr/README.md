# delta-ai ADR 목록

기술 스택과 설계 결정을 팀 ADR 템플릿 형식으로 정리했다. 모두 **상태: 제안**이며 팀 합의 전이다. Notion에 올릴 때 팀 번호로 바꾼다. 26번 이후는 Notion 초안과 같은 번호를 쓴다. 구조도, 워크플로, 데이터 계약은 [architecture.md](../architecture.md)에 있다.

## MVP 구조

```text
호출자(플랫폼·CI·에이전트 — 미정)
  │ POST /v1/assessments {kind, 인벤토리·보고서}
  ▼
FastAPI ─ Bearer 토큰 인증
  ▼
규칙 검사 → 근거 문서 → LLM 1회(설명·탐지기) → 코드 판정(pass·fix·needs_human)
  │                      └ 없어도 환경 차이 판단은 동작
  ▼
응답: verdict · reasons · findings · checks · remediation · rule_candidates · meta
```

- **있는 것:** 판단 API 2종(`migration_parity`, `feedback_ingest`), 환경 차이 규칙 7개, 규칙 후보와 승인 API, 테스트 66개(2026-11-02).
- **없는 것:** 샌드박스 적용, 컨테이너 이미지, 팀 실제 입력 형식, 승인 규칙을 적용할 코드 검사.
- **뺀 것:** 코드 보안 판단(`code_security`, SARIF 입력, 코드 패치). 2026-11-02 SAST 담당이 별도 서비스로 다시 설계하기로 해 delta-ai에서 뺐다(ADR 14, 15 폐기).

## 결정 목록

| ADR | 결정 질문 | 현재 선택 | 권고 |
|---|---|---|---|
| [01](ADR-AI-01-service-boundary.md) | 제공 형태 | 독립 HTTP 서비스 | 유지 |
| [02](ADR-AI-02-api-shape.md) | 엔드포인트 구성 | 단일 경로와 `kind` | 유지 |
| [03](ADR-AI-03-sync-vs-async.md) | 동기·비동기 | 동기 | **유지, 시간 제한 추가** |
| [04](ADR-AI-04-verdict-authority.md) | 판정 주체 | 코드 | 유지 |
| [05](ADR-AI-05-execution-authority.md) | 실행 권한 | 제안만 | **팀 결정: 에이전트 실행과 게이트 호출** |
| [06](ADR-AI-06-orchestration.md) | 프레임워크 | 일반 함수 | **팀 결정: Agent SDK는 에이전트 쪽** |
| [07](ADR-AI-07-language-stack.md) | 언어·스택 | Python·FastAPI·Pydantic·uv | 유지, `uv.lock` 커밋 |
| [08](ADR-AI-08-llm-model.md) | 모델 | Claude Sonnet 5.5 | 유지, 데모 앱으로 비교 |
| [09](ADR-AI-09-llm-route.md) | LLM 경로 | Anthropic API 직접 | **팀 확인: 비용 처리·Bedrock** |
| [10](ADR-AI-10-llm-output-trust.md) | LLM 출력 검증 | 전체 검증, 실패 시 폐기 | 유지 |
| [11](ADR-AI-11-llm-data-protection.md) | 데이터 보호 | 패턴 마스킹·비신뢰 경계 | 유지 |
| [12](ADR-AI-12-input-model.md) | 환경 입력 | 제품 중립 인벤토리 | **팀 결정: 제공 담당** |
| [13](ADR-AI-13-evidence-retrieval.md) | 근거 검색 | 규칙 ID 정확 매칭 | 유지, 문서 추가 |
| [14](ADR-AI-14-security-detection-input.md) | 취약점 탐지 | ~~Semgrep SARIF 입력~~ | 폐기: SAST 서비스로 이동 |
| [15](ADR-AI-15-patch-format.md) | 패치 형식 | ~~서버 재생성 diff~~ | 폐기: 코드 패치 경로 제거 |
| [16](ADR-AI-16-waf-parity.md) | WAF 동등성 | 범주 매핑·공격 시험 | 유지. HAProxy 기각으로 Option D 재검토 안 함(37) |
| [17](ADR-AI-17-feedback-activation.md) | 규칙 활성화 | 회귀 시험·사람 승인 | 유지 |
| [18](ADR-AI-18-detector-format.md) | 탐지기 형식 | 문자열 조합 | **변경 권고: Semgrep 규칙** |
| [19](ADR-AI-19-rule-store.md) | 규칙 저장 | JSON 파일 | 유지, 인스턴스 1개 |
| [20](ADR-AI-20-service-auth.md) | API 인증 | 고정 토큰 | **변경 권고: 판단·승인 토큰 분리** |
| [21](ADR-AI-21-evaluation.md) | 품질 검증 | pytest·평가 YAML | 유지, 데모 앱으로 교체 |
| [22](ADR-AI-22-deployment.md) | 배포 | 미구현(로컬) | **컨테이너 1개 권고** |
| [23](ADR-AI-23-prelim-reuse.md) | 예선 코드 재사용 | 범용 부품만 복사 | 유지, **규정 확인** |
| [24](ADR-AI-24-progress-graph.md) | 진행 그래프 | 미구현 | **팀 결정: 단계 이벤트와 화면 담당** |
| [25](ADR-AI-25-jenkins-gate.md) | 배포 파이프라인 연결 | 로컬 시험(Jenkins) | **단계별 게이트 권고, 팀 결정: Jenkins 와 AWS 구성도** |
| [26](ADR-AI-26-onprem-runtime.md) | 온프레미스 증거 연동 | `runtime_security`와 수집기 구현 | **팀 결정: 데모 범위 포함 여부** |
| [27](ADR-AI-27-dast-verdict.md) | DAST 역할·판정, 구동 주체 | `dast_verification` 과 시연 콘솔 구현. Jenkins 구동, 관리 에이전트 우선 기각 | **준우 님 설계와 맞춤, 패치 흐름 합의** |
| [28](ADR-AI-28-fix-guidance-review.md) | 보안 패치 경계 | `fix_review` 구현, 그래프 연결은 스테이징·DAST 등록 뒤 | **준우 님과 합의 필요** |
| [29](ADR-AI-29-deployment-decision-log.md) | 배포 결정 경위 | Jenkins 구동·delta-ai 판정 구조 | **권고, 팀 결정** |
| [30](ADR-AI-30-jenkins-argo.md) | Jenkins + Argo CD | Jenkins 단독 | **권고, 팀 결정: 고객 앱 인프라** |
| [31](ADR-AI-31-demo-time-budget.md) | 시연 구성 | 사전 실행 + 카나리 롤백 라이브 | **권고, 시간 미측정** |
| [32](ADR-AI-32-onprem-environment.md) | 온프레미스 시험 환경 | 홈 서버 전용 VM·격리 VLAN | **팀 결정: 본선 범위, VPN 적용** |
| [33](ADR-AI-33-network-posture.md) | 환경별 격리 구분 | 실측 3계층 태세 구현 | **팀 결정: 데모 범위, 호출 주체** |
| [34](ADR-AI-34-onprem-waf-priority.md) | 온프레미스 WAF 순서 | 태세 구분 먼저, WAF 미착수 | **앞단 결정 후 재검토** |
| [35](ADR-AI-35-jenkins-main-alignment.md) | main 맞추기 | 새 브랜치 병합, SAST 사람 확인 | 로컬 구현, 리뷰 전 |
| [36](ADR-AI-36-gate-preflight.md) | 판단 서비스 사전 확인 | 토큰 조회로 트래픽 전 중단 | 로컬 구현, 리뷰 전 |
| [37](ADR-AI-37-aws-front-door.md) | 트래픽 앞단 | HAProxy 기각, AWS 기능 사용 | **ALB 가중치 권고, 시험 전** |
| [38](ADR-AI-38-terraform-vs-cartography.md) | 출발 환경 값의 출처 | Terraform 기준 | 유지, Cartography 미구현 |
| [39](ADR-AI-39-migration-mapping.md) | CSP 간 매핑 판단 | `migration_mapping` 구현, 대응표·등급 | 로컬 구현, **대응 값 팀 확인** |
| [40](ADR-AI-40-pipeline-graph.md) | 전체 흐름 구동 | 고정 전이 그래프가 구동, Jenkins 는 도구 | 이전 단계는 대역으로 구현, **팀 확정 전** |

## 오늘 회의에서 정할 것

1. 누가 언제 호출하는가 (01, 03, 05)
2. 점검 에이전트와 어떻게 나누는가 (05, 06)
3. 인벤토리, 공격 결과, 보고서는 누가 어떤 형식으로 주는가 (12, 16, 17)
4. 승인된 탐지 규칙을 SAST 쪽 어디에 적용하는가 (17, 18)
5. 게이트웨이와 LLM 비용을 어떻게 처리하는가 (09, 16)
6. 진행 그래프 화면을 누가 어떻게 만드는가 (24)
