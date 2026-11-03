import { test, expect, type Page } from "@playwright/test";

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("아이디", { exact: true }).fill("test-team");
  await page
    .getByLabel("비밀번호", { exact: true })
    .fill("local-integration-test-only");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "내 서비스", exact: true }),
  ).toBeVisible();
}
async function register(page: Page, name: string, dockerfile = "Dockerfile") {
  await page
    .getByRole("button", { name: "+ 서비스 등록", exact: true })
    .click();
  await page.getByLabel("서비스 이름", { exact: true }).fill(name);
  await page
    .getByLabel("공개 GitHub 저장소", { exact: true })
    .fill("https://github.com/example/orders");
  await page.getByLabel("Dockerfile 경로").fill(dockerfile);
  await page
    .getByRole("button", { name: "등록하고 상세 보기", exact: true })
    .click();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
}

test("로그인 → 등록 → 실제 DB·Worker 검사 → 새로고침 → 로그아웃", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.screenshot({ path: info.outputPath("login.png"), fullPage: true });
  await login(page);
  await register(page, "검증용 주문 서비스");
  await page.getByRole("button", { name: "소스 확인", exact: true }).click();
  await expect(
    page.getByText("소스 확인 완료", { exact: true }).first(),
  ).toBeVisible({ timeout: 15000 });
  await expect(
    page.getByText("소스 확인 성공은 실행 검사·배포 성공을 뜻하지 않습니다.", {
      exact: false,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("desktop-result.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(
    page.getByRole("button", { name: /검증용 주문 서비스/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /검증용 주문 서비스/ }).click();
  await expect(
    page.getByText("소스 확인 완료", { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "워크스페이스 로그인" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("잘못된 Dockerfile은 실패 이유를 표시하고 새 검사 가능", async ({
  page,
}) => {
  await login(page);
  await register(page, "경로 실패 검증", "MissingDockerfile");
  await page.getByRole("button", { name: "소스 확인", exact: true }).click();
  await expect(
    page.getByText("확인 실패", { exact: true }).first(),
  ).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("path_not_found", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "소스 확인", exact: true }),
  ).toBeEnabled();
});

test("모바일 화면과 잘못된 로그인", async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByLabel("아이디", { exact: true }).fill("test-team");
  await page.getByLabel("비밀번호", { exact: true }).fill("wrong");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("ID 또는 비밀번호");
  await page
    .getByLabel("비밀번호", { exact: true })
    .fill("local-integration-test-only");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "내 서비스", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("mobile-workspace.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("소스 커밋 빌드 → digest 보존 → 로그를 텍스트로 표시", async ({
  page,
}, info) => {
  await login(page);
  await register(page, "빌드 검증 서비스");
  await expect(
    page.getByRole("button", { name: "이 커밋 빌드" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "소스 확인", exact: true }).click();
  await expect(page.getByRole("button", { name: "이 커밋 빌드" })).toBeEnabled({
    timeout: 15000,
  });
  await page.getByRole("button", { name: "이 커밋 빌드" }).click();
  await expect(page.getByText("빌드 완료", { exact: true })).toBeVisible({
    timeout: 15000,
  });
  await expect(page.locator(".build-result")).toContainText("@sha256:");
  await page
    .getByRole("button", { name: "이 이미지 실행 확인", exact: true })
    .click();
  await expect(page.getByText("실행 확인 통과", { exact: true })).toBeVisible({
    timeout: 15000,
  });
  await expect(page.locator(".sandbox-result")).toContainText(
    "보안 검사 미실행",
  );
  await expect(page.locator(".sandbox-result")).toContainText("@sha256:");
  await page.getByRole("button", { name: "빌드 로그 보기" }).click();
  await expect(page.locator(".build-logs")).toContainText("<script>");
  expect(
    await page.evaluate(
      () => (window as unknown as Record<string, unknown>).untrustedBuildLog,
    ),
  ).toBeUndefined();
  await page.screenshot({
    path: info.outputPath("build-result.png"),
    fullPage: true,
  });
  await page.reload();
  await page.getByRole("button", { name: /빌드 검증 서비스/ }).click();
  await expect(page.locator(".sandbox-result")).toContainText("@sha256:");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: info.outputPath("build-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("빌드 실패는 이미지 성공으로 표시하지 않는다", async ({ page }) => {
  await login(page);
  await register(page, "빌드 실패 서비스");
  await page.getByRole("button", { name: "소스 확인", exact: true }).click();
  await expect(page.getByRole("button", { name: "이 커밋 빌드" })).toBeEnabled({
    timeout: 15000,
  });
  await page.getByRole("button", { name: "이 커밋 빌드" }).click();
  await expect(page.getByText("빌드 실패", { exact: true })).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByText("build_failed", { exact: true })).toBeVisible();
  await expect(page.locator(".build-result")).toHaveCount(0);
});

for (const cancel of [false, true]) {
  test(
    cancel
      ? "실행 중단 후 태스크 정리 결과"
      : "앱 응답 실패는 실행 통과로 표시하지 않는다",
    async ({ page }) => {
      await login(page);
      await register(page, cancel ? "중단 검증 서비스" : "실행 실패 서비스");
      await page
        .getByRole("button", { name: "소스 확인", exact: true })
        .click();
      await expect(
        page.getByRole("button", { name: "이 커밋 빌드" }),
      ).toBeEnabled({ timeout: 15000 });
      await page.getByRole("button", { name: "이 커밋 빌드" }).click();
      await expect(
        page.getByRole("button", { name: "이 이미지 실행 확인", exact: true }),
      ).toBeEnabled({ timeout: 15000 });
      await page
        .getByRole("button", { name: "이 이미지 실행 확인", exact: true })
        .click();
      if (cancel)
        await page
          .getByRole("button", { name: "실행 중단", exact: true })
          .click();
      await expect(page.locator(".sandbox-panel .status")).toHaveText(
        cancel ? "중단 완료" : "실행 확인 실패",
        { timeout: 15000 },
      );
      await expect(page.locator(".sandbox-result")).toHaveCount(0);
      await expect(
        page.getByRole("button", { name: "이 이미지 실행 확인", exact: true }),
      ).toBeEnabled();
    },
  );
}
