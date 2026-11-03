# Server VPC 관제 Runbook

아래 ANF 경로/차단 기준은 중앙 `inspection_mode=enabled`일 때 적용한다. 비용 절감 우회 중에는 8081도 통과할 수 있다. 모드 전환과 원복은 [중앙 Runbook](../network-security/RUNBOOK.md#anf-비용-중단과-원복)을 따른다.

실제 배포/검증 상태는 [AI-DLC](../../docs/ai-dlc/aws-workload-server.md)를 따른다. 아래 명령 자체는 성공 기록이 아니다. Workload는 `sbh-delta-workload`, 중앙은 `sbh-delta-security`, Region은 `ap-northeast-2`를 사용한다.

## 사설 기동과 관리

`terraform output`의 Server instance/management endpoint/VPC IDs로 실제 조회한다. EC2 running/status checks ok, EIC create-complete, 공인 IPv4/IPv6와 IGW/NAT 부재, Subnet 자동 공인 할당 false, Route Table의 인터넷 기본 Route 부재를 확인한다.

EIC의 임시 키와 터널로 SSH한다. 장기 개인 키를 state나 저장소에 넣지 않는다. SSH source는 Endpoint SG, Endpoint egress는 Server SG의 TCP 22만 허용한다.

```bash
aws ec2-instance-connect ssh --profile sbh-delta-workload --region ap-northeast-2 \
  --instance-id "$DELTA_SERVER_ID" --os-user ec2-user --connection-type eice
```

서버 SSH 안에서 확인한다.

```bash
sudo systemctl is-active delta-test
sudo journalctl -u delta-test --no-pager -n 20
ss -lnt
curl --noproxy '*' http://127.0.0.1:8080/health
curl --noproxy '*' http://127.0.0.1:8081/health
```

둘 다 cloud=aws/service=delta-private-server/해당 port의 JSON 응답이어야 한다. 앱은 `/opt/delta-test/server.py`, systemd는 delta-test.service다. Python 런타임은 고정 AL2023 standard 이미지에 있는 것을 사용한다. 인터넷 apt/pip 다운로드가 필요하지 않다.

## 중앙 Route 확인

- Client/Server Attachment는 available 및 Spoke RT Association이어야 한다. Inspection Attachment만 Inspected RT에 연결한다.
- Spoke의 Client/Server `/16`은 모두 Inspection Attachment를 가리켜야 한다. 같은 계정이라고 직접 Attachment로 보내지 않는다.
- 각 AZ의 Inspection TGW Subnet RT는 같은 AZ의 ANF Primary Endpoint, Firewall Subnet RT는 TGW를 가리켜야 한다.
- Inspected RT에는 각 VPC `/16`의 해당 Attachment 반환 Route가 있어야 한다. 자동 Propagation과 Peering 우회를 만들지 않는다.
- Client 두 Subnet RT에는 Server `/16`, Server 두 Subnet RT에는 Client `/16`의 TGW Route가 있어야 한다. 실제 Route state active와 Attachment appliance mode를 확인한다.
- Firewall과 두 AZ Endpoint는 READY/IN_SYNC여야 한다. 이 상태만으로 서비스 통신 성공을 판정하지 않는다.

## 서비스와 로그 시험

기존 Client EC2의 EIC 사설 SSH에서 실행한다.

```bash
curl --noproxy '*' --connect-timeout 5 --max-time 10 http://10.20.1.10:8080/health
curl --noproxy '*' --connect-timeout 5 --max-time 10 http://10.20.1.10:8080/delta-test-marker
curl --noproxy '*' --connect-timeout 5 --max-time 10 http://10.20.1.10:8081/health
```

앞의 두 요청은 실제 Server JSON 응답이어야 한다. 마지막 요청은 실패해야 하며 timeout만으로 ANF 차단이라고 판정하지 않는다. Client SG egress와 Server SG ingress가 두 포트를 모두 수용하고 Server localhost 두 리스너가 정상인지 대조한다.

중앙 Log Group `/aws/vendedlogs/network-firewall/delta-lab/flow`와 `/aws/vendedlogs/network-firewall/delta-lab/alert`에서 실제 시각/flow_id/IP/포트/프로토콜을 대조한다.

- 허용: Client `10.10.1.143` → Server `10.20.1.10:8080`의 HTTP 응답과 요청/응답 FLOW를 확인한다. 허용 flowbit 규칙 SID 1000001은 noalert이므로 그 SID의 ALERT가 없어도 정상이다.
- 관측: `/delta-test-marker`의 HTTP alert SID 2000001과 allowed action/실제 HTTP 응답을 함께 확인한다.
- 차단: `:8081` 실패와 SID 9000000의 실제 blocked action 또는 존재하는 verdict를 확인한다. ALERT의 존재만으로 차단을 판정하지 않는다.

[FLOW query](../network-security/queries/flows.cwli)와 [ALERT query](../network-security/queries/alerts.cwli)에 실제 IP/포트 필터를 추가한다. FLOW는 단방향이므로 같은 flow_id의 반대 방향도 확인한다. 로그 지연이 있으면 조회 시각 범위를 넓히고 실제 유입을 기다린다. 관리 SSH/localhost 응답은 중앙 ANF 통과 증거가 아니다.

## 최종 확인과 롤백

세 root의 실계정 Plan이 No changes인지 확인한다. 공인 노출/Route/정책/HTTP/FLOW/ALERT를 날짜/명령/실제 결과로 각각 기록한다. 검증하지 않은 부하, 장애 전환, TLS 복호화 또는 다른 서비스는 미검증으로 남긴다.

원격 통신만 중지할 때는 추가 Client egress/Route와 중앙 허용 정책의 제거 Plan을 우선 검토한다. 전체 Server 해체는 Client Route/중앙 정책과 검사/반환 경로, Attachment 소유권, Server/Endpoint/SG/Subnet/VPC 순서로 의존 관계를 확인한다. 중앙 accepter의 prevent_destroy를 자동으로 해제하거나 같은 Attachment를 두 state에서 중복 삭제하지 않는다. 기존 Client/Inspection과 state bucket/버전 및 로그는 기본 보존한다.
