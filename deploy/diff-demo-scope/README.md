# 시연 Diff IAM·Lambda 범위 수정

고정된 기존 Diff 이미지 위에 `iam.py`, `terraform.py`, `contract.py`, `diff.py` 네 모듈만 읽기 전용으로 적용한다. 런타임 환경·토큰·역할·WIF·로그·상태 마운트와 API 인증은 보존한다. 후보의 루프백 포트에서 실제 state·실제 LLM 결과를 확인한 뒤 8095로 교체한다. 단계 실패 시 기존 컨테이너를 복구한다. 고객 앱 파이프라인, 운영 배포와 DNS는 호출하지 않는다.

릴리스 디렉터리에 네 소스와 `manifest.json`, `deploy.py`, `verify.py`, `rollback.py`를 넣고 해시를 확인한 뒤 실제 Diff 호스트에서 `python3 deploy.py <release-directory>`로 실행한다. 이미 변경된 서비스나 같은 이름의 기존 릴리스가 있으면 덮어쓰지 않는다. 이 절차는 기존 `07931d6e…` 이미지와 TGW 패치 `687cf1cc…`에만 적용한다. 새 범용 이미지의 배포 경로로 재사용하지 않는다.

승인된 복구가 필요하면 진행 중인 Diff 요청·파이프라인이 없음을 먼저 조회하고 호스트에서 `python3 <release-directory>/rollback.py <release-directory>`를 실행한다. 이전 컨테이너와 현재 수정본을 모두 보존하며 state를 덮어쓰지 않는다. 원래 자리표시자로 돌아가는 절차가 아니라 이 수정 직전의 실제 Diff로 복귀하는 절차다.

[설계·검증·실제 적용 상태](../../docs/ai-dlc/diff-iam-scope.md)
