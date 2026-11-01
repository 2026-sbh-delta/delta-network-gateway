# sample-target

DAST 수정 에이전트가 고칠 대상 앱 소스 예제. 로컬 실습 게시판(`dast_agent/fixture.py`)과 같은 취약 코드 경로를 가진 작은 Flask 게시판이다.
팀의 실제 앱 저장소가 정해지면 `dast_agent/targets/<id>.json`의 `repo.path`·`repo.remote`를 그 저장소로 바꾼다.
