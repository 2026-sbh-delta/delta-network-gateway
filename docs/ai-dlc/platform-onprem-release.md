# 온프레미스 FE·BE 배포 묶음

2026-11-05 `Okay implement the onprem_plan` 지시로 ONPREM_PLAN.md의 설계와 구현 단위를 승인받았다. 기존 k3s·demo 앱·DB·VPN을 보존한다. 팀의 검사 서버는 공개 API에 연결하며 서버와 S3 state를 수정하지 않는다. 11월 5일 추가 지시에 따라 DAST 자동 API가 준비되지 않았다면 이번 실행에서 제외한다.

## 구현

- 같은 프로젝트·저장소·커밋의 성공한 amd64 FE·BE 빌드를 묶는 API와 실행기. 두 실제 digest·Pod UID·환경 설정·FE→BE 통신을 검증하고 실패·취소·재시작에도 해당 실행 자원만 정리한다.
- 고정 namespace/RBAC/Quota/Service/NetworkPolicy는 YAML, 임시 Pod와 이미지 인증은 Worker가 관리한다. 별도의 `delta-notes` 운영 후보와 전용 PostgreSQL/PVC를 구성한다.
- 비밀값 없는 상태 수집은 팀의 `cluster_facts`를 재사용한다. 출발 환경 입력이 있으면 기존 `migration_parity` 계약도 생성한다. AI API 호출/판정 자동 승인과는 구분한다.
- 제한된 FE→BE 정책의 내용 해시 승인과 resourceVersion 확인. 넓은 허용·다른 namespace 변경은 거부한다.
- 고정 이미지의 운영 후보 준비, 빈 DB만 허용하는 승인된 덤프 복원, 플랫폼 VM 읽기 역할의 ECR 인증 발급·갱신. ECR 인증의 SSM 전달은 임시 공개키로 암호화한다.
- 샘플 앱 PR: [delta-sample-app #1](https://github.com/2026-sbh-delta/delta-sample-app/pull/1). 선택 PostgreSQL과 OTel HTTP histogram·5xx 로그·FE→BE trace를 추가한다. 기본 메모리 실행도 유지한다.

## 실제 검증 — 2026-11-05 KST

| 항목 | 결과 |
|---|---|
| Jenkins 빌드 | 소스 `f16fa3d`, FE `48a09db5-4289-4d09-b3b8-280695503fe6`, BE `97d5813a-6da6-49de-9dcb-9a1fbb3748ab` 성공 |
| k3s 실행 | FE·BE 실제 imageID/UID 대조, FE `/api/health` 성공, 임시 Pod·ECR Secret 정리 |
| RBAC | scanner의 Secret 조회·demo 변경, controller의 demo Pod 생성 모두 거부 |
| 통신 | 정상 FE→BE 허용. 출발지 egress가 허용된 다른 namespace Pod→BE는 ECONNREFUSED로 차단 |
| 정책 복구 | 격리된 샌드박스의 허용 규칙 제거 후 FE 502. 내용 해시 승인 적용 후 정상 복구. 검토된 템플릿으로 실행했으며 AI 생성으로 기록하지 않음 |
| 운영 후보 | `delta-notes`의 FE·BE·PostgreSQL 각 1개 Running. 보안 승인 대기. 사설 NodePort 32081 |
| 플랫폼 등록 | FE 서비스에 `온프레미스 운영 후보` 환경 등록. 환경 ID `1ca2d65f-0ebb-4d16-ad48-866a46f9818f`. 실제 노드 UID로 호스트 식별. 관측 전송은 `pending_private_route`로 기록 |
| DB 리허설 | 로컬 PostgreSQL 16 덤프→별도 온프레미스 빈 DB 복원. 기존 메모 2건과 새 쓰기 확인. BE 재시작 후 3건 유지. AWS 실제 고객 DB 이전은 아님 |
| 기존 자원 | `demo/db-0`, 기존 web 2개 유지. 라우터·VPN·팀 서버·S3 state 변경 없음 |
| 자원 관측 | 배포 후 노드 CPU 약 2%, memory working set 1845Mi/37%. FE 26Mi, BE 27Mi, DB 38Mi. 디스크 여유 약 13GiB. 부하 시험/최고 사용량 결과가 아님 |
| 관측 코드 | 샘플 앱 테스트에서 실제 OTLP HTTP 수신, FE→BE trace 부모 관계, 요청 histogram, 5xx 로그 및 원문 query 제외 검증 |

실검증은 SSH 관리 연결을 통해 **실제 K3s API와 실제 controller 토큰**을 사용했다. AWS Worker→온프레미스 사설 경로 성공으로 표시하지 않는다. 민감한 연결 파일·원본 증거는 무시된 `platform/.runtime/onprem-release/`에만 저장한다.

## 테스트와 상태 구분

- 샘플 앱: Node.js 24에서 `npm test` 21개 통과. PostgreSQL 통합 및 OTLP 수신 검증 포함. GitHub CI 성공.
- 플랫폼: `uv run pytest tests/test_onprem_sandbox.py tests/test_onprem_bundle.py -q` 14개 통과.
- 배포 도구: `uv run --project platform/deploy python -m unittest discover -s platform/deploy/tests -p 'test_onprem*.py' -q` 7개 통과. 상태 비밀값 제거, 변경 이미지 거부, 정책 범위, 보안 미검사 차단 포함.
- 관련 소스 Ruff 검사·포맷과 diff whitespace 확인. 전체 CI는 PR에서 실행한다.
- VM 내부 Apply와 플랫폼 환경 등록 완료. 새 플랫폼 API/Worker 릴리스와 실 서비스 DNS 전환은 아직 미실행이다. 별도 PR #45의 프론트 변경은 main에 반영됐지만, 이번 `backend_build_id` 요청과의 연동은 별도 확인이 필요하다.

## 남은 연결

1. AWS↔온프레미스 왕복 경로: Worker→6443/32080, DAST→6443/32080, Collector→중앙 4318/4319. 현재 10.40.0.10→플랫폼 10.80.1.70 연결 실패다. 팀의 VPN 설정을 변경하지 않았다.
2. 공개 검사 API: `d7i08p4jsjouo.cloudfront.net`은 origin header가 `dast`로 고정된 DAST 전용 진입점이다. 클라이언트에서 `X-Delta-App`을 바꿔 SAST 주소로 사용하면 안 된다. DAST 플랫폼 API는 현재 401이며 사용자 지시대로 미실행 처리한다. SAST 공개 경로도 별도 확인이 필요하다.
3. 중앙 관측: 기존 native Collector는 중지 상태. 인증된 gateway 전송과 CloudWatch/X-Ray 조회·화면 표시를 확인하기 전에는 완료로 표시하지 않는다. 이번 작업으로 별도 로그 서버를 온프레미스에 띄우지 않았다.
4. 팀 AI: 비밀값 없는 YAML/실제 상태와 계약을 제공한다. 실제 AI 호출·판정→정책 제안 연결은 별도 연동이다. `data.md`의 차분 정보는 `/delta/diff/connection`, AI 판단은 `/delta/ai/connection`에 있으며 팀의 서버는 그대로 둔다.
5. 실제 AWS 소스 DB와 서비스 도메인/활성 쓰기 대상 확정. 현재는 후보 앱과 복원 리허설이다. 완전 무중단·세션 이전·다중 DB 동시 쓰기를 주장하지 않는다.

실행 절차와 복구 범위는 [온프레미스 실행 안내](../../platform/deploy/onprem/RELEASE.md)를 따른다. 단일 VM 장애 대응과 악성 고객 코드의 강한 격리는 이번 범위가 아니다.
