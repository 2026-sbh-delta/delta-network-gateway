# 앱 소스를 바꾸지 않는 자동 계측

플랫폼이 배포할 때 OTel 라이브러리와 시작 옵션을 연결한다. 고객의 소스·Dockerfile·앱 이미지 digest는 유지한다. Collector만 설치하거나 endpoint 환경변수만 넣어서는 trace가 생기지 않는다. 앱 프로세스 안에서 자동 계측 라이브러리가 먼저 실행되어야 한다.

현재 지원·검증 대상은 **Node.js 24의 HTTP FE→BE 요청**이다. Python·Java·.NET, DB 쿼리, 브라우저 내부 동작은 이번 지원 범위에 포함하지 않는다. 플랫폼 웹의 배포 버튼·AWS/GCP 실행기에는 아직 연결하지 않았으며 아래 배포 도구에서 선택한다.

```text
Jenkins가 만든 앱 이미지 ────────────────────┐
플랫폼의 고정 OTel 이미지 → 계측 파일 볼륨 ──┤
                                            ↓
                       FE → BE (동일 trace ID)
                            ↓
                     사설 OTel Collector
                            ↓
                     중앙 저장·플랫폼 조회
```

## 수집하는 것

| 신호 | 이번 구성 |
|---|---|
| Trace | FE의 수신·전달과 BE의 수신을 연결 |
| HTTP 지표 | FE의 요청 시간 histogram: 요청 수·오류율·지연 집계에 사용. BE 지표는 중복 합산을 피하려고 끔 |
| 로그 | 기존 stdout 수집 경로 사용. 이 라이브러리의 로그 exporter는 끔 |
| CPU·메모리·디스크 | 기존 호스트 Collector 담당 |

FE 지표에는 헬스 체크도 포함될 수 있다. 대시보드에서 사용자 요청만 표시하려면 필터 기준을 별도로 맞춰야 한다. 자동 계측은 업무 처리 성공 여부나 모든 라이브러리를 자동으로 이해하는 기능이 아니다.

## 온프레미스 k3s

완료된 샌드박스 결과로 후보 YAML을 만든다. 보안 검사가 미완료라면 `--preview`를 유지한다.

```bash
uv run --project platform/deploy python platform/deploy/onprem/render-release.py \
  --result '<샌드박스 결과.json>' --output '<후보.yaml>' --preview \
  --instrumentation nodejs \
  --collector-endpoint http://10.40.0.10:4318
```

처음 준비할 때는 `prepare-runtime.py`에도 같은 두 옵션을 전달할 수 있다. 기존 DB 연결이 있는 배포는 초기 준비 도구가 거부하므로 저장된 운영 YAML을 검토해 갱신한다. 계측 변경 때문에 DB 연결을 제거하지 않는다.

- init container가 digest로 고정한 공식 계측 파일을 전용 볼륨에 복사한다. 앱은 이를 읽기 전용으로 마운트한다.
- `NODE_OPTIONS`로 CommonJS 등록과 ESM loader를 연결한다. 앱 명령과 이미지, 비root 실행 설정은 유지한다.
- Collector의 사설 주소와 TCP4318만 허용한다. Kubernetes Service 주소를 쓰면 해당 Pod에 `app.kubernetes.io/name=<Service 이름>` 라벨이 있어야 한다.
- 기존 `OTEL_*` 설정·계측 옵션·숨겨진 환경변수 주입은 중복 계측을 피하려고 거부한다. 앱 자체에 SDK가 포함됐는지도 운영자가 확인한다.
- 끌 때는 동일 결과에서 `--instrumentation off`로 다시 생성·적용한다. 계측 옵션·볼륨과 Collector 전용 egress 허용을 제거한다. 다른 NetworkPolicy가 허용한 통신까지 취소하는 것은 아니다.

## Docker VM

VM에서도 같은 프로필을 사용할 수 있다. 아래는 Compose 생성 도구이며 AWS/GCP VM에 자동 배포하는 기능은 아니다.

```bash
# 배포 VM에서 고정 계측 파일을 새 디렉터리에 준비한다.
python3 platform/deploy/instrumentation/prepare-agent.py --output /opt/delta/otel-agent-v1

uv run --project platform/deploy python platform/deploy/instrumentation/render-compose.py \
  --input compose.yaml --output compose.instrumented.yaml --service frontend \
  --agent-path /opt/delta/otel-agent-v1 --commit '<소스 전체 커밋 SHA>' \
  --collector-endpoint http://10.40.0.10:4318
```

기존 Compose는 `image`와 명시적인 `environment` 매핑을 사용해야 한다. `build`·`env_file`은 받지 않는다. `agent-path`는 실제 Docker 호스트의 경로다. 컨테이너의 `127.0.0.1`은 호스트가 아니므로 컨테이너에서 도달 가능한 Collector 주소를 사용한다. BE에도 적용하려면 결과를 다음 입력으로 사용하고 `--service backend --traces-only`를 지정한다. 원복은 원래 Compose로 재배포한다.

## 검증과 중앙 연결

`verify-node.py`는 수정하지 않은 샘플 앱을 실행해 FE→BE trace의 부모 관계, DELTA HTTP histogram, OFF 시 전송 없음, Collector 장애 시 앱 응답 유지를 검사한다. CI는 샘플의 고정 커밋과 같은 고정 계측 이미지를 사용한다.

`collector-preview.yaml`은 **온프레미스 수신 확인용 임시 Collector**다. 외부 노출·클라우드 자격 증명·지속 저장소 없이 요청 속성을 제한해 로그로 확인한다. 검증 후 앱 계측을 끄고 이 파일의 자원만 삭제한다. 운영용 중앙 Collector를 대체하지 않는다.

중앙 전송에는 기존 [온프레미스 Collector](../../observability/onprem/README.md)의 환경값·인증서·사설 경로가 필요하다. 로컬 수신 성공을 CloudWatch/X-Ray 저장이나 플랫폼 대시보드 연결 성공으로 표시하지 않는다.

현재 전체 trace 수집은 소규모 해커톤용이다. 실제 대규모 운영에서는 샘플링·민감정보 제거·대기열/저장 비용을 조정하고, 계측을 켠 구성의 부하와 장애를 검증한다. 이번 샌드박스 검사는 원본 앱 기준이며 계측 주입 자체는 별도의 후보 환경에서 검증했다.
