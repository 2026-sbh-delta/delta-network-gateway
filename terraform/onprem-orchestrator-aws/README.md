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
