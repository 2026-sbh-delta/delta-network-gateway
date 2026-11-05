# 플랫폼에서 온프레미스 앱 삭제

개요 → 온프레미스 환경 카드 → **실행 앱 삭제**에서 대상 이름을 확인하고 서비스 이름을 입력한다. Worker가 앱 종료를 확인한 뒤 삭제 완료를 표시한다. 새로고침해도 진행 상태가 유지되며, 일부만 제거되거나 연결이 끊기면 실패 내용과 재시도를 표시한다.

| 제거 | 보존 |
|---|---|
| 등록된 앱 Deployment와 그 하위 Pod·ReplicaSet, 전용 Service | DB·PVC·VM·k3s·namespace·Secret·네트워크 정책, 다른 앱, 플랫폼 등록·이력 |

앱 메모리에만 저장한 데이터는 종료와 함께 사라진다. DB 보존이 메모리 데이터 보존을 의미하지 않는다. 플랫폼 목록의 **프로젝트 삭제**는 기존과 같이 등록·검사 이력만 삭제하며 실행 자원을 제거하지 않는다. 앱 제거 후에도 환경·이력은 남으므로 프로젝트 기록 삭제가 자동으로 허용되는 것은 아니다.

첫 지원 대상은 플랫폼이 배포한 단일 컨테이너 FE·BE 앱이다. PVC 또는 임시 PVC를 직접 마운트한 앱, 여러 컨테이너가 함께 실행되는 앱은 삭제 전에 운영자 검토가 필요하므로 거부한다. DB는 앱과 별도 자원으로 유지한다.

## 운영자가 삭제 대상을 연결하는 방법

삭제 대상은 사용자가 입력하는 주소가 아니라 배포 시 운영자가 등록한 자원 UID다. Kubernetes의 `app.kubernetes.io/managed-by=delta-platform` 라벨만으로 삭제하지 않는다. 별도의 서비스 ID·환경 ID 소유 라벨과 UID가 모두 일치해야 한다. 같은 이름으로 재생성되거나 소유 관계가 달라졌다면 삭제를 중단한다.

1. 환경 ID를 확인한다. 새 환경이면 UUID를 먼저 정한다. 기본 입력 JSON은 기존 `EnvironmentInput` 형태(`name`, `cloud=onprem`, `region`, `version`, `instances`)를 사용한다. 앱 이미지 digest는 실제 배포 결과와 맞춘다.
2. 아래 명령으로 기존 앱의 소유 관계와 삭제 전용 권한을 준비한다. **앱을 삭제하거나 재시작하지 않는다.** 지정한 namespace와 자원이 플랫폼 관리 대상인지 확인하고, Deployment 이미지 및 Service의 연결 대상도 검사한다.

```bash
python3 platform/deploy/onprem/prepare-removal.py \
  --host '<사용자>@<호스트>' --key ~/.ssh/teammate_vm \
  --node-ip '<온프레미스 사설 IP>' --namespace delta-notes \
  --service-id '<플랫폼 서비스 UUID>' --environment-id '<환경 UUID>' \
  --binding-input '<기본 환경 입력.json>' \
  --deployment 'frontend=<FE 이미지>@sha256:<digest>' \
  --deployment 'backend=<BE 이미지>@sha256:<digest>' \
  --service frontend --service backend --output '<새 비공개 디렉터리>'
```

3. 새 플랫폼 코드가 배포된 실행 환경에서 출력된 `binding.json`을 **같은 환경 ID**로 등록한다. 기존 환경을 연결할 때는 기존 환경 이름도 일치해야 한다.

```bash
platform-environment --service-id '<플랫폼 서비스 UUID>' \
  --environment-id '<환경 UUID>' --input '<출력 디렉터리>/binding.json'
```

4. 플랫폼 API·Worker의 기존 비밀 설정을 보존하면서 다음 두 값만 추가한다. 토큰을 채팅·Git·명령 출력에 넣지 않는다.

| 설정 | 값 |
|---|---|
| `PLATFORM_ONPREM_RUNTIME_TARGET` | 출력 `target.json`의 JSON 문자열 |
| `PLATFORM_ONPREM_RUNTIME_TOKEN` | 출력 `runtime.token` |

Worker→온프레미스 TCP6443의 왕복 경로와 서버 CA 검증이 필요하다. Tailscale SSH를 통한 관리 검증은 AWS의 사설 경로 검증과 다르다. 설정이 없으면 삭제 버튼은 비활성화된다. 설정 후 연결이 끊긴 경우에는 실제 삭제가 실패했다고 표시하며 성공으로 처리하지 않는다.

이 준비 도구가 발급한 토큰은 해당 환경의 지정 Deployment·Service 이름에 대한 `get/delete`만 갖는다. 여러 고객의 자격 증명을 선택하는 구조는 별도 확장 대상이며, 현재 단일 실행기 설정은 한 앱 묶음을 기준으로 준비한다. 모든 namespace를 삭제할 수 있는 관리자 토큰을 넣지 않는다. 토큰은 7일을 요청하므로 실제 만료 전에 교체하고, 폐기 시 전용 ServiceAccount를 삭제·재발급한다.

## 실패·동시 작업·복구

- 진행 중인 작업이나 승인/실행 중인 배포·이전 계획이 있으면 삭제를 거부한다.
- 삭제 접수부터 완료까지 환경 재연결과 새 실행을 막는다. 실패한 부분 삭제도 운영 자원 상태가 불명확하므로 재시도로 정리한 뒤 진행한다.
- Worker가 중단되면 lease를 회수해 같은 UID의 삭제를 이어간다. 이미 없는 자원은 정리된 것으로 취급하지만 다른 UID의 새 자원은 삭제하지 않는다.
- 확인창을 연 뒤 대상이 달라지면 다시 확인해야 한다. 화면이 자동 갱신됐다는 이유로 승인 대상까지 바꾸지 않는다.
- 삭제된 환경은 새 배포에 재사용하지 않고 새 환경으로 등록한다. 앱 복구는 보관한 이미지·배포 설정으로 재배포하며 기존 DB/PVC를 보존한다.
- 앱 Deployment·Service 외에 남겨 둔 전용 계정·정책·Secret의 수명 관리는 운영자 작업이다. 현재 도구는 스토리지를 포함한 전체 환경 철거 기능이 아니다.

## 배포 상태

2026-11-05 API·Worker·화면을 구현했고 로컬 테스트와 온프레미스 임시 앱 검증을 진행했다. 운영 플랫폼은 아직 릴리스하지 않았다. AWS 플랫폼에서 `10.40.0.10:6443`, `:32081` 연결은 시간 초과였다. 팀의 VPN/TGW 연결 후 실제 플랫폼에서 활성화해야 한다. 기존 운영 후보 앱과 DB/PVC는 삭제하지 않았다. 상세 결과는 [구현 기록](../../../docs/ai-dlc/platform-onprem-removal.md)을 따른다.
