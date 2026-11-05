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
  series:
    status === "ok" && value !== null
      ? [
          { at: "2026-11-03T23:45:00Z", value: value * 0.6 },
          { at: "2026-11-03T23:49:00Z", value: value * 0.9 },
          { at: "2026-11-04T00:00:00Z", value },
        ]
      : [],
});
function snapshot(
  env: ReturnType<typeof environment>,
  degraded = false,
  migration: Record<string, unknown> | null = null,
) {
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
    migration,
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
    const selected = services.items.find(
      (item) => path === `/services/${item.id}`,
    );
    if (selected) return route.fulfill({ json: selected });
    const envMatch = path.match(/^\/services\/(svc-[ab])\/environments$/);
    if (envMatch)
      return route.fulfill({
        status: 200,
        json: {
          items:
            envMatch[1] === "svc-a"
              ? [
                  environment("env-a", "운영"),
                  environment("env-b", "검증"),
                ].concat([
                  environment("env-c", "복제 차단"),
                  environment("env-d", "전환 완료"),
                  {
                    ...environment("env-onprem", "온프레미스"),
                    cloud: "onprem",
                    region: "onprem-lab",
                  },
                ])
              : [environment("env-c", "운영")],
        },
      });
    const dataMatch = path.match(/^\/services\/(svc-[ab])\/observability$/);
    if (dataMatch) {
      const envId = url.searchParams.get("environment_id") ?? "env-a";
      if (envId === "env-onprem") {
        const value = snapshot({
          ...environment(envId, "온프레미스"),
          cloud: "onprem",
          region: "onprem-lab",
        });
        return route.fulfill({
          status: 200,
          json: {
            ...value,
            infra: {
              status: "no_data",
              message: "수집 확인 필요",
              instances: [
                {
                  id: "onprem-" + "a".repeat(32),
                  name: "onprem-test",
                  state: "reporting",
                  cpu_percent: metric("ok", 25),
                  memory_percent: metric("no_data", null),
                  disk_percent: metric("ok", 38),
                },
              ],
            },
          },
        });
      }
      const degraded = envId === "env-b";
      const migration =
        envId === "env-b"
          ? {
              workflow_id: "workflow-stale",
              run_id: "run-stale",
              stage: "synchronizing",
              lag_bytes: null,
              validation: "pending",
              observed_at: "2026-11-04T00:00:00Z",
              status: "stale",
            }
          : envId === "env-c"
            ? {
                workflow_id: "workflow-blocked",
                run_id: "run-blocked",
                stage: "fencing_source",
                lag_bytes: 16384,
                validation: "pending",
                observed_at: "2026-11-04T00:01:00Z",
                status: "blocked",
              }
            : envId === "env-d"
              ? {
                  workflow_id: "workflow-cleaned",
                  run_id: "run-cleaned",
                  stage: "cleaned",
                  lag_bytes: 0,
                  validation: "passed",
                  observed_at: "2026-11-04T00:02:00Z",
                  status: "completed",
                }
              : null;
      return route.fulfill({
        status: 200,
        json: snapshot(
          environment(
            envId,
            (
              {
                "env-a": "운영",
                "env-b": "검증",
                "env-c": "복제 차단",
                "env-d": "전환 완료",
              } as Record<string, string>
            )[envId] ?? "운영",
          ),
          degraded,
          migration,
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
  await page.getByRole("button", { name: "메뉴 열기" }).click();
  await page.getByRole("button", { name: "모니터링", exact: true }).click();
  await page.getByRole("button", { name: /주문 서비스 example/ }).click();
  await expect(
    page.getByRole("heading", { name: "대시보드", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("23", { exact: true })).toBeVisible();
  await expect(page.getByText("test warning")).toBeVisible();
  await expect(page.getByText("GET /orders")).toBeVisible();
  await expect(
    page.getByRole("img", { name: "요청 수 선택 기간 추이" }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("dashboard-healthy.png"),
    fullPage: true,
  });
  await page
    .getByRole("group", { name: "실행 환경" })
    .getByRole("button", { name: "검증", exact: true })
    .click();
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
  await page
    .getByRole("group", { name: "실행 환경" })
    .getByRole("button", { name: "온프레미스", exact: true })
    .click();
  const vm = page.locator(".obs-instance");
  await expect(vm).toContainText("수집 중");
  await expect(vm).toContainText("CPU 25%");
  await expect(vm).toContainText("디스크 38%");
  await expect(vm).toContainText("데이터 없음");
  await expect(vm).not.toContainText("running");
  await expect(
    page.getByText(
      "수신 상태는 Collector의 보고 여부이며 VM 전원 상태와 다릅니다.",
    ),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("onprem-mobile.png"),
    fullPage: true,
  });
});

test("DB 이전의 stale·blocked·cleaned 상태와 WAL 바이트를 표시한다", async ({
  page,
}, info) => {
  await installBoundary(page);
  await page.goto("/");
  await page.getByLabel("아이디", { exact: true }).fill("browser-fixture");
  await page.getByLabel("비밀번호", { exact: true }).fill("test-only");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.getByRole("button", { name: "메뉴 열기" }).click();
  await page.getByRole("button", { name: "모니터링", exact: true }).click();
  await page.getByRole("button", { name: /주문 서비스 example/ }).click();

  const migration = page.locator(".obs-migration");
  await expect(
    page.getByText("DB 이전 실행 없음", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("group", { name: "실행 환경" })
    .getByRole("button", { name: "검증", exact: true })
    .click();
  await expect(migration).toContainText("오래된 상태");
  await expect(migration).toContainText("변경 동기화 중");
  await expect(migration).toContainText("검증: 대기");
  await expect(migration).toContainText("미반영 WAL: 정보 없음");
  await expect(migration).toContainText("마지막 갱신");

  await page
    .getByRole("group", { name: "실행 환경" })
    .getByRole("button", { name: "복제 차단", exact: true })
    .click();
  await expect(migration).toContainText("차단됨");
  await expect(migration).toContainText("원본 쓰기 차단 중");
  await expect(migration).toContainText("16,384 바이트");

  await page
    .getByRole("group", { name: "실행 환경" })
    .getByRole("button", { name: "전환 완료", exact: true })
    .click();
  await expect(migration).toContainText("실행 단계 완료");
  await expect(migration).toContainText("DB 전환·복제 정리 완료");
  await expect(migration).toContainText("검증: 통과");
  await expect(migration).toContainText("미반영 WAL: 0 바이트");
  await expect(migration).not.toContainText("서비스 이전 완료");
  await page.screenshot({
    path: info.outputPath("migration-cleaned-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: info.outputPath("migration-cleaned-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("프로젝트 대시보드는 프로젝트 선택 없이 현재 프로젝트의 지표를 표시한다", async ({
  page,
}) => {
  await installBoundary(page);
  await page.goto("/#/monitoring/svc-a");
  await page.getByLabel("아이디", { exact: true }).fill("browser-fixture");
  await page.getByLabel("비밀번호", { exact: true }).fill("test-only");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page.getByText("23", { exact: true })).toBeVisible();
  await expect(page.locator("main").getByRole("combobox")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "주문 서비스" })).toBeVisible();
  await page
    .getByRole("group", { name: "실행 환경" })
    .getByRole("button", { name: "검증", exact: true })
    .click();
  await expect(page.locator(".obs-metric").first()).toContainText(
    "데이터 없음",
  );
  await expect(page).toHaveURL(/#\/monitoring\/svc-a$/);
  await page.reload();
  await expect(page.getByRole("link", { name: "주문 서비스" })).toBeVisible();
  await expect(page.locator("main").getByRole("combobox")).toHaveCount(0);
});

test("환경 미연결 상태도 지표 구조를 유지하며 같은 프로젝트에서 재조회할 수 있다", async ({
  page,
}, info) => {
  await installBoundary(page);
  let queries = 0;
  await page.route("**/api/v1/services/svc-a/environments", (route) => {
    queries++;
    return route.fulfill({ json: { items: [] } });
  });
  await page.goto("/#/monitoring/svc-a");
  await page.getByLabel("아이디", { exact: true }).fill("browser-fixture");
  await page.getByLabel("비밀번호", { exact: true }).fill("test-only");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(
    page.getByText("실행 환경 연결이 필요합니다", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".obs-metric")).toHaveCount(3);
  await expect(page.locator(".obs-metric .obs-value")).toHaveText([
    "—",
    "—",
    "—",
  ]);
  await expect(page.getByRole("img", { name: /선택 기간 추이/ })).toHaveCount(
    0,
  );
  await expect(page.locator("main").getByRole("combobox")).toHaveCount(0);
  const before = queries;
  await page.getByRole("button", { name: "지금 새로고침" }).click();
  await expect.poll(() => queries).toBeGreaterThan(before);
  await expect(
    page.getByRole("button", { name: "지금 새로고침" }),
  ).toBeEnabled();
  await page.screenshot({
    path: info.outputPath("dashboard-empty-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: info.outputPath("dashboard-empty-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("link", { name: "서비스 개요로 이동 →" }).click();
  await expect(page).toHaveURL(/#\/overview\/svc-a$/);
});

test("기간 변경은 현재 프로젝트와 환경을 유지하고 이전 기간의 값을 숨긴다", async ({
  page,
}) => {
  await installBoundary(page);
  let release: (() => void) | undefined;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    "**/api/v1/services/svc-a/observability?**",
    async (route) => {
      const url = new URL(route.request().url());
      if (url.searchParams.get("minutes") !== "30") return route.fallback();
      await pending;
      const value = snapshot(environment("env-a", "운영"));
      value.window.minutes = 30;
      value.metrics.requests.value = 46;
      return route.fulfill({ json: value });
    },
  );
  await page.goto("/#/monitoring/svc-a");
  await page.getByLabel("아이디", { exact: true }).fill("browser-fixture");
  await page.getByLabel("비밀번호", { exact: true }).fill("test-only");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page.getByText("23", { exact: true })).toBeVisible();
  await page
    .getByRole("group", { name: "조회 기간" })
    .getByRole("button", { name: "30분", exact: true })
    .click();
  await expect(page.getByText("23", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("status")).toContainText(
    "관측 데이터를 불러오는 중",
  );
  release!();
  await expect(page.getByText("46", { exact: true })).toBeVisible();
  await expect(
    page
      .getByRole("group", { name: "실행 환경" })
      .getByRole("button", { name: "운영", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page).toHaveURL(/#\/monitoring\/svc-a$/);
});
