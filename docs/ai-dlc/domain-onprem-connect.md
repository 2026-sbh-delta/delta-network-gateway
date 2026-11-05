# 관리·시연 도메인과 온프레미스 연결

2026-11-05 사용자가 `delta.mungji.com` 관리 플랫폼 / `demo.delta.mungji.com` 이전 시연 분리와 필요한 연결 진행을 승인했다. 앞선 점검·순서를 바탕으로 도메인 TLS·DNS·Origin을 맞춘다. 추가로 AWS 관리 플랫폼·DAST 두 기기와 온프레미스 한 VM 사이의 TCP 6443/32080/32081만 허용하는 Tailscale 연결·ACL 변경을 승인했다.

## 설계와 적용 순서

1. 관리 도메인은 기존 운영 CloudFront에 연결한다. us-east-1 ACM DNS 검증 후 alias·TLS1.2와 앱의 canonical origin을 맞춘다. 인증·DB·Jenkins 설정은 기존 값을 병합해 보존한다. Terraform 입력과 GitHub 인프라 입력에도 같은 설정을 반영한다.
2. 이전 시연 도메인은 실제 운영 앱의 AWS/GCP origin을 확인한 뒤 DNS Only·TTL60으로 준비한다. 스테이징 ALB나 임시 관리 화면에 연결하지 않는다. origin이 준비되지 않으면 미연결 상태를 유지한다.
3. 온프레미스는 기존 Tailscale을 사용한다. 기존 VM의 10.40.0.10/32만 광고하고, 역할 태그를 가진 AWS 두 기기에서 지정 포트만 허용한다. 홈랩 다른 대역은 광고하지 않는다. IPv4 주소와 기존 Kubernetes CA 검증·namespace 제한 토큰은 유지한다.
4. 실제 API·앱 응답과 다른 포트/대역 차단을 확인한 뒤 실행기 등록과 배포·정리를 검증한다. 중앙 로그는 최종 수집 API와 인증이 확인된 뒤 연결하며 임시 로그 서버로 원본을 보내지 않는다.

## 검증·상태

- 별도 worktree `codex/domain-onprem-connect`, 기준 main `21c3356`. 다른 세션의 작업·팀 인프라 state를 가져오거나 덮어쓰지 않는다.
- 기존 관리 플랫폼 HTTP200, 온프레미스 노드 Ready·FE/BE Running·Collector inactive를 재확인했다.
- 관리 도메인 인증서 ISSUED, CloudFront alias 전파 Deployed, Cloudflare DNS Only·TTL60 등록 완료. `https://delta.mungji.com` HTTP200·브라우저 로그인 화면을 확인했다. 빈 로그인 요청에서 승인 Origin은422(입력 검증), 다른 Origin은403이며 로그인 실패 시도를 발생시키지 않았다. 기존 로그인·DB·23개 설정 키를 보존해 Origin만 변경했다.
- Tailscale 1.104.5를 기존 AWS 관리 플랫폼·DAST 두 VM에 공식 서명 패키지로 설치했다. `tag:delta-client`→`10.40.0.10/32` TCP6443/32080/32081만 추가했다. 기존 온프레미스 노드는 자기 /32만 광고한다. 실제 AWS route table52에는 이 호스트·필요한 Tailscale 주소만 있고 다른 홈랩 대역은 없다.
- 두 AWS VM에서 온프레미스 API TCP 연결·기존 앱 `/api/health` HTTP200, SSH 차단, 홈랩 관리망/서버 HTTPS 차단을 확인했다. 기존 NetworkPolicy에 두 Tailscale 출발지 /32만 병합하고 이전 정책은 VM의 root 전용 경로에 보존했다.
- AWS Worker의 실제 K3s 실행기 검증: `run_id=0d554c9d-161e-475f-8723-2e0968250b8a`. 같은 고정 frontend 이미지로 Pod UID·실제 digest·NodePort32080 HTTP 확인 후 해당 Pod와 pull Secret 제거까지 통과했다. 기존 앱·DB/PVC는 보존했다. controller의 클러스터 노드·Pod 목록 조회는 각각403으로 차단했다. DAST 실제 공격 검사나 전체 웹 UI 배포 흐름 성공으로 기록하지 않는다.
- namespace 제한 controller 토큰은72시간, 2026-11-08 19:26 KST경 만료한다. 기존 SSM 설정이 Advanced8192바이트 상한에 가까워 온프레미스 두 설정을 별도 `/delta-platform/onprem/runtime` SecureString에 저장했다. 기존8KB 설정은 삭제·축소하지 않았다. 실행 중 Worker 작업0건 확인 후 API/Worker에 병합·정상 기동을 확인했다. 새 배포 helper는 전용 파라미터만 읽고 다른 환경 키 덮어쓰기·줄바꿈을 거부한다.
- Terraform mock9개·배포 도구 unittest48개 통과. Ruff·셸 문법·diff whitespace 검사 통과. 실제 전체 Terraform Apply는 수행하지 않았고 도메인 필드만 ETag로 보호해 변경했다. GitHub `PLATFORM_TF_INPUTS`의 기존5개 키를 보존하고 도메인·인증서2개 키를 추가했다. 이 브랜치의 소스가 main에 반영되기 전에는 예전 infra Apply/플랫폼 릴리스를 실행하지 않는다.
- 실제 연결 ID·백업·명령 결과는 Git 제외 `platform/.runtime/domains/`와 `platform/.runtime/onprem-connect/`에 둔다. 토큰·개인 키는 출력하거나 Git에 넣지 않는다.

## 남은 작업

- `demo.delta.mungji.com`: 현재 공개 ALB는 스테이징뿐이며 실제 고객 앱 운영 origin이 없다. 이름·DNS Only·TTL60은 확정했으나 레코드는 미생성이다. 운영 AWS/GCP LB와 동일 서비스·TLS를 확인한 뒤 기존 전환 스크립트의 dry-run·실제 유입 검증으로 연결한다.
- 팀 DAST는 네트워크 도달성까지 검증했다. 실제 scanner 토큰/API 등록과 공격 검사는 해당 컴포넌트의 운영 계약을 맞춰 후속으로 수행한다. 팀 소스·기존 DAST 설정은 바꾸지 않았다.
- 중앙 로그 수집기는 최종 endpoint·인증·식별자 확인 전이므로 중지 상태를 유지한다. 임시 로그 서버에 인증서나 원본을 전송하지 않았다.
- PR 병합·다음 플랫폼 릴리스는 별도 승인/실행이다. 새 helper를 포함한 릴리스 전에 기존 배포 스크립트를 실행하면 별도 온프레미스 설정을 읽지 못하므로 유의한다.

## ADR 초안 — Notion 번호 미배정

상태: 검토 중. 사용자 결정은 도메인 분리와 제한된 Tailscale 연결이다. 관리 화면은 기존 운영 CloudFront를 유지하며, 고객 앱 전환 도메인과 분리해 시연 중 관리 화면의 DNS가 바뀌지 않도록 한다. 온프레미스는 새 유료 VPN·공유 TGW/라우터 변경 대신 이미 사용하는 tailnet의 단일 호스트 경로를 사용한다. TLS/namespace 권한은 유지하고 라우팅·정책은 본선 후 제거한다. 관리 플랫폼·DAST가 tailnet과 해당 기기의 가용성에 의존하는 한계가 있다. Notion 팀 공유 ADR 게시와 번호 배정은 아직 수행하지 않았다.

## 원복

도메인은 저장한 CloudFront 설정·SSM 이전 origin을 복원하고 이번에 생성한 DNS 레코드만 ID와 내용 대조 후 제거한다. 온프레미스는 이번 기기·태그 grant·10.40.0.10/32 광고만 원복한다. 기존 Tailscale 관리 연결·다른 기기·k3s·demo 앱·DB/PVC는 보존한다. 홈랩 전체 원복은 상위 notes 런북을 따른다.
