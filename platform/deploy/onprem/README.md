# 온프레미스 실행 공간 준비

기존 k3s에 임시 `delta-platform-sandbox`와 운영 후보 `delta-notes`를 분리한다. Jenkins가 같은 커밋으로 빌드한 FE·BE digest를 실행하고 통신·실제 이미지를 확인한 뒤 임시 자원만 정리한다. 기존 `demo` 앱·DB, 홈 라우터와 VPN은 변경하지 않는다. 운영 서비스·DB 이전 완료를 의미하지 않는다.

2026-11-05 원본 샘플의 FE·BE 실행과 플랫폼의 Node.js 자동 계측을 온프레미스에서 확인했다. 현재 후보는 원본 앱으로 실행하며 중앙 연결 준비 전까지 계측은 꺼 두었다. DB는 중지하고 데이터 PVC는 보존했다. DAST는 사용자 지시에 따라 API 준비 전까지 제외한다. **AWS↔온프레미스 VPN/TGW는 팀에서 연결 중**이므로 AWS 플랫폼의 온프레미스 선택은 아직 활성화하지 않았다. [FE·BE 검증](../../../docs/ai-dlc/platform-onprem-release.md) · [자동 계측 검증](../../../docs/ai-dlc/platform-auto-instrumentation.md)

## 서버 확인과 준비

일반 SSH로 호스트 키를 먼저 확인한다. 실제 주소·키·결과 파일은 커밋하지 않는다.

```bash
python3 platform/deploy/onprem/preflight.py \
  --host '<user>@<host>' --key ~/.ssh/teammate_vm \
  --output platform/.runtime/onprem/preflight.json

uv run --project platform/deploy python platform/deploy/onprem/prepare.py \
  --host '<user>@<host>' --key ~/.ssh/teammate_vm \
  --source '<Worker 사설 IP>' --source '<DAST 사설 IP>' \
  --node-name '<k3s 노드 이름>' --node-ip '<온프레미스 사설 IP>' \
  --repository '<계정>.dkr.ecr.ap-northeast-2.amazonaws.com/<저장소>' \
  --output platform/.runtime/onprem/connection
```

- `preflight.py`는 OS·자원·경로·기존 Pod/서비스/PVC/정책만 읽는다. Secret·kubeconfig·앱 환경변수는 읽지 않는다.
- `prepare.py`는 amd64 노드·주소, namespace 소유권, 포트 충돌 확인 후 서버 dry-run/apply한다. 기존 VM 내부 Kubernetes 자원을 선언형 manifest로 관리하며 신규 VM·VPN·TGW Terraform은 추가하지 않는다.
- 출력은 `target.json`, `controller.token`, `scanner.token`, manifest·적용 결과다. 토큰 파일은0600이며 출력 디렉터리가 이미 있으면 중단한다.
- namespace에는 제한된 Role, 서비스 계정3개, Pod2개/CPU 1코어/메모리 512MiB/임시 저장소 512MiB 제한, PVC 금지, NodePort32080, NetworkPolicy를 둔다.
- Pod는 UID10001·읽기 전용 루트로 실행하고 `/tmp`64MiB만 쓴다. API 토큰·호스트 경로·운영 DB 인증 정보는 전달하지 않는다. 최대 실행 시간은15분이다.
- Worker·DAST의 등록된 출발지만 앱에 접근할 수 있고 나머지 연결은 기본 차단하며 FE→BE:8080과 FE→클러스터 DNS만 허용한다. 이미지 다운로드는 노드가 수행하므로 ECR/S3 HTTPS 접근은 별도로 필요하다.

한 호스트의 namespace는 전용 VM 수준의 격리가 아니다. 이번 구성은 팀의 승인된 해커톤 앱용이며, 불특정 고객의 임의 코드를 받는 운영 환경은 별도 실행 노드/VM이 필요하다. 이미지 레이어·노드 캐시는 Pod 저장소 제한으로 제한되지 않으므로 작은 이미지를 쓰고 호스트 디스크를 확인한다. 기존 이미지·공유 볼륨을 일괄 삭제하지 않는다.

## 네트워크 팀 전달 항목

실제 IP는 배포 시 비공개로 전달한다. Tailscale SSH는 관리용이며 AWS 사설 경로를 대체하지 않는다.

| 출발지 | 목적지 | TCP 포트 | 용도 |
|---|---|---|---|
| AWS Worker | 온프레미스 노드 사설 IP | 6443 | 제한된 Kubernetes API로 생성·조회·정리 |
| AWS DAST | 온프레미스 노드 사설 IP | 6443 | 읽기 전용으로 Pod UID·이미지 확인 |
| AWS Worker·DAST | 온프레미스 노드 사설 IP | 32080 | 임시 앱의 HTTP 확인·검사 |
| 온프레미스 Collector | 배포할 AWS 관측 gateway 사설 IP | 4318 / 4319 | 앱 / 호스트 지표 mTLS 전송 |
| 온프레미스 노드 | 승인된 ECR·S3 경로 | 443 | 이미지 다운로드 |

Worker VPC와 공용 DAST VPC 양쪽의 왕복 경로·방화벽을 확인한다. VPC peering이 TGW 연결을 자동 중계한다고 가정하지 않는다. NodePort는 인터넷에 공개하지 않고 VPN 안에서만 사용한다. API는 CA 검증 HTTPS, 관측 수신기는 mTLS를 사용한다.

## VPN 연결 후 활성화

1. Worker와 DAST 각각에서 API 연결을 확인한다. controller 토큰은 전용 namespace만, scanner는 읽기만 가능한지 확인한다. 임시 앱의32080 HTTP 응답과 미허용 출발지 차단도 확인한다.
2. 기존 플랫폼 비밀 설정의 로그인·DB·Jenkins·AWS/GCP 값을 보존하면서 아래 값만 병합한다. 토큰은 채팅·CI 로그·Git에 출력하지 않는다.

| 실행 위치 | 설정 | 값 |
|---|---|---|
| 플랫폼 API/Worker | `PLATFORM_ONPREM_SANDBOX_TARGET` | `target.json` 내용 |
| 플랫폼 API/Worker | `PLATFORM_ONPREM_SANDBOX_TOKEN` | `controller.token` 내용 |
| 팀 DAST | `DAST_ONPREM_TARGET` | 같은 `target.json` 내용 |
| 팀 DAST | `DAST_ONPREM_TOKEN` | `scanner.token` 내용 |

3. 진행 중인 검사가 없을 때 DAST와 플랫폼을 릴리스한다. DAST 기존 토큰·카탈로그·상태를 보존한다. 서버 관리자 kubeconfig나 SSH 개인 키는 플랫폼에 전달하지 않는다.
4. 공개 플랫폼에서 실행 → commit/digest/Pod UID → HTTP → DAST → 정리를 확인한다. 성공·실패·취소 후 Pod와 pull Secret이 모두 없어야 한다. 정리를 확인하지 못하면 실행 잠금을 유지한다.

토큰은 TokenRequest로7일을 요청한다. 실제 만료를 확인하고 만료 전에 제한된 저장소에서 교체한다. 2026-11-04 발급분은2026-11-11 11:35 UTC경 만료한다. 만료 없는 관리자 토큰으로 대체하지 않는다. 즉시 철회하려면 해당 전용 ServiceAccount 삭제·재생성 후 새 UID의 토큰을 발급하고 두 실행기를 갱신한다.

## 모니터링 준비

[Collector 설정](../../observability/onprem/README.md)을 사용한다. CPU·메모리·루트 디스크는60초마다 수집한다. 앱 요청 수·5xx·p95·로그·trace는 기존 대시보드와 연결한다. 원본은 CloudWatch/X-Ray, 업무 DB에는 환경 식별자만 둔다. 조회는 기본15분·최대2시간, 5분 이상 지난 값은 수집 지연으로 표시한다.

`install-collector.sh`는 VM에서 root로 실행한다. 먼저 `host.yaml`, `app.yaml`을 각각 `/tmp/delta-onprem-host-collector.yaml`, `/tmp/delta-onprem-app-collector.yaml`로 복사한다. SHA256을 확인한 Collector0.164.0, 전용 사용자와 systemd 서비스만 설치하며 시작하지 않는다.

- 설정: `/etc/delta-onprem/otel/{host,app}.yaml`
- 환경값: `/etc/delta-onprem/otel/{host,app}.env` — root 소유0600, 변수는 Collector 안내를 따른다.
- 인증서·개인 키: YAML의 `/etc/delta-otel/` 경로 — `delta-otel`이 필요한 키만 읽도록 한다. CA 개인 키는 배포하지 않는다.
- 시작 전에 gateway 주소·인증서 SAN·별도 앱/호스트 client CA·IAM·사설 경로를 검증한다. 온프레미스에는 AWS 장기 키를 두지 않는다.
- 검증 후 `delta-otel-host`, `delta-otel-app`을 enable/start한다. 지금은 중지·비활성 상태이며 실제 AWS 저장 성공은 아직 확인하지 않았다.

앱이 호스트 지표를 위조하지 못하도록 별도 인증서·수신기를 사용한다. 샌드박스와 운영 앱은 환경 ID를 분리한다. 네트워크 차단 중인 샌드박스에 관측을 이유로 광범위한 외부 접근을 허용하지 않는다.

## 되돌리기

새 실행을 막고 진행 중인 실행을 정리한 뒤 플랫폼의 온프레미스 설정만 제거한다. 전용 namespace에 다른 사용자의 자원이 없는지 확인하고 삭제한다. 기존 `demo`나 공유 스토리지는 삭제하지 않는다. Collector만 중지하려면 `systemctl disable --now delta-otel-host delta-otel-app`을 사용한다. 서버 전체 k3s·Tailscale·VPN은 중지하지 않는다.

## FE·BE 발표 구성

새 배포 묶음 API, YAML 관리, 정책 승인, 운영 후보, DB 복원과 접속 방법은 [실행 안내](RELEASE.md)를 따른다. 기존 단일 이미지 API도 유지한다.
