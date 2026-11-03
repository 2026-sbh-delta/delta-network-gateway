# Delta network gateway

AWS Network / Security 계정의 Inspection VPC, Transit Gateway, 공유 Network Firewall, AWS 측 Site-to-Site VPN과 로그를 관리한다.

- [Terraform 실행 안내와 전체 입력/출력](terraform/network-security/README.md)
- [업무 계정 및 원격 네트워크 연결 계약](terraform/network-security/CONNECTIONS.md)
- [AWS 사설 Workload 클라이언트](terraform/workload-client/README.md)
- [AWS 스테이징 기반](terraform/staging-aws/README.md)과 [스테이징 도구](services/delta-staging/README.md)
- [검증, 장애 대응, 해체 Runbook](terraform/network-security/RUNBOOK.md)
- [프로젝트 상태와 AI-DLC](docs/README.md)
- [개발자용 플랫폼 작업 영역](platform/README.md)
- [AI 판단 서비스](services/delta-ai/README.md)
- [AI DAST 점검 서비스](services/delta-dast/README.md)

중앙 실행 프로필은 `AWS_PROFILE=sbh-delta-security`, Workload는 `AWS_PROFILE=sbh-delta-workload`다. 실제 계정과 CIDR/AZ는 필수 입력으로 검증한다. Workload는 별도 root/state의 사설 EC2와 VPC를 사용하며 공인 IP나 인터넷 출구를 생성하지 않는다.

코드 검증, mock Plan, 실계정 Plan, Apply와 통신 검증은 서로 다른 상태다. 현재 검증 기록은 [AI-DLC 문서](docs/ai-dlc/central-network.md)를 확인한다.
