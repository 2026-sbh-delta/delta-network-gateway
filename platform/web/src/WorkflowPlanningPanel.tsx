import { useCallback, useEffect, useRef, useState } from "react";
import { api, message, type Page, type Run } from "./api";
import { EnvironmentComparison, type Execution } from "./EnvironmentComparison";
import type { Environment } from "./observability-api";

type WorkflowKind = "migration" | "deployment";
export type Workflow = {
  id: string;
  project_id: string;
  service_id: string;
  kind: WorkflowKind;
  status: "planned" | "approved" | "running" | "succeeded" | "failed";
  input_snapshot: {
    commit_sha: string;
    image: string;
    image_digest: string;
    repository_url: string;
    build_id: string;
    source: {
      id: string;
      cloud: Environment["cloud"];
      region: string;
      binding_hash: string;
    } | null;
    targets: {
      id: string;
      cloud: Environment["cloud"];
      region: string;
      binding_hash: string;
    }[];
  };
  input_hash: string;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
};
const workflowStatus: Record<Workflow["status"], string> = {
  planned: "계획 승인 대기",
  approved: "계획 승인됨",
  running: "실행 중",
  succeeded: "완료",
  failed: "실패",
};
const runTime = (value: string) => new Date(value).toLocaleString("ko-KR");
const executionLabel = (value: string) =>
  ({
    prepared: "준비됨",
    queued: "대기",
    running: "실행 중",
    succeeded: "완료",
    failed: "실패",
  })[value] ?? value;
const executionKindLabel = (value: string) =>
  ({
    ai_assessment: "AI 사전 평가",
    migration_step: "이전 단계",
    app_deployment: "앱 배포",
  })[value] ?? value;
function pinnedBuild(run: Run) {
  return (
    run.kind === "image_build" &&
    run.status === "succeeded" &&
    !!run.resolved_commit_sha &&
    !!run.result?.image_digest &&
    !!run.result.image
  );
}

export function WorkflowPlanningPanel({ serviceId }: { serviceId: string }) {
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [builds, setBuilds] = useState<Run[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState("");
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [kind, setKind] = useState<WorkflowKind>("migration");
  const [buildId, setBuildId] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [targetIds, setTargetIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const createKey = useRef<{ payload: string; key: string } | null>(null);
  const readyBuilds = builds.filter(pinnedBuild);

  const load = useCallback(
    async (signal: AbortSignal) => {
      const [envResult, buildResult, workflowResult] = await Promise.all([
        api<{ items: Environment[] }>(
          `/services/${serviceId}/environments`,
          undefined,
          undefined,
          signal,
        ),
        api<Page<Run>>(
          `/services/${serviceId}/builds`,
          undefined,
          undefined,
          signal,
        ),
        api<{ items: Workflow[] }>(
          `/services/${serviceId}/workflows`,
          undefined,
          undefined,
          signal,
        ),
      ]);
      if (signal.aborted) return;
      setEnvironments(envResult.items);
      setBuilds(buildResult.items);
      setWorkflows(workflowResult.items);
      setSelectedWorkflowId(
        (current) => current || workflowResult.items[0]?.id || "",
      );
      const availableBuilds = buildResult.items.filter(pinnedBuild);
      setBuildId((current) =>
        availableBuilds.some((item) => item.id === current)
          ? current
          : (availableBuilds[0]?.id ?? ""),
      );
      setSourceId((current) => current || envResult.items[0]?.id || "");
      setError("");
    },
    [serviceId],
  );
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    load(controller.signal)
      .catch((e) => {
        if (!controller.signal.aborted) setError(message(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [load, refresh]);
  useEffect(() => {
    setTargetIds((current) => current.filter((id) => id !== sourceId));
  }, [sourceId]);
  useEffect(() => {
    setWorkflow(null);
    setExecutions([]);
    if (!selectedWorkflowId) return;
    const controller = new AbortController();
    Promise.all([
      api<Workflow>(
        `/workflows/${selectedWorkflowId}`,
        undefined,
        undefined,
        controller.signal,
      ),
      api<{ items: Execution[] }>(
        `/workflows/${selectedWorkflowId}/executions`,
        undefined,
        undefined,
        controller.signal,
      ),
    ])
      .then(([detail, result]) => {
        if (controller.signal.aborted) return;
        setWorkflow(detail);
        setExecutions(result.items);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(message(e));
      });
    return () => controller.abort();
  }, [selectedWorkflowId, refresh]);

  function toggleTarget(id: string) {
    setTargetIds((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : current.length < 2
          ? [...current, id]
          : current,
    );
  }
  async function createPlan(event: React.FormEvent) {
    event.preventDefault();
    const payload = {
      kind,
      build_id: buildId,
      ...(kind === "migration" || sourceId
        ? { source_environment_id: sourceId }
        : {}),
      target_environment_ids: targetIds,
    };
    const serialized = JSON.stringify(payload);
    if (createKey.current?.payload !== serialized)
      createKey.current = { payload: serialized, key: crypto.randomUUID() };
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await api<Workflow>(
        `/services/${serviceId}/workflows`,
        payload,
        createKey.current.key,
      );
      createKey.current = null;
      setWorkflow(null);
      setExecutions([]);
      setSelectedWorkflowId(created.id);
      setNotice("계획을 만들었습니다. 계획 승인 전에는 변경이 없습니다.");
      setRefresh((n) => n + 1);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function approvePlan() {
    if (!workflow) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const approved = await api<Workflow>(
        `/workflows/${workflow.id}/approval`,
        { input_hash: workflow.input_hash },
      );
      setWorkflow(approved);
      setWorkflows((items) => [
        approved,
        ...items.filter((item) => item.id !== approved.id),
      ]);
      setNotice(
        "계획 승인이 기록됐습니다. 실제 배포·DB 전환 실행은 자동으로 시작되지 않습니다.",
      );
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  const environmentName = (id: string) => {
    const environment = environments.find((item) => item.id === id);
    return environment
      ? `${environment.name} · ${environment.cloud.toUpperCase()} · ${environment.region}`
      : `환경 ${id.slice(0, 8)}`;
  };

  return (
    <section className="panel workflow-panel" aria-label="배포·이전 계획">
      <div className="panel-top">
        <div>
          <div className="eyebrow">CHANGE PLAN</div>
          <h2>배포·이전 계획</h2>
        </div>
        <button onClick={() => setRefresh((n) => n + 1)}>새로고침</button>
      </div>
      <p className="workflow-boundary">
        여기서는 계획을 만들고 승인 기록을 남깁니다.{" "}
        <b>
          실제 배포·DB 전환 실행기는 아직 연결되지 않았으며, 승인해도 실행되지
          않습니다.
        </b>
      </p>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <p className="success-note" role="status">
          {notice}
        </p>
      )}
      {loading ? (
        <p className="muted">환경과 빌드, 최근 계획을 불러오는 중입니다.</p>
      ) : !environments.length ? (
        <div className="workflow-empty">
          <strong>연결된 환경이 없습니다.</strong>
          <p>
            운영자가 이 서비스에 출발·대상 환경을 먼저 등록해야 합니다. 이
            화면에서는 환경을 추가하지 않습니다.
          </p>
        </div>
      ) : (
        <details className="release-plan-create" open={!workflows.length}>
          <summary>새 변경 계획 만들기</summary>
          <form className="workflow-form" onSubmit={createPlan}>
            <div className="workflow-form-row">
              <label>
                계획 종류
                <select
                  value={kind}
                  onChange={(e) => setKind(e.target.value as WorkflowKind)}
                >
                  <option value="migration">서비스 이전</option>
                  <option value="deployment">앱 배포</option>
                </select>
              </label>
              <label>
                고정 이미지 빌드
                <select
                  value={buildId}
                  onChange={(e) => setBuildId(e.target.value)}
                  required
                >
                  <option value="">빌드 선택</option>
                  {readyBuilds.map((build) => (
                    <option key={build.id} value={build.id}>
                      {build.resolved_commit_sha?.slice(0, 12)} ·{" "}
                      {build.result?.image_digest?.slice(0, 19)} ·{" "}
                      {runTime(build.created_at)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {kind === "migration" ? (
              <label>
                출발 환경
                <select
                  value={sourceId}
                  onChange={(e) => setSourceId(e.target.value)}
                  required
                >
                  <option value="">출발 환경 선택</option>
                  {environments.map((env) => (
                    <option key={env.id} value={env.id}>
                      {env.name} · {env.cloud.toUpperCase()} · {env.region}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <label>
                출발 환경 <span className="muted">(선택 사항)</span>
                <select
                  value={sourceId}
                  onChange={(e) => setSourceId(e.target.value)}
                >
                  <option value="">출발 환경 없음</option>
                  {environments.map((env) => (
                    <option key={env.id} value={env.id}>
                      {env.name} · {env.cloud.toUpperCase()} · {env.region}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <fieldset className="workflow-targets">
              <legend>
                대상 환경 <small>1~2개 · 출발 환경 제외</small>
              </legend>
              {environments
                .filter((env) => env.id !== sourceId)
                .map((env) => (
                  <label key={env.id} className="workflow-target">
                    <input
                      type="checkbox"
                      checked={targetIds.includes(env.id)}
                      disabled={
                        !targetIds.includes(env.id) && targetIds.length >= 2
                      }
                      onChange={() => toggleTarget(env.id)}
                    />
                    <span>
                      {env.name}
                      <small>
                        {env.cloud.toUpperCase()} · {env.region}
                      </small>
                    </span>
                  </label>
                ))}
              {environments.filter((env) => env.id !== sourceId).length ===
                0 && (
                <p className="muted">
                  출발과 다른 대상 환경이 필요합니다. 운영자에게 환경 등록을
                  요청하세요.
                </p>
              )}
            </fieldset>
            {!readyBuilds.length && (
              <p className="muted">
                고정된 이미지 digest가 있는 성공 빌드가 필요합니다. 완료된
                이미지 빌드가 없습니다.
              </p>
            )}
            <button
              className="primary"
              disabled={
                busy ||
                !buildId ||
                targetIds.length < 1 ||
                targetIds.some((id) => id === sourceId) ||
                (kind === "migration" && !sourceId)
              }
            >
              {busy ? "계획 저장 중…" : "변경 계획 만들기"}
            </button>
          </form>
        </details>
      )}
      <div className="workflow-existing">
        <h3>최근 계획</h3>
        {loading ? (
          <p className="muted">최근 계획을 불러오는 중입니다.</p>
        ) : workflows.length ? (
          <label>
            계획 선택
            <select
              aria-label="최근 계획 선택"
              value={selectedWorkflowId}
              onChange={(e) => {
                setWorkflow(null);
                setExecutions([]);
                setSelectedWorkflowId(e.target.value);
              }}
            >
              {workflows.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.kind === "migration" ? "이전" : "배포"} ·{" "}
                  {workflowStatus[item.status]} · {runTime(item.created_at)}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <p className="muted">저장된 계획이 없습니다.</p>
        )}
        <EnvironmentComparison
          environments={environments}
          sourceId={
            workflow ? (workflow.input_snapshot.source?.id ?? null) : sourceId
          }
          targetIds={
            workflow
              ? workflow.input_snapshot.targets.map((t) => t.id)
              : targetIds
          }
          executions={workflow ? executions : []}
          workflowId={workflow?.id ?? null}
          planHash={workflow?.input_hash ?? null}
        />
        {workflow && (
          <div className="workflow-detail">
            <div className="workflow-detail-heading">
              <strong>
                {workflow.kind === "migration" ? "서비스 이전" : "앱 배포"}
              </strong>
              <span className={`status ${workflow.status}`}>
                {workflowStatus[workflow.status]}
              </span>
            </div>
            <p className="workflow-route">
              {workflow.input_snapshot.source
                ? environmentName(workflow.input_snapshot.source.id)
                : "출발 환경 없음"}
              {workflow.input_snapshot.targets.map((target) => (
                <span key={target.id}>
                  <i aria-hidden="true">→</i> {environmentName(target.id)}
                </span>
              ))}
            </p>
            <details className="release-plan-details">
              <summary>승인 대상 · 커밋 · 이미지 · 계획 버전</summary>
              <dl className="workflow-facts">
                <div>
                  <dt>커밋</dt>
                  <dd>
                    <code>
                      {workflow.input_snapshot.commit_sha.slice(0, 12)}
                    </code>
                  </dd>
                </div>
                <div>
                  <dt>이미지</dt>
                  <dd>
                    <code>{workflow.input_snapshot.image}</code>
                  </dd>
                </div>
                <div>
                  <dt>계획 해시</dt>
                  <dd>
                    <code>{workflow.input_hash.slice(0, 16)}…</code>
                  </dd>
                </div>
                <div>
                  <dt>계획 승인</dt>
                  <dd>
                    {workflow.approved_by
                      ? `${workflow.approved_by} · ${workflow.approved_at ? runTime(workflow.approved_at) : ""}`
                      : "승인 대기"}
                  </dd>
                </div>
              </dl>
            </details>
            {workflow.status === "planned" && (
              <button disabled={busy} onClick={approvePlan}>
                계획 승인
              </button>
            )}
            <p className="workflow-boundary">
              승인 범위는 선택한 단일 이미지와 대상 환경입니다. FE·BE 묶음 승인
              및 AI 코드 수정 승인은 아직 연결되지 않았습니다. 계획 승인은 입력
              해시를 확인해 기록합니다. 배포 또는 DB 쓰기 전환은 시작되지
              않습니다.
            </p>
            <div className="workflow-executions">
              <h4>최근 실행</h4>
              {executions.length ? (
                <ul>
                  {executions.map((execution) => (
                    <li key={execution.id}>
                      <span>
                        {environmentName(execution.environment_id)} ·{" "}
                        {executionKindLabel(execution.kind)}
                      </span>
                      <b>{executionLabel(execution.status)}</b>
                      <time>{runTime(execution.created_at)}</time>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">이 계획에 연결된 실행 기록이 없습니다.</p>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
