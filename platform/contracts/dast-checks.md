# 샌드박스 DAST 검사 계약

현재 구현은 AWS ECS에서 실행한다. 같은 빌드의 SAST 결과와 이미지 digest를 확인한 뒤 팀의 `services/delta-dast`에 검사를 요청한다. AWS에서의 결과는 GCP·온프레미스 검증 결과가 아니다.

```text
성공한 빌드 + 같은 커밋의 SAST
  → 원본 SARIF 해시 확인
  → AWS ECS에서 같은 digest 실행·헬스 확인
  → 팀 DAST의 AI·브라우저로 XSS 후보 확인
  → DAST 종료 확인 + ECS 앱 종료
  → 결과 저장·화면 표시
```

## 플랫폼 API

모든 경로는 `/api/v1` 아래다. 기존 로그인과 변경 요청의 CSRF 방어를 그대로 사용한다.

| 메서드·경로 | 내용 |
| --- | --- |
| POST `/services/{id}/dast-checks` | `{ "build_id": "UUID", "security_check_id": "UUID" }`, `Idempotency-Key` 필수 |
| GET `/services/{id}/dast-checks` | 해당 서비스의 검사 이력 |
| GET `/dast-checks/{id}` | 진행·결과·정리 상태 |
| GET `/dast-checks/{id}/events` | 순번 기준 진행 이력 |
| POST `/dast-checks/{id}/cancel` | `{}`, 검사 중단 요청. 정리 확인 전까지 실행 공간 유지 |

빌드와 SAST는 같은 서비스·커밋·이미지여야 한다. 성공한 SAST란 결과 수집 완료를 뜻하며 취약점이 없다는 뜻은 아니다. 외부 검사 URL을 사용자가 임의로 입력할 수 없다. 서버의 사전 등록된 샌드박스만 실행한다.

## 결과 해석

| 값 | 뜻 |
| --- | --- |
| `execution_cloud=aws` | 실제 실행 환경 |
| `environment_check=aws_sandbox_only` | AWS 샌드박스에서 확인한 결과. 다른 배포 환경으로 승계 불가 |
| `REPRODUCED` | 해당 후보를 재현함 |
| `NOT_REPRODUCED` | 이번 시도에서 재현하지 못함. 안전 보증 아님 |
| `BLOCKED` | 브라우저 정책에 차단됨. 추가 확인 필요 |
| `INCONCLUSIVE` | 검사를 완료하지 못했거나 판단 근거 부족 |
| `dast_check=not_run` | 지원하는 XSS 후보가 없어 브라우저 검사를 실행하지 않음 |
| `findings_truncated=true` | 최대 10건만 검사했으며 나머지 확인 필요 |
| `deployment_approved=false` | AI 수정·사람 승인·운영 배포와 별개 |

결과에는 빌드·SAST·커밋·이미지 digest·검사 ID와 근거 SHA-256을 함께 저장한다. 원본 코드·페이로드·인증 값은 공개 API 응답에 넣지 않는다. 원본 DAST 근거는 기존 도구 서버의 실행별 볼륨에 남는다.

## 복구와 확장

일반 실행 확인과 DAST는 DB 유일 제약과 Worker 잠금으로 하나의 풀을 공유한다. 응답이 유실되면 동일 ID를 조회한다. DAST 취소 표식은 디스크에 저장해 늦은 요청이 다음 서비스에 실행되지 않게 한다. 앱 또는 검사 서버의 정리가 불확실하면 풀을 계속 잠그고 재시도한다. 설정 변경 시 이전 실행을 다른 대상으로 보내지 않는다.

GCP·온프레미스 실행기는 후속 연결이다. 대상별 실행기에서 동일 digest 실행·헬스·종료를 확인하고, DAST의 등록 대상과 결과도 환경별로 묶어야 한다. 두 곳으로 배포하면 각각 검사한다. 이때 환경별 풀·권한·사설 접근·DB 테스트 데이터도 분리한다. 현재 AWS 전용 잠금 하나를 그대로 멀티 환경 공용 풀로 쓰지는 않는다.

실제 운영 활성화는 [배포 안내](../deploy/dast/README.md)를 따른다.
