# 보안 도구(SAST·DAST) 배포

SAST·DAST를 보안 도구 호스트(EC2 1대)에 상시 서비스로 올린다. 인프라는 [terraform/tooling-aws](../../terraform/tooling-aws/README.md)가 만든다.

```text
노트북 ── deploy.sh ──▶ 이미지 빌드(arm64 단일 매니페스트) → ECR
                    └─▶ SSM send-command ──▶ 서버 remote.sh: 이미지 받기 → 컨테이너 재시작 → 상태 확인

서버(호스트 네트워크, 모두 127.0.0.1에만 열림)
  delta-sast  :8790   SAST API
  delta-dast  :8765   작업실(관리 화면) · :8766 실습 게시판

노트북 ── SSM 포트 포워딩 ──▶ 서버 127.0.0.1:8765 → 브라우저 http://127.0.0.1:8765
```

## 처음 한 번

1. 노트북에 Session Manager 플러그인을 설치한다.

   ```bash
   brew install --cask session-manager-plugin
   ```

2. 호스트를 만든다. 저장된 Plan을 적용한다.

   ```bash
   AWS_PROFILE=sb terraform -chdir=terraform/tooling-aws apply tooling.tfplan
   ```

3. 2~3분 뒤 SSM에 등록됐는지 확인한다(`Online`이면 준비 완료).

   ```bash
   AWS_PROFILE=sb aws ssm describe-instance-information --query 'InstanceInformationList[].[InstanceId,PingStatus]' --output table
   ```

4. (선택) LLM 인증. 서버는 API 키 없이 OIDC(워크로드 아이덴티티 페더레이션)로 인증한다.
   - 준비: AWS 계정의 아웃바운드 웹 아이덴티티 페더레이션 켜기, Anthropic 콘솔에 발급자·규칙·서비스 계정 등록.
   - Parameter Store(String)에 ID 네 개를 넣는다: `anthropic_federation_rule_id`, `anthropic_organization_id`, `anthropic_service_account_id`, `anthropic_workspace_id`(`/delta/tooling/` 아래). 식별자라 비밀값이 아니다.
   - 배포하면 서버가 AWS 신원 토큰(15분)을 5분마다 `/opt/delta/wif/token`에 새로 받고(`delta-wif-refresh.timer`), SAST는 SDK 환경 변수, DAST 에이전트는 같은 값의 페더레이션 프로필로 단기 토큰을 받아 호출한다.
   - DAST는 항목마다 에이전트를 따로 띄워 같은 토큰을 여러 번 교환하므로, Anthropic 발급자 설정에서 일회용 토큰(JTI) 검사를 끈다.
   - 모델은 `sast_llm_model`, `dast_llm_model`(String)로 바꾼다. 없으면 각 서비스 기본값(Sonnet)을 쓴다.
   - ID가 없으면 `/delta/tooling/anthropic_api_key`(SecureString)를 API 키로 쓰고, 그것도 없으면 SAST는 규칙·캐시까지만, DAST는 모델이 필요 없는 수동 검토 계획만 쓴다.

## 배포

```bash
AWS_PROFILE=sb deploy/tooling/deploy.sh
```

- 이미지 태그는 커밋 SHA다. 커밋하지 않은 변경이 있으면 `-dirty-<시각>`이 붙는다(저장소가 태그 덮어쓰기를 막는다).
- 끝나면 서버 결과(`ok: …`, 컨테이너 목록)와 작업실 접속 명령을 출력한다.
- 플랫폼용 사설 HTTPS 연결을 추가한 경우 `/opt/delta/sast-runtime.env`의 저장소 정책을 SAST 재배포에도 유지한다. [플랫폼 연결 안내](../../platform/deploy/sast/README.md)를 따른다.
- SAST·DAST 내부 토큰은 서버에서 처음 한 번 만들어 `/opt/delta/secrets.env`(root 전용)에 둔다. 이미지와 저장소에는 비밀값이 없다.

## 작업실 열기

```bash
AWS_PROFILE=sb aws ssm start-session --region ap-northeast-2 --target <instance_id> --document-name AWS-StartPortForwardingSession --parameters 'portNumber=["8765"],localPortNumber=["8765"]'
```

이 명령을 켜 둔 채로 브라우저에서 `http://127.0.0.1:8765`를 연다. 명령 전체는 `terraform -chdir=terraform/tooling-aws output port_forward_command`로도 볼 수 있다.

## 업데이트·롤백

| 할 일 | 명령 |
|---|---|
| 새 코드 배포 | `AWS_PROFILE=sb deploy/tooling/deploy.sh` |
| 이전 이미지로 롤백 | `AWS_PROFILE=sb TAG=<이전 태그> deploy/tooling/deploy.sh` (빌드 생략) |
| 사용 가능한 태그 보기 | `AWS_PROFILE=sb aws ecr list-images --repository-name delta-tooling/dast --query 'imageIds[].imageTag'` |

## 로그·상태 확인

서버에 들어가지 않고 SSM으로 확인한다.

```bash
AWS_PROFILE=sb aws ssm start-session --target <instance_id>
sudo docker ps
sudo docker logs --tail 100 delta-dast
sudo docker logs --tail 100 delta-sast
```

## 서버 디렉터리

| 경로 | 내용 |
|---|---|
| `/opt/delta/sast-state` | SAST 점검 상태·SARIF·규칙 캐시 |
| `/opt/delta/dast-runs` | DAST 점검 증거(스크린샷, report.json·html·pdf) |
| `/opt/delta/dast-data` | DAST 프로젝트 데이터 |
| `/opt/delta/sources` | SAST가 점검할 소스(지금은 실습 게시판과 같은 코드인 sample-target) |
| `/opt/delta/secrets.env`, `/opt/delta/llm.env` | 내부 토큰, LLM 인증 설정(root 전용) |
| `/opt/delta/wif/` | AWS 신원 토큰(컨테이너 사용자만 읽기), DAST 에이전트용 페더레이션 프로필 |
