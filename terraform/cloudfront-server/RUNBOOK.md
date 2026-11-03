# CloudFront ANF 시험 Runbook

아래 허용/차단 기준은 중앙 `inspection_mode=enabled`일 때 적용한다. 비용 절감 우회 중에는 deny API도 서버의 HTTP 200 응답을 받을 수 있다. 정적 페이지의 과거 검증 기록은 현재 요청의 ANF 판정이 아니다. 전환과 원복은 [중앙 Runbook](../network-security/RUNBOOK.md#anf-비용-중단과-원복)을 따른다.

## 배포 확인

1. Workload STS 계정, ALB active/internal과 두 Target Group healthy를 조회한다. deny TG 포트는 8081이지만 HealthCheckPort는 8080이어야 한다.
2. CloudFront ALB VPC Origin/Distribution Deployed, S3 Public Access Block/OAC SourceArn, ALB 서비스 SG source TCP 80과 bootstrap=false를 확인한다.
3. ALB Subnet의 Server /16→TGW, Server의 Client /16→TGW, 중앙 Spoke/Inspection/Inspected 왕복 Route와 appliance mode를 확인한다. 직접 TGW propagation, VPC peering/인터넷 반환 우회를 허용하지 않는다.
4. EIC 사설 SSH로 서버 localhost8080/8081 두 리스너를 확인한다. 서버 SG는 ALB Subnet에서 두 포트 모두 허용해야 한다. 서버 공인 IP/IPv6/인터넷 Route 부재를 확인한다.

## 두 버튼과 실제 로그

`site_url`을 열어 허용 요청과 차단 요청을 각각 누른다. 허용은 실제 HTTP 200/서버 JSON/port8080, 차단은 보통 약 10초 후 HTTP 504다. 요청 UUID/시각/소요 시간을 기록한다. 503이나 timeout만으로 ANF 차단을 확정하지 않는다.

중앙 계정 CloudWatch Logs Insights에서 FLOW와 ALERT 두 그룹을 함께 선택한다.

```sql
fields @timestamp, availability_zone, event.event_type, event.flow_id,
       event.src_ip, event.src_port, event.dest_ip, event.dest_port,
       event.alert.signature_id, event.alert.signature,
       event.alert.action, event.verdict.action,
       event.netflow.pkts, event.netflow.bytes, event.netflow.start, event.netflow.end, @message
| filter @message like /10[.]10[.](4|5)[.]/ and @message like /10[.]20[.]1[.]10/
| sort @timestamp desc
| limit 200
```

- 허용 마커 `/api/allow/delta-test-marker`는 HTTP 200과 SID2000001 `allowed`/`alert`를 대조한다. ALB source port/flow_id로 양방향 FLOW를 묶는다. FLOW 집계가 늦을 수 있다.
- 차단은 destination8081, SID9000000, alert.action=blocked 및 verdict.action=drop을 확인한다. FLOW 자체의 존재는 허용을 의미하지 않는다.
- 기존 앱 journal은 client/port/method만 기록한다. ALB source의8080 도달과8081 외부 도달 부재를 확인한다. Health Check와 사용자 요청의 path/query 구분은 실제 HTTP 응답과 ANF 마커 로그에서 수행한다.
- 페이지 하단의 `verification.json`은 배포 당시 실제 HTTP와 로그를 대조한 기록이다. 각 버튼 요청의 원인을 실시간으로 판정하는 관제 API가 아니다. 증거를 확인하기 전 verified로 변경하지 않는다.

## 실패 진단

- 페이지403: S3 객체/Content-Type/OAC SourceArn/정책과 Distribution 배포 상태 확인.
- API503: 두 Target Group 등록/healthy, 중앙8080허용/SG/반환 Route 확인.
- 허용 API504: ALB→TGW→ANF→Server의 source CIDR/8080 규칙, SG/NACL/리스너 및 대칭 경로 확인.
- 차단 API200: 8081이 중앙 정책에 들어갔는지 또는 우회 Route가 있는지 확인. 성공으로 기록하지 않는다.
- deny TG unhealthy: health port8080 설정을 바로잡는다. 8081 health failure를 ANF 요청 검증으로 기록하지 않는다.

## 롤백과 정리

이전 직접 EC2 Origin은 검사 경로가 아니므로 ANF 시험을 위해 되살리지 않는다. 문제 발생 시 Distribution의 API 변경을 검토해 중단하고 현재 저장 Plan/state 버전을 보존한다. 허용 범위를 확대해 억지로 통과시키지 않는다. 정책과 Route/SG를 원인에 맞게 수정하고 mock/실제 Plan/통신을 재검증한다.

전환 완료 뒤 이전 직접 Origin/Server SG rule/Server IGW와 ALB prefix-list bootstrap만 저장 Plan으로 제거한다. AWS 소유 서비스 SG/ENI는 AWS가 관리한다. 전체 해체는 별도 요청과 검토 후 CloudFront 비활성화/배포 완료, Origin 연결 해제, ALB/규칙/전용 Route/Subnet/S3 의존 순서로 진행한다. 기존 Client/Server/중앙 기반, state와 로그를 보존한다.
