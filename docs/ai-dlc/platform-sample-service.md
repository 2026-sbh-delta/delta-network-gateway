# 발표용 고객 서비스: Delta Notes

## 선택과 범위

2026-11-04 사용자가 [delta-sample-app](https://github.com/2026-sbh-delta/delta-sample-app)을 플랫폼에 등록할 테스트용 고객 서비스로 지정했다. 플랫폼 자체와 구분한다. 이번 변경은 등록값과 시연 절차를 기록하며, 실제 서비스 등록·빌드·배포 완료를 의미하지 않는다.

## 등록값

저장소 URL은 두 서비스 모두 `https://github.com/2026-sbh-delta/delta-sample-app`이다. 루트 Dockerfile은 없으며 프론트엔드와 백엔드를 각각 등록한다. 경로는 저장소 최상위 기준이다.

| 항목 | 백엔드 | 프론트엔드 |
| --- | --- | --- |
| 서비스 이름 | `delta-sample-backend` | `delta-sample-frontend` |
| 브랜치 | `main` | `main` |
| 빌드 경로 | `backend` | `frontend` |
| Dockerfile 경로 | `backend/Dockerfile` | `frontend/Dockerfile` |
| 컨테이너 포트 | `8080` | `3000` |
| 헬스 체크 | `/healthz` | `/healthz` |

확인한 main 커밋은 `df9e26b38ad47040176432b6f411de801c2ea9be`다. 재현 가능한 시연에서는 두 서비스를 같은 커밋으로 고정한다. 새 버전을 사용할 때도 두 서비스의 실제 소스 검사 결과를 확인한다.

## 시연 순서와 연결

1. 백엔드 소스를 확인하고 Jenkins에서 이미지를 빌드한다. 실행 후 `/healthz`와 `/api/health`를 확인한다.
2. 프론트엔드 소스를 확인하고 이미지를 빌드한다. 실행 환경에 `API_HOST=<백엔드 접근 주소>`, `API_PORT=8080`, `API_PROTOCOL=http`, `API_ENDPOINT=/api`를 설정한다. HTTPS 백엔드를 사용하면 프로토콜과 포트를 함께 맞춘다.
3. 프론트엔드의 `/healthz`로 프로세스 상태를, `/api/health`로 실제 백엔드 연결을 확인한다. 브라우저에서 메모 생성·조회·삭제를 확인한다.
4. SAST는 검사한 커밋과 빌드 이미지를 연결해 확인한다. DAST는 FE→BE 연결이 되는 실행 환경에서 수행하고, 실제 검사 여부와 결과를 구분해 발표한다. 취약점 발견이나 재현 성공은 미리 가정하지 않는다.

`API_HOST`는 FE 서버에서 접근할 수 있는 DNS/IP이며 프로토콜과 포트를 포함하지 않는다. FE 기본값 `127.0.0.1`은 별도 컨테이너의 BE를 가리키지 않는다. Compose의 `backend` 이름도 같은 Compose 네트워크 밖에서는 자동으로 해석되지 않는다. 등록 폼에는 환경변수 입력이 없으므로, 연결값 주입과 두 서비스의 통신 경로를 실행 환경에서 마련해야 전체 앱 시연이 가능하다. 독립적인 이미지 실행 검사 성공만으로 FE→BE 연결 성공을 판단하지 않는다.

## 이전 시연의 한계

실제 전환 순서는 **백엔드 → 프론트엔드**로 잡는다. 전환 중 기존 FE도 새 BE에 연결할 수 있어야 하며, 연결값은 프로세스 시작 시 읽으므로 변경 후 재시작이 필요하다.

이 샘플에는 DB가 없다. 메모를 BE 프로세스 메모리에 저장하므로 재시작하면 사라지고 여러 BE 인스턴스 사이에 공유되지 않는다. 따라서 현재 앱으로는 DB 이전이나 데이터 보존을 포함한 무중단 이전을 입증할 수 없다. FE/BE 빌드·연결·검사 시연에 사용하고, 데이터 보존 시연이 필요하면 별도 영속 저장소 설계가 필요하다.

## 확인 기록

- 2026-11-04: `gh api repos/2026-sbh-delta/delta-sample-app/readme`, `contents/compose.yaml`, `contents/frontend/Dockerfile`, `contents/backend/Dockerfile`, `commits/main` 조회로 구성·경로·포트·헬스 체크·커밋을 확인했다.
- 플랫폼 `ServiceInput`과 소스 검사 코드를 읽어 Dockerfile 경로가 저장소 기준이며 빌드 경로 안에 있어야 함을 확인했다.
- 문서 변경만 수행했다. 이 샘플의 플랫폼 등록, Jenkins 빌드, SAST/DAST, 실제 배포 및 이전 검증은 아직 수행하지 않았다.
