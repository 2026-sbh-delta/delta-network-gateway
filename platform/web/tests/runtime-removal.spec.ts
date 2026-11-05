import { expect, test, type Page } from "@playwright/test";

const service = {
  id: "app-1",
  name: "발표 앱",
  repository_url: "https://github.com/example/app",
  source_ref: "main",
  build_context: ".",
  dockerfile_path: "Dockerfile",
  container_port: 8080,
  healthcheck_path: "/health",
  created_at: "2026-11-05T00:00:00Z",
  latest_source_check: null,
};
const environment = {
  id: "onprem-1",
  name: "온프레미스 운영",
  cloud: "onprem",
  region: "onprem-lab",
  version: "v1",
  runtime_registered: true,
};

async function fixture(page: Page, configured = true) {
  let state = "active";
  let run: Record<string, unknown> | null = null;
  let hash = "a".repeat(64);
  let requests = 0;
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/v1", "");
    const answer = (json: unknown, status = 200) =>
      route.fulfill({ json, status });
    if (path === "/auth/session")
      return answer({
        login_id: "team",
        csrf_token: "test",
        expires_at: "2099-01-01T00:00:00Z",
      });
    if (path === "/services")
      return answer({ items: [service], next_cursor: null });
    if (path === "/services/app-1") return answer(service);
    if (path.endsWith("/environments")) return answer({ items: [environment] });
    if (path.endsWith("/removal")) {
      if (req.method() === "POST") {
        requests++;
        expect(req.postDataJSON().confirmation_name).toBe(service.name);
        expect(req.headers()["idempotency-key"]).toBeTruthy();
        if (req.postDataJSON().binding_hash !== hash)
          return answer(
            {
              error: { message: "삭제 대상이 변경됐습니다. 다시 확인하세요." },
            },
            409,
          );
        state = "removing";
        run = {
          id: "remove-1",
          service_id: service.id,
          kind: "runtime_remove",
          status: "queued",
          result: null,
          error: null,
        };
        return answer(run, 202);
      }
      return answer({
        configured,
        reason: configured
          ? null
          : "온프레미스 앱 삭제 실행기가 연결되지 않았습니다.",
        resources: [
          { kind: "Deployment", name: "frontend" },
          { kind: "Service", name: "frontend" },
        ],
        state,
        binding_hash: hash,
        run,
      });
    }
    if (path.endsWith("/observability"))
      return answer({
        service_id: service.id,
        environment,
        metrics: { app_up: { status: "ok", value: 1, collected_at: null } },
      });
    if (path === "/sandbox-targets") return answer({ items: [] });
    if (
      /\/(builds|source-checks|security-checks|sandbox-checks|dast-checks|workflows)$/.test(
        path,
      )
    )
      return answer({ items: [], next_cursor: null });
    return answer({ error: { message: "not found" } }, 404);
  });
  return {
    count: () => requests,
    change: () => {
      hash = "b".repeat(64);
    },
    fail: () => {
      run = {
        ...run,
        status: "failed",
        error: { message: "사설 경로 연결을 확인하세요." },
      };
    },
    finish: () => {
      state = "removed";
      run = {
        ...run,
        status: "succeeded",
        error: null,
        result: { runtime_status: "removed" },
      };
    },
  };
}

test("confirm, pending, failed retry, completion and reload", async ({
  page,
}) => {
  const control = await fixture(page);
  await page.goto("/#/overview/app-1");
  await page.getByRole("button", { name: "실행 앱 삭제", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Deployment · frontend")).toBeVisible();
  await expect(dialog.getByText(/DB·저장 볼륨·VM·k3s/)).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "실행 앱 삭제 확인" }),
  ).toBeDisabled();
  await dialog.getByRole("button", { name: "취소" }).click();
  expect(control.count()).toBe(0);
  await page.getByRole("button", { name: "실행 앱 삭제", exact: true }).click();
  await dialog
    .getByLabel("확인을 위해 서비스 이름을 입력하세요")
    .fill(service.name);
  await dialog.getByRole("button", { name: "실행 앱 삭제 확인" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(
    page.getByText("앱 삭제 대기 중", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "실행 앱 삭제", exact: true }),
  ).toBeDisabled();
  control.fail();
  await expect(page.getByText(/삭제 실패 ·/)).toBeVisible();
  await page.getByRole("button", { name: "앱 삭제 다시 시도" }).click();
  await dialog
    .getByLabel("확인을 위해 서비스 이름을 입력하세요")
    .fill(service.name);
  await dialog.getByRole("button", { name: "실행 앱 삭제 확인" }).click();
  control.finish();
  await expect(
    page.getByText("앱 삭제 완료 · DB·볼륨·서버와 이력은 보존했습니다."),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByText("앱 삭제 완료", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "실행 앱 삭제", exact: true }),
  ).toHaveCount(0);
  expect(control.count()).toBe(2);
});

test("changed preview does not silently change the approved deletion", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const control = await fixture(page);
  await page.goto("/#/overview/app-1");
  await page.getByRole("button", { name: "실행 앱 삭제", exact: true }).click();
  control.change();
  await page.waitForResponse(
    (response) =>
      response.url().endsWith("/removal") &&
      response.request().method() === "GET",
  );
  const dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("확인을 위해 서비스 이름을 입력하세요")
    .fill(service.name);
  await dialog.getByRole("button", { name: "실행 앱 삭제 확인" }).click();
  await expect(dialog.getByRole("alert")).toHaveText(
    "삭제 대상이 변경됐습니다. 다시 확인하세요.",
  );
  const box = await dialog.boundingBox();
  expect(box!.width).toBeLessThanOrEqual(390);
});

test("unconfigured execution is visibly unavailable", async ({ page }) => {
  await fixture(page, false);
  await page.goto("/#/overview/app-1");
  await expect(
    page.getByText("온프레미스 앱 삭제 실행기가 연결되지 않았습니다."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "실행 앱 삭제", exact: true }),
  ).toBeDisabled();
});
