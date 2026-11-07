import { useEffect, useState } from "react";
import { api, type Page } from "./api";
import { stamp } from "./application-model";
import { EnvironmentComparison, type Execution } from "./EnvironmentComparison";
import type { Environment } from "./observability-api";
import type { Workflow } from "./WorkflowPlanningPanel";

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

export function MigrationDiffComparison({ serviceId }: { serviceId: string }) {
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
    <div className="panel" data-migration-workload-diff="">
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
    </div>
  );
}
