# Delta network gateway

AWS Network / Security 계정의 Inspection VPC, Transit Gateway, 공유 Network Firewall, AWS 측 Site-to-Site VPN과 로그를 관리한다.

- [Terraform 실행 안내와 전체 입력/출력](terraform/network-security/README.md)
- [업무 계정 및 원격 네트워크 연결 계약](terraform/network-security/CONNECTIONS.md)
- [검증, 장애 대응, 해체 Runbook](terraform/network-security/RUNBOOK.md)
- [프로젝트 상태와 AI-DLC](docs/README.md)

기본 실행 프로필은 `AWS_PROFILE=sbh-delta-security`다. 실제 계정과 CIDR/AZ는 필수 입력으로 검증한다. 이 저장소에 업무 계정 리소스 생성 코드나 중앙 인터넷 출구는 포함하지 않는다.

코드 검증, mock Plan, 실계정 Plan, Apply와 통신 검증은 서로 다른 상태다. 현재 검증 기록은 [AI-DLC 문서](docs/ai-dlc/central-network.md)를 확인한다.
