import { test, expect, type Page } from "@playwright/test";

const services = ["주문 프로젝트", "유지할 프로젝트"].map((name, i) => ({
  id: `delete-${i}`,
  name,
  repository_url: `https://github.com/example/project-${i}`,
  source_ref: "main",
  build_context: ".",
  dockerfile_path: "Dockerfile",
  container_port: 8080,
  healthcheck_path: "/health",
  created_at: "2026-11-05T00:00:00Z",
}));
async function fixture(page: Page, blocked = false) {
  let items = [...services];
  const requests: { path: string; body: unknown; csrf?: string }[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    if (path === "/auth/session")
      return route.fulfill({
        json: {
          login_id: "fixture",
          csrf_token: "delete-fixture-csrf",
          expires_at: "2099-01-01T00:00:00Z",
        },
      });
    if (path === "/services")
      return route.fulfill({ json: { items, next_cursor: null } });
    if (path.endsWith("/delete")) {
      requests.push({
        path,
        body: request.postDataJSON(),
        csrf: request.headers()["x-csrf-token"],
      });
      if (blocked)
        return route.fulfill({
          status: 409,
          json: {
            error: {
              code: "service_busy",
              message: "실행 중인 작업이 있습니다. 종료 후 다시 시도하세요.",
            },
          },
        });
      items = items.filter((s) => path !== `/services/${s.id}/delete`);
      return route.fulfill({ status: 204 });
    }
    return route.fulfill({
      status: 404,
      json: { error: { code: "not_found", message: "fixture" } },
    });
  });
  return {
    requests,
    unblock() {
      blocked = false;
    },
  };
}
for (const width of [1440, 390])
  test(`${width}px 프로젝트 삭제는 이름 확인·취소·영구 반영을 지원한다`, async ({
    page,
  }, info) => {
    const io = await fixture(page);
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    const opener = page.getByRole("button", {
      name: "주문 프로젝트 프로젝트 삭제",
      exact: true,
    });
    await opener.click();
    const modal = page.getByRole("dialog", { name: "프로젝트를 삭제할까요?" });
    await expect(modal).toBeVisible();
    await expect(
      modal.getByRole("button", { name: "취소", exact: true }),
    ).toBeFocused();
    await expect(
      modal.getByRole("button", { name: "프로젝트 삭제", exact: true }),
    ).toBeDisabled();
    await modal
      .getByLabel("확인을 위해 프로젝트 이름을 입력하세요")
      .fill("다른 이름");
    await expect(
      modal.getByRole("button", { name: "프로젝트 삭제", exact: true }),
    ).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(modal).toHaveCount(0);
    await expect(opener).toBeFocused();
    expect(io.requests).toHaveLength(0);
    await opener.click();
    await modal
      .getByLabel("확인을 위해 프로젝트 이름을 입력하세요")
      .fill("주문 프로젝트");
    await page.screenshot({
      path: info.outputPath(`project-delete-${width}.png`),
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await modal
      .getByRole("button", { name: "프로젝트 삭제", exact: true })
      .click();
    await expect(modal).toHaveCount(0);
    await expect(opener).toHaveCount(0);
    await expect(page.getByRole("status")).toContainText(
      "주문 프로젝트 프로젝트를 삭제했습니다.",
    );
    expect(io.requests).toEqual([
      {
        path: "/services/delete-0/delete",
        body: { confirmation_name: "주문 프로젝트" },
        csrf: "delete-fixture-csrf",
      },
    ]);
    await page.reload();
    await expect(opener).toHaveCount(0);
    await expect(
      page.getByRole("button", {
        name: "유지할 프로젝트 프로젝트 삭제",
        exact: true,
      }),
    ).toBeVisible();
    await page.getByRole("button", { name: "메뉴 열기" }).click();
    await expect(
      page
        .getByRole("combobox", { name: "작업 서비스 선택" })
        .locator("option", { hasText: "주문 프로젝트" }),
    ).toHaveCount(0);
  });

test("삭제 거부 시 목록을 유지하고 오류를 보여주며 재시도할 수 있다", async ({
  page,
}) => {
  const io = await fixture(page, true);
  await page.goto("/");
  await page
    .getByRole("button", { name: "주문 프로젝트 프로젝트 삭제", exact: true })
    .click();
  const modal = page.getByRole("dialog");
  await modal
    .getByLabel("확인을 위해 프로젝트 이름을 입력하세요")
    .fill("주문 프로젝트");
  await modal
    .getByRole("button", { name: "프로젝트 삭제", exact: true })
    .click();
  await expect(modal.getByRole("alert")).toContainText("실행 중인 작업");
  await expect(page.locator(".service-list-item")).toHaveCount(2);
  await expect(
    modal.getByRole("button", { name: "프로젝트 삭제", exact: true }),
  ).toBeEnabled();
  io.unblock();
  await modal
    .getByRole("button", { name: "프로젝트 삭제", exact: true })
    .click();
  await expect(modal).toHaveCount(0);
  await expect(page.locator(".service-list-item")).toHaveCount(1);
  expect(io.requests).toHaveLength(2);
});

test("삭제 처리 중 중복 요청과 닫기를 막고 오래된 목록 응답을 무시한다", async ({
  page,
}) => {
  const io = await fixture(page);
  await page.goto("/");
  await expect(page.locator(".service-list-item")).toHaveCount(2);
  let releaseList!: () => void;
  const pendingList = new Promise<void>((resolve) => {
    releaseList = resolve;
  });
  await page.route("**/api/v1/services", async (route) => {
    await pendingList;
    return route.fulfill({ json: { items: services, next_cursor: null } });
  });
  await page.getByRole("button", { name: "새로고침", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "조회 중…", exact: true }),
  ).toBeVisible();
  let releaseDelete!: () => void;
  const pendingDelete = new Promise<void>((resolve) => {
    releaseDelete = resolve;
  });
  await page.route("**/api/v1/services/delete-0/delete", async (route) => {
    await pendingDelete;
    return route.fallback();
  });
  await page
    .getByRole("button", { name: "주문 프로젝트 프로젝트 삭제", exact: true })
    .click();
  const modal = page.getByRole("dialog");
  await modal
    .getByLabel("확인을 위해 프로젝트 이름을 입력하세요")
    .fill("주문 프로젝트");
  await modal
    .getByRole("button", { name: "프로젝트 삭제", exact: true })
    .click();
  await expect(
    modal.getByRole("button", { name: "삭제 중…", exact: true }),
  ).toBeDisabled();
  await expect(
    modal.getByRole("button", { name: "취소", exact: true }),
  ).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(modal).toBeVisible();
  releaseDelete();
  await expect(modal).toHaveCount(0);
  await expect(page.locator(".service-list-item")).toHaveCount(1);
  const loaded = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/v1/services",
  );
  releaseList();
  await loaded;
  await expect(page.locator(".service-list-item")).toHaveCount(1);
  expect(io.requests).toHaveLength(1);
});

test("실제 자원 정리 완료까지 프로젝트를 유지하고 실패 후 재시도한다", async ({
  page,
}) => {
  await fixture(page);
  let status = "running";
  let requests = 0;
  await page.route("**/api/v1/services/delete-0/delete", (route) => {
    requests++;
    return route.fulfill({
      status: 202,
      json: { status: "queued", run: { id: "cleanup" } },
    });
  });
  await page.route("**/api/v1/services/delete-0/deletion", (route) =>
    route.fulfill({
      json: {
        status,
        run:
          status === "failed"
            ? {
                error: {
                  code: "cleanup_failed",
                  message: "VM 정리에 실패했습니다. 다시 시도하세요.",
                },
              }
            : null,
      },
    }),
  );
  await page.goto("/");
  await page
    .getByRole("button", { name: "주문 프로젝트 프로젝트 삭제", exact: true })
    .click();
  const modal = page.getByRole("dialog");
  await modal
    .getByLabel("확인을 위해 프로젝트 이름을 입력하세요")
    .fill("주문 프로젝트");
  await modal
    .getByRole("button", { name: "프로젝트 삭제", exact: true })
    .click();
  await expect(modal.getByRole("status")).toContainText("실제 자원을 정리");
  await expect(page.locator(".service-list-item")).toHaveCount(2);
  await expect(
    modal.getByRole("button", { name: "삭제 중…", exact: true }),
  ).toBeDisabled();
  status = "failed";
  await expect(modal.getByRole("alert")).toContainText("VM 정리에 실패");
  await expect(page.locator(".service-list-item")).toHaveCount(2);
  status = "deleted";
  await modal
    .getByRole("button", { name: "프로젝트 삭제", exact: true })
    .click();
  await expect(modal).toHaveCount(0);
  await expect(page.locator(".service-list-item")).toHaveCount(1);
  expect(requests).toBe(2);
});

test("새로고침 후에도 진행 중인 삭제 상태를 조회한다", async ({ page }) => {
  await fixture(page);
  await page.route("**/api/v1/services", (route) =>
    route.fulfill({
      json: {
        items: [{ ...services[0], deletion_requested: true }, services[1]],
        next_cursor: null,
      },
    }),
  );
  await page.route("**/api/v1/services/delete-0/deletion", (route) =>
    route.fulfill({ json: { status: "running", run: null } }),
  );
  await page.goto("/");
  await page
    .getByRole("button", { name: "주문 프로젝트 프로젝트 삭제", exact: true })
    .click();
  await expect(page.getByRole("dialog").getByRole("status")).toContainText(
    "실제 자원을 정리",
  );
  await page.getByRole("button", { name: "닫기", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".service-list-item")).toHaveCount(2);
});
