# 고정 온프레미스 Worker 연결

사용자 승인된 오케스트레이터→기존 AWS Worker 연결이다. 신규 SSM 문서1개와 기존 오케스트레이터 역할의 추가 정책1개만 관리한다. 임의 RunShellScript/다른 EC2/Secret 조회는 허용하지 않는다. 문서는 고정 코드와 JSON action만 받으며 버전1/문서 해시를 클라이언트가 고정한다. Worker와 플랫폼 설정은 변경하지 않는다.

GetCommandInvocation은 AWS가 리소스 단위 권한을 제공하지 않으므로 Resource=*가 필요하다. ListCommandInvocations/ListCommands는 부여하지 않는다. [AWS 권한 정의](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ssm.html)

backend.hcl.example을 사용해 init→validate/test→plan을 확인한 뒤 같은 plan을 apply한다. 대상 계정616723161368/서울, 변경용 delta-finals. 최종 No changes와 실제 역할의 고정 문서 호출을 검증한다.

## 입력 속성

변수 없음. 문서 content는 deploy/onprem-runtime/document.json의 고정 소스, 대상 Worker는 한 대로 고정한다.

## 출력 속성

| 이름 | 역할 |
|---|---|
| document_name | content hash가 포함된 불변 문서 이름 |
| document_hash | 호출자가 고정할 SSM 문서 SHA256 |

## 네이티브 검사 확장

현재 CP SAST/DAST의 고정 API를 기존 Worker가 SSM으로 호출한다. 추가 불변 문서1개와 `delta-platform-app` 역할 정책1개만 관리한다. 문서 version/hash·두 고정 EC2 목적지만 SendCommand 가능하며 사용자 URL/셸 명령/인증 토큰을 매개변수로 받지 않는다. 기존 Worker의 HTTPS 설정은 바꾸지 않고 새 경로는 정확한 SSM target snapshot으로 식별한다.

| 추가 출력 | 역할 |
|---|---|
| scanner_document_name | 고정 CP 검사 SSM 문서 이름 |
| scanner_document_hash | Worker가 고정할 검사 문서 SHA256 |

DAST의 전용 `/delta-cp/dast/onprem_connection` SecureString은 `prepare_dast_connection.py`가 24시간 scanner TokenRequest를 직접 발급해 저장한다. 토큰은 Terraform/state/Git/출력에 넣지 않는다. 기존 DAST 역할의 `/delta-cp/dast/` 권한을 사용한다. 만료 전에 동일 절차로 명시적으로 회전하고 서버를 idle 상태에서 갱신해야 한다. 기존 parameter 덮어쓰기는 기본 거부한다.
