# 온프레미스 Collector 연결

기존 대시보드와 CloudWatch/X-Ray를 사용한다. 실제 온프레미스 VM에 Collector 파일을 설치하고 호스트 지표 생성까지 확인했다. 서비스는 중지 상태이며 VPN·AWS gateway 연결과 중앙 저장 검증은 남아 있다. 실제 주소·인증서·개인 키는 `platform/.runtime/` 또는 서버의 제한된 설정 경로에만 둔다. [설치·운영 절차](../../deploy/onprem/README.md)

## 연결

```text
앱 OTel → app Collector → AWS gateway :4318 → CloudWatch / X-Ray → 플랫폼
VM hostmetrics → host Collector → AWS gateway :4319 → CloudWatch → 플랫폼
```

두 수신기는 서로 다른 클라이언트 CA를 신뢰한다. 앱 Collector 인증서로 호스트 지표 수신기에 접근할 수 없다. gateway는 앱 경로의 호스트 지표를 버리고 서비스·환경·호스트 ID를 고정값으로 덮어쓴다. 앱이 다른 환경이나 VM 상태를 가장하지 못하도록 하는 경계다. VM 자체를 장악한 공격에 대한 보호를 보장하지는 않는다.

- `app.yaml`: 앱 전용 OTLP 입력. 앱 network 또는 loopback에만 바인딩한다. 외부로 수집 포트를 공개하지 않는다.
- `host.yaml`: Linux VM의 CPU·메모리·루트 디스크를 60초마다 수집한다. 앱 입력을 받지 않는다. 네이티브 서비스이면 `HOST_ROOT=/`, 컨테이너이면 읽기 전용 hostfs 마운트와 실제 경로를 확인한다. 고객 컨테이너의 자원 값을 VM 전체 값으로 표시하지 않는다.
- `gateway-overlay.yaml`: 기존 `../collector.yaml` 다음에 읽는 추가 설정. AWS 수신기에서만 사용하며 해당 서비스·환경의 저장 권한을 가진 실행 역할이 필요하다. 수집 포트는 검증한 사설 경로와 방화벽 허용 출발지로 제한한다.

AWS gateway와 로컬 설정 검증은 고정 이미지 `otel/opentelemetry-collector-contrib:0.164.0@sha256:340c9ca6a36617084c6661bdc4f0fe92b7cda5a805d61673ec8db375fddc522b`를 사용한다. 온프레미스는 같은 버전의 공식 Linux amd64 바이너리를 SHA256 확인 후 전용 systemd 사용자로 실행한다.

## 설정값

| 위치 | 변수 | 값 |
|---|---|---|
| app Collector | `DELTA_APP_LISTEN` | 앱 전용 네트워크의 바인딩 주소:4318 |
| app Collector | `DELTA_APP_GATEWAY` | 인증서 SAN과 일치하는 사설 gateway HTTPS 주소:4318 |
| host Collector | `HOST_ROOT` | 네이티브 실행이면 `/` |
| host Collector | `DELTA_HOST_GATEWAY` | 인증서 SAN과 일치하는 사설 gateway HTTPS 주소:4319 |
| AWS gateway | `AWS_REGION` | `ap-northeast-2` |
| AWS gateway | `DELTA_PROJECT_ID`, `DELTA_SERVICE_ID`, `DELTA_ENVIRONMENT_ID` | 플랫폼에 등록한 UUID |
| AWS gateway | `DELTA_HOST_ID` | 등록한 `onprem-<UUID hex>` |

AWS gateway는 다음 두 설정을 순서대로 사용한다.

```bash
otelcol-contrib --config=collector.yaml --config=onprem/gateway-overlay.yaml
```

인증서 파일은 각 YAML의 `/etc/delta-otel/` 경로를 따른다. 서버 인증서는 실제 접속 주소의 SAN, 서버/클라이언트별 용도와 만료를 검증한다. 앱과 호스트의 개인 키를 서로 공유하지 않는다. CA 개인 키는 Collector 서버에 배포하지 않는다. 인증서 교체·검증 후 이전 것을 철회하며 TLS 검증을 끄지 않는다.

온프레미스에는 AWS Access Key를 두지 않는다. AWS gateway의 IAM은 해당 환경 로그 그룹/stream 쓰기·CloudWatch OTLP·X-Ray 쓰기만 허용한다. 업무 DB·배포·관리 권한은 필요하지 않다. 실제 IAM·수신기·VPN 적용은 환경 확인 후 수행한다.

## 검증

```bash
python3 platform/deploy/onprem/verify-observability.py
```

고정 Collector 이미지의 세 설정을 검증하고, localhost 임시 gateway에서 익명/다른 클라이언트 인증서 거부, 고정 식별자 적용, 앱 경로의 호스트 지표 제거를 확인한다. 임시 인증서·컨테이너는 종료 후 삭제한다. AWS에 데이터를 쓰지 않으므로 실제 AWS 저장 성공이나 온프레미스 연결 검증과 구분한다.

실제 설치 후에는 VM 자원 값과 대시보드 대조, Collector 중지 후 누락·수집 지연 표시, 앱 요청/로그/trace, 기존 앱/DB 보존과 재시작을 추가 확인한다.
