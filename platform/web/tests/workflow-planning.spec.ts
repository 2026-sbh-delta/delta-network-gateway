import { test, expect, type Page, type Route } from "@playwright/test";
import type { Execution } from "../src/EnvironmentComparison";

// This browser-only fixture intercepts the HTTP boundary. No workflow or environment is written to a backend.
const sha = "a".repeat(40);
const digest = `sha256:${"b".repeat(64)}`;
const service = {
  id: "svc-workflow",
  name: "이전 계획 대상",
  repository_url: "https://github.com/example/app",
  source_ref: "main",
  build_context: ".",
  dockerfile_path: "Dockerfile",
  container_port: 8080,
  healthcheck_path: "/health",
  created_at: "2026-11-04T00:00:00Z",
  latest_source_check: null,
};
const environments = [
  {
    id: "env-aws",
    name: "AWS 운영",
    cloud: "aws",
    region: "ap-northeast-2",
    version: "v1",
  },
  {
    id: "env-gcp",
    name: "GCP 검증",
    cloud: "gcp",
    region: "asia-northeast3",
    version: "v1",
  },
  {
    id: "env-dc",
    name: "온프레미스 DR",
    cloud: "onprem",
    region: "seoul-dc",
    version: null,
  },
];
const build = {
  id: "build-pinned",
  service_id: service.id,
  kind: "image_build",
  status: "succeeded",
  current_step: null,
  attempt: 1,
  resolved_commit_sha: sha,
  created_at: "2026-11-04T01:00:00Z",
  external_id: "jenkins:22",
  error: null,
  result: {
    image: `registry.example/app@${digest}`,
    image_digest: digest,
    platform: "linux/arm64",
  },
};
const emptyPage = { items: [], next_cursor: null };
const inputHash = "c".repeat(64);
function workflow(status: "planned" | "approved" = "planned") {
  return {
    id: "workflow-1",
    project_id: "project-1",
    service_id: service.id,
    kind: "migration",
    status,
    input_snapshot: {
      commit_sha: sha,
      image: build.result.image,
      image_digest: digest,
      repository_url: service.repository_url,
      build_id: build.id,
      source: {
        id: "env-aws",
        cloud: "aws",
        region: "ap-northeast-2",
        binding_hash: "d".repeat(64),
      },
      targets: [
        {
          id: "env-gcp",
          cloud: "gcp",
          region: "asia-northeast3",
          binding_hash: "e".repeat(64),
        },
        {
          id: "env-dc",
          cloud: "onprem",
          region: "seoul-dc",
          binding_hash: "f".repeat(64),
        },
      ],
    },
    input_hash: inputHash,
    approved_by: status === "approved" ? "operator-fixture" : null,
    approved_at: status === "approved" ? "2026-11-04T02:00:00Z" : null,
    created_at: "2026-11-04T01:30:00Z",
  };
}

async function fixture(
  page: Page,
  saved?: {
    plans: ReturnType<typeof workflow>[];
    executions: Record<string, Execution[]>;
  },
) {
  let loggedIn = Boolean(saved);
  const writes: string[] = [];
  let currentWorkflow = workflow();
  let createdBody: Record<string, unknown> | null = null;
  let createHeader: string | undefined;
  let approvalBody: Record<string, unknown> | null = null;
  let executionPosts = 0;
  await page.route("**/api/v1/**", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    const method = request.method();
    if (method !== "GET") writes.push(path);
    if (path === "/auth/session")
      return route.fulfill({
        status: loggedIn ? 200 : 401,
        json: loggedIn
          ? {
              login_id: "operator-fixture",
              expires_at: "2026-11-05T00:00:00Z",
              csrf_token: "csrf-test",
            }
          : { error: { code: "unauthorized", message: "fixture" } },
      });
    if (path === "/auth/login" && method === "POST") {
      loggedIn = true;
      return route.fulfill({
        status: 200,
        json: {
          login_id: "operator-fixture",
          expires_at: "2026-11-05T00:00:00Z",
          csrf_token: "csrf-test",
        },
      });
    }
    if (path === "/services")
      return route.fulfill({
        status: 200,
        json: { items: [service], next_cursor: null },
      });
    if (path === "/sandbox-targets")
      return route.fulfill({
        status: 200,
        json: {
          items: [
            { cloud: "aws", label: "AWS", runtime: "ECS", configured: true },
            {
              cloud: "gcp",
              label: "GCP",
              runtime: "Cloud Run",
              configured: false,
            },
            {
              cloud: "onprem",
              label: "온프레미스",
              runtime: "미연결",
              configured: false,
            },
          ],
        },
      });
    if (path === `/services/${service.id}`)
      return route.fulfill({ status: 200, json: service });
    if (path === `/services/${service.id}/source-checks`)
      return route.fulfill({ status: 200, json: emptyPage });
    if (path === `/services/${service.id}/builds` && method === "GET")
      return route.fulfill({
        status: 200,
        json: { items: [build], next_cursor: null },
      });
    if (path === `/builds/${build.id}`)
      return route.fulfill({ status: 200, json: build });
    if (path === `/services/${service.id}/environments`)
      return route.fulfill({ status: 200, json: { items: environments } });
    if (
      path === `/services/${service.id}/sandbox-checks` ||
      path === `/services/${service.id}/security-checks` ||
      path === `/services/${service.id}/dast-checks`
    )
      return route.fulfill({ status: 200, json: emptyPage });
    if (path === `/services/${service.id}/workflows` && method === "GET")
      return route.fulfill({
        status: 200,
        json: { items: saved?.plans ?? (createdBody ? [currentWorkflow] : []) },
      });
    if (path === `/services/${service.id}/workflows` && method === "POST") {
      createdBody = request.postDataJSON();
      createHeader = request.headers()["idempotency-key"];
      return route.fulfill({ status: 201, json: currentWorkflow });
    }
    if (path === `/workflows/${currentWorkflow.id}` && method === "GET")
      return route.fulfill({ status: 200, json: currentWorkflow });
    const savedPlan = saved?.plans.find(
      (plan) => path === `/workflows/${plan.id}`,
    );
    if (savedPlan && method === "GET")
      return route.fulfill({ status: 200, json: savedPlan });
    const savedExecutionPlan = saved?.plans.find(
      (plan) => path === `/workflows/${plan.id}/executions`,
    );
    if (
      (savedExecutionPlan ||
        path === `/workflows/${currentWorkflow.id}/executions`) &&
      method === "GET"
    )
      return route.fulfill({
        status: 200,
        json: {
          items: savedExecutionPlan
            ? (saved?.executions[savedExecutionPlan.id] ?? [])
            : [],
        },
      });
    if (
      path === `/workflows/${currentWorkflow.id}/approval` &&
      method === "POST"
    ) {
      approvalBody = request.postDataJSON();
      currentWorkflow = workflow("approved");
      return route.fulfill({ status: 200, json: currentWorkflow });
    }
    if (/\/workflows\/[^/]+\/executions$/.test(path) && method === "POST")
      executionPosts++;
    return route.fulfill({
      status: 404,
      json: { error: { code: "fixture_not_found", message: path } },
    });
  });
  return {
    writes,
    get createdBody() {
      return createdBody;
    },
    get createHeader() {
      return createHeader;
    },
    get approvalBody() {
      return approvalBody;
    },
    get executionPosts() {
      return executionPosts;
    },
  };
}

test("이전 계획은 고정 빌드·서로 다른 환경으로 만들고 승인해도 실행하지 않는다", async ({
  page,
}, info) => {
  const boundary = await fixture(page);
  await page.goto("/");
  await page.getByLabel("아이디", { exact: true }).fill("operator-fixture");
  await page.getByLabel("비밀번호", { exact: true }).fill("test-only");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.getByRole("button", { name: /이전 계획 대상 example/ }).click();
  await page.getByRole("button", { name: "메뉴 열기" }).click();
  await page.getByRole("button", { name: "이전 계획", exact: true }).click();
  await page.getByText("기존 분석·검토 계획 보기").click();
  const panel = page.getByRole("region", { name: "배포·이전 계획" });
  await expect(
    panel.getByText("실제 배포·DB 전환 실행기는 아직 연결되지 않았으며", {
      exact: false,
    }),
  ).toBeVisible();
  await panel
    .locator(".workflow-target")
    .filter({ hasText: "GCP 검증" })
    .locator("input")
    .check();
  await panel
    .locator(".workflow-target")
    .filter({ hasText: "온프레미스 DR" })
    .locator("input")
    .check();
  await panel.getByRole("button", { name: "변경 계획 만들기" }).click();
  await expect(
    panel
      .locator(".workflow-route")
      .getByText("GCP 검증 · GCP · asia-northeast3", {
        exact: false,
      }),
  ).toBeVisible();
  expect(boundary.createdBody).toMatchObject({
    kind: "migration",
    build_id: build.id,
    source_environment_id: "env-aws",
    target_environment_ids: ["env-gcp", "env-dc"],
  });
  expect(boundary.createHeader).toBeTruthy();
  await panel.getByText("승인 대상 · 커밋 · 이미지 · 계획 버전").click();
  await expect(
    panel
      .locator(".workflow-facts")
      .getByText(sha.slice(0, 12), { exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "계획 승인", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("workflow-plan-desktop.png"),
    fullPage: true,
  });
  await panel.getByRole("button", { name: "계획 승인", exact: true }).click();
  await expect(
    panel.getByText("계획 승인이 기록됐습니다.", { exact: false }),
  ).toBeVisible();
  await expect(
    panel.getByText("operator-fixture", { exact: false }),
  ).toBeVisible();
  expect(boundary.approvalBody).toEqual({ input_hash: inputHash });
  expect(boundary.executionPosts).toBe(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: info.outputPath("workflow-plan-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("마이그레이션 화면은 새로고침해도 저장된 온프레미스 → AWS 차분을 표시한다", async ({
  page,
}) => {
  const unrelated = workflow();
  const plan = {
    ...workflow(),
    id: "onprem-aws-current",
    input_snapshot: {
      ...unrelated.input_snapshot,
      source: unrelated.input_snapshot.targets[1],
      targets: [unrelated.input_snapshot.source],
    },
  };
  const oldPlan = {
    ...plan,
    id: "onprem-aws-old",
    input_hash: "0".repeat(64),
    created_at: "2026-11-03T01:30:00Z",
  };
  function execution(planId: string, item: string): Execution {
    const id = `diff-${planId}`;
    return {
      id,
      environment_id: "env-aws",
      kind: "ai_assessment",
      status: "succeeded",
      created_at: plan.created_at,
      assessment: {
        workflow_id: planId,
        environment_id: "env-aws",
        plan_hash: inputHash,
        assessment_id: id,
        summary: "워크로드 차분 needs_human · 1개 항목",
        impact: "대상 환경 설정을 확인하세요.",
        remaining_gaps: [],
        differences: [
          {
            item,
            source: "온프레미스 Deployment",
            target: "AWS 컨테이너",
            treatment: "런타임 확인",
            judgment: "needs_check",
            before: null,
            after: null,
          },
        ],
        changes: [],
      },
    };
  }
  const boundary = await fixture(page, {
    plans: [unrelated, plan, oldPlan],
    executions: {
      [plan.id]: [execution(plan.id, "저장된 워크로드 차분")],
      [oldPlan.id]: [execution(oldPlan.id, "해시가 다른 차분")],
    },
  });

  await page.goto(`/#/migration/${service.id}`);
  const plans = page.getByRole("combobox", {
    name: "온프레미스 → AWS 비교 계획",
  });
  const comparison = page.getByRole("region", { name: "환경 차이와 AI 제안" });
  const savedDiff = comparison.getByRole("row", {
    name: /저장된 워크로드 차분/,
  });
  await expect(plans).toHaveValue(plan.id);
  await expect(plans.locator("option")).toHaveCount(2);
  await expect(savedDiff).toBeVisible();
  await expect(comparison.getByText(/needs_human/)).toBeVisible();

  await plans.selectOption(oldPlan.id);
  await expect(comparison.getByText("상세 결과 미연결")).toBeVisible();
  await expect(comparison.getByText("해시가 다른 차분")).toHaveCount(0);
  await expect(savedDiff).toHaveCount(0);

  await page.reload();
  await expect(plans).toHaveValue(plan.id);
  await expect(savedDiff).toBeVisible();
  expect(boundary.writes).toEqual([]);
});
