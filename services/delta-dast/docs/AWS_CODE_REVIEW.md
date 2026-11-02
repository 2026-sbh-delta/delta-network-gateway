# AWS 코드 리뷰를 SAST 입력으로 사용하는 방법

확인일: 2026-11-01. 아래 시간은 공식 문서의 안내이며, 우리 계정·소스에 대한 실측이나 SLA가 아니다. 이번 구현에서 AWS 리소스를 생성하거나 코드 리뷰를 실행하지 않았다.

## 시간

| 방식 | 공식 안내 | 이번 데모에서의 사용 |
|---|---|---|
| IDE 연동 전체 코드 스캔 | 약 1시간. 코드 규모에 따라 더 길어질 수 있음 | 사전 실행 |
| IDE 연동 변경분 스캔 | 5~15분 | 개발 중 사전 실행. 5분 발표의 실시간 단계로 보장하지 않음 |
| Simulated validation 옵션 | 별도 환경을 만들고 악용을 시험하며 통상 1~3시간 소요 | 자체 DAST를 만들므로 우선 비활성화 검토 |

전체·변경분 시간은 [IDE 연동 가이드](https://docs.aws.amazon.com/securityagent/latest/userguide/code-review-ide-integration.html)의 수치다. Simulated validation 시간은 [코드 리뷰 생성 가이드](https://docs.aws.amazon.com/securityagent/latest/userguide/perform-code-review-scan.html)의 해당 단계 안내이며, 일반 정적 스캔 시간과 동일한 수치로 취급하지 않는다.

## 출력

- 웹 화면: 위험도, 설명, 코드 위치, 판단 근거, 권장 수정 내용을 finding별로 제공한다.
- IDE 연동: 중요도별 결과와 `.security-agent/findings-{scan_id}.md` 보고서를 제공한다.
- API: JSON 객체를 제공하므로 화면의 Markdown을 파싱하는 것보다 이 경로를 우선한다. 현재 확인한 문서에서 SARIF를 기본 출력이라고 확인하지 못했다.

출처: [코드 리뷰 결과](https://docs.aws.amazon.com/securityagent/latest/userguide/review-code-scan-findings.html), [IDE 보고서](https://docs.aws.amazon.com/securityagent/latest/userguide/code-review-ide-integration.html).

## API 연결 순서

1. 미리 구성한 코드 리뷰에 [StartCodeReviewJob](https://docs.aws.amazon.com/securityagent/latest/APIReference/API_StartCodeReviewJob.html)을 호출한다. 변경분은 `diffSource`를 사용한다.
2. [BatchGetCodeReviewJobs](https://docs.aws.amazon.com/securityagent/latest/APIReference/API_BatchGetCodeReviewJobs.html)로 종료 상태와 오류를 확인한다.
3. [ListFindings](https://docs.aws.amazon.com/securityagent/latest/APIReference/API_ListFindings.html)에 `agentSpaceId`와 `codeReviewJobId`를 전달한다. `nextToken` 페이지를 모두 처리한다.
4. [BatchGetFindings](https://docs.aws.amazon.com/securityagent/latest/APIReference/API_BatchGetFindings.html)로 finding 상세를 회수한다.
5. 상세 finding을 우리 DAST 체크리스트 입력으로 변환한다. 코드 위치만으로 실행 URL·폼 필드를 자동 확정하지 않는다.

## 확인된 finding 필드

| 필드 | 용도 |
|---|---|
| `findingId`, `codeReviewJobId` | 분석 결과와 원본 스캔 추적 |
| `name`, `description`, `reasoning` | 검사 후보 설명 |
| `riskLevel`, `riskType`, `confidence` | 위험도·분류·분석 신뢰도. 동적 재현 결과와 별개 |
| `codeLocations[].filePath`, `lineStart`, `lineEnd`, `label` | 소스 위치와 source/sink 역할 |
| `remediationCode` | 제안된 수정. 자동 실행하지 않음 |
| `validationStatus` | AWS의 선택적 검증 상태. 우리 브라우저 재현 결과와 구분 |
| `attackScript`, `verificationScript` | 선택 필드. 없을 수 있으며 신뢰된 실행 파일로 취급하지 않음 |

출처: [Finding](https://docs.aws.amazon.com/securityagent/latest/APIReference/API_Finding.html), [CodeLocation](https://docs.aws.amazon.com/securityagent/latest/APIReference/API_CodeLocation.html).

## SAST → DAST에서 추가로 필요한 정보

SAST 출력에 브라우저용 페이로드와 구체적인 재현 절차가 항상 포함된다고 보장할 수 없다. 따라서 SAST 단계 또는 사람이 검토하는 변환 단계에서 아래를 확정해야 한다.

- 실행할 스테이징 버전과 허용된 대상
- 등록된 페이지·폼·저장·재방문 경로
- 검사할 필드와 무해한 페이로드
- 실행 여부를 판정할 관측 방법

이번 로컬 MVP는 SAST가 제공한 `payload`와 `trigger`를 사용한다. 예제 입력의 `source=fixture`는 실제 AI/AWS 스캔 결과가 아니라는 뜻이다. AWS 결과를 받았다는 이유로 빠진 페이로드나 URL을 조용히 만들어 실행하지 않는다.

현재 구현은 [`../examples/aws-code-review-sample.json`](../examples/aws-code-review-sample.json)의 `findingId`, `name`, `description`, `reasoning`, `riskType`, `riskLevel`, `codeLocations[]`를 내부 finding으로 변환한다. 예제에는 trigger가 없으며 Claude는 registry에 이미 등록된 식별자만 선택한다. 선택 조합은 서버에서 다시 검증한다. 샘플은 실제 AWS 스캔 결과가 아니다.

DAST 판정은 별도 기록한다. 양성은 실제 저장·재방문 뒤 실행 사건이 관측됐을 때다. 음성은 정상 입력·저장·재방문·관측을 완료한 특정 테스트에서 실행을 관측하지 못한 경우이며, 사이트 전체의 안전성을 의미하지 않는다. 오류·범위 차단·시간 초과는 판정 불가로 남긴다.
