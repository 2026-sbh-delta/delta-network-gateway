# 고정 온프레미스 Worker 연결

사용자 승인된 오케스트레이터→독립 온프레미스 실행기 연결이다. 현재/legacy 브리지와 고정 검사 문서, 두 기존 역할의 별도 정책을 관리한다. 임의 RunShellScript/다른 EC2/Secret 조회는 허용하지 않는다. 문서는 고정 코드와 JSON action만 받으며 버전1/문서 해시를 클라이언트가 고정한다. 팀 Worker의 이미지·설정·큐는 변경하지 않는다. 실제 실행 컨테이너와 전용 큐는 `deploy/onprem-runtime/release_executor.py`를 사용하는 별도 운영 단계다.

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

현재 CP SAST/DAST의 고정 API를 독립 실행기가 SSM으로 호출한다. 검사 불변 문서1개와 실행기 호스트의 `delta-platform-app` 역할 정책1개를 관리한다. 문서 version/hash·두 고정 EC2 목적지만 SendCommand 가능하며 사용자 URL/셸 명령/인증 토큰을 매개변수로 받지 않는다. 팀 Worker의 HTTPS 설정은 바꾸지 않고 새 경로는 정확한 SSM target snapshot으로 식별한다.

| 추가 출력 | 역할 |
|---|---|
| scanner_document_name | 고정 CP 검사 SSM 문서 이름 |
| scanner_document_hash | Worker가 고정할 검사 문서 SHA256 |

DAST의 전용 `/delta-cp/dast/onprem_connection` SecureString은 `prepare_dast_connection.py`가 24시간 scanner TokenRequest를 직접 발급해 저장한다. 토큰은 Terraform/state/Git/출력에 넣지 않는다. 기존 DAST 역할의 `/delta-cp/dast/` 권한을 사용한다. 만료 전에 동일 절차로 명시적으로 회전하고 서버를 idle 상태에서 갱신해야 한다. 기존 parameter 덮어쓰기는 기본 거부한다.

원복 호환: 이전 배포가 사용하는 `delta-cp-onprem-bridge-a209f55948f0`도 별도 legacy_bridge 자원으로 보존한다. 브리지 역할은 두 검토된 불변 문서와 동일 호스트 한 대만 호출할 수 있다. 사용자는 팀 Worker 유지·독립 경로를 선택했으며 이 인수본은 팀 Worker를 교체하지 않는다.
