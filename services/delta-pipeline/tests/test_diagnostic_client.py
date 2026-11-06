import io
import urllib.error

import pytest
from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.diagnostic_client import DiagnosticClient


@pytest.mark.parametrize(
    "url",
    [
        "http://ai.cp.internal",
        "https://user:password@ai.internal",
        "https://ai.internal?token=x",
        "https://ai.internal/path",
        "file:///tmp/x",
    ],
)
def test_diagnostic_transport_rejects_unsafe_configuration(url):
    with pytest.raises(ValueError):
        DiagnosticClient(url, "private-token")


@pytest.mark.parametrize("body", [b"invalid", b"[]", b"x" * 128001])
def test_response_is_bounded_and_errors_never_echo_payloads(body):
    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            assert request.get_header("Authorization") == "Bearer private-token"
            assert timeout > 0
            return Response(body)

    client = DiagnosticClient("https://ai.internal", "private-token")
    client._opener = Opener()
    with pytest.raises(ServiceUnavailable) as error:
        client.diagnose({"case_id": "case-1"})
    assert "private-token" not in str(error.value)


def test_http_error_body_is_not_exposed():
    class Opener:
        def open(self, *a, **kw):
            raise urllib.error.HTTPError(
                "https://ai.internal",
                403,
                "private-token",
                {},
                io.BytesIO(b"private-token"),
            )

    client = DiagnosticClient("https://ai.internal", "private-token")
    client._opener = Opener()
    with pytest.raises(ServiceUnavailable) as error:
        client.diagnose({})
    assert "private-token" not in str(error.value)
