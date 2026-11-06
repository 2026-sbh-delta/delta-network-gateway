"""운영 배포기 시험. 클라우드·Terraform·Docker 명령은 모두 가짜 runner로 대체한다."""

import sys
from pathlib import Path

import pytest

# 상위 저장소 pytest 설정(importlib 모드)은 sys.path를 바꾸지 않으므로 공용 도구 경로를 직접 둔다.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_support import config_value, data_value  # noqa: E402


@pytest.fixture
def config(tmp_path):
    return config_value(tmp_path)


@pytest.fixture
def data():
    return data_value()
