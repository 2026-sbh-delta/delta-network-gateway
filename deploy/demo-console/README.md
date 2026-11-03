# 시연 콘솔

온프레미스 증거, 준우 님 DAST, AI 판단, Jenkins 카나리 배포를 한 흐름으로 돌리고 화면으로 보여 주는 **임시 시연 도구**다. 설계와 검증 기록은 [AI-DLC 문서](../../docs/ai-dlc/demo-integration.md)에 있다.

**로컬 전용이다.** 127.0.0.1 에만 열리고 Jenkins 시험대를 그대로 쓰므로 서버에 올리지 않는다.

## 실행

```bash
./up.sh               # Claude 설명 포함
DEMO_LLM=fake ./up.sh # Claude 호출 없음
```

http://127.0.0.1:18100 을 연다. 끌 때는 `./down.sh` 를 쓴다. 빌드 기록은 남는다.

필요한 것: Docker, `uv`, Chrome, `~/.config/anthropic/delta-ai.key`(Claude 를 쓸 때), 온프레미스 재수집 시 `ssh -J pve` 접속.

## 시나리오

| 화면 | 흐름 | 끝 |
|---|---|---|
| 정상 릴리스 | 온프레미스 판단 → 사람 승인 → DAST 통과 → 10·50·100% | 배포 완료 |
| 취약한 릴리스 | DAST 가 XSS 재현 → 판단이 배포를 멈춤 → 수정본 재검증 통과 → 배포 | 배포 완료 |
| 장애 릴리스 | 10% 에서 오류율 초과 → 자동 롤백 → Claude 가 로그로 원인 설명 | 안정 버전 유지 |

- 온프레미스 판단은 현재 증거로는 항상 `needs_human` 이다. 사람 승인 단계도 시연의 일부다.
- 같은 장애 릴리스를 다시 돌리면 Jenkins 가 재발 기록을 보고 트래픽 전에 사람 확인을 요청한다. 콘솔에서 답할 수 있다.
- 운영 중인 버전이 없으면 기준 버전 배포가 먼저 돌아 1~2분 더 걸린다. 발표 전에 한 번 돌려 둔다.

## 온프레미스 증거

`var/onprem/` 의 저장된 수집본과 판단을 쓴다. 이 폴더는 내부 주소를 담고 있어 커밋하지 않는다.

- `VM 200 에서 다시 수집`: 약 2분. VM 200 에 시험 공격 요청과 스캔용 파드를 만든다. 그 뒤 첫 실행에서 다시 판단한다.
- 바깥 지점 시험에는 VM 201 이 켜져 있어야 한다: `ssh pve qm start 201`, 끝나면 `ssh pve qm shutdown 201`.
- 수집 설정은 저장소 밖 `notes/delta-onprem-collect.json` 이다. 다른 위치면 `DEMO_ONPREM_CONFIG` 로 지정한다.

## 구조

```text
콘솔(흐름 구동) ─ 증거 파일 ─▶ delta-ai ─ 판정 ─▶ 멈춤·사람 확인·진행
     ├─ DAST API ─ 관측 결과 ─▶ delta-ai (dast_verification)
     └─ Jenkins API ─ 빌드 시작 ─▶ 파이프라인 ─ deployment_gate ─▶ delta-ai
```

판정은 delta-ai 만 내린다. 어느 서비스든 응답이 없으면 통과로 보지 않고 멈춘다.

콘솔이 흐름을 구동하는 것은 임시다. DAST 는 루프백 전용이고 재수집에는 Mac 의 SSH 가 필요해 Jenkins 컨테이너에서 부를 수 없다. 서버 배포에서는 [ADR-AI-27](../../services/delta-ai/docs/adr/ADR-AI-27-dast-verdict.md) 의 결정에 따라 파이프라인으로 옮긴다.

## 테스트

```bash
../../services/delta-ai/.venv/bin/python -m pytest -q tests
```
