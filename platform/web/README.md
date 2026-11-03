# 플랫폼 웹

고정 계정 로그인, 서비스 등록·목록, 소스 확인 결과와 이력을 제공한다. React·TypeScript·Vite를 사용하고 `package-lock.json`으로 버전을 고정한다. Node.js 24를 사용한다.

```bash
npm ci
npm run build
```

API가 `dist/`를 같은 origin에서 제공한다. 전체 실행은 [플랫폼 안내](../README.md)를 따른다. 별도 Vite 개발 서버가 필요하면 백엔드 `PLATFORM_PUBLIC_ORIGIN=http://127.0.0.1:5173`으로 설정하고 `npm run dev`를 실행한다. `/api`는 8088의 API로 전달한다.

## 화면 원칙

- 실제 API가 반환한 서비스·검사 결과만 표시한다.
- `succeeded`는 **소스 확인 완료**다. 빌드·배포 성공으로 표시하지 않는다.
- 진행 중인 검사만 3초 간격으로 조회한다. 화면을 떠나면 조회를 취소한다.
- 쓰기 요청에는 CSRF 토큰을 보내고, 중복 클릭·통신 재시도에는 같은 요청 키를 사용한다.
- 비밀번호·세션 토큰을 localStorage에 저장하지 않는다.

## 브라우저 시험

시험용 PostgreSQL을 준비하고 웹을 빌드한 뒤 `npm run test:e2e`를 실행한다. Playwright가 시험용 API·별도 Worker를 기동한다. GitHub 응답은 fixture이며 DB와 실행 상태는 실제로 저장한다. 로그인·성공·실패·새로고침·로그아웃·모바일 화면을 확인한다.

`npm run format:check`로 코드 형식을 확인한다. 캡처·시험 결과·빌드 출력은 Git에서 제외한다.
