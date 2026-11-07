# 운영 롤백과 DB 호환성

## Ideation과 Inception (2026-11-07 KST)

- 목적: Git main을 `3cbf841984132ab260d5997c5347c573dcb0e29b`의 파일 상태로 복원하고 운영을 해당 코드 계열로 되돌린다.
- 성공 기준: DB 데이터를 보존하며 기존 `0013`과 신규 DB의 마이그레이션이 정상 동작하고 운영 상태를 검증한다.
- 범위: 호환 마이그레이션, 이전 워커의 지원 작업 제한, 운영 설정 병합 및 배포 검증. 데이터 삭제나 DB 복원은 범위 밖이다.
- 사용자가 DB 보존을 선택하고 지정 커밋과 일부 다른 호환 수정 및 검증 후 배포를 승인했다.

## Construction (2026-11-07 KST)

- [PR #78](https://github.com/2026-sbh-delta/delta-network-gateway/pull/78)을 squash 병합했다. `721c90b3bed47237873924cc613d43d1262ccc45`의 파일 트리는 대상 커밋과 동일하다.
- 읽기 전용 운영 조회에서 Alembic `0013`, 예약 테이블 0건, 완료된 `data_migrate` 실행 1건을 확인했다. `0012`는 예약 테이블을 만들고 `0013`은 실행 종류 제약을 확장한다.
- 호환 수정은 동일한 버전 연결과 스키마를 제공하고 `data_migrate` 이력을 보존한다. 이전 워커는 지원하지 않는 작업을 가져가지 않는다.
- 이전 배포에서 운영 설정에 추가된 4개 파이프라인 키가 허용 목록에서 빠져 병합에 실패했다. 값을 출력하거나 변경하지 않고 확인된 선택 키를 허용한다.
- 임시 PostgreSQL을 이용해 `backend/.venv/bin/pytest -q` 실행: 275개 통과, 1개 제외. 신규 DB 마이그레이션을 실행했다. `pytest -q tests/test_worker.py tests/test_rollback_schema_compatibility.py`: 11개 통과, 기존 `0013` 예약 데이터 보존과 작업 선택을 검증했다.
- `python3 -m unittest discover -s deploy/tests -p test_onprem_runtime_merge.py -v`: 5개 통과. 변경 파일 Ruff 검사 통과.

- 원격 CI 첫 실행에서 배포 테스트 65개는 통과했으나 새 테스트 파일의 Ruff 형식 검사에서 실패했다. `ruff format`으로 수정하고 로컬 형식 및 병합 테스트를 다시 통과했다. 원격 재검증은 다음 실행을 따른다.

### PR #79 후속 검사 (2026-11-07 KST)

- 사용자가 PR #79의 문제 해결을 요청했다. 기존 DB 보존과 호환 수정 범위에서 검사 상태를 확인하고 필요한 수정과 재검증을 진행한다.
- PR head `cf60001ba011b6e934f64e59e9bda1ec9ac16d61`의 리뷰 스레드와 대화 댓글은 없다. `Platform checks` 실행 `42372869330`의 첫 시도는 백엔드 및 배포 검사를 통과한 뒤 브라우저 테스트 중 `cancelled`로 종료됐다. 로그의 마지막 오류는 `The operation was canceled.`이며 취소 주체와 이유는 확인하지 않았다.
- 2026-11-07 실행: 변경 Python 9개 파일의 `platform/backend/.venv/bin/ruff check <변경 파일>`과 `ruff format --check <변경 파일>`은 통과했다. `python3 -m unittest discover -s platform/deploy/tests -p test_onprem_runtime_merge.py -v`는 5개 통과했다.
- 2026-11-07 실행: `gh run rerun 42372869330 --job 128404405544 --repo 2026-sbh-delta/delta-network-gateway`로 취소된 작업을 재실행했다. GitHub 연결 도구는 재실행 권한이 없어 HTTP 403을 반환했고 로컬 GitHub 인증을 이용한 재실행은 성공했다. 최종 검사 결과는 아래 후속 기록을 따른다.
- 2026-11-07 22:02 KST 최종 결과: 같은 실행의 두 번째 시도와 `test` 작업 `128409852577`이 `success`로 완료됐다. 브라우저 61개, PostgreSQL 복사와 전환 6개, 실제 로컬 앱과 DB의 세션 유지 이전 검증이 통과했다. 백엔드 275개 통과 및 1개 제외, 배포 검사 65개 통과를 포함한 모든 테스트 단계를 실제 실행했다.
- 2026-11-07 실행: `gh pr view 79 --repo 2026-sbh-delta/delta-network-gateway --json headRefOid,mergeStateStatus,state,statusCheckRollup`으로 head `cf60001b`, 검사 20개 모두 `SUCCESS`, PR `OPEN` 및 병합 상태 `CLEAN`을 확인했다. 취소된 검사 재실행으로 해소됐고 이번 후속에서 실행 코드는 변경하지 않았다. PR 병합과 운영 배포는 실행하지 않았다.

## Operation (2026-11-07 KST)

- 사용자가 PR #79의 main 병합과 운영 배포를 요청했다. head `c8980f454ccba6caff7d3a5ebfa424765ea75102`의 검사 20개 통과와 병합 가능 상태를 재확인한 후 squash 병합했다. main은 `2862e9235ed20e194624f0d088cbf2f52c60a3be`다.
- 직전 Release `42373243650`의 SSM 명령 `99bb1137-162e-440d-a581-517ee9e84494` 실패 로그에서 `read-runtime.py`의 `Invalid or conflicting component runtime parameter`를 확인했다. PR #79는 확인된 선택 키의 병합을 지원한다.
- 2026-11-07 배포 전 HTTPS `/health/ready`는 HTTP 200이었다. SSM 읽기 전용 명령 `2a1fabdc-c151-49a3-9521-b89678406b9d`에서 revision `0013`, services 2, runs 8, run_events 68, reservations 0, data_migrate 1을 확인했다. 읽기 전용 명령 `bed0e877-ce6b-41d5-8a46-2411369c9115`에서 대기 또는 실행 중인 작업은 없었고, 기존 API와 Worker는 서로 다른 이미지 digest로 실행 중이었다.
- 기존 서비스에는 Wazuh 시작 wrapper가 설치되어 있다. DB downgrade, revision stamp, 운영 데이터 복원은 이번 배포에서 수행하지 않았다.
- 2026-11-07 `gh workflow run platform-release.yml --repo 2026-sbh-delta/delta-network-gateway --ref main` 실행. 병합 커밋 `2862e923`을 대상으로 한 [Release 42376126370](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/42376126370)은 사전 검사 20개와 운영 배포에 성공했다. 게시된 플랫폼 이미지 digest는 `sha256:d2372d2e202c3113601436a083be37dc01841dc27c94f3e07783799518a61133`이고, SSM 배포 명령 `cb1f720e-f1a8-4090-a306-25d6d899083b`는 `Success`다.
- 2026-11-07 22:24 KST 배포 명령이 DB 백업 `database/2026/11/07/132414-database-iydxZwiL.dump`을 생성했다. `aws s3api head-object`로 객체 크기 72281바이트와 AES256 암호화를 확인했다. 백업 복원 시험은 실행하지 않았다.
- SSM 읽기 전용 명령 `093281c0-e77f-4c6b-a262-29e5a4582086`: API는 Wazuh overlay 이미지로 healthy, Worker는 게시된 `d2372d2e` 이미지로 running, DB는 기존 `a78d9af3` 이미지로 healthy다. 서비스와 백업 타이머는 active이고 로컬 readiness는 HTTP 200이다. revision `0013`, services 2, runs 8, run_events 68, reservations 0, data_migrate 1은 배포 전과 같다.
- 2026-11-07 `curl` 확인: CloudFront와 사용자 도메인의 `/health/ready` 및 사용자 도메인 `/`은 모두 HTTP 200이다. 미인증 서비스 API는 HTTP 401이다. SSM 명령 `868eab3e-7c4b-4c10-b75d-f8ed87e04bdd`로 Wazuh 자산 무결성과 인증 검사를 실행해 통과했고, `c04fae00-26b1-468a-a428-4f3f426f5c40`으로 최근 Worker 로그에서 오류 문구 0건을 확인했다. 실제 새 작업 실행과 인증된 사용자 로그인 검증은 수행하지 않았다.
- Release 진행 중 별도 PR #80, #81, #82가 main에 병합되어 원격 main이 `9cd12f5458b4dc8c6f2ac70d363bd37ecdf03e9a`로 진행됐다. 운영에 검증된 Release는 `2862e923` 기준이며 이후 병합된 플랫폼 변경의 운영 배포 여부는 이 검증에서 확인하지 않았다.
