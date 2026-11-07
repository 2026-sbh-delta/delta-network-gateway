import { test, expect, type Page } from "@playwright/test";

// Public response fixtures only; these tests never access the deployed service.
const service = {
  id: "completed-application",
  name: "Migration verification",
  repository_url: "https://github.com/example/notes",
  source_ref: "main",
  build_context: "frontend",
  dockerfile_path: "frontend/Dockerfile",
  container_port: 3000,
  healthcheck_path: "/healthz",
  created_at: "2026-11-07T00:00:00Z",
};
const environments = [
  {
    id: "fixture-aws",
    name: "AWS",
    cloud: "aws",
    region: "ap-northeast-2",
    account_id: "012345678901",
    gcp_project: null,
    version: "85df8a637c8791f452708bf231a9b40a9a32dab9",
    instances: [{ id: "i-0123456789abcdef0", name: "app", zone: null }],
    database_profile_id: "aws-postgres-new",
    runtime_status: "active",
    service_url: "http://10.80.60.60:8088/",
  },
  {
    id: "fixture-onprem",
    name: "온프레미스",
    cloud: "onprem",
    region: "onprem-local",
    account_id: null,
    gcp_project: null,
    version: "85df8a637c8791f452708bf231a9b40a9a32dab9",
    instances: [
      { id: "onprem-fixture-runtime", name: "team-onprem", zone: null },
    ],
    database_profile_id: "onprem-postgres-prepared",
    runtime_status: "active",
    service_url: "http://10.40.0.10:32081/",
  },
  {
    id: "fixture-gcp",
    name: "GCP",
    cloud: "gcp",
    region: "asia-northeast3",
    account_id: null,
    gcp_project: "fixture-project",
    version: "85df8a637c8791f452708bf231a9b40a9a32dab9",
    instances: [
      {
        id: "fixture-gcp-instance",
        name: "delta-app-fixture",
        zone: "asia-northeast3-a",
      },
    ],
    database_profile_id: "gcp-postgres-prepared",
    runtime_status: "active",
    service_url: "http://10.32.2.7:8088/",
  },
];

async function fixture(
  page: Page,
  options: {
    running?: boolean;
    environmentError?: boolean;
    empty?: boolean;
    removed?: boolean;
  } = {},
) {
  let status = options.running ? "running" : "succeeded";
  let environmentError = options.environmentError ?? false;
  let mutations = 0;
  function current() {
    return {
      ...service,
      application: {
        status,
        target: { cloud: "aws" },
        steps: [],
        error: null,
        service_url: environments[0].service_url,
      },
    };
  }
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (route.request().method() !== "GET") mutations++;
    if (path === "/auth/session")
      return route.fulfill({
        json: {
          login_id: "presentation-fixture",
          csrf_token: "fixture-only",
          expires_at: "2099-01-01T00:00:00Z",
          presentation_mode: true,
        },
      });
    if (path === "/services")
      return route.fulfill({ json: { items: [current()], next_cursor: null } });
    if (path === `/services/${service.id}`)
      return route.fulfill({ json: current() });
    if (path === `/services/${service.id}/environments`) {
      if (environmentError)
        return route.fulfill({
          status: 503,
          json: {
            error: { code: "fixture_error", message: "환경 조회 실패" },
          },
        });
      return route.fulfill({
        json: {
          items: options.empty
            ? []
            : environments.map((environment, index) => ({
                ...environment,
                runtime_status: options.removed
                  ? ["active", "removing", "removed"][index]
                  : environment.runtime_status,
              })),
        },
      });
    }
    if (path.endsWith("/observability"))
      return route.fulfill({
        status: 503,
        json: {
          error: { code: "fixture_unavailable", message: "시험용 관측 응답" },
        },
      });
    return route.fulfill({ json: { items: [], next_cursor: null } });
  });
  return {
    complete() {
      status = "succeeded";
    },
    recover() {
      environmentError = false;
    },
    get mutations() {
      return mutations;
    },
  };
}

for (const width of [1440, 390]) {
  test(`${width}px 완료 앱은 최신 배포 환경 카드를 표시하고 주소 안내를 제외한다`, async ({
    page,
  }, info) => {
    const io = await fixture(page);
    await page.setViewportSize({ width, height: 960 });
    await page.goto(`/#/overview/${service.id}`);
    const panel = page.getByRole("region", { name: "배포 환경", exact: true });
    await expect(panel.getByRole("article")).toHaveCount(3);
    await expect(panel.getByRole("heading", { level: 3 })).toHaveText([
      "AWS",
      "GCP",
      "On-Premise",
    ]);
    for (const card of await panel.getByRole("article").all()) {
      await expect(card.locator("img")).toBeVisible();
      await expect
        .poll(() =>
          card
            .locator("img")
            .evaluate(
              (image: HTMLImageElement) =>
                image.complete && image.naturalWidth > 0,
            ),
        )
        .toBe(true);
    }
    await expect(panel.getByText("012345678901")).toBeVisible();
    await expect(panel.getByText("fixture-project")).toBeVisible();
    await expect(panel.getByText("team-onprem")).toBeVisible();
    await expect(panel.getByText("aws-postgres-new")).toBeVisible();
    await expect(panel.getByText("asia-northeast3-a")).toBeVisible();
    await expect(panel.getByRole("link")).toHaveCount(0);
    await expect(panel).not.toContainText("서비스 주소 열기");
    await expect(panel).not.toContainText("사설 주소");
    for (const environment of environments)
      await expect(panel).not.toContainText(environment.service_url);
    await expect(
      page.getByRole("region", { name: "자동 배포 진행" }),
    ).toHaveCount(0);
    await expect(page.locator(".release-summary")).toHaveCount(0);
    await expect(page.locator(".breadcrumbs strong")).toHaveText("배포 환경");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: info.outputPath(`deployed-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });

    await panel.getByRole("button", { name: "이관 진행", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`#/migration/${service.id}$`));
    await page.goBack();
    await expect(panel.getByRole("article")).toHaveCount(3);
    await panel.getByRole("button", { name: "모니터링", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`#/monitoring/${service.id}$`));
    expect(io.mutations).toBe(0);
  });
}

test("배포 완료를 조회하면 새로고침 없이 환경 카드로 전환한다", async ({
  page,
}) => {
  const io = await fixture(page, { running: true });
  await page.goto(`/#/overview/${service.id}`);
  const progress = page.getByRole("region", { name: "자동 배포 진행" });
  await expect(progress).toBeVisible();
  await expect(page.locator(".release-summary")).toHaveCount(0);
  io.complete();
  const panel = page.getByRole("region", { name: "배포 환경", exact: true });
  await expect(panel.getByRole("article")).toHaveCount(3, { timeout: 7000 });
  await expect(progress).toHaveCount(0);
  await expect(page.locator(".breadcrumbs strong")).toHaveText("배포 환경");
  expect(io.mutations).toBe(0);
});

test("환경 조회 오류를 미연결로 표시하지 않고 자동 재조회로 복구한다", async ({
  page,
}) => {
  const io = await fixture(page, { environmentError: true });
  await page.goto(`/#/overview/${service.id}`);
  const panel = page.getByRole("region", { name: "배포 환경", exact: true });
  await expect(panel.getByRole("alert")).toContainText("환경 조회 실패");
  await expect(panel.getByText("연결된 배포 환경이 없습니다.")).toHaveCount(0);
  io.recover();
  await expect(panel.getByRole("article")).toHaveCount(3, { timeout: 15000 });
  await expect(panel.getByRole("alert")).toHaveCount(0);
  expect(io.mutations).toBe(0);
});

test("환경 미연결을 표시하면서 기존 이동 버튼은 유지한다", async ({ page }) => {
  await fixture(page, { empty: true });
  await page.goto(`/#/overview/${service.id}`);
  const panel = page.getByRole("region", { name: "배포 환경", exact: true });
  await expect(panel.getByText("연결된 배포 환경이 없습니다.")).toBeVisible();
  await expect(panel.getByRole("article")).toHaveCount(0);
  await expect(
    panel.getByRole("button", { name: "이관 진행", exact: true }),
  ).toBeEnabled();
  await expect(
    panel.getByRole("button", { name: "모니터링", exact: true }),
  ).toBeEnabled();
});

test("배포 등록과 앱 정리 중 및 삭제 상태를 구분한다", async ({ page }) => {
  await fixture(page, { removed: true });
  await page.goto(`/#/overview/${service.id}`);
  const panel = page.getByRole("region", { name: "배포 환경", exact: true });
  await expect(panel.getByText("배포 등록됨", { exact: true })).toHaveCount(1);
  await expect(panel.getByText("앱 정리 중", { exact: true })).toHaveCount(1);
  await expect(panel.getByText("앱 삭제됨", { exact: true })).toHaveCount(1);
});
