import { useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { api, type Page } from "./api";
import { stamp } from "./application-model";
import { EnvironmentComparison, type Execution } from "./EnvironmentComparison";
import type { Environment } from "./observability-api";
import type { Workflow } from "./WorkflowPlanningPanel";

const uuid = "[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}";
const migrationRoute = new RegExp(`^#/migration/(${uuid})$`, "i");

type Plans =
  | { status: "loading" }
  | { status: "failed" }
  | { status: "ready"; plans: Workflow[]; environments: Environment[] };
type Results =
  | { status: "loading" }
  | { status: "failed" }
  | { status: "ready"; items: Execution[] };

function PlanComparison({
  plan,
  environments,
}: {
  plan: Workflow;
  environments: Environment[];
}) {
  const [results, setResults] = useState<Results>({ status: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    api<Page<Execution>>(
      `/workflows/${encodeURIComponent(plan.id)}/executions`,
      undefined,
      undefined,
      controller.signal,
    )
      .then(({ items }) => {
        if (!controller.signal.aborted) setResults({ status: "ready", items });
      })
      .catch(() => {
        if (!controller.signal.aborted) setResults({ status: "failed" });
      });
    return () => controller.abort();
  }, [plan.id]);

  if (results.status === "failed")
    return <p role="alert">이전 계획의 차분 결과를 불러오지 못했습니다.</p>;
  if (results.status === "loading")
    return <p role="status">차분 결과를 불러오는 중입니다.</p>;

  return (
    <EnvironmentComparison
      environments={environments}
      sourceId={plan.input_snapshot.source?.id ?? null}
      targetIds={plan.input_snapshot.targets
        .filter((target) => target.cloud === "aws")
        .map((target) => target.id)}
      executions={results.items}
      workflowId={plan.id}
      planHash={plan.input_hash}
    />
  );
}

function MigrationDiff({ serviceId }: { serviceId: string }) {
  const [data, setData] = useState<Plans>({ status: "loading" });
  const [selectedId, setSelectedId] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const get = <T,>(endpoint: string) =>
      api<Page<T>>(
        `/services/${serviceId}/${endpoint}`,
        undefined,
        undefined,
        controller.signal,
      );
    Promise.all([get<Workflow>("workflows"), get<Environment>("environments")])
      .then(([plans, environments]) => {
        if (controller.signal.aborted) return;
        setData({
          status: "ready",
          plans: plans.items.filter(
            (plan) =>
              plan.service_id === serviceId &&
              plan.kind === "migration" &&
              plan.input_snapshot.source?.cloud === "onprem" &&
              plan.input_snapshot.targets.some(
                (target) => target.cloud === "aws",
              ),
          ),
          environments: environments.items,
        });
      })
      .catch(() => {
        if (!controller.signal.aborted) setData({ status: "failed" });
      });
    return () => controller.abort();
  }, [serviceId]);

  if (data.status === "failed")
    return (
      <p role="alert">온프레미스 → AWS 이전 계획을 불러오지 못했습니다.</p>
    );
  if (data.status !== "ready" || data.plans.length === 0) return null;
  const plan =
    data.plans.find((item) => item.id === selectedId) ?? data.plans[0];
  return (
    <>
      <label>
        온프레미스 → AWS 비교 계획
        <select
          value={plan.id}
          onChange={(event) => setSelectedId(event.target.value)}
        >
          {data.plans.map((item) => (
            <option key={item.id} value={item.id}>
              {stamp(item.created_at)} ·{" "}
              {item.approved_at ? "승인 기록 있음" : "미승인"}
            </option>
          ))}
        </select>
      </label>
      <PlanComparison
        key={plan.id}
        plan={plan}
        environments={data.environments}
      />
    </>
  );
}

// The deployed shell has its own React root. Attach only to its migration
// route and leave the shell bundle and its existing pipeline controls intact.
let mounted: { serviceId: string; host: HTMLElement; root: Root } | undefined;
let scheduled = false;

function sync() {
  scheduled = false;
  const serviceId = migrationRoute.exec(location.hash)?.[1];
  if (mounted && mounted.serviceId === serviceId && mounted.host.isConnected)
    return;
  if (mounted) {
    mounted.root.unmount();
    mounted.host.remove();
    mounted = undefined;
  }
  if (!serviceId) return;
  const map = document.querySelector(
    "#workspace-content section.migration-panel > section.migration-map",
  );
  if (!map) return;
  const host = document.createElement("div");
  host.dataset.migrationWorkloadDiff = "";
  map.after(host);
  const root = createRoot(host);
  mounted = { serviceId, host, root };
  root.render(<MigrationDiff serviceId={serviceId} />);
}

function schedule() {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(sync);
}

window.addEventListener("hashchange", schedule);
new MutationObserver(() => {
  if (migrationRoute.test(location.hash) && !mounted?.host.isConnected)
    schedule();
}).observe(document.body, { childList: true, subtree: true });
schedule();
