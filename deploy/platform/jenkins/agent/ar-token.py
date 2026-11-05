"""에이전트 인스턴스 역할(IMDSv2)로 서명해 GCP WIF로 delta-build-push@ 단기 토큰을 받아 출력한다(docker login 입력용)."""
import google.auth
import google.auth.transport.requests

creds, _ = google.auth.load_credentials_from_file("/run/delta/gcp-build.json",
                                                  scopes=["https://www.googleapis.com/auth/cloud-platform"])
creds.refresh(google.auth.transport.requests.Request())
print(creds.token)
