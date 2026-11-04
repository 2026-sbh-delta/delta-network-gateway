import { test, expect, type Page } from "@playwright/test";

// Browser fixtures only: no production data or execution is created.
const services = [
  {
    id: "presentation-orders",
    name: "주문 서비스",
    repository_url: "https://github.com/example/orders",
    source_ref: "main",
    build_context: ".",
    dockerfile_path: "Dockerfile",
    container_port: 8080,
    healthcheck_path: "/health",
    created_at: "2026-11-04T00:00:00Z",
    latest_source_check: null,
  },
  {
    id: "presentation-payments",
    name: "결제 서비스",
    repository_url: "https://github.com/example/payments",
    source_ref: "release/v2",
    build_context: ".",
    dockerfile_path: "Dockerfile",
    container_port: 3000,
    healthcheck_path: "/health",
    created_at: "2026-11-04T00:00:00Z",
    latest_source_check: null,
  },
];
async function fixture(page: Page) {
  let loggedIn = true;
  let mutations = 0;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (route.request().method() !== "GET") mutations++;
    if (path === "/auth/logout") {
      loggedIn = false;
      return route.fulfill({ status: 204 });
    }
    if (path === "/auth/session")
      return route.fulfill({
        status: loggedIn ? 200 : 401,
        json: loggedIn
          ? {
              login_id: "presentation-fixture",
              expires_at: "2099-01-01T00:00:00Z",
              csrf_token: "fixture-only",
            }
          : { error: { code: "unauthorized", message: "fixture" } },
      });
    if (path === "/services")
      return route.fulfill({ json: { items: services, next_cursor: null } });
    const service = services.find((s) => path === `/services/${s.id}`);
    if (service) return route.fulfill({ json: service });
    if (path === "/sandbox-targets")
      return route.fulfill({ json: { items: [] } });
    if (
      /\/services\/[^/]+\/(source-checks|builds|workflows|environments|sandbox-checks|security-checks|dast-checks)$/.test(
        path,
      )
    )
      return route.fulfill({ json: { items: [], next_cursor: null } });
    return route.fulfill({
      status: 404,
      json: {
        error: { code: "not_found", message: "서비스를 찾을 수 없습니다." },
      },
    });
  });
  return {
    get mutations() {
      return mutations;
    },
  };
}

test("단계별 화면은 서비스를 보존하고 새로고침·뒤로가기를 지원한다", async ({
  page,
}, info) => {
  const boundary = await fixture(page);
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "내 서비스", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    animations: "disabled",
    path: info.outputPath("workspace-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: /주문 서비스 example/ }).click();
  await expect(page).toHaveURL(/#\/overview\/presentation-orders$/);
  await expect(
    page.getByRole("heading", { name: "주문 서비스", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    animations: "disabled",
    path: info.outputPath("service-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "메뉴 열기" }).click();
  await page.getByRole("button", { name: "빌드·검사", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "이 커밋 빌드" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("region", { name: "배포·이전 계획" }),
  ).toHaveCount(0);
  await page
    .getByRole("navigation", { name: "빌드와 검사 단계" })
    .getByRole("button", { name: /보안 검사/ })
    .click();
  await expect(page.locator(".security-panel")).toBeVisible();
  await expect(page.locator(".build-panel")).not.toBeVisible();
  await page.getByRole("button", { name: "메뉴 열기" }).click();
  await page.getByRole("button", { name: "이전 계획", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "배포·이전 계획" }),
  ).toBeVisible();
  await expect(page.locator(".build-panel")).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("region", { name: "배포·이전 계획" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "메뉴 열기" }).click();
  await expect(
    page.getByRole("button", { name: "이전 계획", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await page.keyboard.press("Escape");
  await page.goBack();
  await expect(page).toHaveURL(/#\/build\/presentation-orders$/);
  await expect(page.locator(".build-panel")).toBeVisible();
  await page.getByRole("button", { name: "메뉴 열기" }).click();
  await page
    .getByRole("combobox", { name: "작업 서비스 선택" })
    .selectOption("presentation-payments");
  await expect(
    page.getByRole("heading", { name: "결제 서비스", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "메뉴 열기" }).click();
  await page.getByRole("button", { name: "모니터링", exact: true }).click();
  await expect(page.getByLabel("서비스 선택", { exact: true })).toHaveValue(
    "presentation-payments",
  );
  await expect(
    page.getByText("이 서비스에 연결된 환경이 없습니다."),
  ).toBeVisible();
  expect(boundary.mutations).toBe(0);
});

for (const viewport of [
  { name: "desktop", width: 1440, height: 960 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`${viewport.name} 서랍은 키보드 포커스를 유지하고 닫기·선택·로그아웃을 지원한다`, async ({
    page,
  }, info) => {
    await fixture(page);
    await page.setViewportSize(viewport);
    await page.goto("/#/overview/presentation-orders");
    await expect(
      page.getByRole("heading", { name: "주문 서비스", exact: true }),
    ).toBeVisible();
    await page.screenshot({
      animations: "disabled",
      path: info.outputPath(`service-${viewport.name}.png`),
      fullPage: true,
    });
    const trigger = page.getByRole("button", { name: "메뉴 열기" });
    const drawer = page.getByRole("dialog", { name: "작업 공간 메뉴" });
    await expect(drawer).not.toBeVisible();
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(
      await page
        .locator(".shell-body")
        .evaluate((el) => el.getBoundingClientRect().left),
    ).toBe(0);
    await trigger.click();
    await expect(drawer).toBeVisible();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator("body")).toHaveCSS("overflow", "hidden");
    await page.screenshot({
      animations: "disabled",
      path: info.outputPath(`drawer-${viewport.name}.png`),
      fullPage: false,
    });
    for (let i = 0; i < 12; i++) {
      await page.keyboard.press("Tab");
      expect(
        await page.evaluate(() => !!document.activeElement?.closest("dialog")),
      ).toBe(true);
    }
    await page.keyboard.press("Escape");
    await expect(drawer).not.toBeVisible();
    await expect(trigger).toBeFocused();
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator("body")).not.toHaveCSS("overflow", "hidden");
    await trigger.click();
    await drawer.getByRole("button", { name: "메뉴 닫기" }).click();
    await expect(drawer).not.toBeVisible();
    await expect(trigger).toBeFocused();
    await trigger.click();
    await page.mouse.click(viewport.width - 10, 100);
    await expect(drawer).not.toBeVisible();
    await trigger.click();
    await page.setViewportSize({
      width: viewport.name === "desktop" ? 390 : 1440,
      height: 960,
    });
    await expect(drawer).toBeVisible();
    await page.setViewportSize(viewport);

    await drawer
      .getByRole("button", { name: "이전 계획", exact: true })
      .click();
    await expect(drawer).not.toBeVisible();
    await expect(
      page.getByRole("region", { name: "배포·이전 계획" }),
    ).toBeVisible();
    await expect(page.locator("#workspace-content")).toBeFocused();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await trigger.click();
    await drawer.getByRole("button", { name: "로그아웃", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "워크스페이스 로그인" }),
    ).toBeVisible();
  });
}

test("직접 연 서비스 오류를 표시하며 메뉴로 복구할 수 있다", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/#/build/missing-service");
  await expect(page.getByRole("alert")).toContainText(
    "서비스를 찾을 수 없습니다.",
  );
  await page
    .getByRole("button", { name: "← 서비스 목록", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "내 서비스", exact: true }),
  ).toBeVisible();
});

test("잘못된 경로는 서비스 목록으로 복구하며 API 경로로 전달하지 않는다", async ({
  page,
}) => {
  await fixture(page);
  const paths: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/"))
      paths.push(new URL(request.url()).pathname);
  });
  await page.goto("/#/build/..%2Fauth%2Flogout");
  await expect(
    page.getByRole("heading", { name: "내 서비스", exact: true }),
  ).toBeVisible();
  expect(paths).not.toContain("/api/v1/auth/logout");
  await page.goto("/#/migration/%E0%A4%A");
  await expect(
    page.getByRole("heading", { name: "내 서비스", exact: true }),
  ).toBeVisible();
});
