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

## Operation (2026-11-07 KST)

- 호환 코드 운영 배포는 아직 실행하지 않았다. 기존 운영 서비스는 `/health/ready` 응답을 확인했다.
- 배포 및 롤백 검증 결과는 실행 후 기록한다. DB downgrade, revision stamp, 운영 데이터 복원은 실행하지 않는다.
