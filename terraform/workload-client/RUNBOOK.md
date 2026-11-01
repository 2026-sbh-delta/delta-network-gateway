# Workload 클라이언트 Runbook

## 배포 상태와 public 노출 확인

업무 디렉터리에서 `AWS_PROFILE=sbh-delta-workload`를 사용한다. 실제 ID는 Terraform 출력으로 대입한다.

```bash
aws sts get-caller-identity --profile sbh-delta-workload
terraform output
aws ec2 describe-instances --instance-ids "$DELTA_CLIENT_ID" \
  --query 'Reservations[].Instances[].{State:State.Name,PrivateIp:PrivateIpAddress,PublicIp:PublicIpAddress,Interfaces:NetworkInterfaces[].Association,Ipv6:NetworkInterfaces[].Ipv6Addresses,Metadata:MetadataOptions}'
aws ec2 describe-instance-status --instance-ids "$DELTA_CLIENT_ID" --include-all-instances
aws ec2 describe-instance-connect-endpoints --instance-connect-endpoint-ids "$DELTA_EICE_ID"
aws ec2 describe-subnets --filters "Name=vpc-id,Values=$DELTA_WORKLOAD_VPC_ID" \
  --query 'Subnets[].{Id:SubnetId,Az:AvailabilityZoneId,Cidr:CidrBlock,PublicIpv4:MapPublicIpOnLaunch,Ipv6:AssignIpv6AddressOnCreation}'
aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$DELTA_WORKLOAD_VPC_ID"
aws ec2 describe-internet-gateways --filters "Name=attachment.vpc-id,Values=$DELTA_WORKLOAD_VPC_ID"
aws ec2 describe-nat-gateways --filter "Name=vpc-id,Values=$DELTA_WORKLOAD_VPC_ID"
aws ec2 describe-security-groups --filters "Name=vpc-id,Values=$DELTA_WORKLOAD_VPC_ID"
```

EC2 running/status checks ok, EIC Endpoint create-complete를 확인한다. 공인 IP/IPv6 없음, Subnet public IP 자동 할당 false, IGW/NAT 없음, 각 RT에 인터넷 기본 Route 없음이 정상이다. SG는 EC2의 Endpoint SG→TCP 22 ingress, Endpoint의 Client SG→TCP 22 egress, 등록한 사설 원격/시험 포트만 허용해야 한다. 기본 VPC의 리소스를 Workload 결과에 섞지 않는다.

## 관리 접속

운영자에게 해당 Endpoint의 `ec2-instance-connect:OpenTunnel`, 인스턴스의 `ec2-instance-connect:SendSSHPublicKey`와 필요한 EC2 조회 권한이 있어야 한다. SCP가 이를 거부하면 접속은 미검증으로 기록하고 공인 IP나 광범위한 SSH ingress를 추가하지 않는다.

```bash
aws ec2-instance-connect ssh --profile sbh-delta-workload --region ap-northeast-2 \
  --instance-id "$DELTA_CLIENT_ID" --os-user ec2-user --connection-type eice
```

EC2에서 `uname -m`, `hostname -I`, `command -v curl`, `ip route`를 확인한다. EIC는 임시 SSH 키를 사용하며 Terraform에서 장기 개인 키를 만들지 않는다. 관리 SSH는 VPC local 통신이므로 ANF 검사 결과로 기록하지 않는다. 인터넷 출구가 없어도 AMI에 사전 설치된 EIC로 접속한다.

## TGW/ANF 연결

1. 중앙 RAM Share의 principal에 업무 계정이 존재하고 업무 프로필에서 TGW가 보이는지 확인한다.
2. 업무 Attachment의 ID/소유 계정/두 TGW Subnet/상태를 조회한다. 중앙에 처음 전달할 `reported_state`는 `pendingAcceptance` 또는 `available`의 실제값을 사용한다.
3. 중앙 accepter와 Spoke RT Association, Workload CIDR의 Inspection 경유 및 Inspected 반환 Route를 확인한다. Attachment available만으로 GCP 연결을 성공 처리하지 않는다.
4. GCP/VPN 구성 후 원격 CIDR을 양쪽 경로에 등록한다. Client SG는 시험 포트 8080/8081 둘 다 허용하고 중앙 ANF 정책은 대조군에 맞춰 하나만 허용한다.
5. GCP 서버도 두 포트를 실제 listen하고 GCP 방화벽이 두 시험 요청을 수용하는지 확인한다. 허용 연결의 응답/FLOW, 차단 연결의 실패와 SID 9000000 처리 결과를 대조한다. ALERT만으로 차단 여부를 판정하지 않는다.

## 롤백과 해체

추가 원격 Route/송신 규칙만 되돌릴 때는 해당 입력을 제거하고 Plan의 변경 범위를 확인한다. EC2나 VPC를 교체하는 Plan은 별도 검토한다.

전체 철거는 다음 의존 관계를 확인한 후 별도 실행한다.

1. GCP의 Workload 광고/반환 경로와 시험 트래픽을 정리한다.
2. 업무 Client의 원격 Route와 중앙의 해당 TGW Association/검사/반환 Route를 정리한다. 중앙 accepter의 `prevent_destroy` 및 실제 Attachment 삭제 동작을 검토하고, 두 state에서 중복 삭제하지 않도록 관리 소유권을 정리한다.
3. Workload Attachment, EC2/EIC Endpoint/SG, Route Table Association/Subnet/VPC를 의존 순서로 삭제한다. 실제 Terraform Plan이 의도한 리소스만 삭제하는지 확인한다.
4. RAM 공유 대상은 해당 계정의 다른 연결이 없는지 확인한 후 정리한다.
5. Workload state bucket/버전과 중앙 Firewall 로그는 기본 보존한다. 이 root의 destroy가 bucket을 삭제하지 않는다.

공식 접속 절차: [AWS EIC Endpoint 연결](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/connect-using-eice.html).
