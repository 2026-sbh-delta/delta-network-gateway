# 플랫폼 DAST 운영 연결

현재는 구현·로컬 검증 완료 단계다. 운영 도구 이미지 갱신, AWS 네트워크 Apply, 플랫폼 런타임 활성화와 공개 웹의 실제 DAST는 아직 하지 않았다. 사용자 main 병합 후 아래 순서로 진행한다.

```text
플랫폼 API/Worker ─사설 HTTPS→ 기존 도구 VM의 DAST
       │                           │
       └─ ECS API로 앱 실행         └─ peering → 내부 ALB:80 → 검사 앱
```

새 VM·LB를 만들지 않는다. 공용 도구 VM과 플랫폼 전용 ECS 풀을 재사용한다. 기존 팀 스테이징·모델 인증·SAST/DAST 데이터는 보존한다. 검사 대상은 테스트 데이터와 운영 권한이 없는 샌드박스다.

## 연결 순서

1. 실행 중인 플랫폼 샌드박스·DAST 검사를 비운다. 새 요청도 잠시 막고, 도구 VM이 실행 중인지 확인한다.
2. [기존 사설 연결 root](../sast/network/README.md)의 ignored tfvars에 `dast_sandbox`를 추가한다. 기존 플랫폼 내부 ALB ARN과 ALB 두 서브넷 → Route Table 매핑을 넣는다. 저장 Plan이 ALB 방향 Route와 SG만 추가하는지 검토 후 적용한다. VM·ALB·ECS를 교체하는 Plan은 적용하지 않는다.
3. root의 `dast_sandbox_url`과 플랫폼 `PLATFORM_SANDBOX_TARGET.url`이 같은지 확인한다. `configure.py`를 SSM으로 도구 VM에 전달하고 root로 실행한다.

```bash
python3 /opt/delta/platform-dast-configure.py --sandbox-url 'http://internal-delta-platform-....ap-northeast-2.elb.amazonaws.com'
```

이 스크립트는 기존 `/api/runs`에 실행 중인 검사가 있으면 중단한다. `/opt/delta/dast-platform.env`에 별도 토큰을 0600으로 저장하고 `/opt/delta/platform-sandbox.json`에 고정 ALB 대상만 등록한다. 재실행해도 토큰은 유지한다. 컨테이너 자체는 재시작하지 않는다.

4. main의 기존 도구 배포 절차로 새 DAST 이미지를 배포한다. `deploy/tooling/remote.sh`가 위 환경 파일과 대상 파일을 읽고 기존 상태·모델 인증 마운트를 유지한다. 이 스크립트는 SAST도 재시작하므로 두 검사 서비스 모두 비운 상태에서 실행한다. 복구용으로 이전 이미지 digest를 기록해 둔다.
5. 갱신된 HTTPS installer를 설치한다. SAST 경로를 유지하고 DAST 접수·조회·취소 경로만 추가한다. DAST 자체의 웹 화면이나 임의 제어 API는 공개하지 않는다.

```bash
uv run --project platform/deploy python platform/deploy/sast/aws/connect.py install --profile delta-platform --outputs platform/.runtime/sast-network-outputs.json
uv run --project platform/deploy python platform/deploy/sast/aws/connect.py stage --service DAST --profile delta-platform --outputs platform/.runtime/sast-network-outputs.json
```

`stage`는 실행 중인 DAST의 등록 대상·토큰을 확인하고 `/delta-platform/dast/connection` SecureString에 저장한다. 토큰을 터미널·채팅에 출력하지 않는다. 플랫폼 설정은 아직 바꾸지 않는다.

6. 플랫폼 코드가 main에 있고 검사 풀이 비어 있을 때 활성화한다.

```bash
uv run --project platform/deploy python platform/deploy/sast/aws/connect.py activate --service DAST --profile delta-platform --outputs platform/.runtime/sast-network-outputs.json
```

기존 `/delta-platform/runtime`의 ID/PW·DB·Jenkins·SAST를 보존하고 `PLATFORM_DAST_URL`, `PLATFORM_DAST_TOKEN`, `PLATFORM_DAST_CA_BASE64`만 추가한다. 등록된 DAST 대상과 플랫폼 샌드박스 URL이 다르면 거부한다. 이어 GitHub **Platform release**로 DB `0005`와 앱을 배포한다.

## 실제 검증과 복구

- 공개 웹에서 성공한 빌드 → 같은 빌드의 SAST → DAST를 실행한다. 정확한 이미지·커밋·SAST 근거·실행 환경을 확인한다.
- 지원하는 XSS가 있는 통제된 테스트 앱으로 브라우저 재현을 확인한다. 후보가 없는 일반 앱의 `미실행`은 실제 DAST 성공 증거로 사용하지 않는다.
- 완료·오류·취소 후 원격 검사가 끝났고 ECS desired/running/pending count가 모두 0인지 확인한다. 검사 서버 단절 시 `dast_cleanup_wait`로 풀이 잠기는지도 확인한다.
- 잠긴 실행은 연결·기존 설정을 복원하면 Worker가 정리를 재시도한다. 원격 브라우저가 살아 있는 동안 DB 상태를 수동으로 완료 처리하거나 풀을 재사용하지 않는다.
- 비활성화 전 실행을 모두 정리한다. DAST 런타임 세 값만 제거하되 DB `0005` 호환 앱을 유지한다. DAST 기록이 있으면 자동 DB downgrade를 거부한다. 이전 도구 이미지 복구 시에도 `dast-runs`·`dast-data`와 `.platform` 취소 기록을 삭제하지 않는다.
- HTTPS 인증서는 기존 SAST와 공유하므로 [인증서 운영 조건](../sast/README.md)을 따른다. DAST는 단일 프로세스·단일 Worker로 실행하며 여러 Uvicorn Worker에 같은 기록 디렉터리를 공유하는 구성은 지원하지 않는다.

GCP·온프레미스에서는 각 환경의 실행기를 연결한 뒤 별도 검사한다. 현재 이 문서의 실제 연결 대상은 AWS ECS다.
