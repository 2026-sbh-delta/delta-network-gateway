# 운영 SAST 연결

플랫폼 Worker가 기존 도구 호스트의 SAST를 사설 HTTPS로 호출한다. 고객의 서비스 요청을 이 경로로 보내는 구성은 아니다.

```text
플랫폼 웹 → API/Worker → VPC peering → 도구 VM의 HTTPS → 기존 SAST(127.0.0.1:8790)
                 └─ 빌드 커밋·이미지 digest와 검사 결과·SARIF 해시 연결
```

서버를 추가하지 않는다. `network/`는 기존 두 VM의 `/32` 왕복 경로와 플랫폼 SG의 443 수신만 관리한다. `aws/install.py`는 도구 VM의 사설 IP에 NGINX HTTPS를 구성하고 SAST 접수·상태·SARIF 경로만 전달한다. DAST 포트와 컨테이너는 그대로 둔다.

## 적용 순서

1. [네트워크 root](network/README.md)의 Plan을 검토·적용하고 `terraform output -json`을 ignored 파일에 저장한다.
2. 도구 호스트에 HTTPS를 설치한다. SAST에 실행 중인 검사가 있으면 저장소 정책 변경을 중단한다. 정책이 바뀔 때 SAST만 같은 이미지·기존 볼륨·모델 설정으로 다시 띄우며, 기동 실패 시 이전 컨테이너로 복구한다.

```bash
uv run --project platform/deploy python platform/deploy/sast/aws/connect.py install --profile delta-platform --outputs platform/.runtime/sast-network-outputs.json
uv run --project platform/deploy python platform/deploy/sast/aws/connect.py stage --profile delta-platform --outputs platform/.runtime/sast-network-outputs.json
```

`stage`는 URL·토큰·공개 CA를 `/delta-platform/sast/connection` SecureString에 준비한다. 실행 중인 플랫폼의 설정은 바꾸지 않는다. SAST의 기존 토큰을 재사용하므로 DAST 연결을 다시 설정할 필요가 없다. 초기 발급한 CA 개인키는 삭제하고 서버 개인키는 도구 VM의 root 전용 경로에만 보관한다.

3. **SAST CA 지원 코드가 main에 반영된 뒤** 다음 명령으로 기존 런타임에 SAST 설정만 병합하고 GitHub **Platform release**를 실행한다. 기존 ID/PW·DB·Jenkins·샌드박스 설정을 보존한다.

```bash
uv run --project platform/deploy python platform/deploy/sast/aws/connect.py activate --profile delta-platform --outputs platform/.runtime/sast-network-outputs.json
```

4. 공개 플랫폼의 성공한 이미지 빌드에서 **이 빌드의 코드 검사**를 실행한다. 같은 커밋·digest·검사 ID와 근거 해시가 연결됐는지 확인한다. 발견 사항 0건도 전체 보안 통과나 배포 승인으로 표시하지 않는다.

## 저장소 정책과 운영

- SAST 허용 조직은 기존 `2026-sbh-delta`와 공개 검증 저장소의 `docker`다. 공개 저장소만 받는 플랫폼 정책과 검사 서버의 허용 목록은 별개다. 추가 조직이 필요하면 목록을 검토해 변경한다.
- `/opt/delta/sast-runtime.env`에 저장소 정책을 보존한다. 갱신된 `deploy/tooling/remote.sh`도 이 파일을 읽으므로 도구 재배포 후 유지된다.
- 기존 SAST/DAST 모델 인증과 로컬 검사 경로·상태 볼륨은 유지한다. 이번 작업은 LLM 설정을 바꾸지 않는다.
- 서버 인증서는 30일, CA는 90일이다. 자동 갱신은 아직 없다. 만료 전에 검사를 비우고 인증서·플랫폼 CA 설정을 함께 교체한다. 기존 실행의 CA가 달라지면 새 대상으로 이어가지 않는다.
- 서버 로그에는 토큰이나 요청 본문을 기록하지 않는다. SSM 명령 결과를 조회할 수 있는 운영자는 연결 토큰도 조회할 수 있으므로 출력 원문을 공유하지 않는다.
- 플랫폼에서 연결을 해제하려면 런타임의 `PLATFORM_SAST_*` 세 값을 제거하고 호환 릴리스를 배포한다. 네트워크는 이 root의 Route/SG/peering만 제거한다. 도구 VM과 SAST·DAST 데이터는 삭제하지 않는다.

실제 적용·검증·릴리스 상태는 [진행 기록](../../../docs/ai-dlc/platform-security.md)을 따른다.
