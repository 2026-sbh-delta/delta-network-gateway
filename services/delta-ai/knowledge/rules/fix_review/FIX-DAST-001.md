---
rule_id: FIX-DAST-001
doc_type: rule
provider: any
title: 패치 빌드 재검증
---
## 왜 위험한가
수정 전에 재현된 항목은 패치 빌드에서 같은 절차로 재현되지 않아야 고쳤다고 말할 수 있다.

## 판정 기준
재현된 항목마다 패치 빌드에서 `NOT_REPRODUCED` 여야 한다. `REPRODUCED` 는 패치를 다시 만들게 하고, `BLOCKED` 는 출력이 여전히 인코딩되지 않았을 수 있어 사람에게, 결과가 없거나 `INCONCLUSIVE` 면 모르는 것으로 사람에게 넘긴다.

## 고치는 법
재현 경로의 출력 위치에 맞게 인코딩한다. `NOT_REPRODUCED` 는 이번 입력과 절차의 결과이며 다른 출력 지점의 안전을 뜻하지 않는다.
