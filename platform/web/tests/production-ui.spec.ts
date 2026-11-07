import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { prepareBaseline } from "../scripts/production-ui.mjs";

const baseline = prepareBaseline();
const beforeHTML = fs.readFileSync(
  path.join(baseline.directory, "index.html"),
  "utf8",
);
test.afterAll(() =>
  fs.rmSync(baseline.directory, { recursive: true, force: true }),
);
const service = {
  id: "fixture-app",
  name: "기존 운영 서비스",
  repository_url: "https://github.com/example/app",
  source_ref: "main",
  build_context: ".",
  dockerfile_path: "Dockerfile",
  container_port: 8080,
  healthcheck_path: "/health",
  created_at: "2026-11-07T00:00:00Z",
  latest_source_check: null,
  application: {
    status: "succeeded",
    target: { cloud: "aws" },
    steps: [],
    error: null,
  },
};
const environment = {
  id: "fixture-aws",
  name: "AWS",
  cloud: "aws",
  region: "ap-northeast-2",
  account_id: "012345678901",
  version: "a".repeat(40),
  instances: [{ id: "i-fixture", name: "app" }],
  runtime_status: "active",
  service_url: "https://example.invalid/",
};
const board = {
  run_id: "fixture123",
  options: { branch: "main", commit: "a".repeat(40) },
  started_at: 1794000000,
  outcome: "verified",
  nodes: ["build", "sast"].map((id) => ({
    id,
    label: id,
    owner: "fixture",
    state: "pass",
    summary: "기존 실행 결과",
    started_at: 1794000000,
    ended_at: 1794000010,
    detail: {},
    decision: null,
    round: 1,
    history: [],
  })),
};

async function fixture(page: Page, unavailable = false) {
  const writes: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/v1/**", async (route) => {
    const api = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (route.request().method() !== "GET") {
      writes.push(api);
      return route.fulfill({
        status: 409,
        json: { error: { message: "조회 전용 검사" } },
      });
    }
    if (api === "/auth/session")
      return route.fulfill({
        json: {
          login_id: "fixture",
          presentation_mode: true,
          csrf_token: "fixture-only",
          expires_at: "2099-01-01T00:00:00Z",
        },
      });
    if (api === "/services")
      return route.fulfill({ json: { items: [service], next_cursor: null } });
    if (api === `/services/${service.id}`)
      return route.fulfill({ json: service });
    if (api.endsWith("/environments"))
      return route.fulfill({ json: { items: [environment] } });
    if (api === "/pipeline/runs")
      return route.fulfill(
        unavailable
          ? {
              status: 503,
              json: {
                error: {
                  code: "fixture",
                  message: "파이프라인 연결 확인이 필요합니다.",
                },
              },
            }
          : { json: { runs: [board] } },
      );
    if (api === "/pipeline/runs/fixture123")
      return route.fulfill({ json: board });
    if (api.startsWith("/pipeline/runs/fixture123/log/"))
      return route.fulfill({
        json: { text: "fixture Jenkins log", job: "fixture", build: 1 },
      });
    if (api === "/pipeline/options")
      return route.fulfill({
        json: {
          repo: service.repository_url,
          branch: "main",
          branches: ["main"],
          commits: [{ sha: "a".repeat(40), message: "fixture" }],
          release_targets: [],
          cutover_configured: false,
        },
      });
    return route.fulfill({ json: { items: [], next_cursor: null } });
  });
  return { writes, errors };
}

for (const width of [1440, 390]) {
  test(`${width}px retains the operating overview and adds a pipeline with history and logs`, async ({
    page,
  }, info) => {
    const state = await fixture(page);
    await page.setViewportSize({ width, height: 960 });
    await page.route("http://127.0.0.1:5182/", (route) =>
      route.fulfill({ contentType: "text/html", body: beforeHTML }),
    );
    await page.goto(`/#/overview/${service.id}`);
    await expect(
      page.getByRole("heading", { name: service.name, exact: true }),
    ).toBeVisible();
    await expect(page.getByText("i-fixture", { exact: true })).toBeVisible();
    const before = await page.locator("main").innerText();
    const palette = await page
      .locator("html")
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    await page.unroute("http://127.0.0.1:5182/");
    await page.reload();
    await expect(
      page.getByRole("heading", { name: service.name, exact: true }),
    ).toBeVisible();
    await expect(page.getByText("i-fixture", { exact: true })).toBeVisible();
    expect(await page.locator("main").innerText()).toEqual(before);
    expect(
      await page
        .locator("html")
        .evaluate((el) => getComputedStyle(el).backgroundColor),
    ).toBe(palette);
    await page.getByRole("button", { name: "메뉴 열기", exact: true }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "파이프라인", exact: true })
      .click();
    await expect(
      page.getByRole("combobox", { name: "실행 기록", exact: true }),
    ).toHaveValue("fixture123");
    await expect(page.locator(".production-pipeline .lf-node")).toHaveCount(5);
    await page.locator(".production-pipeline .lf-node").first().click();
    await expect(
      page.getByRole("complementary", { name: "단계 상세" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "로그", exact: false }).click();
    await expect(page.getByRole("region", { name: "실행 로그" })).toContainText(
      "fixture Jenkins log",
    );
    await page.screenshot({
      path: info.outputPath(`pipeline-${width}.png`),
      fullPage: true,
    });
    await page.getByRole("button", { name: "상세 닫기" }).click();
    await page.getByRole("button", { name: "새 실행", exact: true }).click();
    const form = page.getByRole("region", { name: "새 실행", exact: true });
    await expect(form.locator("select").first()).toBeVisible();
    const bounds = await form.boundingBox();
    expect(bounds?.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    await expect(
      form.getByRole("button", { name: "취소", exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: info.outputPath(`start-form-${width}.png`),
      fullPage: true,
    });
    await form.getByRole("button", { name: "취소", exact: true }).click();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.reload();
    await expect(
      page.getByRole("combobox", { name: "실행 기록", exact: true }),
    ).toHaveValue("fixture123");
    await page.getByRole("button", { name: "메뉴 열기", exact: true }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "배포 환경", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: service.name, exact: true }),
    ).toBeVisible();
    await expect(page.getByText("i-fixture", { exact: true })).toBeVisible();
    expect(await page.locator("main").innerText()).toEqual(before);
    expect(state.writes).toEqual([]);
    expect(state.errors).toEqual([]);
  });
}

test("pipeline connection failures remain visible and never start a run", async ({
  page,
}) => {
  const state = await fixture(page, true);
  await page.goto("/#/pipeline");
  await expect(page.locator(".production-pipeline")).toContainText(
    "파이프라인 연결 확인이 필요합니다.",
  );
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("pipeline resumes and refreshes CSRF after repeated cached-page restores", async ({
  page,
}) => {
  const state = await fixture(page);
  let sessions = 0;
  await page.route("**/api/v1/auth/session", (route) => {
    sessions++;
    return route.fulfill({
      json: {
        login_id: "fixture",
        presentation_mode: true,
        csrf_token: `fixture-restored-${sessions}`,
        expires_at: "2099-01-01T00:00:00Z",
      },
    });
  });
  await page.goto("/#/pipeline");
  const history = page.getByRole("combobox", {
    name: "실행 기록",
    exact: true,
  });
  await expect(history).toHaveValue("fixture123");
  for (let restoration = 0; restoration < 2; restoration++) {
    const before = sessions;
    await page.evaluate(() => {
      window.dispatchEvent(
        new PageTransitionEvent("pagehide", { persisted: true }),
      );
    });
    await expect(page.locator(".production-pipeline .flow-page")).toHaveCount(
      0,
    );
    await page.evaluate(() => {
      window.dispatchEvent(
        new PageTransitionEvent("pageshow", { persisted: true }),
      );
    });
    await expect(history).toHaveValue("fixture123");
    expect(sessions).toBeGreaterThan(before);
    await expect(page.locator(".production-pipeline .flow-page")).toHaveCount(
      1,
    );
  }
  const starts: {
    csrf: string | undefined;
    platform: string | undefined;
    key: string | undefined;
  }[] = [];
  await page.route("**/api/v1/pipeline/runs", (route) => {
    if (route.request().method() === "GET") return route.fallback();
    const headers = route.request().headers();
    starts.push({
      csrf: headers["x-csrf-token"],
      platform: headers["x-platform-request"],
      key: headers["idempotency-key"],
    });
    return route.fulfill({
      status: 409,
      json: {
        error: { message: "Fixture start stays within the browser test" },
      },
    });
  });
  await page.getByRole("button", { name: "새 실행", exact: true }).click();
  await page
    .getByRole("region", { name: "새 실행", exact: true })
    .getByRole("button", { name: "실행", exact: true })
    .click();
  await expect(page.locator(".pl-error")).toContainText(
    "Fixture start stays within the browser test",
  );
  expect(starts).toHaveLength(1);
  expect(starts[0].csrf).toBe(`fixture-restored-${sessions}`);
  expect(starts[0].platform).toBe("1");
  expect(starts[0].key).toMatch(/^[0-9a-f-]{36}$/);
  expect(state.writes).toEqual([]);
  expect(state.errors).toEqual([]);
});
