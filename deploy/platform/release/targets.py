"""운영 배포 대상 카탈로그. 동규님 delta_platform.core.deployment_targets에서 이 배포기가 쓰는 최소분만 옮겼다.

pydantic·delta_platform 패키지 의존 없이 같은 규칙(지원 리전·사양·온프렘 호스트)을 검사한다.
"""

OPTIONS = {
    "aws": {
        "cloud": "aws",
        "runtime": "VM",
        "region": "ap-northeast-2",
        "profiles": [{"id": "t3.small", "memory_mb": 2048, "description": "FE·BE 함께 실행"}],
    },
    "gcp": {
        "cloud": "gcp",
        "runtime": "VM",
        "region": "asia-northeast3",
        "profiles": [{"id": "e2-small", "memory_mb": 2048, "description": "FE·BE 함께 실행"}],
    },
    "onprem": {
        "cloud": "onprem",
        "runtime": "K3s",
        "region": "onprem-local",
        "profiles": [{"id": "small", "memory_mb": 512, "description": "앱 전체 최대 자원"}],
        "hosts": ["delta-onprem"],
    },
}


class DeploymentTarget(dict):
    """{cloud, region, profile_id[, host_id]} 그 외 키는 거부한다."""

    KEYS = {"cloud", "region", "profile_id", "host_id"}

    @classmethod
    def model_validate(cls, value):
        if not isinstance(value, dict) or set(value) - cls.KEYS:
            raise ValueError("배포 대상 형식이 올바르지 않다")
        option = OPTIONS.get(value.get("cloud"))
        if option is None:
            raise ValueError("지원하지 않는 클라우드다")
        if value.get("region") != option["region"] or value.get("profile_id") not in {
            p["id"] for p in option["profiles"]
        }:
            raise ValueError("지원하는 리전과 사양을 선택하세요.")
        if value.get("host_id") != ("delta-onprem" if value["cloud"] == "onprem" else None):
            raise ValueError("등록된 온프레미스 호스트를 선택하세요.")
        return cls(value)

    @property
    def cloud(self):
        return self["cloud"]


def profile(cloud, profile_id):
    return next(p for p in OPTIONS[cloud]["profiles"] if p["id"] == profile_id)
