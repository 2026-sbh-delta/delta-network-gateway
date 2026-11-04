import { test, expect, type Page } from "@playwright/test";
import type { Run, Service } from "../src/api";
import type { AssessmentView } from "../src/EnvironmentComparison";

// Proposed optional read contracts exist only in this intercepted browser fixture.
// No fixture is imported by the production application and no backend write is made.
const at = "2026-11-05T00:00:00Z";
const sha = "a".repeat(40);
const digest = `sha256:${"b".repeat(64)}`;
const base = {
  repository_url: "https://github.com/2026-sbh-delta/delta-sample-app",
  source_ref: "main",
  dockerfile_path: "Dockerfile",
  healthcheck_path: "/healthz",
  created_at: at,
  latest_source_check: null,
};
const frontend: Service = {
  ...base,
  id: "notes-fe",
  name: "Delta Notes",
  build_context: "frontend",
  container_port: 3000,
  application_bundle: {
    version: "bundle-1",
    database: "memory",
    components: [
      { role: "frontend", service_id: "notes-fe", build_id: "build-fe" },
      { role: "backend", service_id: "notes-be", build_id: "build-be" },
    ],
  },
};
const backend: Service = {
  ...base,
  id: "notes-be",
  name: "Notes API",
  build_context: "backend",
  container_port: 8080,
};
const build = (id: string, service_id: string): Run => ({
  id,
  service_id,
  kind: "image_build",
  status: "succeeded",
  current_step: null,
  external_id: null,
  attempt: 1,
  resolved_commit_sha: sha,
  created_at: at,
  result: { image: `registry.example/app@${digest}`, image_digest: digest },
  error: null,
});
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
    version: "v2",
  },
  {
    id: "env-dc",
    name: "온프레미스 검증",
    cloud: "onprem",
    region: "seoul-dc",
    version: null,
  },
];
const workflow = {
  id: "workflow-1",
  service_id: frontend.id,
  project_id: "project-1",
  kind: "migration",
  status: "planned",
  input_hash: "plan-1",
  created_at: at,
  approved_by: null,
  approved_at: null,
  input_snapshot: {
    commit_sha: sha,
    image: `registry.example/app@${digest}`,
    image_digest: digest,
    repository_url: base.repository_url,
    build_id: "build-fe",
    source: environments[0],
    targets: environments.slice(1),
  },
};
const assessment: AssessmentView = {
  workflow_id: workflow.id,
  environment_id: "env-gcp",
  plan_hash: workflow.input_hash,
  assessment_id: "assessment-gcp",
  summary: "대상 통신 규칙을 검토하세요.",
  impact: "프론트엔드에서 API에 접근할 수 있도록 제한된 통신을 허용합니다.",
  remaining_gaps: ["위협 탐지 기능은 제공되지 않습니다."],
  differences: [
    {
      item: "위협 탐지",
      source: "활성화 확인",
      target: "대응 기능 없음",
      treatment: "남는 위험 검토",
      judgment: "unsupported",
      before: "미지원",
      after: null,
    },
  ],
  changes: [
    {
      file: "network-policy.yaml",
      kind: "configuration",
      diff: "- policy: deny\n+ policy: restricted",
    },
  ],
};
async function boundary(
  page: Page,
  { failedBackend = false, wrongAssessment = false, unlinked = false } = {},
) {
  let mutations = 0;
  let approved = false;
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    if (route.request().method() !== "GET") mutations++;
    const answer = (json: unknown) => route.fulfill({ json });
    if (path === "/auth/session")
      return answer({
        login_id: "fixture",
        csrf_token: "fixture",
        expires_at: "2099-01-01T00:00:00Z",
      });
    if (path === "/services")
      return answer({ items: [frontend, backend], next_cursor: null });
    if (path === `/services/${frontend.id}`)
      return answer(
        unlinked ? { ...frontend, application_bundle: undefined } : frontend,
      );
    if (path === `/services/${backend.id}`) return answer(backend);
    if (path === "/sandbox-targets") return answer({ items: [] });
    if (path === "/workflows/workflow-1/approval") {
      approved = true;
      return answer({
        ...workflow,
        status: "approved",
        approved_by: "fixture",
        approved_at: at,
      });
    }
    if (path === "/workflows/workflow-1")
      return answer({ ...workflow, status: approved ? "approved" : "planned" });
    if (path === "/workflows/workflow-1/executions")
      return answer({
        items: [
          {
            id: "assessment-gcp",
            environment_id: "env-gcp",
            kind: "ai_assessment",
            status: "succeeded",
            created_at: at,
            assessment: {
              ...assessment,
              plan_hash: wrongAssessment ? "old-plan" : assessment.plan_hash,
            },
          },
          {
            id: "run-dc",
            environment_id: "env-dc",
            kind: "app_deployment",
            status: "failed",
            created_at: at,
          },
        ],
      });
    const component = path.includes("notes-be") ? backend : frontend;
    const buildId = component.id === "notes-be" ? "build-be" : "build-fe";
    if (path.startsWith("/builds/") && !path.endsWith("/events")) {
      const id = path.split("/")[2];
      return answer(
        id === "build-be" && failedBackend
          ? {
              ...build(id, backend.id),
              status: "failed",
              result: null,
              error: { code: "build_failed", message: "백엔드 빌드 명령 실패" },
            }
          : build(id, id === "build-be" ? backend.id : frontend.id),
      );
    }
    if (path.endsWith("/builds"))
      return answer({
        items: [build(buildId, component.id)],
        next_cursor: null,
      });
    if (path.endsWith("/environments")) return answer({ items: environments });
    if (path.endsWith("/workflows"))
      return answer({
        items: [{ ...workflow, status: approved ? "approved" : "planned" }],
      });
    if (path.endsWith("/security-checks"))
      return answer({
        items: [
          {
            ...build(`sast-${component.id}`, component.id),
            kind: "security_check",
            result: {
              build_id: buildId,
              security_check: "sast_completed",
              findings: [],
              counts: {},
            },
          },
        ],
        next_cursor: null,
      });
    if (path.endsWith("/dast-checks"))
      return answer({
        items: [
          {
            ...build(`dast-${component.id}`, component.id),
            kind: "dast_check",
            result: {
              build_id: buildId,
              execution_cloud: "gcp",
              dast_check: "not_run",
              cleanup: "stopped",
            },
          },
        ],
        next_cursor: null,
      });
    if (path.endsWith("/observability")) {
      const env = environments.find(
        (e) => e.id === url.searchParams.get("environment_id"),
      )!;
      const metric = {
        status: "no_data",
        value: null,
        unit: "",
        collected_at: null,
        message: null,
        series: [],
      };
      return answer({
        service_id: frontend.id,
        environment: env,
        observed_at: at,
        window: { minutes: 15, start: at, end: at },
        metrics: {
          requests: metric,
          error_rate: metric,
          p95_ms: metric,
          app_up: metric,
          db_up: metric,
        },
        infra: { status: "no_data", message: null, instances: [] },
        logs: { status: "no_data", message: null, items: [] },
        trace: {
          status: "no_data",
          message: null,
          id: null,
          duration_ms: null,
          spans: [],
        },
        migration: null,
        application: {
          bundle_version: "bundle-1",
          components: [
            {
              role: "frontend",
              service_id: frontend.id,
              build_id: "build-fe",
              commit_sha: sha,
              image_digest: digest,
              readiness: {
                ...metric,
                status: "ok",
                value: 1,
                collected_at: at,
              },
            },
          ],
        },
      });
    }
    if (/\/(source-checks|sandbox-checks|events)$/.test(path))
      return answer({ items: [], next_cursor: null });
    return route.fulfill({
      status: 404,
      json: {
        error: { code: "not_found", message: "fixture endpoint absent" },
      },
    });
  });
  return {
    get mutations() {
      return mutations;
    },
  };
}

test("FE 성공과 BE 실패를 구분하고 메모리 데이터·환경 미수집을 표시한다", async ({
  page,
}, info) => {
  const io = await boundary(page, { failedBackend: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/#/overview/notes-fe");
  await expect(
    page.getByRole("heading", { name: "이미지 빌드 실패" }),
  ).toBeVisible();
  const bundle = page.getByRole("region", { name: "애플리케이션 구성" });
  await expect(bundle.getByText("빌드 완료", { exact: true })).toBeVisible();
  await expect(bundle.getByText("빌드 실패", { exact: true })).toBeVisible();
  await expect(bundle.getByText(/재시작 시 데이터 소실/)).toBeVisible();
  await expect(page.getByText("데이터 없음", { exact: true })).toHaveCount(3);
  await expect(page.getByRole("link", { name: "서비스 열기" })).toHaveCount(0);
  await page.screenshot({
    path: info.outputPath("release-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(bundle.getByText("빌드 실패", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: info.outputPath("release-mobile.png"),
    fullPage: true,
  });
  expect(io.mutations).toBe(0);
});

test("SAST 완료와 GCP DAST 미실행을 분리하고 온프레미스에 결과를 재사용하지 않는다", async ({
  page,
}) => {
  await boundary(page);
  await page.goto("/#/build/notes-fe");
  const bundle = page.getByRole("region", { name: "애플리케이션 구성" });
  await expect(
    bundle.getByText("검사 완료 · 발견 0건", { exact: true }),
  ).toHaveCount(2);
  const targets = page.getByRole("region", { name: "대상별 검사 결과" });
  const gcp = targets
    .locator("article")
    .filter({ has: page.getByRole("heading", { name: "GCP", exact: true }) });
  await expect(gcp.getByText("검사 미실행", { exact: true })).toHaveCount(4);
  await expect(gcp.getByText("샌드박스 정리: 완료")).toHaveCount(2);
  const dc = targets.locator("article").filter({
    has: page.getByRole("heading", { name: "온프레미스", exact: true }),
  });
  await expect(dc.getByText("샌드박스 정리: 완료")).toHaveCount(0);
  await expect(dc.getByText("검사 미실행", { exact: true })).toHaveCount(4);
});

test("대상·계획에 맞는 차이와 diff만 표시하고 승인 후에도 미지원은 남는다", async ({
  page,
}, info) => {
  await boundary(page);
  await page.goto("/#/migration/notes-fe");
  const comparison = page.getByRole("region", { name: "환경 차이와 AI 제안" });
  await expect(
    comparison.getByText("위협 탐지", { exact: true }),
  ).toBeVisible();
  await comparison.getByText("설정 변경 · network-policy.yaml").click();
  await expect(comparison.locator(".diff-add")).toContainText("restricted");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("environment-diff.png"),
    fullPage: true,
  });
  await comparison.getByRole("button", { name: /온프레미스/ }).click();
  await expect(comparison.getByText("위협 탐지", { exact: true })).toHaveCount(
    0,
  );
  await expect(comparison.getByText("분석 결과 대기")).toBeVisible();
  await comparison.getByRole("button", { name: /GCP/ }).click();
  await page.getByRole("button", { name: "계획 승인", exact: true }).click();
  await expect(page.getByText("계획 승인됨", { exact: true })).toBeVisible();
  await expect(comparison.getByText("미지원", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("변경된 계획의 AI 결과를 숨기고 기존 API의 단일 서비스도 지원한다", async ({
  page,
}) => {
  await boundary(page, { wrongAssessment: true, unlinked: true });
  await page.goto("/#/migration/notes-fe");
  const comparison = page.getByRole("region", { name: "환경 차이와 AI 제안" });
  await expect(comparison.getByText("상세 결과 미연결")).toBeVisible();
  await expect(comparison.getByText("위협 탐지", { exact: true })).toHaveCount(
    0,
  );
  await page.goto("/#/overview/notes-fe");
  await expect(page.getByText(/FE·BE 배포 묶음 미연결/)).toBeVisible();
  await expect(page.getByText("DB 구성 확인 필요")).toBeVisible();
});

test("대시보드는 FE 관측만으로 BE 준비를 단정하지 않고 선택한 환경을 유지한다", async ({
  page,
}) => {
  await boundary(page);
  await page.goto("/#/monitoring/notes-fe");
  await expect(
    page
      .locator(".release-runtime-components")
      .getByText("준비됨", { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator(".release-runtime-components")
      .getByText("관측 미연결", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("미구성", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /GCP 검증/ }).click();
  await page.reload();
  await expect(page.getByRole("button", { name: /GCP 검증/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByText("DB 이전 실행 없음")).toHaveCount(0);
});

test("조회 실패는 빈 정상 화면으로 바꾸지 않으며 재조회로 복구한다", async ({
  page,
}) => {
  const io = await boundary(page);
  let failed = true;
  await page.route("**/api/v1/builds/build-fe", async (route) => {
    if (failed)
      return route.fulfill({
        status: 503,
        json: {
          error: { code: "unavailable", message: "검사 저장소 연결 실패" },
        },
      });
    return route.fallback();
  });
  await page.goto("/#/overview/notes-fe");
  await expect(
    page.getByRole("alert").getByText("배포 상태를 불러오지 못했습니다."),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "애플리케이션 구성" }),
  ).toHaveCount(0);
  failed = false;
  await page.getByRole("button", { name: "다시 조회", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "애플리케이션 구성" }),
  ).toBeVisible();
  expect(io.mutations).toBe(0);
});

test("빌드 연결이 없는 최신 실패 뒤에 이전 성공을 재사용하지 않는다", async ({
  page,
}) => {
  await boundary(page);
  await page.route("**/api/v1/services/notes-fe/sandbox-checks", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            ...build("runtime-new", frontend.id),
            kind: "sandbox_check",
            status: "failed",
            execution_cloud: "gcp",
            created_at: "2026-11-05T01:00:00Z",
            result: null,
            error: { code: "failed", message: "runtime failed" },
          },
          {
            ...build("runtime-old", frontend.id),
            kind: "sandbox_check",
            result: {
              build_id: "build-fe",
              execution_cloud: "gcp",
              runtime_check: "passed",
              cleanup: "stopped",
            },
          },
        ],
        next_cursor: null,
      },
    }),
  );
  await page.goto("/#/build/notes-fe");
  const gcp = page
    .getByRole("region", { name: "대상별 검사 결과" })
    .locator("article")
    .filter({ has: page.getByRole("heading", { name: "GCP", exact: true }) });
  await expect(
    gcp.getByText("검사 연결 확인 필요", { exact: true }),
  ).toBeVisible();
  await expect(gcp.getByText("검사 완료", { exact: true })).toHaveCount(0);
});
