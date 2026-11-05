# 고객 앱 AWS VM

플랫폼이 승인한 환경 ID마다 새 EC2 한 대를 생성한다. 기존 네트워크·IAM·S3 state는 변경하지 않는다. AL2023 **x86_64의 고정 AMI ID**를 지정하며 SSM Agent·AWS CLI가 포함된 이미지가 필요하다. 사설 subnet의 NAT 또는 필요한 VPC Endpoint, SSM/이미지 pull 역할, 배포 Agent→앱 TCP 8088 접근은 연결 담당자가 준비한다. SG의 8088은 지정한 LB·검사 Agent만 허용한다.

## 입력 속성

모두 필수다. 접속 정보는 사용자가 아닌 운영자 설정에서 전달한다.

| 속성 | 타입 | 기본값 | 역할 |
|---|---|---|---|
| account_id | string | 필수 | 생성할 AWS 계정 제한 |
| ami_id | string | 필수 | AL2023 x86_64 고정 이미지 |
| subnet_id | string | 필수 | 준비된 사설 subnet |
| security_group_ids | list(string) | 필수 | 앱 진입·관리 경로 제한 |
| instance_profile | string | 필수 | SSM 및 지정 ECR 읽기만 가능한 VM 역할 |
| environment_id | string | 필수 | 신규 환경 UUID·소유 태그 |
| service_id | string | 필수 | 서비스 UUID·소유 태그 |
| profile_id | string | 필수 | t3.micro 또는 t3.small |

## 출력 속성

| 속성 | 역할 |
|---|---|
| instance_id | SSM 배포와 환경 등록에 사용하는 VM ID |
| private_ip | 배포 Agent에서 실행 검사를 하는 주소 |

실행기는 환경별 영속 로컬 state에 저장한다. 새 생성과 변경 없음만 허용한다. 기존 VM 수정·교체·삭제는 이 흐름에서 거부한다. 중단된 배포의 state를 삭제하지 말고 실제 자원을 확인한 후 같은 디렉터리에서 정리한다.
