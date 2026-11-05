import { test, expect, type Page } from "@playwright/test";
const target = {
  cloud: "aws",
  region: "ap-northeast-2",
  profile_id: "t3.small",
};
const database = {
  id: "demo-postgres",
  label: "시연 PostgreSQL",
  engine: "postgres",
  revision: "v1",
  clouds: ["aws"],
  modes: ["new", "existing"],
};
const base = {
  id: "application-1",
  name: "Delta Notes",
  repository_url: "https://github.com/2026-sbh-delta/delta-sample-app",
  source_ref: "main",
  build_context: "frontend",
  dockerfile_path: "frontend/Dockerfile",
  container_port: 3000,
  healthcheck_path: "/healthz",
  created_at: "2026-11-05T00:00:00Z",
};
async function fixture(page: Page, withDatabase = true) {
  const bodies: Record<string, unknown>[] = [];
  let registered = false;
  const service = {
    ...base,
    application: {
      status: "running",
      target,
      steps: [
        {
          key: "source_frontend",
          label: "프론트엔드 소스 확인",
          status: "running",
          detail: "소스 확인 중",
        },
      ],
      error: null,
    },
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let json: unknown = { items: [], next_cursor: null };
    if (path === "/auth/session")
      json = {
        login_id: "team",
        csrf_token: "csrf",
        expires_at: "2099-01-01T00:00:00Z",
      };
    else if (path === "/deployment-options")
      json = {
        items: [
          {
            ...target,
            label: "AWS",
            runtime: "VM",
            configured: true,
            reason: null,
            hosts: [],
            profiles: [
              {
                id: "t3.small",
                label: "t3.small",
                memory_mb: 2048,
                cpu: "2 vCPU",
                description: "FE·BE",
              },
            ],
          },
        ],
      };
    else if (path === "/application-databases")
      json = { items: withDatabase ? [database] : [] };
    else if (path === "/applications") {
      bodies.push(route.request().postDataJSON());
      registered = true;
      json = service;
    } else if (path === "/services")
      json = { items: registered ? [service] : [], next_cursor: null };
    else if (path === "/services/" + service.id) json = service;
    await route.fulfill({ json });
  });
  return { bodies, service };
}
for (const width of [1360, 390]) {
  test(`앱 등록은 FE·BE와 DB를 함께 보내고 새로고침 후 진행을 유지한다 ${width}`, async ({
    page,
  }) => {
    const io = await fixture(page);
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/");
    await page.getByRole("button", { name: "+ 서비스 등록" }).click();
    await expect(page.getByLabel("PostgreSQL 서버")).toHaveValue(
      "demo-postgres",
    );
    await page
      .getByRole("button", { name: "등록 및 배포", exact: true })
      .click();
    await expect.poll(() => io.bodies.length).toBe(1);
    expect(io.bodies[0]).toMatchObject({
      frontend_context: "frontend",
      backend_context: "backend",
      frontend_dockerfile: "frontend/Dockerfile",
      backend_dockerfile: "backend/Dockerfile",
      database: { profile_id: "demo-postgres", mode: "new" },
      deployment_target: target,
    });
    const progress = page.getByRole("region", { name: "자동 배포 진행" });
    await expect(progress).toBeVisible();
    await expect(progress.getByRole("status")).toHaveText("진행 중");
    await page.reload();
    await expect(progress).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  });
}
test("DB 연결이 없으면 가짜 배포를 시작하지 않는다", async ({ page }) => {
  const io = await fixture(page, false);
  await page.goto("/");
  await page.getByRole("button", { name: "+ 서비스 등록" }).click();
  await expect(
    page.getByText("운영자가 선택한 환경의 PostgreSQL 연결을 등록해야 합니다."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "등록 및 배포", exact: true }),
  ).toBeDisabled();
  expect(io.bodies).toHaveLength(0);
});
