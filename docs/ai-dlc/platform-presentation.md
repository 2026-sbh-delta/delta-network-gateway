# 플랫폼 발표용 화면 개편

## 승인과 목적

- 2026-11-04: 사용자가 왼쪽 서랍 메뉴와 발표에 적합한 구조를 요청했고, 서비스 개요 / 빌드·검사 / 이전 계획 / 모니터링 설계에 `Then fix them and commit and open PR.`로 구현·검증·커밋·PR 생성을 승인했다.
- 대상: 발표자와 플랫폼을 처음 보는 청중. 긴 단일 화면을 단계별 화면으로 나누고 현재 서비스, 상태, 다음 동작을 쉽게 찾게 한다.
- 로그인 제거는 후속이다. 현재 인증·CSRF와 API 실행 계약을 유지하고 화면 틀은 인증 정보와 분리한다. 실제 배포·인프라 변경·PR 병합은 이번 요청에 포함하지 않는다.

## 설계와 구현 계획

1. 인증과 독립된 AppShell: 데스크톱 고정 사이드바, 모바일 접근 가능한 모달 서랍, 서비스 선택, 현재 화면 표시.
2. URL hash로 서비스/화면을 보존하고 뒤로가기·새로고침을 지원한다.
3. 개요는 서비스 정보와 다음 행동을 표시한다. 빌드·검사는 기존 API와 결과를 재사용하고 세부 기록은 접는다. 이전 계획과 모니터링은 별도 화면으로 연결한다.
4. 밝은 본문, 짙은 사이드바, 일관된 강조색·간격·타이포그래피를 적용한다. 모니터링의 데이터 없음·오류·오래된 상태를 유지한다.

## 성공 기준과 검증

- 서비스 선택 후 각 화면 이동, 새로고침·뒤로가기, 모바일 서랍의 키보드·닫기 동작이 정상이다.
- 기존 등록·소스 확인·빌드·SAST/DAST·계획 승인·로그인 기능이 보존된다.
- 1440px 및 390px 화면을 캡처해 확인하고 가로 넘침이 없다.
- TypeScript/Vite 빌드, Prettier, 기존 브라우저 회귀와 새 탐색 회귀를 실제 실행한다.
- 구현·검증·커밋·PR 상태는 아래에 기록한다.

## 구현과 검증 결과

- 화면 틀 `AppShell`과 인증 코드를 분리하고 사이드바·모바일 모달 서랍을 추가했다. Escape, 닫기, 포커스 순환, 배경 스크롤 잠금과 메뉴 선택 후 본문 포커스를 지원한다.
- 서비스/화면을 hash에 유지한다. 잘못된 경로는 목록으로 복구하고 서비스 ID에 경로 구분자·쿼리를 허용하지 않는다.
- 서비스 개요에 등록 정보와 다음 작업 카드를 배치했다. 빌드 화면은 이미지/실행/보안으로 나누고 소스 세부 정보와 이력을 접었다. 이전 계획·모니터링은 선택한 서비스 기준으로 연결한다.
- 기존 API·인증·CSRF·실행 승인 의미는 유지했다. API나 실제 이전 실행 기능은 추가하지 않았다.
- 검증일: 2026-11-04. Node 24.22.2로 실행했다. 기본 셸의 Node20 대신 `npx --package=node@24 -c '…'`를 사용했다.
- `npm run format:check`: 통과.
- `npm run build`: TypeScript·Vite 빌드 통과.
- `npm run test:e2e`: 실제 로컬 시험 DB·API·Worker를 사용하는 기존 기능과 브라우저 fixture 기반 탐색·관측·계획 시험을 함께 실행해 **20 passed**. 실제 클라우드 실행을 검증한 수치는 아니다.
- `npx playwright test --config=playwright.fixture.config.ts --output=../../.validation/presentation-browser`: **7 passed**. 탐색·키보드·새로고침·뒤로가기·잘못된 경로·모니터링·계획 승인 동작을 검증했다.
- 데스크톱 1440×960과 모바일 390×844 캡처를 시각 확인했다. 아래 이미지는 API fixture를 사용한 화면 검증이며 운영 서비스 데이터가 아니다.

### 화면 미리보기

![서비스 목록](../images/platform-presentation/workspace-desktop.png)

![선택한 서비스와 다음 작업](../images/platform-presentation/service-desktop.png)

![데스크톱 서랍](../images/platform-presentation/drawer-desktop.png)

![모바일 서랍](../images/platform-presentation/drawer-mobile.png)

## 자체 검토

선택한 서비스가 화면 전환·새로고침·뒤로가기·모니터링에서 유지되는지, 읽기 전용 탐색이 실행 API를 호출하지 않는지, 로그인·CSRF·계획 승인 경계가 보존되는지 확인했다. 모바일 포커스 순환을 보완했고 빌드 버튼이 상세 정보에 밀리지 않도록 등록 정보와 커밋 기록을 접었다. 최종 `git diff --check`가 통과했다.

## 운영과 복구

화면 개편 PR #31, 웹 서랍 PR #37, 프로젝트 대시보드 PR #41은 병합·운영 배포를 완료했다. 아래 날짜별 배포 기록을 따른다. 이번 변경은 웹 번들이다. 기존 플랫폼 릴리스 절차로 배포하고 문제가 있으면 이전 웹 번들로 복귀한다. DB 스키마·인프라·로그인 정책 변경은 없다.


## 웹 서랍 후속 수정 (2026-11-04)

- 사용자 `I need drawer for the web platform.` 요청으로 데스크톱 고정 사이드바를 모든 화면 크기에서 메뉴 버튼으로 여는 왼쪽 서랍으로 변경한다. 기존 화면 개편 승인 범위의 수정이다.
- 설계·구현: 기본은 닫힌 상태이고 본문은 전체 너비를 사용한다. 단일 native dialog로 메뉴를 제공하며, 메뉴 버튼에 펼침 상태와 연결 대상을 표시한다. 열릴 때 짧은 슬라이드 효과를 적용하고 동작 줄이기 설정에서는 애니메이션을 생략한다.
- 닫기 버튼·Escape·배경 클릭·메뉴 선택을 지원한다. 열린 동안 배경 조작과 스크롤을 막고 키보드 포커스를 유지한다. 화면 크기를 바꿔도 열린 메뉴를 유지한다. 로그인과 API 계약은 유지한다.
- 테스트: 데스크톱 1440×960·모바일 390×844에서 기본 닫힘, 전체 너비, 열림 상태, 포커스 유지·복귀, 스크롤 잠금·복구, 닫기 방법, 화면 크기 변경, 메뉴 탐색·로그아웃을 검증한다. 기존 탐색 테스트는 메뉴를 먼저 여는 동작으로 갱신했다.
- `npm run build`: TypeScript/Vite 통과. `npm run format:check`: 통과.
- `node node_modules/@playwright/test/cli.js test --config=playwright.fixture.config.ts --output=../../.validation/web-drawer-final`: **8 passed**. 최초 실행의 메뉴 활성 상태 확인 테스트는 닫힌 서랍을 조회해 실패했으며, 메뉴를 열고 확인하도록 수정한 뒤 재검증했다.
- 위 미리보기는 후속 구현의 API fixture 화면이다. 애니메이션을 완료한 상태로 캡처해 데스크톱·모바일을 시각 확인했다. 실제 운영 배포를 검증한 이미지는 아니다.
- `npm run test:e2e`: 실제 로컬 시험 DB·API·Worker와 브라우저 fixture를 포함해 **21 passed (2.1m)**. 클라우드 배포 검증과 구분한다. 최종 `git diff --check`도 통과했다.


## 프로젝트 대시보드 개편 (2026-11-04 KST)

### 요청과 설계

- 사용자가 발표에 맞지 않는 정보 구성·디자인을 지적하고, 프로젝트 내부에서 진입하므로 중복 프로젝트 드롭다운이 필요 없으며 개발자들이 흔히 사용하는 대시보드를 원한다고 명확히 했다. 기존 프론트엔드 수정·검증·커밋·PR/검사 후 병합·배포 요청의 후속 수정이다.
- 초기 운영 확인에서는 서비스 2개에 연결된 환경이 없었다. 사용자 확인으로 기능 장애 추측 대신 디자인·프로젝트 문맥 요구사항에 집중했다. 진단의 오류 원인 가설 단계는 생략하고 원하는 사용자 흐름을 브라우저 회귀 검사로 고정했다.
- URL의 프로젝트를 기준으로 개별 서비스 정보를 읽는다. 대시보드에서 프로젝트 드롭다운과 자동 첫 서비스 선택을 없앤다. 전체 작업 공간에서 모니터링을 누르면 먼저 서비스 목록에서 대상을 선택한다.
- 구조: 프로젝트 링크·대시보드 제목·새로고침 → 실행 환경 탭·조회 기간 버튼 → 요청/오류율/p95 지표와 실제 시계열 → 앱·DB / 서버 상태 → 경고·오류 / 요청 경로.
- 환경 미연결·조회 실패·데이터 없음은 0 또는 정상으로 보이지 않게 한다. 환경이 없어도 동일 카드 구조와 연결 안내를 제공한다. 그래프는 실제 수집 시각으로 간격을 계산하고 데이터가 2점 미만이면 그리지 않는다.

### 구현과 확인

- `ObservabilityPanel`은 필수 `serviceId`를 받으며 프로젝트 재선택 UI를 제거했다. 환경과 기간은 버튼으로 전환한다. 미연결 상태에서도 새로고침으로 환경 목록을 다시 조회한다.
- 오류·지연·미지원·DB 이전 상태의 의미를 유지했다. 환경/기간이 바뀌는 동안 이전 스냅샷을 새 선택의 데이터로 표시하지 않는다. API·인증·실제 데이터는 변경하지 않는다.
- 수정 전 `playwright test --config=playwright.fixture.config.ts --grep "프로젝트 대시보드는"` 실행에서 본문의 드롭다운이 기대 0개/실제 3개로 실패하는 것을 확인했다. 구현 후 프로젝트 문맥·드롭다운 제거·환경 탭·새로고침 검증이 통과했다.
- `npm run build`, `npm run format:check`: 통과. 최초 fixture 검사 **10 passed**. 데스크톱 1360px·모바일 390px의 실제 브라우저 캡처를 시각 확인했다. 캡처의 지표는 테스트 fixture이며 운영 데이터가 아니다.
- 최종 `npm run test:e2e`: **24 passed (2.2m)**. 실제 로컬 시험 DB·API·Worker와 fixture 회귀를 포함하며 운영 데이터 검사와 구분한다. 원격 CI·운영 배포는 후속이다.
- [데스크톱 미리보기](../images/project-dashboard/desktop.png) · [환경 미연결](../images/project-dashboard/empty.png) · [모바일](../images/project-dashboard/mobile.png). 모두 API fixture를 사용한 화면이다.


## 운영 배포 완료 (2026-11-04 23:45 KST)

- 사용자가 `배포해`로 운영 반영을 요청했다. 서랍 수정 PR #37은 검사 10개 통과 후 main에 병합했다(`669f414`).
- main `5f3236c88bd3b632e4b7456e9c1978ce8be43a79`로 `gh workflow run platform-release.yml --ref main`을 실행했다. [Release 41849097814](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41849097814)의 검사·ARM64 이미지 빌드·게시·기존 설정을 사용한 VM 배포·HTTPS readiness가 모두 성공했다. 이번 실행은 인프라 Apply를 수행하지 않는다.
- [운영 주소](https://d3udcz0zch0on.cloudfront.net)는 유지했다. `curl --fail .../health/ready`로 `{"status":"ready"}` 응답을 확인했다.
- Playwright로 실제 운영 로그인, 데스크톱 1440×960 서랍 열기·Escape 닫기, 모바일 390×844 메뉴 열기·모니터링 이동·닫기, 로그아웃을 검증했다. 브라우저 pageerror는 0건이고 모바일 가로 넘침이 없었다. 데스크톱·모바일 캡처를 시각 확인했다.
- 배포 전후 서비스 ID 목록을 비교해 기존 서비스 2개의 보존을 확인했다. 고객 서비스 등록·빌드·SAST/DAST 실행은 이 배포 확인에서 수행하지 않았다.
- 실행 증거·운영 화면은 작업 트리의 ignored `.validation/drawer-release/`에 보관한다. 로그인 비밀값과 세션은 기록에 포함하지 않는다.
- 워크플로는 여전히 수동 실행 방식이다. main 병합 자체가 자동 배포를 의미하지 않는다.


## 프로젝트 대시보드 운영 반영 (2026-11-05 00:16 KST)

- 구현 커밋 `97d32e0`, [PR #41](https://github.com/2026-sbh-delta/delta-network-gateway/pull/41)의 원격 검사 11개가 모두 통과했다. 확인한 head로 병합해 main `7989f6d4cc19885ab3a82bc7af21ac415238d785`에 반영했다.
- `gh workflow run platform-release.yml --ref main`의 [Release 41854211786](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41854211786)가 검사·ARM64 이미지 빌드·기존 설정 기반 배포·HTTPS 검사까지 성공했다. 인프라 Apply나 고객 앱 실행은 하지 않았다.
- 운영에서 Playwright로 서비스 개요 → 서랍의 모니터링 → 해당 서비스 ID의 대시보드 진입을 확인했다. 본문 select 0개, 프로젝트 이름·URL 유지, 새로고침 후 같은 프로젝트, 모바일 가로 넘침 없음, 서비스 개요 복귀, 로그인·로그아웃을 검증했다. pageerror는 0건이다.
- 운영 서비스 2개를 배포 전후 ID로 비교해 보존을 확인했다. 실제 실행 환경은 미연결 상태였으며, 연결 안내·3개 지표 카드의 `—`·재조회 동작을 확인했다. 실측 그래프 표시를 운영에서 확인한 것은 아니며 해당 경로는 브라우저 fixture로 검사했다.
- `curl --fail https://d3udcz0zch0on.cloudfront.net/health/ready`는 `{"status":"ready"}`였다. 주소는 그대로다. 운영 캡처·비밀값 없는 검증 JSON은 ignored `.validation/dashboard/`에 보관하고 데스크톱·모바일 캡처를 시각 확인했다.


## 서랍 드롭다운 테마 (2026-11-05)

- 사용자 요청: 왼쪽 서랍의 드롭다운이 다른 테마로 보여 서랍과 통일한다. 운영에서 선택 요소의 `color-scheme`이 `normal`인 것을 확인했다.
- 서랍의 서비스 선택에 `color-scheme: dark`와 옵션 배경·글자색을 지정했다. 지원 브라우저에서는 native `base-select`와 `::picker(select)`로 펼친 목록·테두리·선택·호버 색을 남색/파란 서랍 테마에 맞춘다. 미지원 브라우저는 기존 native select와 어두운 색상 설정을 사용한다. 다른 폼에는 적용하지 않는다.
- JavaScript·선택·라우팅 계약 변경은 없다. 키보드와 native 선택 의미를 유지한다.
- `npm run build`, `npm run format:check` 통과. 기존 fixture 브라우저 검사 **11 passed**. 단순 스타일 변경이므로 새 영구 테스트는 추가하지 않았다.
- 별도 로컬 Playwright 확인에서 light/dark 두 설정의 계산된 dark 테마, 방향키·Enter 선택, 모바일 옵션 클릭·프로젝트 이동, 가로 넘침 없음을 확인했다. 데스크톱·모바일의 펼친 목록 캡처를 시각 확인했다. [미리보기](../images/platform-presentation/drawer-select.png)는 테스트 데이터다.
- 원격 CI·병합·운영 반영은 후속이다.


### 드롭다운 운영 반영 (2026-11-05 00:46 KST)

- 구현 `83d19ad`, [PR #43](https://github.com/2026-sbh-delta/delta-network-gateway/pull/43)의 원격 검사 11개 통과 후 병합했다. main `4a1924be769074f7278cc4a52643e016e2cd7cc7`의 [Release 41859205247](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41859205247)가 검사·이미지 빌드·배포·HTTPS readiness까지 성공했다.
- 운영 Playwright로 선택 요소의 `color-scheme: dark`, 펼친 목록의 `rgb(28, 43, 67)` 배경, 서비스 선택 후 해당 서비스 화면 이동, 모바일 가로 넘침 없음, 로그인·로그아웃을 확인했다. 페이지 스크립트 오류는 0건이고 기존 서비스 2개를 보존했다.
- 데스크톱·모바일에서 펼친 드롭다운 색상을 확인했다. 운영 확인은 Chromium 기준이며, 다른 브라우저의 native popup 모양이 픽셀 단위로 동일함을 보장하지 않는다. 로컬 light/dark 설정과 native 키보드 선택은 앞선 확인을 따른다.
- 공개 주소는 유지했고 `/health/ready`는 `{"status":"ready"}`였다. 검증 JSON과 운영 캡처는 ignored `.validation/dropdown/`에 보관한다.

## 가독성 개선과 서비스 주소 안내 제거 (2026-11-07 KST)

### 승인과 현재 단계

- 사용자가 기존 기능을 유지하면서 같은 페이지의 색상, 배치, 글꼴을 개선하고 서비스 열기 링크, 반복 주소, 사설망 안내 문구를 제거하도록 요청했다.
- 2026-11-07 사용자의 `고우`로 Ideation의 문제, 대상, 범위, 성공 기준을 승인받았다.
- 2026-11-07 사용자의 `ㄱㄱ`로 Inception 및 Construction U1 설계/구현 계획을 승인받았다.
- 현재 단계: Construction U1 구현, 로컬 검증과 Review 완료. Operation의 실제 배포는 미실행이다.
- 빌드, 형식 검사와 기존 fixture 브라우저 회귀 40개를 실제 실행했다. DB 기반 전체 E2E, 배포, Terraform Plan/Apply, 커밋과 푸시는 실행하지 않았다. 이전 날짜의 검증과 구분한다.

### Ideation

- 문제: 15px 기준 글자와 약 10px까지 작아지는 일부 보조 정보, 흐린 색상 때문에 화면 정보를 읽기 어렵다. 사용자가 지정한 주소 안내 영역은 화면에서 제거한다.
- 사용자: 기존 플랫폼을 사용하는 팀원과 발표 화면을 보는 사람.
- 성공 기준: 본문과 보조 정보의 가독성 개선, 지정 안내 영역 제거, 데스크톱과 모바일 가로 넘침 방지, 기존 기능 회귀 검사 통과.
- Scope: 기존 페이지와 탐색 구조 안에서 글자 크기, 색 대비, 여백, 반응형 배치를 조정하고 지정 안내 표시를 제거한다.
- Non-goals: 새 기능, 페이지 추가, 백엔드/API/DB/인증 정책, 실제 배포 흐름, 인프라 변경.

### Inception

Functional Requirements:

1. `ApplicationProgress.tsx`의 서비스 주소 링크와 사설망 안내 문단을 화면에서 제거한다. 주소 데이터의 생성, 저장, API 계약은 유지한다.
2. 본문 기준은 16px로, 설명과 상태 등 읽어야 하는 보조 정보는 14px 이상으로 정리한다. 기존 시스템 한글 글꼴을 사용하며 외부 글꼴 의존성을 추가하지 않는다.
3. 밝은 본문과 흰색 카드, 짙은 본문 글자, 파란 주요 동작이라는 기존 방향을 유지하고 보조 글자, 테두리, 선택 상태의 대비를 높인다. 상태 이름과 의미는 유지한다.
4. 제목, 설명, 카드, 동작 버튼의 간격을 일관되게 정리한다. 390px에서는 카드와 도구 모음이 내용에 맞게 줄바꿈되도록 한다.
5. 로그인, 서비스 등록/선택, 서랍, URL과 뒤로가기, 빌드/검사, 계획 승인, 모니터링, 삭제 확인 동작을 보존한다.

Non-Functional Requirements:

- 일반 본문과 보조 글자의 전경/배경 대비는 4.5:1 이상을 목표로 검증한다. 비활성 컨트롤과 장식 요소는 구분해 확인한다.
- 키보드 포커스, 상태 텍스트, 기존 접근성 이름과 native 입력 의미를 유지한다.
- 1440px와 390px에서 페이지 전체 가로 넘침이 없어야 한다. 비교 표와 코드/로그의 기존 내부 스크롤은 유지한다.
- 새 패키지, 외부 글꼴 또는 이미지 요청을 추가하지 않는다.

Architecture:

- 기존 React/Vite와 AppShell 구조를 사용한다. 기존 CSS 계층에서 색과 글자 크기, 간격을 조정하며 JSX는 지정 안내 문단 제거로 한정한다.
- 대상은 `web/src/style.css`, `presentation.css`, `dashboard.css`, `release.css`, `application-flow.css`, `project-delete.css`와 `ApplicationProgress.tsx`다. 실제 변경은 필요한 파일에 한정한다.
- 기존 이벤트 처리, 인증/CSRF, 요청 경로, 서비스/배포 상태 전이는 변경하지 않는다.

Unit of Work:

- U1: 주소 안내 제거와 기존 화면 가독성 개선을 하나의 프론트엔드 단위로 구현하고 기존 기능 회귀 및 화면 크기 검증을 실행한다.

Acceptance Criteria:

- 자동 배포 진행 화면의 지정 주소/사설망 안내 문단이 표시되지 않는다.
- 일반 본문 16px와 주요 보조 정보 14px 이상이 계산된 스타일에 반영되고 대비가 개선된다.
- 기존 fixture 기반 기능 검사와 TypeScript/Vite 빌드가 통과한다. 실행한 검사만 결과로 기록한다.
- 데스크톱 1440px 및 모바일 390px에서 메뉴/선택/동작 버튼이 사용 가능하며 페이지 전체 가로 넘침이 없다.

### Construction U1: Design과 Implementation Plan

Design:

- 기본 본문은 짙은 남색, 보조 글자는 회청색으로 통일한다. 강조색은 기존 파랑을 사용하고 초록/주황/빨강 상태 텍스트의 의미를 보존한다.
- 본문 16px, 보조 14px 이상, 줄 높이 약 1.6을 적용한다. 제목은 현재 계층을 유지하면서 본문과 구분한다.
- 카드 안쪽 여백은 24~32px를 기준으로 정리하며, 모바일은 16~20px와 한 열 배치를 사용한다. 고정 높이 때문에 글자가 잘리는 요소는 내용 기준으로 조정한다.
- 지정 안내 JSX 문단만 제거하고 배포 단계, 완료 상태, 계속 진행 버튼과 보고서는 유지한다.

Implementation Plan:

1. 기존 화면별 CSS의 작은 글자와 흐린 색을 수정하고 공통 색/크기/간격을 정리한다.
2. `ApplicationProgress.tsx`의 주소/사설망 안내 문단을 제거한다.
3. `npm run build`, `npm run format:check`, `npx playwright test --config=playwright.fixture.config.ts`를 실제 실행한다. 단순 스타일 수치를 복제하는 영구 테스트는 추가하지 않는다.
4. 기존 데스크톱/모바일 회귀와 캡처를 확인하고 실제 실행 결과, 한계, 자체 Review를 이 기록에 추가한다.
5. 관련 README의 상태와 검증 기록을 갱신한다. 운영 반영, 커밋, 푸시는 수행 상태를 별도로 기록한다.

Approval: 2026-11-07 사용자 `ㄱㄱ`로 Inception 및 Construction U1 설계/계획 승인 완료.
Implementation / Test / Review: 아래 로컬 구현과 실제 검증 범위에서 완료. 운영 배포와 구분한다.

### Operation 계획과 현재 확인 한계

- 배포는 아직 수행하지 않는다. 기존 플랫폼 웹 번들 릴리스 경로를 따른다.
- 배포 후에는 기존 로그인/서비스 선택/화면 이동/오류 표시와 readiness를 확인한다. 문제가 발생하면 이전 웹 번들로 복귀한다. DB migration과 인프라 Apply는 필요하지 않다.
- 사용자 원문 `서비스 주소 열기`와 `사설 주소는 해당 네트워크에서만 접근할 수 있습니다.`는 현재 저장소에서 그대로 발견되지 않았다. 같은 역할의 표시가 `ApplicationProgress.tsx`에 있으나 실제 사설 서비스와 현재 체크아웃의 버전 동일성은 확인하지 못했다.
- CUA는 `The browser access host is unavailable`이어서 운영 화면을 보지 못했다.

### 2026-11-07 읽기 전용 확인

| 실행 명령/도구 | 결과 |
| --- | --- |
| `git status --short` | 변경 없는 작업 트리를 확인했다. 이후 이 문서와 상태 인덱스를 갱신한다. |
| `rg -n`으로 안내 문구와 사설 주소 검색 | 원문은 없음. `ApplicationProgress.tsx`의 서비스 주소/사설망 안내 문단을 확인했다. |
| `curl --noproxy '*' -fsS --connect-timeout 5 --max-time 10 http://10.80.60.60:8088/` | 종료 28, 연결 시간 초과. 운영 화면과 삭제 대상의 동일성은 미검증이다. |
| `ls -ld web/node_modules` | 디렉터리가 없다. 구현 승인 후 빌드/시험 전에 의존성 준비가 필요하다. |

### Construction U1 구현과 실제 검증 (2026-11-07 KST)

Implementation:

- 공통 CSS 변수에 본문/보조 색, 배경, 테두리, 강조/포커스 색과 보조 글자 크기를 모았다. 본문은 16px, 수정 대상 CSS의 보조 정보는 최소 0.875rem(14px)로 정리했다. 기존 한글 시스템 글꼴을 유지했다.
- 보조 글자와 대기/실패/진행/완료 상태의 대비를 높였다. 어두운 서랍의 텍스트 색을 보존하고 메뉴 숫자의 불투명도를 올렸다.
- 카드 제목/도구 모음/상태 행의 줄바꿈, 그리드 자식의 최소 너비, 최소 44px 버튼과 내용 기준 헤더 높이를 적용했다. 모바일 카드와 서랍 안의 글자가 커져도 내용에 맞게 배치된다.
- `ApplicationProgress.tsx`의 주소 링크/사설망 안내 문단만 제거했다. 주소 API 필드, 인증, 요청 처리, 폴링, 배포 단계와 계속 진행 버튼은 유지했다.
- 수정 범위는 CSS 6개와 해당 JSX 문단, 웹 README/구현 기록 및 공통 AI-DLC/진행 인덱스다. 의존성 명세, API/백엔드/DB/인프라는 변경하지 않았다.

Test:

| 날짜 | 실제 실행 명령/확인 | 결과 |
| --- | --- | --- |
| 2026-11-07 | `node --version`, `npm --version` | Node v24.19.0, npm 11.17.0 |
| 2026-11-07 | `npm ci --cache /private/tmp/delta-platform-ui-npm-cache --no-audit --no-fund` | lockfile에 고정된 의존성 73개 설치. 명세/lockfile 변경 없음 |
| 2026-11-07 | `PLAYWRIGHT_BROWSERS_PATH=/private/tmp/delta-ui-playwright npx --no-install playwright install chromium --only-shell` | 기존 시험용 Chromium headless 및 FFmpeg를 임시 경로에 설치 |
| 2026-11-07 | `npm run build` | 최종 TypeScript/Vite 빌드 통과. CSS `index-CmCWxI8L.css`, JS `index-DimdzZgw.js` 생성 |
| 2026-11-07 | `npm run format:check` | 최종 Prettier 검사 통과 |
| 2026-11-07 | `PLAYWRIGHT_BROWSERS_PATH=/private/tmp/delta-ui-playwright npx --no-install playwright test --config=playwright.fixture.config.ts --output=test-results/ui-readability` | 최종 40 passed (24.0s). 등록, 계획/승인, 결과 구분, 오류 복구, 탐색/뒤로가기, 서랍/키보드/로그아웃, 삭제 확인과 모바일 가로 넘침 검사 포함 |
| 2026-11-07 | `python3 -`로 CSS 크기/선택 색상 대비/제거 문구 정적 검사 | 대상 CSS 6개의 명시적 14px 미만 글자 크기 0개. 선택한 일반 텍스트 색 조합 12개가 4.5:1 이상. 본문/흰색 15.75:1, 보조/흰색 6.73:1, 보조/화면 배경 6.21:1. 포커스는 밝은 배경 4.77:1, 어두운 배경 3.50:1. 주소 안내 JSX 부재 확인 |
| 2026-11-07 | fixture 캡처 시각 확인 | 서비스 개요 1440px 및 모바일 390px, 대시보드 1360px 및 모바일 390px, 모바일 서랍 확인. 메뉴/글자/버튼 잘림과 페이지 전체 가로 넘침을 발견하지 않음 |

- 최종 회귀 전 최초 검사도 40 passed(26.6s)였다. 이후 일부 파란 보조 글자와 포커스 색의 대비를 보정하고 위 최종 검사를 실행했다.
- 단순 스타일 값을 복제하는 영구 테스트는 추가하지 않았다. 기존 회귀 테스트를 변경하지 않고 실행했다.
- 정적 검사 결과는 ignored `platform/web/test-results/ui-readability-style-audit.json`, 기존 시험 캡처는 `platform/web/test-results/ui-readability/`에 있다. 보기 쉬운 파일명으로 복사한 서비스/서랍/대시보드 PNG도 같은 test-results 아래에 있다.
- CSS 선언과 선택한 단색 조합을 정적으로 확인한 결과이며 전체 페이지의 계산된 글꼴/대비를 자동 수집하거나 운영 접근성을 인증한 것은 아니다.
- DB 기반 전체 E2E 및 실제 GitHub/클라우드/배포 동작은 이번 UI 변경에서 실행하지 않았다. 브라우저 결과와 캡처는 API fixture이며 실제 운영 데이터가 아니다.

Review:

- JSX diff는 지정 주소 안내 문단 제거 1건이며, 이벤트 처리와 데이터 계약을 바꾸지 않았다. CSS와 문서 외의 기능 코드 변경이 없다.
- 40개 기존 fixture 회귀와 데스크톱/모바일 캡처를 통해 기존 화면 이동과 조작이 유지되는 범위를 확인했다. 최종 빌드/형식 검사를 완료했다.
- `web/README.md`, `web/IMPLEMENTATION.md`와 이 기록 및 공통 상태 인덱스를 현재 결과에 맞춰 갱신했다.

Operation:

- Deployment: 미실행. 로컬 번들만 빌드했으며 현재 사설 서비스에 배포했다고 표시하지 않는다.
- Observability: 실제 배포 후 기존 readiness, 로그인/서비스 선택/화면 이동, 오류 표시와 브라우저 오류를 확인한다.
- Rollback: 화면 문제가 생기면 기존 릴리스의 웹 번들로 복귀한다. API/DB 변경과 migration은 없다.
- Runbook: 기존 플랫폼 릴리스 절차를 사용하며 사설 주소 접근/버전 동일성 확인은 남아 있다. 커밋/푸시/PR과 Terraform Plan/Apply는 미실행이다.

### 후속 U2: 최신 배포 환경 화면을 기준으로 정정 (2026-11-07 KST)

승인과 문제:

- 사용자가 `Migration verification 85df8a6`의 상세 화면이 최신 버전이라고 지정하고 `여기로 맞춰서 개발 ㄱㄱ`로 기존 UI 개선의 개발 기준을 정정했다. 이 화면과 기존 승인 범위를 기준으로 Inception/Construction U2를 진행한다.
- 실제 기준 주소는 `https://delta.mungji.com/#/overview/61b078c3-f956-4b9b-b394-6c43324dc2af`다. CUA의 브라우저 탭 연결은 사용할 수 없었으나, native Chrome으로 해당 상세 화면과 주소 안내를 확인했다.
- 현재 서비스 조회 응답은 `application.status=succeeded`이며 AWS/온프레미스/GCP 환경 3개를 반환한다. 실제 화면은 배포 환경 카드와 마이그레이션/대시보드 이동 버튼을 사용한다.
- 이전 U1에서 제거한 `ApplicationProgress` 문단과 달리 사용자가 지목한 정확한 안내는 운영 번들의 배포 환경 카드 안에 있다. 이전 40개 회귀 결과는 그때의 로컬 화면 검증이며 최신 운영 화면 수정 완료의 근거로 재사용하지 않는다.
- 실제 번들 `index-CZEcGrcx.js`의 배포 환경 표시 로직과 읽기 전용 API의 필드 이름을 확인했다. 원본 TSX는 로컬과 원격 main에 없고 사용자도 원본 위치를 알지 못한다. 배포된 표시 로직과 기존 API 계약을 기준으로 해당 프론트엔드 화면을 복구한다.
- 원격 참조 갱신 후 기존 main `e17327fb`(#72)의 발표 접근 변경을 fast-forward로 반영했다. 새 커밋 없이 기존 미커밋 UI 변경을 보존했다. 최신 화면 복구 외 백엔드/인프라 기능을 새로 구현하지 않는다.

Inception / Design:

- Functional Requirements: 배포 완료 앱은 최신 배포 환경 카드로, 진행 중 앱은 배포 진행으로 표시한다. 완료로 바뀌면 새로고침 없이 카드로 이동한다. 환경의 리전, 계정/프로젝트/호스트, 버전, VM/실행 호스트와 DB 생성 설정을 유지한다. 지정 주소 링크/반복 주소/사설망 안내를 환경 카드에서 제거한다.
- Non-Functional Requirements: 기존 16px/14px 이상 글자와 대비 개선을 유지한다. 1440px/390px 반응형, 접근성 이름, 요청 취소와 조회 오류/미연결 구분을 보존한다.
- Architecture: 기존 `observabilityApi.environments`와 선택 서비스 hash 경로를 사용한다. `DeployedEnvironments`와 전용 CSS, 환경 조회 타입의 선택 필드 및 완료 상태 화면 분기를 추가한다. API/DB와 배포 실행 계약을 변경하지 않는다.
- Unit of Work: U2는 사용자가 지정한 서비스 상세의 배포 환경 화면과 완료 상태 전환, 지정 안내 제거 및 회귀 검증이다. 다른 서비스 유형의 수동 등록/검사 기능은 유지한다.
- Non-goals: 운영 번들 전체의 원본을 복원했다고 주장하지 않는다. 별도 마이그레이션 실행기/팀 파이프라인 기능, 신규 API, 실제 자원/DB 변경과 운영 배포는 추가하지 않는다.

Implementation Plan / Acceptance Criteria:

1. 실제 배포 환경 카드의 표시 항목과 기존 이동 버튼을 프론트엔드 소스로 복구하고 지정 안내 블록을 제외한다.
2. 배포 완료/진행 중/기존 단일 서비스 화면 분기를 구분하고 완료 시 부모 상태를 갱신한다.
3. 새로운 데이터/화면 분기에 대한 데스크톱/모바일 회귀, 완료 전환 및 조회 실패 구분을 확인한다. 기존 회귀도 실제 실행한다.
4. 최종 빌드/형식 검사, 화면 캡처와 결과를 같은 기록에 갱신한다. 실제 운영 배포 및 Git 게시 상태는 별도로 기록한다.

Approval: 사용자 `여기로 맞춰서 개발 ㄱㄱ`로 최신 기준 화면 및 기존 개선 범위의 U2 구현 승인.
Implementation / Test / Review: U2 로컬 구현/검증/Review 완료. 최종 U2 결과는 아래 46개 실제 회귀와 빌드/형식 검사다. 운영 반영과 구분한다.

U2 구현 결과:

- `DeployedEnvironments.tsx`와 전용 CSS를 추가하고 실제 조회 계약의 선택 필드만 프론트엔드 타입에 반영했다. 환경 목록은 10초 주기로 조회하며 화면 이탈 시 요청/타이머를 취소한다. 오류와 미연결을 구분하고 삭제 중/삭제됨 상태를 유지한다.
- 최신 화면의 리전, 계정/프로젝트/호스트, 버전, VM/실행 호스트, DB 설정 항목을 유지하면서 정확한 주소 안내 블록을 제외했다. 마이그레이션/대시보드 이동은 기존 서비스 ID와 hash를 유지하며 실행 API를 호출하지 않는다.
- 배포 완료 상태는 환경 카드로 표시하고 진행 중 앱은 진행 상태에 집중한다. 완료 조회/계속 진행 응답을 부모와 서비스 목록에 전달해 새로고침 없이 화면과 경로 제목을 갱신한다. 기존 단일 서비스의 수동 작업 화면은 유지한다.
- 기존 가독성 변경을 유지하고 환경 카드를 데스크톱 3열, 1100px 이하 1열로 배치했다. AWS 주황/온프레미스 초록/GCP 파랑의 얇은 구분선, 긴 ID 줄바꿈과 최소 44px 버튼을 적용했다.
- 읽기 전용 화면과 상태 전환을 위한 의미 있는 회귀 6개를 추가했다. 단순 CSS 수치를 복제하는 테스트는 추가하지 않았다.

U2 실제 검증 (2026-11-07 KST):

| 실행 명령/확인 | 결과 |
| --- | --- |
| CUA native Chrome에서 기준 서비스 상세 조회 | 서비스의 배포 환경 3개와 정확한 주소 안내/기존 이동 버튼 확인. 탭 전용 호스트는 사용할 수 없었으나 native Chrome은 동작함 |
| `curl --fail --silent --show-error ... https://delta.mungji.com/` 및 HTML에 명시된 번들 조회 | 운영 번들 `index-CZEcGrcx.js`의 완료 앱 분기와 배포 환경 표시 로직 확인. 원격 코드는 파싱/정적 읽기만 수행 |
| `python3 -`의 표준 TLS 검증과 메모리 쿠키를 사용한 읽기 전용 조회 | 관리 서비스 `application.status=succeeded`, AWS/온프레미스/GCP 3개와 환경 필드 이름 확인. 토큰/쿠키 값은 출력하거나 저장하지 않음 |
| `git fetch origin --no-tags`, `git merge --ff-only origin/main` | 기존 main `e17327fb`로 fast-forward, 새 커밋 없이 이전 UI 변경 보존. 동기화 전 patch는 `/private/tmp/delta-ui-readability-before-latest.patch`에 보관 |
| `npm run build` | TypeScript/Vite 통과. CSS `index-DQKHGpYU.css`, JS `index-Cu4ighBU.js` 생성 |
| `npm run format:check` | Prettier 통과 |
| `PLAYWRIGHT_BROWSERS_PATH=/private/tmp/delta-ui-playwright npx --no-install playwright test --config=playwright.fixture.config.ts --output=test-results/ui-latest` | 46 passed (39.6s). 기존 40개와 새 6개 모두 통과 |
| 새 회귀 6개 | 1440px/390px에서 실제 주소 데이터가 있어도 안내 부재, 카드 정보 보존, 이동/뒤로가기/서비스 ID 유지와 쓰기 요청 0개, 새로고침 없는 완료 전환, 환경 조회 실패/자동 복구, 미연결, 삭제 상태 구분 확인 |
| `python3 -`로 CSS 선언 점검 | 대상 CSS 7개에 16px 기준 14px 미만의 명시적 글자 크기 0개. 이전 선택 색상 대비 결과와 구분해 `ui-latest-style-audit.json` 기록 |
| 로컬 브라우저 PNG 시각 확인 | 1440px 3열 및 390px 1열 환경 카드, 버튼과 긴 값 줄바꿈, 주소 안내 부재 확인. fixture이며 실제 서비스 데이터/배포 결과가 아님 |

Review와 Operation:

- 새 구성요소는 기존 환경 조회만 사용한다. 이번 미커밋 변경에는 백엔드/DB/인프라 변경이 없고 upstream #72를 보존했다. 실제 서비스 등록/삭제/배포/이전 요청은 수행하지 않았다.
- 원본 TSX 위치를 확인하지 못한 상태이므로 운영 번들 전체를 복원/동기화했다고 표시하지 않는다. 최신 배포 환경 상세와 완료 상태 화면을 기존 소스/API에 맞춘 이식이며 별도 마이그레이션/팀 파이프라인 기능은 범위 밖이다.
- 미리보기와 시험 기록은 ignored `platform/web/test-results/ui-latest/`, `latest-environments-desktop.png`, `latest-environments-mobile.png`, `ui-latest-style-audit.json`에 보관했다.
- U2 로컬 검증 시점에는 Deployment/실제 클라우드 동작/DB 기반 전체 E2E/Terraform Plan/Apply와 Git 게시를 실행하지 않았다. Git 게시 후속은 아래 PR 기록을 따르며 운영 주소 안내 제거는 실제 배포 검증과 구분한다.
- 실제 운영 번들 교체 전에는 이번에 복원하지 않은 운영 UI 기능을 포함한 원본/릴리스 통합을 별도로 확인해야 한다. Rollback은 기존 웹 번들이며 DB migration은 없다.

### PR 게시와 main squash 병합 경로 (2026-11-07 KST)

- 사용자가 `PR 올리고 main bracnh로 squash 병합 ㄱㄱ`로 커밋/푸시/PR 생성 및 main squash 병합을 명시적으로 승인했다. 운영 배포는 포함하지 않는다.
- 구현/웹 문서/회귀는 SSH 서명 커밋 `090c51d3c9276cee926f806051c33c092c8de260`으로 `ui/platform`에 푸시했다. [PR #73](https://github.com/2026-sbh-delta/delta-network-gateway/pull/73)의 base는 `main`이며 새 환경 카드, 완료 상태 전환, 안내 제거와 가독성 변경을 검토 범위로 한다.
- 로컬의 46개 fixture 회귀, 빌드와 형식 검사 결과는 위 U2 기록을 따른다. 원격 CI/리뷰/병합 결과는 PR의 실제 검사와 main Git 이력을 기준으로 확인한다. workflow 경로 필터가 UI 변경을 포함함을 확인했다.
- 각 커밋 전에 `git diff --cached --check`를 실행한다. SSH 서명은 현재 설정된 공개키에 한정한 임시 allowed-signers 파일로 검증하며 전역 Git/SSH 신뢰 설정을 변경하지 않는다.
- GitHub 연결 도구의 PR 생성은 HTTP403(`Resource not accessible by integration`)으로 거부됐다. 저장소의 기존 GitHub CLI 인증으로 동일 브랜치 PR 부재를 확인한 후 PR #73을 생성했다. 새로운 계정 권한이나 인증 범위를 부여하지 않았다.
- 게시 기록은 `docs/README.md`, 이 문서와 웹 구현 기록을 함께 갱신한다. 최종 검증 자료는 ignored `platform/web/test-results/ui-pr-publication.json`에 기록한다.
- 병합 전 최종 PR head의 CI, 리뷰/미해결 스레드 및 mergeable 상태를 확인하고 해당 head SHA를 지정해 squash한다. 강제 병합/검사 우회/직접 main 푸시는 사용하지 않는다. 병합 후 원격 main SHA와 로컬 main/작업 트리를 확인한다.
- 원본 운영 번들 전체 복구, 실제 운영 배포, Terraform Plan/Apply와 고객 자원 변경은 이번 Git 게시에 포함하지 않는다.

### Operation: UI 배포 승인과 운영 기능 보존 (2026-11-07 KST)

- 사용자가 `배포도 해줭`으로 UI 운영 반영을 승인했다. 대상 소스는 main squash 커밋 `3767fd3236591a60bbe4c6860e4c41c6af7dcc09`다. main CI 20개 job의 completed/success를 확인한 뒤 배포했다.
- 기본 `platform-release.yml`은 API/Worker 이미지와 migration까지 교체한다. 현재 운영 번들에는 main에 없는 마이그레이션/팀 파이프라인 UI가 있어 전체 이미지를 그대로 교체하지 않는다.
- 배포 설계: 현재 운영 정적 자산과 컨테이너를 기준으로, main의 CSS 변경과 정확한 주소 안내 제거만 반영하는 UI 배포를 준비한다. 기존 API/Worker 코드, 환경 설정, DB, 운영의 추가 정적 모듈과 기능을 보존한다.
- 사전 확인: 운영 HTML/자산과 서비스/환경 조회 결과, 실제 이미지/Compose/웹 경로를 보존한다. 적용 직전에 기준 해시가 달라지면 중단하고 다시 준비한다.
- 변경 범위: CSS 가독성/배치 및 환경 카드의 주소 안내 표시만 변경한다. 기존 JS의 나머지 동작과 추가 모듈을 보존하고 새 API/인프라/IAM/DB 변경은 하지 않는다.
- 검증: 기준 운영 번들에 변경을 적용한 로컬 브라우저 검사, 배포 후 실제 HTTPS readiness/자산 해시/주소 문구/기존 탐색 및 서비스/환경 보존을 확인한다. 실제 고객 빌드/배포/이전은 검증을 위해 실행하지 않는다.
- Rollback: 기존 이미지와 HTML/자산, 실행 설정을 보관하고 API 또는 웹 파일만 이전 상태로 복구한다. Worker/DB를 중지하거나 데이터를 되돌리지 않는다.
- 준비 초기에 이전 프로필의 AWS SSO 갱신을 시도했으나 아래 사전 검증에서 올바른 Workload 프로필의 유효한 인증을 확인했다. 이 준비 단계에서는 운영 변경을 실행하지 않았다.


Operation 사전 검증과 구체 배포 설계 (2026-11-07 17:37 KST):

- `gh run view 42354324590 --json status,conclusion,headSha,jobs`: main `3767fd32`의 20개 job 전부 success, workflow completed/success를 확인했다.
- 앞서 연 `sbh-platform`은 예전 계정 `723225040786`의 프로필이었다. 현재 플랫폼은 Workload `616723161368`이며 기존 `sbh-delta-workload`의 `sts get-caller-identity`가 성공했다. 기존 포털도 같은 Workload 계정을 표시했다. 새 인증/권한 부여 없이 이전 로그인 대기만 종료했다.
- SSM으로 실제 서버 `i-0e5df7881a7de8fbd`의 API 이미지/현재 Compose/웹 경로와 설정 해시를 읽기 전용으로 확인했다. API는 readonly rootfs이며 기존 Wazuh bind mount를 사용하지만 Compose에 해당 mount가 없었다. 기존 런타임 구성을 그대로 복제하며 그 mount도 유지한다.
- 구체 배포: 기존 이미지와 API 설정을 유지하고 기존 웹 전체를 보관한 새 UI 디렉터리를 `/app/web/dist:ro`로 연결한다. API 컨테이너만 교체하고 이전 컨테이너는 정지 상태로 보존한다. 현재 Compose에는 API의 기존 mount와 새 readonly 웹 mount만 추가하며 렌더링된 나머지 설정이 일치하는지 적용 전에 검증한다. Worker/DB의 ID/시작 시각/환경 설정과 기존 env 파일 해시를 보호한다.
- 실제 운영 정적 자산 6개를 전부 유지하고 주소 표시 문단 3곳과 클라우드 표시 class 한 곳만 원본 JS의 정확한 바이트 범위에서 수정했다. 그 외 JS가 원본과 동일함을 역변환으로 확인했다. main의 CSS 변경과 반응형 규칙을 추가 CSS로 적용한다. 기존 추가 마이그레이션/RDS 모듈의 내용은 변경하지 않는다.
- `node .runtime/ui-release-20261107/prepare-ui.mjs`: JS 파싱/범위와 SHA256 검증 통과. 결과 HTML SHA256 `da04d7835aefe36c761a176c2aa68d84cf8b10df2825077557bc2dcc5963b7c6`.
- `PLAYWRIGHT_BROWSERS_PATH=/private/tmp/delta-ui-playwright npx --no-install playwright test --config=../.runtime/ui-release-20261107/playwright.config.mjs --output=../.runtime/ui-release-20261107/test-results-final`: 실제 운영 번들에 UI만 적용한 로컬 fixture 4개 통과(1.3초). 1440px/390px 카드/주소 안내 제거/14px 이상 보조 글자/44px 버튼/가로 넘침 없음, 마이그레이션/대시보드 탐색, 기존 3단계 진행과 쓰기 요청 0개를 확인했다. 초기 2개 실패는 이전 소스의 6단계와 대시보드 문구를 기대한 시험 오류여서 실제 최신 화면 계약으로 바로잡은 후 재실행했다.
- 두 폭의 실제 PNG를 열어 배치와 줄바꿈을 확인했다. 시험은 로컬 공개 응답 fixture이며 운영 배포 검증은 아직 아니다. UI/Worker/DB의 실제 전체 동작을 재실행한 결과로 확대하지 않는다.
- 배포 전 stage는 운영의 현재 symlink, Compose/설정/자산 해시와 API/Worker/DB ID를 확인한다. apply 직전도 다시 검사하며 변경되면 중단한다. API readiness 실패 시 이전 컨테이너와 Compose로 자동 복구한다. 성공 후 별도 rollback 명령도 이전 API만 복원한다.


실제 UI 배포 결과 (2026-11-07 17:38 KST):

- 첫 stage SSM `942516f9-4e18-4ad4-94f0-a847e6dea9d7`는 운영 API ID 변경을 감지해 적용 전에 종료했다. 당시 API/Worker/DB/Compose를 변경하지 않았다. 최신 API `7dd098217f49`와 새 deploy.env 해시를 다시 조회했고, HTTPS 자산 6개가 시험 기준과 동일함을 확인했다. 이 새 기준을 별도 릴리스에 보존했다.
- 최종 릴리스: `ui-readability-3767fd32-da04d7835aef-7dd098217f49`. 비공개 배포 파일은 `s3://delta-platform-artifacts-616723161368/releases/ui-readability-3767fd32-da04d7835aef-7dd098217f49/ui-release.tar.gz`이며 SHA256은 `3794800f62441497aa1084eea95cacd5913fe4ddd30042e57a9b574908261013`다. 자격 증명/DB 데이터는 포함하지 않았다.
- `aws --profile sbh-delta-workload --region ap-northeast-2 ssm send-command --cli-input-json file://.runtime/ui-release-20261107/stage-request-2.json`: SSM `ae9da282-1ea3-4940-8e19-f3dbd98d4980` Success. 기존 웹의 추가 파일 12개, API 설정과 Wazuh bind mount를 보존했고 새 정적 파일 2개/기존 index 수정만 준비했다. 렌더링된 Compose의 변경이 API mount 추가뿐임을 확인했다.
- 같은 프로필의 `ssm send-command --cli-input-json file://.runtime/ui-release-20261107/apply-request.json`: SSM `78db073c-2319-45b7-b9d0-93e8c3ec6fba` Success/종료 0. 실제 실행 17:37:46~17:38:03 KST, 약 17초. 새 API `4d8238d6b56e` healthy/readiness ready. 이 시간은 배포 명령 실행 시간이며 전체 서비스 중단 시간 측정값은 아니다.
- 백엔드 이미지 `sha256:1cacdf34fa3d65f13f5ff6b307ed3ebc96a0a609c48c4a68a95ae8201ca2aba8`와 API의 Env/Cmd/Entrypoint/User/WorkingDir/Labels/Healthcheck 및 기존 HostConfig를 보존하고 readonly 웹 bind만 추가했다. API를 한 번 재생성했으며 API 코드/이미지 교체, migration, Terraform Plan/Apply와 IAM 변경은 실행하지 않았다.
- Worker `40042b070149`와 DB `2c20a93d56b8`의 ID, 실행 상태, 시작 시각, 환경 해시와 모든 기존 env 파일 해시가 유지됐다. Compose SHA256은 API mount를 반영한 `0784604eadc8d2ad4ad311530c9fbb2508714bd03a94bcd47e4c09eb254751c1`다. Worker/DB를 재시작하지 않았다.
- `python3 .runtime/ui-release-20261107/capture-live.py .runtime/ui-release-20261107/after`와 후속 SHA256/응답 비교: 실제 HTTPS readiness ready, 변경 HTML/JS/CSS 3개가 시험한 파일과 일치, 기존 자산 6개 전부 동일한 bytes/HTTP200, 기존 서비스 ID/이름/완료 상태와 환경 3개의 전체 조회 응답 보존을 확인했다. 토큰/쿠키는 저장하거나 출력하지 않았다.
- 배포 후 CUA에서 서비스 목록 새로고침까지 관찰했으나 native Chrome의 접근성 연결/timeout과 browser access host unavailable로 상세 화면 시각 검증을 완료하지 못했다. 실제 운영 상세 화면 캡처를 PASS로 기록하지 않는다. 배포 전 같은 bytes의 1440px/390px PNG 확인 및 로컬 4개 검증과 실제 HTTPS 파일 검증을 구분한다.
- Git: 소스 main은 PR #73 squash `3767fd32`이며 해당 원격 CI는 20개 success다. 이번 후속 운영 기록의 커밋/푸시 상태는 실제 Git 이력을 따른다. 전체 main 이미지 릴리스는 실행하지 않았으며 운영에만 있는 최신 API/팀 파이프라인/추가 UI 기능을 그대로 보존했다.

Runbook / Rollback:

- 운영 UI 경로: `/opt/delta-platform/releases/ui-readability-3767fd32-da04d7835aef-7dd098217f49/web`를 API의 `/app/web/dist`에 readonly로 연결한다. 기존 `/opt/delta-platform/current` symlink와 이미지 pin, env 파일은 유지한다.
- 릴리스 디렉터리에 `web-before`, `compose-before.yaml`, `compose-after.yaml`, `ui-manifest.json`, `expected-host.json`, `stage.json`, `applied.json`, `operate-ui.py`를 root 전용으로 보관했다. 이전 API 컨테이너는 `delta-platform-api-1-before-ui-readability-3767fd32-da04d7835aef-7dd098217f49` 이름으로 정지 보존한다. 웹 디렉터리 내부만 앱이 읽도록 허용했다.
- readonly SSM 점검은 실제 현재 API와 Worker/DB의 ID/상태/시작 시각, 설정 해시, `/health/ready` 및 HTTPS index/자산 해시를 기준으로 한다. API UI 복구에 Worker/DB stop, 데이터 복원, 스키마 migration이나 인프라 Apply를 사용하지 않는다.
- 실제 rollback 명령은 위 VM의 SSM에서 `python3 /opt/delta-platform/releases/ui-readability-3767fd32-da04d7835aef-7dd098217f49/operate-ui.py rollback /opt/delta-platform/releases/ui-readability-3767fd32-da04d7835aef-7dd098217f49`다. 현재 API/Compose가 이번 릴리스와 일치할 때만 이전 API와 Compose를 복원하며 Worker/DB 보존 및 readiness를 다시 확인한다. 적용 후 운영 rollback을 실행하지 않았으므로 실제 rollback 완료로 기록하지 않는다.
- 이후 전체 배포는 main에 없는 운영 소스를 먼저 통합해야 한다. 기본 release workflow가 현재 Worker/백엔드/추가 UI를 이전 코드로 교체하지 않도록 이 릴리스의 보존 범위와 현재 소스를 확인한다.


### U3: 마이그레이션 안내와 보안 카드 숨김 (2026-11-07 KST)

- 사용자 `GuardDuty 보안 기능 대응 포함해서 UI에서 숨겨`는 기존 승인된 UI 안내 제거 작업의 후속 범위 지시다. 앞서 지정한 마이그레이션 상세 화면을 기준으로 GuardDuty/Wazuh 카드 전체와 대상별 배포 주소/사설망 안내를 화면 표시에서 제외한다.
- Ideation: 해당 화면을 보는 서비스 운영자가 이전 대상과 결과에 집중하도록 부가 안내를 숨긴다. 성공 기준은 데스크톱/모바일에서 지정 카드와 주소 안내가 보이지 않고 기존 이전 선택/실행 기록/차분 조회/단계 결과를 사용할 수 있는 것이다. 백엔드 보안 기능, 탐지 설정, 저장된 데이터와 실제 이전 실행은 범위 밖이다.
- Inception / Design: 별도 운영 모듈의 안정된 `data-delta-rds-evidence` marker에 CSS 표시 규칙을 적용한다. 운영 JS의 마이그레이션 주소 문단 한 곳만 AST로 확인해 제외하고 다른 바이트를 보존한다. 정식 웹 소스에도 같은 CSS를 추가한다. 사용자 지시를 기존 U1/U2 UI 변경의 후속 구현 승인으로 적용한다.
- Implementation Plan: 현재 HTTPS 자산/읽기 응답과 컨테이너 정보를 보존하고, 작은 프론트 변경을 만든 뒤 실제 운영 번들의 변경 전후를 1440px/390px에서 비교한다. 기존 readonly 웹 bind의 host 디렉터리에 정적 파일을 준비하고 index를 원자적으로 바꾼다. API/Worker/DB 컨테이너 재생성은 필요하지 않다.
- 초기 구현 상태: 소스 CSS와 제한된 정적 패치 생성기를 구현했고 `npm run build` 및 대상 CSS/TSX 형식 검사를 실행해 통과했다. 기존 Workload SSO 갱신도 성공했다. 운영 적용과 Git 게시/PR은 아직 실행하지 않았다. 실제 브라우저 검증 결과는 후속 기록으로 갱신한다.


U3 Construction 검증 (2026-11-07 KST):

- `npm run build`: TypeScript/Vite 통과. `node --check deploy/scripts/hide-ui-notices.mjs`와 대상 파일 Prettier 검사, `git diff --check`도 통과했다.
- `node deploy/scripts/hide-ui-notices.mjs .runtime/ui-hide-20261107/baseline .runtime/ui-hide-20261107/patched`: 현재 운영 주소 문단 템플릿 1개만 제외하고 나머지 JS를 역변환으로 대조했다. 원래 7개 정적 자산을 모두 보존하고 CSS 1개와 JS 1개를 추가하며 index만 수정한다. 실제 모듈/보안 기능은 유지하고 카드 표시만 숨긴다.
- `PLAYWRIGHT_BROWSERS_PATH=/private/tmp/delta-ui-playwright npx --no-install playwright test --config=../.runtime/ui-hide-20261107/playwright.config.mjs --output=../.runtime/ui-hide-20261107/test-results-complete`: 2 passed(1.6초). 운영 응답 fixture와 동일 운영 번들의 변경 전후를 1440px/390px에서 비교했다. 카드와 Wazuh 버튼, 대상별 주소 링크/사설망 안내가 UI에서 숨겨지고 기존 단계/보고서 텍스트와 대상 선택/시작 버튼 활성 조건이 동일하며 쓰기 요청 0개임을 확인했다.
- 초기 검사 실패는 실제 클래스/역할을 잘못 지정한 선택자와 innerText/textContent 줄바꿈 비교 때문이었다. 실제 DOM을 읽어 검사 선택자와 비교 방식을 수정한 뒤 위 결과를 얻었다. 해당 실패를 PASS로 재분류하지 않는다.
- 사전 읽기에서 기존 계획 `b793774f-be4a-4774-b3bf-95d7bbe808e5`의 실행 조회는 HTTP409 `plan_target_changed`(환경 연결 변경)를 반환했다. 그 응답도 변경 전후 동일하게 유지했으며 이번 표시 수정으로 차분 보호 규칙이나 계획을 변경하지 않는다.
- 운영 적용은 아직이며, API/Worker/DB ID와 시작 시각, 이미지/환경/기존 mount 및 기존 env 파일 해시를 보호한 상태에서 index를 원자적으로 교체할 준비를 마쳤다. 이전 index 복구 도구와 backup을 릴리스 디렉터리에 보관한다.


U3 Operation 실제 결과 (2026-11-07 19:16:43 KST):

- `ssm send-command --cli-input-json file://.runtime/ui-hide-20261107/apply-request.json` 및 `get-command-invocation`: SSM `6f793ef7-cb35-4f0b-bd9c-fbfa7a3e6c18` Success/종료 0. 기존 readonly 웹 bind의 host 디렉터리에서 새 자산을 먼저 준비한 뒤 index만 원자적으로 교체했다.
- 릴리스는 `/opt/delta-platform/releases/ui-hide-20261107-4d12f69972eb`다. 비공개 배포 파일 `s3://delta-platform-artifacts-616723161368/releases/ui-hide-20261107-4d12f69972eb/release.tar.gz`의 SHA256은 `3746ff1d7ff2b63f0af7beea5d1adc1f97c11b972cfe17924863437c38435c83`이다. 원래 index와 적용 스크립트/manifest/적용 결과를 같은 디렉터리에 보존했다.
- API `4d8238d6b56e`, Worker `40042b070149`, DB `2c20a93d56b8`의 ID/시작 시각/이미지/환경/mount와 기존 env 파일 해시가 적용 전후 동일하다. 재시작 0회이며 보안 서비스 설정, API 코드, 이미지, DB/마이그레이션과 Terraform/IAM을 변경하지 않았다.
- `capture-live.py .runtime/ui-hide-20261107/after`와 해시/응답 대조: 실제 HTTPS readiness ready, 새 HTML/JS/CSS 3개가 시험한 bytes와 일치, 기존 자산 7개가 모두 동일한 bytes/HTTP200, 서비스 ID/완료 상태 및 환경 3개의 전체 응답이 유지됐다.
- CUA native Chrome에서 정확한 운영 migration 경로를 새로고침했다. GuardDuty 카드/Wazuh 실행 버튼과 대상별 주소 안내가 접근성 화면에서 제외됐고 기존 AWS 출발/GCP/온프레미스 선택, 전체 이전 계획, 실제 환경 차이 표 및 파이프라인 결과가 계속 표시되는 것을 확인했다. 실제 화면 스크린샷도 열어 확인했다. 이번에는 운영 상세 화면 표시를 실제로 확인했으며 과거 U2의 연결 장애 기록과 구분한다.
- 최종 소스의 정적 패치 생성기를 새 출력 경로로 다시 실행해 배포한 manifest와 모든 변경 파일이 동일함을 확인했다. Git 게시/PR/병합 상태는 실제 Git 이력을 따른다.
- Rollback은 SSM에서 `python3 /opt/delta-platform/releases/ui-hide-20261107-4d12f69972eb/apply.py /opt/delta-platform/releases/ui-hide-20261107-4d12f69972eb rollback`을 실행해 이전 index만 복원한다. 현재 컨테이너와 UI 해시가 이번 릴리스에 일치해야 하며 API/Worker/DB 재시작이나 데이터 복원이 필요 없다. 실제 rollback은 실행하지 않았다.
