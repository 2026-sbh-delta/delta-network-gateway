# 플랫폼 서비스 배포

인프라([terraform/platform-aws](../../terraform/platform-aws/README.md))는 그대로 두고, 서비스 하나의 이미지만 바꾼다.

```bash
AWS_PROFILE=sb deploy/platform/deploy.sh sast dast     # 빌드 → 서비스 전용 ECR → SSM으로 그 인스턴스만 교체
TAG=<이전 태그> AWS_PROFILE=sb deploy/platform/deploy.sh dast   # 빌드 없이 롤백
```

서비스 도메인 전환 설정은 SSM `orchestrator/cutover_config`에 저장된 운영 값을 사용한다. 배포 스크립트는 해당 파라미터가 없을 때만 `terraform/service-domain` 출력으로 초기화한다. 기존 설정과 동시 배포에서 먼저 생성된 설정을 보존하며, 권한 오류 등 다른 AWS 오류는 배포를 중단한다. Terraform 출력이 달라졌어도 기존 SSM 값을 갱신하려면 운영자가 명시적으로 파라미터를 변경한다. 해당 존의 Cloudflare 토큰은 `orchestrator/cloudflare_token` SecureString에서 읽는다.

배포 진입점과 SSM 결과 판정의 회귀 검증은 `python3 -m pytest -q deploy/platform/tests`로 실행한다. [도메인 설정 보존 검증](../../docs/ai-dlc/cutover-config-preservation.md)에 실제 실행 범위를 기록한다.

- **이미지 태그:** 커밋 SHA를 쓴다. 커밋하지 않은 변경이 있으면 `-dirty-<시각>`을 붙인다. 서버에는 digest로 배포한다.
- **비밀값:** 처음 실행할 때 `/delta-cp/` 아래에 없으면 만든다. 값은 출력하지 않는다.
  - `shared/sast_token`: SAST API 토큰
  - `dast/admin_password`: DAST 작업실 비밀번호
  - `dast/session_secret`: 세션 서명 키
- **LLM 인증:** 서버는 아래 순서대로 찾는다.
  1. `anthropic_federation_rule_id`, `…_organization_id`, `…_service_account_id`, `…_workspace_id`가 있으면 OIDC를 쓴다(키 없음). 서비스 경로(`sast/` 등)를 먼저 보고, 없으면 `shared/`를 본다. 인스턴스 역할로 공용 LLM 역할(`delta-cp-llm`)을 위임받아 그 이름으로 신원 토큰을 받으므로, Anthropic 규칙은 `delta-cp-llm` ARN 하나면 된다.
  2. 없으면 `shared/anthropic_api_key`(SecureString)를 쓴다.
  3. 둘 다 없으면 LLM 없이 동작한다.
- **모델:** `sast/llm_model`, `dast/llm_model`로 바꾼다.

DAST 작업실 비밀번호는 다음 명령으로 확인한다.

```bash
AWS_PROFILE=sb aws ssm get-parameter --region ap-northeast-2 --name /delta-cp/dast/admin_password --with-decryption --query Parameter.Value --output text
```
