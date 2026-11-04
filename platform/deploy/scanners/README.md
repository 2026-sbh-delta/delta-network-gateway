# 플랫폼 전용 SAST·DAST 서버

기존 공용 도구 VM을 유지하면서, 플랫폼이 사용할 SAST와 DAST를 각각 전용 사설 subnet에 준비한다. 고객 코드를 실행하는 샌드박스와 검사 서버는 별개다. 현재 팀의 `services/delta-sast`·`services/delta-dast` 이미지를 digest로 받아 사용한다.

| 역할 | 주소 | 크기 | 접근 |
| --- | --- | --- | --- |
| SAST | 10.80.30.10:443 | t4g.small | 플랫폼/DAST SG만 허용 |
| DAST | 10.80.40.10:443 | t4g.medium | 플랫폼 SG만 허용 |

DAST는 플랫폼 내부 샌드박스 ALB의 HTTP 80에 접근한다. GCP Cloud Run·AWS API·모델 API는 기존 NAT의 HTTPS로 접근한다. 외부 80이나 광범위한 사설망 egress는 열지 않는다. 온프레미스 경로는 팀 VPN 연결 후 따로 검증한다.

## 소유권과 모델 인증

VM·SG·데이터 EBS는 이 root가 소유한다. 기존 도구 VM, 팀원 S3/state, ECR 이미지, IAM 역할은 수정/가져오기/삭제하지 않는다. state는 **플랫폼 전용 버킷의 `platform/scanners.tfstate`**를 사용한다.

현재 `delta-lab-tooling-host` 역할을 읽기 전용으로 참조해 새 VM에 연결한다. Anthropic 인증과 GCP 스캐너 WIF가 이 역할에 연결돼 있기 때문이다. 이는 **네트워크·실행 서버 분리**이며 IAM 신원까지 별개라는 뜻은 아니다. 이후 전용 역할로 바꾸려면 Anthropic/GCP 신뢰 정책도 함께 전환해야 한다. 플랫폼 API나 Jenkins 빌드 실행기에는 이 역할을 부여하지 않는다.

## 적용 순서

1. 플랫폼 root에서 SAST/DAST 전용 subnet과 NAT 경로를 준비한다.
2. 입력을 채운 뒤 validate/mock test, 실제 저장 Plan을 검토·Apply한다. 기존 공용 도구 VM의 삭제/교체가 없어야 한다.
3. 진행 중 검사가 없는지 확인하고, 고정 이미지·설정·검사 기록을 새 서버로 복사한다. 비밀값은 Git/Terraform state에 넣지 않는다.
4. 각 서버에는 해당 검사 컨테이너 하나만 실행한다. 사설 HTTPS와 인증서 검증을 유지한다. DAST→SAST 주소도 갱신한다.
5. 플랫폼에서 검사·결과 조회, DAST→SAST, 등록 샌드박스/GCP 인증을 검증한 뒤 플랫폼 연결을 전환한다. 실제 공격 재현을 하지 않았다면 건강 상태 확인과 구분한다.
6. 실패하면 플랫폼 SSM 런타임을 기존 공용 도구 주소/CA로 복원한다. 기존 VM과 검사 기록은 보존한다.

검사 기록 EBS는 각 20 GiB, 암호화와 `prevent_destroy`를 사용한다. 원본과 복사본의 쓰기를 동시에 병합하지 않는다. 실제 완료 여부는 [진행 기록](../../../docs/ai-dlc/platform-subnets.md)을 따른다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `account_id` | string | 필수 | 플랫폼 AWS 계정 |
| `region` | string | ap-northeast-2 | AWS 리전 |
| `subnet_ids` | object | 필수 | 역할별 전용 subnet ID |
| `platform_security_group_id` | string | 필수 | 플랫폼 API/Worker SG |
| `ami_id` | string | 필수 | 고정 AL2023 ARM64 AMI |
| `tooling_instance_profile` | string | delta-lab-tooling-host | 기존 모델/GCP 인증을 유지하는 실행 역할 참조 |

`subnet_ids` 내부 속성:

| 속성명 | 타입 | 역할 |
| --- | --- | --- |
| `sast` | string | 10.80.30.0/24 subnet ID |
| `dast` | string | 10.80.40.0/24 subnet ID |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `scanners` | sast/dast별 instance_id, private_ip, api_url, security_group_id, data_volume_id |
| `account_id` | 플랫폼 AWS 계정 |
| `region` | AWS 리전 |

## 검사 기록 복사

`move.py`는 원본 검사 작업이 없는지 확인하고 해당 컨테이너를 잠깐 pause한 상태에서 스냅샷을 만든 뒤 즉시 재개한다. 전송은 **플랫폼 아티팩트 버킷**의 임시 암호화 객체를 사용하며, 접근 URL은 SecureString에만 넣는다. 원본 VM과 팀원 S3/state는 삭제하거나 옮기지 않는다.

```bash
uv run --project platform/deploy python platform/deploy/scanners/move.py --profile delta-platform --outputs platform/.runtime/scanners-outputs.json --source-instance i-020c3bb90804b736c --component sast
uv run --project platform/deploy python platform/deploy/scanners/move.py --profile delta-platform --outputs platform/.runtime/scanners-outputs.json --source-instance i-020c3bb90804b736c --component dast
```

먼저 SAST를 복사하고 DAST가 새 SAST의 CA를 신뢰하도록 연결한다. 새 서버는 비어 있는 전용 데이터 디스크여야 한다. 원본과 동일한 digest·환경·모델 인증 설정·기록을 가져오되, 한 서버에는 해당 검사기만 실행한다. 새 연결 정보는 `/delta-platform/scanners/{sast,dast}/connection`에 저장하고 플랫폼 연결은 자동 전환하지 않는다.

성공하면 이번 작업에서 생성한 임시 객체의 모든 버전과 전송 Parameter를 삭제한다. 실패하면 암호화된 복구 자료를 플랫폼 버킷에 남긴다. 대상 디스크가 이미 채워진 경우 자동으로 덮어쓰지 않으므로 원인 확인 후 대상만 복구한다. 새 CA는 90일, 서버 인증서는 30일이며 자동 갱신은 별도다.
