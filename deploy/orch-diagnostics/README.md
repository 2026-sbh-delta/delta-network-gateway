# 진단·보고서 오케스트레이터 릴리스

팀 인수/PR 적용은 [인수 안내](../../docs/ai-dlc/diagnostics-pipeline-handoff.md)를 먼저 읽는다. 이 디렉터리의 `check.sh`는 AI·파이프라인·오케스트레이터 회귀만 실행하고 배포하지 않는다. 배포 helper는 상수를 주입하는 호스트 실행용 템플릿이다.

기존 runtime/volume/env/logging을 보존하며 기존 SSM `/delta-cp/orchestrator/diagnostic_ai_connection`만 추가한다. 역할/정책/SG/DNS를 변경하지 않는다. `remote_release.py`는 호스트에서만 실행하고 연결 값/환경 값을 출력하지 않는다. 새 이미지 digest는 로컬 렌더러가 `IMAGE` 상수로 주입한다.

1. 서비스 소스를 커밋하고 `python3 deploy/orch-diagnostics/build_image.py`로 기존 ECR에 ARM64 이미지를 올린다. 결과는 ignored `image.json`.
2. 현재 컨테이너와 영속 상태를 기록한다. 미완료 실행이 있으면 중단하고 교체하지 않는다.
3. `remote_release.py` 앞에 `IMAGE=<고정 digest>`를 붙여 고정 인스턴스의 SSM 명령으로 실행한다.
4. 상태가 없는 후보 컨테이너에서 TLS/인증/스키마422를 확인한 뒤 idle 재확인→기존 컨테이너 stop/보존→새 컨테이너를 시작한다. 오류 시 이전 컨테이너로 되돌린다.
5. 기존 파일 해시/설정/로그를 확인하고 새 실제 검사→진단→화면/다운로드→stop·lease해제를 검증한다.

이전 버전은 결과의 `previous_container` 이름으로 중지 상태에 남는다. 되돌릴 때 미완료 실행을 먼저 확인하고 새 컨테이너를 중지·이름 보존한 뒤 이전 컨테이너의 이름을 복구해 시작한다. 보고서/체크포인트 볼륨을 지우지 않는다.
