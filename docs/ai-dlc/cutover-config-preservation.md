# 재배포 시 시연 도메인 설정 보존

## 승인과 범위

2026-11-07 사용자가 배포 스크립트 수정과 검증을 승인했다. 사용자 접속 주소는 `demo.delta.mungji.com`이고 AWS·GCP 입구는 기존 `aws.adajosou.site`·`gcp.adajosou.site`를 사용한다. 기존 SSM 전환 설정을 운영자가 관리하는 값으로 보존한다.

## 설계

`deploy/platform/deploy.sh`의 서비스 도메인 초기화는 SSM `PutParameter`의 덮어쓰기 금지 기능을 사용한다. 기존 설정이 있으면 `ParameterAlreadyExists`를 정상적인 보존 결과로 처리한다. 첫 배포에는 기존 Terraform 출력으로 설정을 생성하며, 동시 생성에서도 먼저 저장된 설정을 보존한다. 권한 오류와 일시적인 AWS 오류는 배포를 중단한다.

Cloudflare 토큰은 기존 SecureString 읽기 경로를 사용한다. 도메인 설정을 바꾸려면 운영자가 SSM `orchestrator/cutover_config`와 해당 존 DNS 편집 토큰을 명시적으로 갱신한다.

## 검증

구현 전 기존 `deploy.sh`를 실제 진입점으로 실행하는 로컬 회귀 검증을 준비했다. AWS·Terraform은 임시 실행 파일로 대체하며, 테스트에서 클라우드 요청이나 실제 컨테이너 교체를 수행하지 않는다.

2026-11-07 검증 결과는 다음과 같다.

| 검증 | 결과 |
| --- | --- |
| 수정 전 배포 진입점 회귀 | 사용자 도메인과 명시적 비활성화 설정을 Terraform 값으로 덮어쓰고 버전10을11로 올리는 실패2건을 재현했다. |
| `python -m pytest -q deploy/platform/tests` | 신규 회귀7개와 기존 SSM 결과 판정15개, 총22개 통과. 기존 값 보존, 최초 생성, 동시 생성, 권한/제한 오류에서 배포 중단, Terraform 출력 부재를 검증했다. |
| `bash -n deploy/platform/deploy.sh`, `git diff --check` | 통과. |
| 수정된 도메인 초기화 블록과 실제 AWS SSM | 기존 값이 있는 것을 먼저 확인하고 오래된 임시 도메인 출력을 공급했다. 덮어쓰기 금지 요청은 `ParameterAlreadyExists`로 처리되어 블록이 정상 종료했다. `demo.delta.mungji.com`과 설정 버전10, 토큰 버전4가 그대로였다. |

테스트는 `deploy.sh orchestrator`를 임시 AWS·Terraform 실행 파일과 함께 실행해 정상 배포 진입점을 검증한다. 실제 AWS 검증은 도메인 초기화 블록에 한정했다. Terraform Plan·Apply, 이미지 빌드·게시와 실제 컨테이너 교체는 이번 검증 범위에서 실행하지 않았다. `.github/workflows/cutover-deploy-check.yml`은 배포 경로 변경이 있는 PR과 main push에서 셸 문법과 배포 테스트를 검사한다. 사용자 커밋·PR·main 병합 요청으로 게시를 진행하며, 커밋과 원격 CI 결과는 Git 및 GitHub 실행 이력을 따른다.

## 운영

현재 운영 설정은 SSM을 정본으로 삼는다. 새 도메인과 DNS 존으로 변경할 때는 운영자가 해당 파라미터와 SecureString 토큰을 함께 명시적으로 갱신한 뒤 오케스트레이터가 두 값을 읽는지 확인한다. 기존 파라미터를 보존하는 동작은 `PutParameter`의 덮어쓰기 금지 요청으로 AWS가 처리한다. [AWS PutParameter API](https://docs.aws.amazon.com/systems-manager/latest/APIReference/API_PutParameter.html)
