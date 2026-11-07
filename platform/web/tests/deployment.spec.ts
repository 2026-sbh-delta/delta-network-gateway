import { test, expect, type Page, type Route } from "@playwright/test";

const service = {
  id: "svc-deploy",
  project_id: "project-1",
  name: "주문 서비스",
  repository_url: "https://github.com/example/orders",
  source_ref: "main",
  build_context: ".",
  dockerfile_path: "Dockerfile",
  container_port: 8080,
  healthcheck_path: "/health",
  created_at: "2026-11-05T00:00:00Z",
  latest_source_check: null,
};
const options = [
  {
    cloud: "aws",
    label: "AWS",
    runtime: "EC2",
    region: "ap-northeast-2",
    configured: true,
    reason: null,
    profiles: [
      {
        id: "small",
        label: "소형",
        cpu: "2 vCPU (버스트)",
        memory_mb: 4096,
        description: "검증용",
      },
    ],
    hosts: [],
  },
  {
    cloud: "gcp",
    label: "GCP",
    runtime: "Compute Engine",
    region: "asia-northeast3",
    configured: true,
    reason: null,
    profiles: [
      {
        id: "g-small",
        label: "GCP 소형",
        cpu: "2 vCPU",
        memory_mb: 4096,
        description: "검증용",
      },
    ],
    hosts: [],
  },
  {
    cloud: "onprem",
    label: "온프레미스",
    runtime: "등록 호스트",
    region: "seoul-dc",
    configured: false,
    reason: "호스트 연결 준비 중",
    profiles: [
      {
        id: "container-small",
        label: "앱 소형",
        cpu: "공유 코어",
        memory_mb: 2048,
        description: "호스트 자원 한도",
      },
    ],
    hosts: [{ id: "host-1", label: "서울 호스트 1" }],
  },
];
const imageBuild = {
  id: "build-1",
  service_id: service.id,
  kind: "image_build",
  status: "succeeded",
  current_step: null,
  attempt: 1,
  resolved_commit_sha: "a".repeat(40),
  created_at: "2026-11-05T01:00:00Z",
  external_id: "jenkins:1",
  error: null,
  result: {
    image: "registry.example/orders@sha256:abc",
    image_digest: "sha256:abc",
    platform: "linux/amd64",
  },
};
const environments = [
  {
    id: "env-aws",
    name: "AWS 운영",
    cloud: "aws",
    region: "ap-northeast-2",
    version: "v1",
  },
];
const empty = { items: [], next_cursor: null };
const snapshotTarget = (
  cloud: string,
  region: string,
  profile_id: string,
  host_id?: string,
) => ({ cloud, region, profile_id, ...(host_id ? { host_id } : {}) });
function plan(
  targets: Array<{
    cloud: string;
    region: string;
    profile_id: string;
    host_id?: string;
  }>,
  status: string = "planned",
) {
  return {
    id: "plan-1",
    service_id: service.id,
    kind: targets.length > 1 ? "migration" : "deployment",
    status,
    input_hash: "f".repeat(64),
    created_at: "2026-11-05T01:10:00Z",
    approved_at: null,
    executable: true,
    reason: null,
    input_snapshot: {
      image: imageBuild.result.image,
      commit_sha: imageBuild.resolved_commit_sha,
      source: null,
      targets,
      components: [],
    },
    targets: targets.map((target, index) => ({
      target,
      environment_id: `env-new-${index}`,
      run: null,
      status:
        status === "partial" && index === 1
          ? "failed"
          : status === "queued"
            ? "queued"
            : status === "succeeded"
              ? "succeeded"
              : "planned",
      reason:
        status === "partial" && index === 1
          ? "호스트 연결이 끊겼습니다."
          : null,
      service_url: null,
    })),
  };
}

async function fixture(
  page: Page,
  failTarget = false,
  catalogAvailable = true,
  extra: {
    deploymentTarget?: {
      cloud: string;
      region: string;
      profile_id: string;
      host_id?: string;
    };
    envs?: Array<Record<string, unknown>>;
    services?: (typeof service)[];
    backendBuilds?: (typeof imageBuild)[];
    completeOnExecute?: boolean;
    pipelineRepo?: string;
  } = {},
) {
  let pipelineBody: Record<string, unknown> | null = null;
  let loggedIn = false;
  const fixtureService = {
    ...service,
    ...(extra.deploymentTarget
      ? { deployment_target: extra.deploymentTarget }
      : {}),
  };
  let fixtureEnvironments = extra.envs ?? [];
  let currentPlan = plan([snapshotTarget("aws", "ap-northeast-2", "small")]);
  let createBody: Record<string, unknown> | null = null;
  let registrationBody: Record<string, unknown> | null = null;
  let executeCount = 0;
  let planPollCount = 0;
  let backendBuildRequests = 0;
  let alternateBuildRequests = 0;
  await page.route("**/api/v1/**", async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace("/api/v1", "");
    const method = req.method();
    if (path === "/auth/session")
      return route.fulfill({
        status: loggedIn ? 200 : 401,
        json: loggedIn
          ? {
              login_id: "fixture",
              expires_at: "2026-11-06T00:00:00Z",
              csrf_token: "csrf",
            }
          : { error: { code: "unauthorized", message: "fixture" } },
      });
    if (path === "/auth/login") {
      loggedIn = true;
      return route.fulfill({
        status: 200,
        json: {
          login_id: "fixture",
          expires_at: "2026-11-06T00:00:00Z",
          csrf_token: "csrf",
        },
      });
    }
    if (path === "/deployment-options" && !catalogAvailable)
      return route.fulfill({
        status: 503,
        json: {
          error: { code: "catalog_unavailable", message: "카탈로그 점검 중" },
        },
      });
    if (path === "/deployment-options")
      return route.fulfill({ status: 200, json: { items: options } });
    if (path === "/services" && method === "GET")
      return route.fulfill({
        status: 200,
        json: {
          items: [fixtureService, ...(extra.services ?? [])],
          next_cursor: null,
        },
      });
    if (path === "/services" && method === "POST") {
      registrationBody = req.postDataJSON();
      return route.fulfill({ status: 201, json: fixtureService });
    }
    if (path === `/services/${service.id}`)
      return route.fulfill({ status: 200, json: fixtureService });
    if (
      path === `/services/${service.id}/source-checks` ||
      path === `/services/${service.id}/security-checks` ||
      path === `/services/${service.id}/sandbox-checks`
    )
      return route.fulfill({ status: 200, json: empty });
    if (path === `/services/${service.id}/builds`)
      return route.fulfill({
        status: 200,
        json: { items: [imageBuild], next_cursor: null },
      });
    if (path === "/services/svc-backend/builds") {
      backendBuildRequests++;
      return route.fulfill({
        status: 200,
        json: { items: extra.backendBuilds ?? [], next_cursor: null },
      });
    }
    if (path === "/services/svc-backend-alt/builds") {
      alternateBuildRequests++;
      return route.fulfill({
        status: 200,
        json: { items: [], next_cursor: null },
      });
    }
    if (path === `/services/${service.id}/environments`)
      return route.fulfill({
        status: 200,
        json: { items: fixtureEnvironments },
      });
    if (path === `/services/${service.id}/deployment-plans` && method === "GET")
      return route.fulfill({
        status: 200,
        json: { items: createBody ? [currentPlan] : [] },
      });
    if (
      path === `/services/${service.id}/deployment-plans` &&
      method === "POST"
    ) {
      createBody = req.postDataJSON();
      const body = createBody as {
        targets: typeof currentPlan.input_snapshot.targets;
      };
      currentPlan = plan(body.targets, failTarget ? "partial" : "planned");
      return route.fulfill({ status: 201, json: currentPlan });
    }
    if (path === `/deployment-plans/${currentPlan.id}`) {
      if (extra.completeOnExecute && currentPlan.status === "queued") {
        planPollCount++;
        if (planPollCount >= 1)
          currentPlan = plan(currentPlan.input_snapshot.targets, "succeeded");
      }
      return route.fulfill({ status: 200, json: currentPlan });
    }
    if (
      path === `/deployment-plans/${currentPlan.id}/execute` &&
      method === "POST"
    ) {
      executeCount++;
      currentPlan = plan(currentPlan.input_snapshot.targets, "queued");
      if (extra.completeOnExecute) {
        fixtureEnvironments = [
          {
            id: "env-gcp-new",
            name: "GCP 신규",
            cloud: "gcp",
            region: "asia-northeast3",
            version: "v1",
            runtime_status: "active",
          },
        ];
      }
      return route.fulfill({ status: 202, json: currentPlan });
    }
    if (path === "/pipeline/options" && extra.pipelineRepo)
      return route.fulfill({
        status: 200,
        json: {
          repo: extra.pipelineRepo,
          branch: "main",
          branches: ["main"],
          commits: [],
          release_targets: [
            { id: "a-corp-gcp-prod", cloud: "gcp", label: "A기업 GCP 운영" },
          ],
          cutover_configured: true,
        },
      });
    if (path === "/pipeline/runs" && method === "POST") {
      pipelineBody = req.postDataJSON();
      return route.fulfill({ status: 202, json: { run_id: "run123" } });
    }
    return route.fulfill({
      status: 404,
      json: { error: { code: "not_found", message: path } },
    });
  });
  await page.goto("/");
  await page.getByLabel("아이디", { exact: true }).fill("fixture");
  await page.getByLabel("비밀번호", { exact: true }).fill("fixture-password");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page.getByRole("heading", { name: "내 서비스" })).toBeVisible();
  return {
    get createBody() {
      return createBody;
    },
    get registrationBody() {
      return registrationBody;
    },
    get executeCount() {
      return executeCount;
    },
    get pipelineBody() {
      return pipelineBody;
    },
    get backendBuildRequests() {
      return backendBuildRequests;
    },
    get alternateBuildRequests() {
      return alternateBuildRequests;
    },
  };
}

test("서비스 등록에 최초 환경 사양이 포함된다", async ({ page }) => {
  const fixtureState = await fixture(page);
  await page.getByRole("button", { name: "+ 서비스 등록" }).click();
  await page.getByRole("button", { name: "단일 컨테이너 등록" }).click();
  await page.getByLabel("서비스 이름").fill("신규 주문 앱");
  await page
    .getByLabel("공개 GitHub 저장소")
    .fill("https://github.com/example/orders");
  await page.getByLabel("최초 배포 환경").selectOption("onprem");
  await expect(page.getByLabel("최초 배포 호스트")).toHaveValue("host-1");
  await page.getByRole("button", { name: "등록하고 상세 보기" }).click();
  await expect.poll(() => fixtureState.registrationBody).not.toBeNull();
  expect(fixtureState.registrationBody?.deployment_target).toEqual({
    cloud: "onprem",
    region: "seoul-dc",
    profile_id: "container-small",
    host_id: "host-1",
  });
});

test("카탈로그 조회가 실패하면 등록을 보류하고 다시 불러오기를 제공한다", async ({
  page,
}) => {
  const fixtureState = await fixture(page, false, false);
  await page.getByRole("button", { name: "+ 서비스 등록" }).click();
  await page.getByRole("button", { name: "단일 컨테이너 등록" }).click();
  await page.getByLabel("서비스 이름").fill("카탈로그 없는 서비스");
  await page
    .getByLabel("공개 GitHub 저장소")
    .fill("https://github.com/example/orders");
  await expect(
    page.getByText(/환경을 선택할 수 있을 때까지 등록이 보류됩니다/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "등록하고 상세 보기" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "다시 불러오기" }).click();
  await expect(
    page.getByText(/배포 선택 목록을 가져오지 못했습니다/),
  ).toBeVisible();
  expect(fixtureState.registrationBody).toBeNull();
});

test("최초 배포 계획은 대상 하나로 만들고 명시적 실행 승인 후 접수한다", async ({
  page,
}) => {
  const fixtureState = await fixture(page);
  await page.locator(".service-row").click();
  const panel = page.getByRole("region", { name: "최초 배포 및 이전" });
  await expect(panel).toBeVisible();
  await panel.getByRole("radio", { name: /최초 배포/ }).check();
  await panel.getByLabel("검증된 이미지").selectOption("build-1");
  await panel.getByRole("checkbox", { name: /GCP/ }).check();
  await panel.getByRole("button", { name: "배포 계획 만들기" }).click();
  await expect(
    panel.getByRole("heading", { name: "배포 계획 검토" }),
  ).toBeVisible();
  expect(fixtureState.createBody).toMatchObject({
    kind: "deployment",
    build_id: "build-1",
    targets: [snapshotTarget("gcp", "asia-northeast3", "g-small")],
  });
  await expect(
    panel.getByRole("button", { name: "검토 후 배포 실행 승인" }),
  ).toBeEnabled();
  expect(fixtureState.executeCount).toBe(0);
  await panel.getByRole("button", { name: "검토 후 배포 실행 승인" }).click();
  await expect.poll(() => fixtureState.executeCount).toBe(1);
});

test("등록 때 선택한 최초 대상을 계획 화면에 미리 선택한다", async ({
  page,
}) => {
  await fixture(page, false, true, {
    deploymentTarget: snapshotTarget("gcp", "asia-northeast3", "g-small"),
  });
  await page.locator(".service-row").click();
  const panel = page.getByRole("region", { name: "최초 배포 및 이전" });
  await expect(panel.getByRole("radio", { name: /최초 배포/ })).toBeChecked();
  await expect(panel.getByRole("checkbox", { name: /GCP/ })).toBeChecked();
  await expect(panel.getByLabel("GCP 사양")).toHaveValue("g-small");
});

test("활성 환경은 이전 출발점으로 표시하고 제거 중·제거된 환경은 숨긴다", async ({
  page,
}) => {
  await fixture(page, false, true, {
    envs: [
      { ...environments[0], runtime_status: "removed" },
      {
        id: "env-gcp-active",
        name: "GCP 활성",
        cloud: "gcp",
        region: "asia-northeast3",
        version: "v1",
        runtime_status: "active",
      },
      {
        id: "env-dc-removing",
        name: "온프레미스 제거 중",
        cloud: "onprem",
        region: "onprem-local",
        version: "v1",
        runtime_status: "removing",
      },
    ],
  });
  await page.goto("/#/migration/svc-deploy");
  const panel = page.getByRole("region", { name: "최초 배포 및 이전" });
  await expect(panel.getByRole("radio", { name: /이전/ })).toBeChecked();
  await expect(panel.getByRole("radio", { name: /최초 배포/ })).toBeDisabled();
  const source = panel.getByLabel("현재 출발 환경");
  await expect(source).toHaveValue("env-gcp-active");
  await expect(source.locator("option")).toHaveCount(2);
  await expect(source).not.toContainText("제거 중");
  await expect(source).not.toContainText("removed");
});

test("실행 완료 뒤 환경 목록을 다시 불러와 새 환경을 이전 출발점으로 제공한다", async ({
  page,
}) => {
  await fixture(page, false, true, { completeOnExecute: true });
  await page.goto("/#/overview/svc-deploy");
  const panel = page.getByRole("region", { name: "최초 배포 및 이전" });
  await panel.getByLabel("검증된 이미지").selectOption("build-1");
  await panel.getByRole("checkbox", { name: /GCP/ }).check();
  await panel.getByRole("button", { name: "배포 계획 만들기" }).click();
  await panel.getByRole("button", { name: "검토 후 배포 실행 승인" }).click();
  await expect(
    panel.getByText("대상 앱 배포 완료", { exact: true }),
  ).toBeVisible({ timeout: 10000 });
  await expect(panel.getByRole("radio", { name: /이전/ })).toBeChecked();
  await expect(panel.getByLabel("현재 출발 환경")).toHaveValue("env-gcp-new");
});

test("같은 프로젝트와 저장소의 다른 서비스에서 선택한 빌드만 조회한다", async ({
  page,
}) => {
  const matchingBackendBuild = {
    ...imageBuild,
    id: "backend-build-match",
    service_id: "svc-backend",
  };
  const otherCommitBuild = {
    ...matchingBackendBuild,
    id: "backend-build-other",
    resolved_commit_sha: "b".repeat(40),
  };
  const backendService = {
    ...service,
    id: "svc-backend",
    name: "주문 백엔드",
  };
  const alternateBackend = {
    ...service,
    id: "svc-backend-alt",
    name: "주문 백엔드 대안",
  };
  const fixtureState = await fixture(page, false, true, {
    services: [backendService, alternateBackend],
    backendBuilds: [matchingBackendBuild, otherCommitBuild],
  });
  await page.goto("/#/migration/svc-deploy");
  const panel = page.getByRole("region", { name: "최초 배포 및 이전" });
  const backendServicePicker = panel.getByLabel("백엔드 서비스 (선택)");
  await expect(backendServicePicker).toHaveValue("");
  await expect.poll(() => fixtureState.backendBuildRequests).toBe(0);
  await backendServicePicker.selectOption("svc-backend");
  await expect.poll(() => fixtureState.backendBuildRequests).toBe(1);
  await expect.poll(() => fixtureState.alternateBuildRequests).toBe(0);
  const backend = panel.getByLabel("백엔드 빌드 (선택 · 주문 백엔드)");
  await expect(backend).toBeVisible();
  await expect(backend.locator("option")).toHaveCount(2);
  await expect(backend.locator("option").nth(1)).toHaveAttribute(
    "value",
    "backend-build-match",
  );
  await expect(
    backend.locator('option[value="backend-build-other"]'),
  ).toHaveCount(0);
});

test("이전 계획은 GCP와 온프레미스 두 대상을 받고 대상별 실패를 보여준다", async ({
  page,
}) => {
  await fixture(page, false, true, { envs: environments });
  await page.locator(".service-row").click();
  const panel = page.getByRole("region", { name: "최초 배포 및 이전" });
  await panel.getByRole("radio", { name: /이전/ }).check();
  await panel.getByLabel("검증된 이미지").selectOption("build-1");
  const targetSet = panel.getByRole("group", {
    name: "이전 대상 환경 1~2개를 선택하세요",
  });
  await targetSet.getByLabel("GCP").check();
  await targetSet.getByLabel("온프레미스").check();
  await panel.getByLabel("온프레미스 등록 호스트").selectOption("host-1");
  await panel.getByRole("button", { name: "배포 계획 만들기" }).click();
  await expect(
    panel.getByRole("heading", { name: "이전 계획 검토" }),
  ).toBeVisible();
  await expect(panel).toContainText(
    "대상 앱 배포 결과입니다. 트래픽·데이터베이스 전환과 전체 이전 완료는 여기서 처리하지 않습니다.",
  );
  await expect(panel.getByText("온프레미스 · seoul-dc")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("대상 하나의 배포 실패를 전체 성공으로 표시하지 않는다", async ({
  page,
}) => {
  await fixture(page, true, true, { envs: environments });
  await page.locator(".service-row").click();
  const panel = page.getByRole("region", { name: "최초 배포 및 이전" });
  await panel.getByRole("radio", { name: /이전/ }).check();
  await panel.getByLabel("검증된 이미지").selectOption("build-1");
  const targetSet = panel.getByRole("group", {
    name: "이전 대상 환경 1~2개를 선택하세요",
  });
  await targetSet.getByLabel("GCP").check();
  await targetSet.getByLabel("온프레미스").check();
  await panel.getByLabel("온프레미스 등록 호스트").selectOption("host-1");
  await panel.getByRole("button", { name: "배포 계획 만들기" }).click();
  await expect(
    panel.getByText("일부 대상 배포 실패", { exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByText("호스트 연결이 끊겼습니다.", { exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByText("일부 대상 배포 실패", { exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByText("대상 앱 배포 결과입니다.", { exact: false }),
  ).toBeVisible();
});

test("GCP 이전은 보안 파이프라인으로 시작하고 온프레미스만 플랫폼 계획으로 실행한 뒤 파이프라인 화면으로 넘어간다", async ({
  page,
}) => {
  const state = await fixture(page, false, true, {
    envs: environments,
    pipelineRepo: "https://github.com/example/orders.git",
  });
  await page.goto("/#/migration/svc-deploy");
  const panel = page.getByRole("region", { name: "최초 배포 및 이전" });
  await panel.getByLabel("검증된 이미지").selectOption("build-1");
  const targetSet = panel.getByRole("group", {
    name: "이전 대상 환경 1~2개를 선택하세요",
  });
  await targetSet.getByLabel("GCP").check();
  await targetSet.getByLabel("온프레미스").check();
  await panel.getByLabel("온프레미스 등록 호스트").selectOption("host-1");
  await expect(panel).toContainText("GCP · 보안 파이프라인");
  await expect(panel).toContainText("온프레미스 · 플랫폼 배포 함께 실행");
  await panel.getByRole("button", { name: "파이프라인 시작" }).click();
  await expect(page).toHaveURL(/#\/pipeline\/run123$/);
  expect(state.pipelineBody).toEqual({
    branch: "main",
    commit: "a".repeat(40),
    cutover: true,
    release_target: "a-corp-gcp-prod",
  });
  expect(
    (state.createBody as { targets: { cloud: string }[] }).targets.map(
      (t) => t.cloud,
    ),
  ).toEqual(["onprem"]);
  expect(state.executeCount).toBe(1);
});

test("보안 파이프라인 저장소가 아니면 GCP 이전도 플랫폼 계획으로 진행한다", async ({
  page,
}) => {
  await fixture(page, false, true, {
    envs: environments,
    pipelineRepo: "https://github.com/example/other",
  });
  await page.goto("/#/migration/svc-deploy");
  const panel = page.getByRole("region", { name: "최초 배포 및 이전" });
  await panel.getByLabel("검증된 이미지").selectOption("build-1");
  await panel
    .getByRole("group", { name: "이전 대상 환경 1~2개를 선택하세요" })
    .getByLabel("GCP")
    .check();
  await expect(panel).toContainText("보안 파이프라인 대상 저장소가 아닙니다");
  await expect(
    panel.getByRole("button", { name: "배포 계획 만들기" }),
  ).toBeEnabled();
});
