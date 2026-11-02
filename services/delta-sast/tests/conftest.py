import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory) -> Path:
    """Opengrep은 tests/ 아래를 기본으로 건너뛰므로 예제 앱을 임시 폴더로 복사해 점검한다."""
    root = tmp_path_factory.mktemp("apps")
    for app in FIXTURES.iterdir():
        if app.is_dir():
            shutil.copytree(app, root / app.name)
    return root
