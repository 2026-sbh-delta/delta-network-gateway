# 운영 UI 기준과 파이프라인 확장

운영의 개요, 배포 환경, 마이그레이션과 추가 관측 모듈을 유지하면서 PR #75의 파이프라인 화면을 추가한다. 원본 TSX 전체가 없는 기존 운영 화면은 `baseline.tar.gz`로 보관한다. 이 파일은 2026-11-07의 공개 정적 자산만 포함하며 API 응답, 쿠키, 설정이나 비밀값은 포함하지 않는다.

`manifest.json`은 출처와 파일별 SHA256을 기록한다. `scripts/production-ui.mjs`는 이 해시를 검사한 뒤 메뉴, 라우터, 아이콘, 새 화면을 위한 제한된 삽입만 수행한다. 원본 JS의 수정 범위 밖 바이트와 기존 모듈을 보존한다. 새 화면은 `src/PipelinePanel.tsx`와 `production-pipeline-entry.tsx`에서 빌드한다.

```bash
npm run build:production
npm run test:production
npm run dev:production -- --port 5175
```

`build:production`은 보존한 운영 UI와 새 확장을 합친 `dist/`를 만든다. `dev:production`도 같은 운영 기준을 사용하고 새 확장만 Vite에서 제공한다. 일반 `dev`와 `build`는 저장소의 TSX 전체를 사용하는 개발/회귀 경로다. 기존 운영 화면의 TSX 복원 완료를 의미하지 않는다.

운영 기준을 갱신할 때에는 자산 변경과 원본 복원 여부를 검토하고 archive 및 파일별 해시를 함께 갱신한다. 해시나 예상 AST 구조가 달라지면 빌드를 중단한다. 배포는 기존 runtime을 보존하는 [운영 통합 설계](../../../docs/ai-dlc/platform-production-ui-integration.md)를 따른다.
