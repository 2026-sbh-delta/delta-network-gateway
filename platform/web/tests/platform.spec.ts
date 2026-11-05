import { test, expect, type Page } from "@playwright/test";

for (const mode of ["재현", "없음", "중단"]) {
  test(`빌드·SAST → DAST ${mode} → 결과 유지`, async ({ page }, info) => {
    await login(page);
    const name = `DAST ${mode} 서비스`;
    await register(page, name);
    await page.getByRole("button", { name: "소스 확인", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "이 커밋 빌드" }),
    ).toBeEnabled({ timeout: 15000 });
    await page.getByRole("button", { name: "이 커밋 빌드" }).click();
    await page
      .getByRole("navigation", { name: "빌드와 검사 단계" })
      .getByRole("button", { name: /보안 검사/ })
      .click();
    await expect(
      page.getByRole("button", { name: "이 빌드의 코드 검사" }),
    ).toBeEnabled({ timeout: 15000 });
    await page.getByRole("button", { name: "이 빌드의 코드 검사" }).click();
    const panel = page.locator(".dast-panel");
    await expect(panel).toContainText("현재 검사 환경: AWS ECS 샌드박스");
    await expect(panel.getByLabel("DAST 실행 환경")).toHaveValue("aws");
    await expect(panel).toContainText(
      "GCP·온프레미스는 연결 상태에 따라 선택할 수 있습니다.",
    );
    await expect(
      panel.getByRole("button", { name: "이 빌드의 DAST 검사" }),
    ).toBeEnabled({ timeout: 15000 });
    await panel.getByRole("button", { name: "이 빌드의 DAST 검사" }).click();
    const expected =
      mode === "중단"
        ? "sandbox_cancelled"
        : mode === "없음"
          ? "DAST 미실행"
          : "취약점 재현";
    if (mode === "중단")
      await panel.getByRole("button", { name: "검사 중단" }).click();
    await expect(panel).toContainText(expected, { timeout: 15000 });
    if (mode !== "중단") {
      await expect(panel).toContainText("현재 선택한 빌드와 코드 검사 결과");
      await expect(panel).toContainText("실행 위치 AWS ECS");
    }
    await page.reload();
    await page
      .getByRole("navigation", { name: "빌드와 검사 단계" })
      .getByRole("button", { name: /보안 검사/ })
      .click();
    await expect(panel).toContainText(expected);
    if (mode !== "중단") {
      await panel.getByText("DAST 검사 근거", { exact: true }).click();
      await expect(panel).toContainText("DAST 근거 SHA-256");
    }
    await page.screenshot({
      path: info.outputPath("dast-desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: info.outputPath("dast-mobile.png"),
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}

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
  await expect(
    page.getByRole("heading", { name, exact: true, level: 1 }),
  ).toBeVisible();
  await page.getByRole("button", { name: "메뉴 열기" }).click();
  await page.getByRole("button", { name: "빌드·검사", exact: true }).click();
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
    page.getByRole("heading", {
      name: "검증용 주문 서비스",
      exact: true,
      level: 1,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("소스 확인 완료", { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "메뉴 열기" }).click();
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
    .getByRole("navigation", { name: "빌드와 검사 단계" })
    .getByRole("button", { name: /실행 확인/ })
    .click();
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
  await page
    .getByRole("navigation", { name: "빌드와 검사 단계" })
    .getByRole("button", { name: /이미지 빌드/ })
    .click();
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
  await page
    .getByRole("navigation", { name: "빌드와 검사 단계" })
    .getByRole("button", { name: /실행 확인/ })
    .click();
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

test("GCP 대상 선택은 요청과 결과에 유지되고 기존 AWS 결과는 바뀌지 않는다", async ({
  page,
}) => {
  let postedCloud: string | undefined;
  let startedRun: Record<string, unknown> | undefined;
  let postedDastCloud: string | undefined;
  let startedDast: Record<string, unknown> | undefined;
  const oldAwsRun = {
    id: "old-aws-run",
    service_id: "svc-old-aws",
    kind: "sandbox_check",
    external_id: null,
    status: "succeeded",
    current_step: null,
    attempt: 1,
    resolved_commit_sha: "a".repeat(40),
    created_at: "2026-11-04T00:00:00Z",
    result: {
      runtime_check: "passed",
      execution_cloud: "aws",
      runtime: "ecs",
      image: "example.test/app@sha256:" + "a".repeat(64),
      build_id: "old-build",
      container_port: 8080,
      healthcheck_path: "/health",
    },
    error: null,
  };
  const oldAwsDastRun = {
    ...oldAwsRun,
    id: "old-aws-dast-run",
    kind: "dast_check",
    result: {
      execution_cloud: "aws",
      runtime: "ecs",
      dast_check: "not_run",
      build_id: "old-build",
      security_check_id: "old-security",
      scanner_run_id: "old-scanner",
      dast_evidence_sha256: "b".repeat(64),
      dast_findings: [],
      findings_truncated: false,
      image: "example.test/app@sha256:" + "a".repeat(64),
    },
  };
  await page.route("**/api/v1/sandbox-targets", (route) =>
    route.fulfill({
      status: 200,
      json: {
        items: [
          { cloud: "aws", label: "AWS", runtime: "ECS", configured: true },
          {
            cloud: "gcp",
            label: "GCP",
            runtime: "Cloud Run",
            configured: true,
          },
          {
            cloud: "onprem",
            label: "온프레미스",
            runtime: "미연결",
            configured: false,
          },
        ],
      },
    }),
  );
  await page.route("**/api/v1/services/*/sandbox-checks", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as {
        build_id: string;
        execution_cloud: string;
      };
      postedCloud = body.execution_cloud;
      startedRun = {
        ...oldAwsRun,
        id: "new-gcp-run",
        status: "succeeded",
        result: {
          ...oldAwsRun.result,
          build_id: body.build_id,
          execution_cloud: body.execution_cloud,
          runtime: "cloud_run",
          revision: "rev-17",
          url: "https://sandbox.example.test",
        },
      };
      return route.fulfill({ status: 201, json: startedRun });
    }
    return route.fulfill({
      status: 200,
      json: { items: [oldAwsRun], next_cursor: null },
    });
  });
  await page.route("**/api/v1/sandbox-checks/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/events"))
      return route.fulfill({ status: 200, json: { items: [] } });
    return route.fulfill({ status: 200, json: startedRun ?? oldAwsRun });
  });
  await page.route("**/api/v1/services/*/dast-checks", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as {
        build_id: string;
        security_check_id: string;
        execution_cloud: string;
      };
      postedDastCloud = body.execution_cloud;
      startedDast = {
        ...oldAwsDastRun,
        id: "new-gcp-dast-run",
        status: "succeeded",
        result: {
          ...oldAwsDastRun.result,
          build_id: body.build_id,
          security_check_id: body.security_check_id,
          execution_cloud: body.execution_cloud,
          runtime: "cloud_run",
        },
      };
      return route.fulfill({ status: 201, json: startedDast });
    }
    return route.fulfill({
      status: 200,
      json: { items: [oldAwsDastRun], next_cursor: null },
    });
  });
  await page.route("**/api/v1/dast-checks/*", (route) =>
    route.fulfill({ status: 200, json: startedDast ?? oldAwsDastRun }),
  );

  await login(page);
  await register(page, "GCP 샌드박스 서비스");
  await page.getByRole("button", { name: "소스 확인", exact: true }).click();
  await expect(page.getByRole("button", { name: "이 커밋 빌드" })).toBeEnabled({
    timeout: 15000,
  });
  await page.getByRole("button", { name: "이 커밋 빌드" }).click();
  await page
    .getByRole("navigation", { name: "빌드와 검사 단계" })
    .getByRole("button", { name: /실행 확인/ })
    .click();
  await expect(
    page.getByRole("button", { name: "이 이미지 실행 확인", exact: true }),
  ).toBeEnabled({ timeout: 15000 });
  const panel = page.locator(".sandbox-panel");
  await expect(panel).toContainText("실행 위치: AWS ECS");
  await panel.getByLabel("샌드박스 실행 환경").selectOption("gcp");
  await expect(panel).toContainText("실행 위치: AWS ECS");
  await expect(
    panel.getByRole("button", { name: "이 이미지 실행 확인", exact: true }),
  ).toBeEnabled();
  await expect(
    panel.getByLabel("샌드박스 실행 환경").locator('option[value="onprem"]'),
  ).toHaveAttribute("disabled", "");
  await panel
    .getByRole("button", { name: "이 이미지 실행 확인", exact: true })
    .click();
  await expect.poll(() => postedCloud).toBe("gcp");
  await expect(panel).toContainText("실행 위치: GCP Cloud Run");
  await expect(panel.locator(".sandbox-result")).toContainText("GCP Cloud Run");
  await expect(panel.locator(".sandbox-result")).toContainText("rev-17");

  await page
    .getByRole("navigation", { name: "빌드와 검사 단계" })
    .getByRole("button", { name: /보안 검사/ })
    .click();
  await page.getByRole("button", { name: "이 빌드의 코드 검사" }).click();
  const dastPanel = page.locator(".dast-panel");
  await expect(dastPanel).toContainText("실행 위치 AWS ECS");
  await dastPanel.getByLabel("DAST 실행 환경").selectOption("gcp");
  await expect(dastPanel).toContainText("실행 위치 AWS ECS");
  await expect(
    dastPanel.getByLabel("DAST 실행 환경").locator('option[value="onprem"]'),
  ).toHaveAttribute("disabled", "");
  await expect(
    dastPanel.getByRole("button", { name: "이 빌드의 DAST 검사" }),
  ).toBeEnabled({ timeout: 15000 });
  await dastPanel.getByRole("button", { name: "이 빌드의 DAST 검사" }).click();
  await expect.poll(() => postedDastCloud).toBe("gcp");
  await expect(dastPanel).toContainText("실행 위치 GCP Cloud Run");
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
      await page
        .getByRole("navigation", { name: "빌드와 검사 단계" })
        .getByRole("button", { name: /실행 확인/ })
        .click();
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

for (const mismatch of [false, true]) {
  test(
    mismatch
      ? "SAST 커밋 불일치는 결과로 인정하지 않는다"
      : "SAST 발견 사항·근거를 표시하고 보안 통과로 과장하지 않는다",
    async ({ page }, info) => {
      await login(page);
      await register(
        page,
        mismatch ? "검사 불일치 서비스" : "코드 보안 검사 서비스",
      );
      await page
        .getByRole("button", { name: "소스 확인", exact: true })
        .click();
      await expect(
        page.getByRole("button", { name: "이 커밋 빌드" }),
      ).toBeEnabled({ timeout: 15000 });
      await page.getByRole("button", { name: "이 커밋 빌드" }).click();
      await page
        .getByRole("navigation", { name: "빌드와 검사 단계" })
        .getByRole("button", { name: /보안 검사/ })
        .click();
      await expect(
        page.getByRole("button", { name: "이 빌드의 코드 검사" }),
      ).toBeEnabled({ timeout: 15000 });
      await page.getByRole("button", { name: "이 빌드의 코드 검사" }).click();
      const panel = page.locator(".security-panel");
      if (mismatch) {
        await expect(panel).toContainText("security_identity_mismatch", {
          timeout: 15000,
        });
        await expect(panel.locator(".security-result")).toHaveCount(0);
      } else {
        await expect(panel).toContainText("조치 대상 1건 · 확인 필요 1건", {
          timeout: 15000,
        });
        await expect(panel).toContainText("app.py:12");
        await expect(panel).toContainText("운영 배포 승인을 뜻하지 않습니다");
        await panel.getByText("검사 근거", { exact: true }).click();
        await expect(panel).toContainText("SARIF SHA-256");
        await page.screenshot({
          path: info.outputPath("security-desktop.png"),
          fullPage: true,
        });
        await page.setViewportSize({ width: 390, height: 844 });
        await page.screenshot({
          path: info.outputPath("security-mobile.png"),
          fullPage: true,
        });
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        ).toBe(true);
      }
    },
  );
}

test("등록한 프로젝트를 삭제하면 실제 저장소와 새로고침한 목록에서 사라진다", async ({
  page,
}) => {
  await login(page);
  const name = "삭제 검증 프로젝트";
  await register(page, name);
  await page
    .getByRole("button", { name: "← 서비스 목록", exact: true })
    .click();
  await page
    .getByRole("button", { name: `${name} 프로젝트 삭제`, exact: true })
    .click();
  const modal = page.getByRole("dialog", { name: "프로젝트를 삭제할까요?" });
  await modal.getByLabel("확인을 위해 프로젝트 이름을 입력하세요").fill(name);
  const deleted = page.waitForResponse(
    (response) =>
      response.url().endsWith("/delete") &&
      response.request().method() === "POST",
  );
  await modal
    .getByRole("button", { name: "프로젝트 삭제", exact: true })
    .click();
  expect((await deleted).status()).toBe(204);
  await expect(modal).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "내 서비스", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: `${name} 프로젝트 삭제`, exact: true }),
  ).toHaveCount(0);
});
