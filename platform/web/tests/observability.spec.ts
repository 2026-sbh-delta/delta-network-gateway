import { test, expect, type Page, type Route } from "@playwright/test";

// These fixtures terminate at the browser HTTP boundary and are test-only.
const services = {
  items: [
    {
      id: "svc-a",
      name: "주문 서비스",
      repository_url: "https://github.com/example/orders",
      source_ref: "main",
      build_context: ".",
      dockerfile_path: "Dockerfile",
      container_port: 8080,
      healthcheck_path: "/health",
      created_at: "2026-11-04T00:00:00Z",
    },
    {
      id: "svc-b",
      name: "결제 서비스",
      repository_url: "https://github.com/example/payments",
      source_ref: "main",
      build_context: ".",
      dockerfile_path: "Dockerfile",
      container_port: 8080,
      healthcheck_path: "/health",
      created_at: "2026-11-04T00:00:00Z",
    },
  ],
  next_cursor: null,
};
const environment = (id: string, name: string) => ({
  id,
  name,
  cloud: "aws",
  region: "ap-northeast-2",
  version: "test",
});
const metric = (
  status: "ok" | "no_data" | "stale" | "error",
  value: number | null,
) => ({
  status,
  value,
  unit: "",
  collected_at: status === "ok" ? "2026-11-04T00:00:00Z" : null,
  message: status === "ok" ? null : `${status} test fixture`,
  series: [],
});
function snapshot(env: ReturnType<typeof environment>, degraded = false) {
  return {
    service_id: "svc-a",
    environment: env,
    observed_at: "2026-11-04T00:00:00Z",
    window: {
      minutes: 15,
      start: "2026-11-03T23:45:00Z",
      end: "2026-11-04T00:00:00Z",
    },
    metrics: {
      requests: metric(degraded ? "no_data" : "ok", degraded ? null : 23),
      error_rate: metric(degraded ? "error" : "ok", degraded ? null : 1.25),
      p95_ms: metric(degraded ? "stale" : "ok", degraded ? null : 84),
      app_up: metric("ok", 1),
      db_up: metric("ok", 1),
    },
    infra: {
      status: "ok",
      message: null,
      instances: [
        {
          id: "vm-test",
          name: "app-test",
          state: "running",
          cpu_percent: metric("no_data", null),
          memory_percent: metric("error", null),
        },
      ],
    },
    logs: {
      status: "ok",
      message: null,
      items: [
        {
          timestamp: "2026-11-04T00:00:00Z",
          severity: "warning",
          message: "test warning",
          trace_id: "trace-test",
        },
        {
          timestamp: "2026-11-04T00:00:00Z",
          severity: "info",
          message: "filtered info",
          trace_id: null,
        },
      ],
    },
    trace: {
      status: "ok",
      message: null,
      id: "trace-test",
      duration_ms: 84,
      spans: [
        {
          span_id: "span-a",
          parent_id: null,
          name: "GET /orders",
          service: "orders-api",
          started_at: "2026-11-04T00:00:00Z",
          duration_ms: 84,
        },
        {
          span_id: "span-b",
          parent_id: "span-a",
          name: "SELECT orders",
          service: "postgres",
          started_at: "2026-11-04T00:00:00Z",
          duration_ms: 12,
        },
      ],
    },
    migration: null,
  };
}
async function installBoundary(page: Page) {
  let loggedIn = false;
  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    if (path === "/auth/session")
      return route.fulfill({
        status: loggedIn ? 200 : 401,
        json: loggedIn
          ? {
              login_id: "browser-fixture",
              expires_at: "2026-11-05T00:00:00Z",
              csrf_token: "test",
            }
          : { error: { code: "unauthorized", message: "test" } },
      });
    if (path === "/auth/login") {
      loggedIn = true;
      return route.fulfill({
        status: 200,
        json: {
          login_id: "browser-fixture",
          expires_at: "2026-11-05T00:00:00Z",
          csrf_token: "test",
        },
      });
    }
    if (path === "/services")
      return route.fulfill({ status: 200, json: services });
    const envMatch = path.match(/^\/services\/(svc-[ab])\/environments$/);
    if (envMatch)
      return route.fulfill({
        status: 200,
        json: {
          items:
            envMatch[1] === "svc-a"
              ? [environment("env-a", "운영"), environment("env-b", "검증")]
              : [environment("env-c", "운영")],
        },
      });
    const dataMatch = path.match(/^\/services\/(svc-[ab])\/observability$/);
    if (dataMatch) {
      const envId = url.searchParams.get("environment_id") ?? "env-a";
      const degraded = envId === "env-b";
      return route.fulfill({
        status: 200,
        json: snapshot(
          environment(envId, envId === "env-b" ? "검증" : "운영"),
          degraded,
        ),
      });
    }
    return route.fulfill({
      status: 404,
      json: { error: { code: "fixture_not_found", message: path } },
    });
  });
}

test("대시보드에서 환경을 바꾸고 누락·오류·오래된 값을 구분한다", async ({
  page,
}, info) => {
  await installBoundary(page);
  await page.goto("/");
  await page.getByLabel("아이디", { exact: true }).fill("browser-fixture");
  await page.getByLabel("비밀번호", { exact: true }).fill("test-only");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.getByRole("button", { name: "대시보드", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "서비스 대시보드" }),
  ).toBeVisible();
  await expect(page.getByText("23", { exact: true })).toBeVisible();
  await expect(page.getByText("test warning")).toBeVisible();
  await expect(page.getByText("GET /orders")).toBeVisible();
  await page.getByLabel("환경 선택").selectOption("env-b");
  await expect(
    page.getByText("데이터 없음 · no_data test fixture").first(),
  ).toBeVisible();
  await expect(
    page.getByText("오래된 데이터 · stale test fixture").first(),
  ).toBeVisible();
  await expect(
    page.getByText("조회 오류 · error test fixture").first(),
  ).toBeVisible();
  await expect(page.getByText("0", { exact: true })).toHaveCount(0);
  await page.screenshot({
    path: info.outputPath("observability-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: info.outputPath("observability-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
