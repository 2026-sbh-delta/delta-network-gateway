import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  message,
  type DeploymentOption,
  type DeploymentPlan,
  type DeploymentTargetInput,
  type Page,
  type Run,
  type Service,
} from "./api";

type Environment = {
  id: string;
  name: string;
  cloud: DeploymentTargetInput["cloud"];
  region: string;
  version: string | null;
  runtime_status?: "active" | "removing" | "removed" | string;
};
const cloudName = { aws: "AWS", gcp: "GCP", onprem: "온프레미스" };
const planStatus: Record<string, string> = {
  planned: "계획 검토 대기",
  queued: "실행 대기",
  running: "대상별 배포 중",
  succeeded: "대상 앱 배포 완료",
  partial: "일부 대상 배포 실패",
  failed: "배포 실패",
};
const targetStatus: Record<string, string> = {
  planned: "계획 준비됨",
  queued: "실행 대기",
  running: "배포 중",
  succeeded: "앱 배포 완료",
  failed: "배포 실패",
};
// The security pipeline (build → SAST → staging → DAST → diff → GCP release → cutover) owns the
// GCP migration target: its release uses the same GCP deployment, so the platform plan skips GCP.
type PipelineOptions = {
  repo: string;
  release_targets: { id: string; cloud?: string; label: string }[];
  cutover_configured: boolean;
};
const sameRepo = (a: string, b: string) =>
  a.toLowerCase().replace(/(\.git)?\/*$/, "") ===
  b.toLowerCase().replace(/(\.git)?\/*$/, "");
const readyBuild = (run: Run) =>
  run.kind === "image_build" &&
  run.status === "succeeded" &&
  !!run.result?.image &&
  !!run.result?.image_digest &&
  !!run.resolved_commit_sha;

async function listServices(signal: AbortSignal) {
  const items: Service[] = [];
  let cursor: string | null = null;
  do {
    const queryString: string = cursor
      ? `?cursor=${encodeURIComponent(cursor)}`
      : "";
    const resultPage: Page<Service> = await api<Page<Service>>(
      `/services${queryString}`,
      undefined,
      undefined,
      signal,
    );
    items.push(...resultPage.items);
    cursor = resultPage.next_cursor;
  } while (cursor && !signal.aborted);
  return items;
}

export function DeploymentPanel({ serviceId }: { serviceId: string }) {
  const [options, setOptions] = useState<DeploymentOption[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [hasEnvironmentNotRemoved, setHasEnvironmentNotRemoved] =
    useState(false);
  const [builds, setBuilds] = useState<Run[]>([]);
  const [backendCandidates, setBackendCandidates] = useState<Service[]>([]);
  const [backendServiceId, setBackendServiceId] = useState("");
  const [backendBuilds, setBackendBuilds] = useState<Run[]>([]);
  const [backendBuildLoading, setBackendBuildLoading] = useState(false);
  const [backendBuildError, setBackendBuildError] = useState("");
  const [plans, setPlans] = useState<DeploymentPlan[]>([]);
  const [selectedPlan, setSelectedPlan] = useState<DeploymentPlan | null>(null);
  const [kind, setKind] = useState<"deployment" | "migration">("deployment");
  const [sourceId, setSourceId] = useState("");
  const [buildId, setBuildId] = useState("");
  const [backendBuildId, setBackendBuildId] = useState("");
  const [targets, setTargets] = useState<DeploymentTargetInput[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const createKey = useRef<{ body: string; key: string } | null>(null);
  const executeKey = useRef("");
  const pipelineKey = useRef<{ body: string; key: string } | null>(null);
  const [pipelineRun, setPipelineRun] = useState("");
  const [service, setService] = useState<Service | null>(null);
  const [pipeline, setPipeline] = useState<PipelineOptions | null>(null);
  const buildsReady = useMemo(() => builds.filter(readyBuild), [builds]);

  useEffect(() => {
    const controller = new AbortController();
    setPipeline(null);
    api<PipelineOptions>(
      "/pipeline/options",
      undefined,
      undefined,
      controller.signal,
    )
      .then((data) => {
        if (!controller.signal.aborted) setPipeline(data);
      })
      // Without the pipeline link the panel keeps the platform-only plan flow.
      .catch(() => undefined);
    return () => controller.abort();
  }, [serviceId]);

  useEffect(() => {
    const controller = new AbortController();
    setOptions([]);
    setEnvironments([]);
    setHasEnvironmentNotRemoved(false);
    setBuilds([]);
    setBackendCandidates([]);
    setBackendServiceId("");
    setBackendBuilds([]);
    setBackendBuildLoading(false);
    setBackendBuildError("");
    setPlans([]);
    setSelectedPlan(null);
    setKind("deployment");
    setSourceId("");
    setBuildId("");
    setBackendBuildId("");
    setTargets([]);
    setError("");
    setNotice("");
    createKey.current = null;
    executeKey.current = "";
    setLoading(true);
    Promise.all([
      api<{ items: DeploymentOption[] }>(
        "/deployment-options",
        undefined,
        undefined,
        controller.signal,
      ),
      api<{ items: Environment[] }>(
        `/services/${serviceId}/environments`,
        undefined,
        undefined,
        controller.signal,
      ),
      api<Page<Run>>(
        `/services/${serviceId}/builds`,
        undefined,
        undefined,
        controller.signal,
      ),
      api<{ items: DeploymentPlan[] }>(
        `/services/${serviceId}/deployment-plans`,
        undefined,
        undefined,
        controller.signal,
      ),
      api<Service>(
        `/services/${serviceId}`,
        undefined,
        undefined,
        controller.signal,
      ),
      listServices(controller.signal).catch(() => [] as Service[]),
    ])
      .then(([catalog, envs, buildPage, planPage, service, services]) => {
        if (controller.signal.aborted) return;
        const active = envs.items.filter(
          (item) =>
            item.runtime_status !== "removed" &&
            item.runtime_status !== "removing",
        );
        setHasEnvironmentNotRemoved(
          envs.items.some((item) => item.runtime_status !== "removed"),
        );
        const candidates = service.project_id
          ? services.filter(
              (candidate) =>
                candidate.id !== service.id &&
                candidate.project_id === service.project_id &&
                candidate.repository_url === service.repository_url,
            )
          : [];
        setService(service);
        setOptions(catalog.items);
        setEnvironments(active);
        setBuilds(buildPage.items);
        setBackendCandidates(candidates);
        setBackendServiceId(candidates.length === 1 ? candidates[0].id : "");
        setBackendBuilds([]);
        setBackendBuildId("");
        setPlans(planPage.items);
        setKind(active.length > 0 ? "migration" : "deployment");
        setSourceId(active[0]?.id ?? "");
        const initialBuild = buildPage.items.find(readyBuild);
        setBuildId(initialBuild?.id ?? "");
        const registered = service.deployment_target;
        const registeredOption = catalog.items.find(
          (item) => item.cloud === registered?.cloud,
        );
        const validRegistered =
          registered &&
          registeredOption?.profiles.some(
            (profile) => profile.id === registered.profile_id,
          ) &&
          (registered.cloud !== "onprem" ||
            registeredOption.hosts.some(
              (host) => host.id === registered.host_id,
            ));
        if (active.length === 0 && validRegistered) setTargets([registered]);
        setSelectedPlan(planPage.items[0] ?? null);
        setError("");
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(message(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [serviceId, reload]);

  useEffect(() => {
    const candidate = backendCandidates.find(
      (item) => item.id === backendServiceId,
    );
    setBackendBuilds([]);
    setBackendBuildId("");
    setBackendBuildError("");
    if (!candidate) {
      setBackendBuildLoading(false);
      return;
    }
    const controller = new AbortController();
    setBackendBuildLoading(true);
    api<Page<Run>>(
      `/services/${candidate.id}/builds`,
      undefined,
      undefined,
      controller.signal,
    )
      .then((page) => {
        if (!controller.signal.aborted)
          setBackendBuilds(page.items.filter(readyBuild));
      })
      .catch((e) => {
        if (!controller.signal.aborted) setBackendBuildError(message(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setBackendBuildLoading(false);
      });
    return () => controller.abort();
  }, [backendCandidates, backendServiceId]);

  const activeEnvironments = environments;
  const hasActiveEnvironment = activeEnvironments.length > 0;
  const matchingBackendBuilds = useMemo(() => {
    const commit = buildsReady.find(
      (build) => build.id === buildId,
    )?.resolved_commit_sha;
    return backendBuilds.filter(
      (item) =>
        item.id !== buildId && (!commit || item.resolved_commit_sha === commit),
    );
  }, [backendBuilds, buildId, buildsReady]);

  useEffect(() => {
    if (!backendBuildId) return;
    if (matchingBackendBuilds.some((item) => item.id === backendBuildId))
      return;
    setBackendBuildId("");
  }, [backendBuildId, matchingBackendBuilds]);

  const selectedBackendService = backendCandidates.find(
    (item) => item.id === backendServiceId,
  );

  useEffect(() => {
    if (!selectedPlan || !["queued", "running"].includes(selectedPlan.status))
      return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const current = await api<DeploymentPlan>(
          `/deployment-plans/${selectedPlan.id}`,
        );
        if (stopped) return;
        setSelectedPlan(current);
        setPlans((items) =>
          items.map((item) => (item.id === current.id ? current : item)),
        );
        if (["queued", "running"].includes(current.status)) {
          timer = setTimeout(poll, 2500);
        }
      } catch (e) {
        if (!stopped) setError(message(e));
      }
    };
    timer = setTimeout(poll, 1200);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [selectedPlan?.id, selectedPlan?.status]);

  useEffect(() => {
    if (
      !selectedPlan ||
      !["succeeded", "partial", "failed"].includes(selectedPlan.status)
    )
      return;
    let stopped = false;
    api<{ items: Environment[] }>(`/services/${serviceId}/environments`)
      .then((result) => {
        if (stopped) return;
        const active = result.items.filter(
          (item) =>
            item.runtime_status !== "removed" &&
            item.runtime_status !== "removing",
        );
        setEnvironments(active);
        setHasEnvironmentNotRemoved(
          result.items.some((item) => item.runtime_status !== "removed"),
        );
        if (active.length > 0) {
          setKind("migration");
          setSourceId((currentId) =>
            active.some((item) => item.id === currentId)
              ? currentId
              : active[0].id,
          );
        }
      })
      .catch((e) => {
        if (!stopped) setError(message(e));
      });
    return () => {
      stopped = true;
    };
  }, [selectedPlan?.id, selectedPlan?.status, serviceId]);

  function chooseTarget(option: DeploymentOption, checked: boolean) {
    setTargets((current) => {
      if (!checked)
        return current.filter((item) => item.cloud !== option.cloud);
      if (current.some((item) => item.cloud === option.cloud)) return current;
      const max = kind === "deployment" ? 1 : 2;
      if (current.length >= max) return current;
      const target: DeploymentTargetInput = {
        cloud: option.cloud,
        region: option.region,
        profile_id: option.profiles[0]?.id ?? "",
        ...(option.cloud === "onprem" && option.hosts[0]
          ? { host_id: option.hosts[0].id }
          : {}),
      };
      return [...current, target];
    });
  }
  function updateTarget(
    cloud: DeploymentTargetInput["cloud"],
    patch: Partial<DeploymentTargetInput>,
  ) {
    setTargets((items) =>
      items.map((item) =>
        item.cloud === cloud ? { ...item, ...patch } : item,
      ),
    );
  }
  function setKindAndTargets(next: "deployment" | "migration") {
    setKind(next);
    setTargets((items) => (next === "deployment" ? items.slice(0, 1) : items));
  }
  async function createPlan(event: React.FormEvent) {
    event.preventDefault();
    if (usePipeline) return startPipeline();
    const body = {
      kind,
      build_id: buildId,
      ...(backendBuildId ? { backend_build_id: backendBuildId } : {}),
      ...(kind === "migration" ? { source_environment_id: sourceId } : {}),
      targets,
    };
    const serialized = JSON.stringify(body);
    if (createKey.current?.body !== serialized)
      createKey.current = { body: serialized, key: crypto.randomUUID() };
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const plan = await api<DeploymentPlan>(
        `/services/${serviceId}/deployment-plans`,
        body,
        createKey.current.key,
      );
      createKey.current = null;
      setSelectedPlan(plan);
      setPlans((items) => [
        plan,
        ...items.filter((item) => item.id !== plan.id),
      ]);
      setNotice(
        "계획을 만들었습니다. 내용을 확인한 뒤 배포 실행을 승인하세요.",
      );
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function execute() {
    if (!selectedPlan) return;
    setBusy(true);
    setError("");
    setNotice("");
    executeKey.current ||= crypto.randomUUID();
    try {
      const current = await api<DeploymentPlan>(
        `/deployment-plans/${selectedPlan.id}/execute`,
        { input_hash: selectedPlan.input_hash },
        executeKey.current,
      );
      executeKey.current = "";
      setSelectedPlan(current);
      setPlans((items) =>
        items.map((item) => (item.id === current.id ? current : item)),
      );
      setNotice("배포 실행을 접수했습니다. 대상별 진행 결과를 확인하세요.");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  // GCP goes through the security pipeline; any other target (on-premises) still runs as a
  // platform plan, approved by the same click. Then the pipeline graph takes over the screen.
  async function startPipeline() {
    if (!service || !chosenBuild?.resolved_commit_sha || !gcpRelease) return;
    const run = {
      branch: service.source_ref,
      commit: chosenBuild.resolved_commit_sha,
      cutover: !!pipeline?.cutover_configured,
      release_target: gcpRelease.id,
    };
    const serialized = JSON.stringify(run);
    if (pipelineKey.current?.body !== serialized)
      pipelineKey.current = { body: serialized, key: crypto.randomUUID() };
    setBusy(true);
    setError("");
    setNotice("");
    let runId = "";
    try {
      runId = (
        await api<{ run_id: string }>(
          "/pipeline/runs",
          run,
          pipelineKey.current.key,
        )
      ).run_id;
      pipelineKey.current = null;
      if (platformTargets.length > 0) {
        const plan = await api<DeploymentPlan>(
          `/services/${serviceId}/deployment-plans`,
          {
            kind,
            build_id: buildId,
            ...(backendBuildId ? { backend_build_id: backendBuildId } : {}),
            source_environment_id: sourceId,
            targets: platformTargets,
          },
          crypto.randomUUID(),
        );
        await api<DeploymentPlan>(
          `/deployment-plans/${plan.id}/execute`,
          { input_hash: plan.input_hash },
          crypto.randomUUID(),
        );
      }
      location.hash = `/pipeline/${encodeURIComponent(runId)}`;
    } catch (e) {
      setError(
        runId
          ? `파이프라인은 시작했지만 ${platformTargets.map((t) => cloudName[t.cloud]).join("·")} 배포를 접수하지 못했습니다: ${message(e)}`
          : message(e),
      );
      if (runId) setPipelineRun(runId);
    } finally {
      setBusy(false);
    }
  }

  const source = environments.find((item) => item.id === sourceId);
  const chosenBuild = buildsReady.find((item) => item.id === buildId);
  const gcpRelease = pipeline?.release_targets.find((t) => t.cloud === "gcp");
  const pipelineRepo =
    !!pipeline && !!service && sameRepo(pipeline.repo, service.repository_url);
  const usePipeline =
    kind === "migration" &&
    targets.some((t) => t.cloud === "gcp") &&
    pipelineRepo &&
    !!gcpRelease;
  const platformTargets = usePipeline
    ? targets.filter((t) => t.cloud !== "gcp")
    : targets;
  const canCreate =
    !!chosenBuild &&
    targets.length === (kind === "deployment" ? 1 : targets.length) &&
    targets.length > 0 &&
    targets.every((target) => {
      const option = options.find((item) => item.cloud === target.cloud);
      return (
        !!option?.profiles.some(
          (profile) => profile.id === target.profile_id,
        ) &&
        (target.cloud !== "onprem" ||
          !!option.hosts.some((host) => host.id === target.host_id))
      );
    }) &&
    (kind !== "deployment" || !hasEnvironmentNotRemoved) &&
    (kind !== "migration" ||
      (targets.length >= 1 &&
        targets.length <= 2 &&
        !!source &&
        targets.every((target) => target.cloud !== source.cloud)));

  return (
    <section className="panel deployment-panel" aria-label="최초 배포 및 이전">
      <div className="panel-top">
        <div>
          <div className="eyebrow">DEPLOYMENT</div>
          <h2>{kind === "deployment" ? "최초 배포" : "다중 대상 이전"}</h2>
        </div>
        <button onClick={() => setReload((n) => n + 1)} disabled={loading}>
          새로고침
        </button>
      </div>
      <p className="muted">
        대상 환경과 검증된 이미지를 선택해 계획을 만들고, 검토 후 실행을
        승인합니다.
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
        <p role="status">배포 선택 항목을 불러오는 중입니다.</p>
      ) : (
        <>
          <form className="deployment-form" onSubmit={createPlan}>
            <fieldset className="choice-row">
              <legend>작업</legend>
              <label>
                <input
                  type="radio"
                  name="deploy-kind"
                  checked={kind === "deployment"}
                  disabled={hasEnvironmentNotRemoved}
                  onChange={() => setKindAndTargets("deployment")}
                />
                최초 배포 · 대상 1개
              </label>
              <label>
                <input
                  type="radio"
                  name="deploy-kind"
                  checked={kind === "migration"}
                  onChange={() => setKindAndTargets("migration")}
                />
                이전 · 대상 1~2개
              </label>
            </fieldset>
            {hasActiveEnvironment && kind === "migration" && (
              <p className="notice">
                이미 배포된 환경이 있어 최초 배포를 사용할 수 없습니다. 현재
                환경을 출발점으로 이전 대상을 선택하세요.
              </p>
            )}
            {!hasActiveEnvironment && hasEnvironmentNotRemoved && (
              <p className="notice">
                기존 환경이 제거 중입니다. 작업이 끝난 뒤 이전 계획을 만들 수
                있습니다.
              </p>
            )}
            {kind === "migration" && (
              <label>
                현재 출발 환경
                <select
                  aria-label="현재 출발 환경"
                  value={sourceId}
                  onChange={(e) => setSourceId(e.target.value)}
                >
                  <option value="">환경 선택</option>
                  {environments.map((env) => (
                    <option key={env.id} value={env.id}>
                      {env.name} · {cloudName[env.cloud]} · {env.region}
                    </option>
                  ))}
                </select>
                <small>출발 환경은 그대로 유지됩니다.</small>
              </label>
            )}
            <label>
              검증된 이미지
              <select
                aria-label="검증된 이미지"
                value={buildId}
                onChange={(e) => setBuildId(e.target.value)}
              >
                <option value="">이미지를 선택하세요</option>
                {buildsReady.map((build) => (
                  <option key={build.id} value={build.id}>
                    {build.resolved_commit_sha?.slice(0, 12)} ·{" "}
                    {build.result?.image}
                  </option>
                ))}
              </select>
              {buildsReady.length === 0 && (
                <small>먼저 빌드와 검사에서 성공한 이미지를 준비하세요.</small>
              )}
            </label>
            {backendCandidates.length > 0 && (
              <label>
                백엔드 서비스 (선택)
                <select
                  aria-label="백엔드 서비스 (선택)"
                  value={backendServiceId}
                  onChange={(e) => setBackendServiceId(e.target.value)}
                >
                  <option value="">선택 안 함</option>
                  {backendCandidates.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {candidate.name}
                    </option>
                  ))}
                </select>
                <small>
                  같은 프로젝트와 저장소의 다른 서비스만 표시합니다.
                </small>
              </label>
            )}
            {selectedBackendService && backendBuildLoading && (
              <p className="muted" role="status">
                {selectedBackendService.name}의 빌드를 불러오는 중입니다.
              </p>
            )}
            {backendBuildError && (
              <p className="notice" role="alert">
                백엔드 빌드를 조회하지 못했습니다: {backendBuildError}
              </p>
            )}
            {selectedBackendService &&
              !backendBuildLoading &&
              matchingBackendBuilds.length > 0 && (
                <label>
                  백엔드 빌드 (선택 · {selectedBackendService.name})
                  <select
                    aria-label={`백엔드 빌드 (선택 · ${selectedBackendService.name})`}
                    value={backendBuildId}
                    onChange={(e) => setBackendBuildId(e.target.value)}
                  >
                    <option value="">선택 안 함</option>
                    {matchingBackendBuilds.map((build) => (
                      <option key={build.id} value={build.id}>
                        {build.resolved_commit_sha?.slice(0, 12)} ·{" "}
                        {build.result?.image}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            {selectedBackendService &&
              !backendBuildLoading &&
              !backendBuildError &&
              matchingBackendBuilds.length === 0 && (
                <p className="notice">
                  {selectedBackendService.name}에 현재 서비스와 같은 커밋의 성공
                  빌드가 없습니다.
                </p>
              )}
            <fieldset className="deployment-targets">
              <legend>
                {kind === "deployment"
                  ? "배포 환경 하나를 선택하세요"
                  : "이전 대상 환경 1~2개를 선택하세요"}
              </legend>
              <div className="deployment-options">
                {options.map((option) => {
                  const selected = targets.some(
                    (item) => item.cloud === option.cloud,
                  );
                  const disabled =
                    (!selected &&
                      targets.length >= (kind === "deployment" ? 1 : 2)) ||
                    (kind === "migration" &&
                      !!source &&
                      source.cloud === option.cloud);
                  const target = targets.find(
                    (item) => item.cloud === option.cloud,
                  );
                  return (
                    <article
                      className={`deployment-option ${selected ? "selected" : ""}`}
                      key={option.cloud}
                    >
                      <label className="target-select">
                        <input
                          type="checkbox"
                          checked={selected}
                          disabled={disabled}
                          onChange={(e) =>
                            chooseTarget(option, e.target.checked)
                          }
                        />
                        <span>
                          <strong>
                            {option.label || cloudName[option.cloud]}
                          </strong>
                          <small>
                            {option.runtime} · {option.region}
                          </small>
                        </span>
                      </label>
                      {option.reason && (
                        <p className="target-reason">{option.reason}</p>
                      )}
                      {target && (
                        <div className="target-config">
                          <label>
                            사양
                            <select
                              aria-label={`${cloudName[option.cloud]} 사양`}
                              value={target.profile_id}
                              onChange={(e) =>
                                updateTarget(option.cloud, {
                                  profile_id: e.target.value,
                                })
                              }
                            >
                              <option value="">사양 선택</option>
                              {option.profiles.map((profile) => (
                                <option key={profile.id} value={profile.id}>
                                  {profile.label} · {profile.cpu} ·{" "}
                                  {profile.memory_mb} MB
                                </option>
                              ))}
                            </select>
                          </label>
                          {option.cloud === "onprem" && (
                            <label>
                              등록 호스트
                              <select
                                aria-label="온프레미스 등록 호스트"
                                value={target.host_id ?? ""}
                                onChange={(e) =>
                                  updateTarget(option.cloud, {
                                    host_id: e.target.value,
                                  })
                                }
                              >
                                <option value="">호스트 선택</option>
                                {option.hosts.map((host) => (
                                  <option key={host.id} value={host.id}>
                                    {host.label}
                                  </option>
                                ))}
                              </select>
                              <small>
                                선택한 호스트의 앱 자원 한도 안에서 배포합니다.
                                새 VM을 만들지 않습니다.
                              </small>
                            </label>
                          )}
                          {!option.configured && (
                            <p className="notice">
                              연결 설정이 아직 준비되지 않아 계획 실행은 막힐 수
                              있습니다. {option.reason}
                            </p>
                          )}
                        </div>
                      )}
                    </article>
                  );
                })}
              </div>
              {options.length === 0 && (
                <p className="notice">
                  배포 환경 목록을 사용할 수 없습니다. 설정이 준비되면 다시
                  불러오세요.
                </p>
              )}
            </fieldset>
            {usePipeline && (
              <div className="pipeline-handoff">
                <strong>GCP · 보안 파이프라인</strong>
                <span>
                  빌드 → SAST → 스테이징 → DAST·AI 수정 → 차분 → 운영 배포 승인
                  → 도메인 전환
                </span>
                {platformTargets.length > 0 && (
                  <span>
                    {platformTargets.map((t) => cloudName[t.cloud]).join("·")} ·
                    플랫폼 배포 함께 실행
                  </span>
                )}
              </div>
            )}
            {kind === "migration" &&
              targets.some((t) => t.cloud === "gcp") &&
              pipeline &&
              !pipelineRepo && (
                <p className="notice">
                  보안 파이프라인 대상 저장소가 아닙니다 ·{" "}
                  {pipeline.repo.replace("https://github.com/", "")}만 연결 ·
                  플랫폼 배포 계획으로 진행
                </p>
              )}
            <button className="primary" disabled={busy || !canCreate}>
              {usePipeline
                ? busy
                  ? "시작 중…"
                  : "파이프라인 시작"
                : busy
                  ? "계획 만드는 중…"
                  : "배포 계획 만들기"}
            </button>
            {pipelineRun && (
              <a
                className="pipeline-link"
                href={`#/pipeline/${encodeURIComponent(pipelineRun)}`}
              >
                파이프라인 화면 열기 →
              </a>
            )}
          </form>
          {plans.length > 0 && (
            <div className="deployment-history">
              <h3>계획과 실행 결과</h3>
              <label>
                계획 선택
                <select
                  aria-label="배포 계획 선택"
                  value={selectedPlan?.id ?? ""}
                  onChange={(e) => {
                    const plan = plans.find(
                      (item) => item.id === e.target.value,
                    );
                    setSelectedPlan(plan ?? null);
                  }}
                >
                  <option value="">계획 선택</option>
                  {plans.map((plan) => (
                    <option key={plan.id} value={plan.id}>
                      {plan.kind === "deployment" ? "최초 배포" : "이전"} ·{" "}
                      {planStatus[plan.status] ?? plan.status} ·{" "}
                      {new Date(plan.created_at).toLocaleString("ko-KR")}
                    </option>
                  ))}
                </select>
              </label>
              {selectedPlan && (
                <div className="deployment-review">
                  <div className="panel-top">
                    <h4>
                      {selectedPlan.kind === "deployment"
                        ? "배포 계획 검토"
                        : "이전 계획 검토"}
                    </h4>
                    <span className={`status ${selectedPlan.status}`}>
                      {planStatus[selectedPlan.status] ?? selectedPlan.status}
                    </span>
                  </div>
                  <p>
                    <strong>이미지</strong>{" "}
                    <code>{selectedPlan.input_snapshot.image}</code>
                  </p>
                  <p>
                    <strong>커밋</strong>{" "}
                    <code>{selectedPlan.input_snapshot.commit_sha}</code>
                  </p>
                  {selectedPlan.input_snapshot.components.length > 0 && (
                    <div className="deployment-components">
                      <h5>계획에 포함된 앱 구성</h5>
                      {selectedPlan.input_snapshot.components.map(
                        (component) => (
                          <p key={component.build_id}>
                            <strong>
                              {component.name === "backend" ? "백엔드" : "앱"}
                            </strong>{" "}
                            <code>{component.image}</code>
                            <small>
                              빌드 {component.build_id} · 포트{" "}
                              {component.container_port}
                            </small>
                          </p>
                        ),
                      )}
                    </div>
                  )}
                  {selectedPlan.reason && (
                    <p className="notice">{selectedPlan.reason}</p>
                  )}
                  <div className="target-progress">
                    {selectedPlan.targets.map((item, index) => {
                      const option = options.find(
                        (candidate) => candidate.cloud === item.target.cloud,
                      );
                      return (
                        <article key={`${item.target.cloud}-${index}`}>
                          <div className="panel-top">
                            <strong>
                              {cloudName[item.target.cloud]} ·{" "}
                              {item.target.region}
                            </strong>
                            <span className={`status ${item.status}`}>
                              {targetStatus[item.status] ?? item.status}
                            </span>
                          </div>
                          <p>
                            {option?.profiles.find(
                              (profile) =>
                                profile.id === item.target.profile_id,
                            )?.label ?? item.target.profile_id}
                          </p>
                          {item.service_url && (
                            <p>
                              <a
                                href={item.service_url}
                                target="_blank"
                                rel="noreferrer"
                              >
                                대상 앱 주소 (연결된 네트워크에서 접속) ↗
                              </a>
                            </p>
                          )}
                          {item.reason && (
                            <p className="error">{item.reason}</p>
                          )}
                          {item.run?.error && (
                            <p className="error">{item.run.error.message}</p>
                          )}
                        </article>
                      );
                    })}
                  </div>
                  {selectedPlan.kind === "migration" && (
                    <p className="notice">
                      대상 앱 배포 결과입니다. 트래픽·데이터베이스 전환과 전체
                      이전 완료는 여기서 처리하지 않습니다.
                    </p>
                  )}
                  <button
                    className="primary"
                    onClick={execute}
                    disabled={
                      busy ||
                      selectedPlan.status !== "planned" ||
                      !selectedPlan.executable
                    }
                  >
                    {busy ? "접수 중…" : "검토 후 배포 실행 승인"}
                  </button>
                  {!selectedPlan.executable && (
                    <small className="blocked-reason">
                      {selectedPlan.reason ||
                        "서버 연결 또는 대상 환경 설정이 준비되지 않아 실행할 수 없습니다."}
                    </small>
                  )}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
