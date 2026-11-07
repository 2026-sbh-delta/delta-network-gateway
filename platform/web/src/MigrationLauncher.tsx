import { useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  message,
  type DeploymentOption,
  type DeploymentPlan,
  type DeploymentTargetInput,
  type Page,
  type Run,
  type Service,
} from "./api";
import { openPipelineRun } from "./pipeline-handoff";
import "./migration-launcher.css";

const names = { aws: "AWS", gcp: "GCP", onprem: "On-Premise" };
const order = { aws: 0, gcp: 1, onprem: 2 };
const defaultProfiles = { aws: "t3.micro", gcp: "e2-micro", onprem: "small" };
type Environment = {
  id: string;
  cloud: DeploymentTargetInput["cloud"];
  runtime_status?: string;
};
type PipelineOptions = {
  repo: string;
  commits?: { sha: string }[];
  release_targets: { id: string; cloud?: string }[];
  cutover_configured: boolean;
};
const sameRepo = (value: string) =>
  value.toLowerCase().replace(/(\.git)?\/*$/, "");

export function MigrationLauncher({ service }: { service: Service }) {
  const [options, setOptions] = useState<DeploymentOption[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [build, setBuild] = useState<Run>();
  const [pipeline, setPipeline] = useState<PipelineOptions>();
  const [targets, setTargets] = useState<DeploymentTargetInput[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const pending = useRef(false);
  const requestKey = useRef<{ body: string; key: string } | undefined>(
    undefined,
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setTargets([]);
    setOptions([]);
    setPipeline(undefined);
    setBuild(undefined);
    setEnvironments([]);
    requestKey.current = undefined;
    const read = <T,>(path: string) =>
      api<T>(path, undefined, undefined, controller.signal);
    Promise.all([
      read<{ items: DeploymentOption[] }>("/deployment-options"),
      read<{ items: Environment[] }>(`/services/${service.id}/environments`),
      read<Page<Run>>(`/services/${service.id}/builds`).catch(() => ({
        items: [],
        next_cursor: null,
      })),
      read<PipelineOptions>(
        `/pipeline/options?branch=${encodeURIComponent(service.source_ref)}`,
      ),
    ])
      .then(([catalog, envs, builds, connection]) => {
        if (controller.signal.aborted) return;
        setOptions(
          [...catalog.items].sort((a, b) => order[a.cloud] - order[b.cloud]),
        );
        setEnvironments(
          envs.items.filter(
            (env) =>
              !["removed", "removing"].includes(env.runtime_status ?? ""),
          ),
        );
        setBuild(
          builds.items.find(
            (run) =>
              run.kind === "image_build" &&
              run.status === "succeeded" &&
              run.result?.image &&
              run.result?.image_digest &&
              run.resolved_commit_sha,
          ),
        );
        setPipeline(connection);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(message(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [service.id, service.source_ref, reload]);

  const sourceCloud =
    service.deployment_target?.cloud ?? service.application?.target?.cloud;
  const source = sourceCloud
    ? environments.find((env) => env.cloud === sourceCloud)
    : environments[0];
  const release = pipeline?.release_targets.find(
    (target) => target.cloud === "gcp",
  );
  const commit = pipeline?.commits?.[0]?.sha;
  const linked =
    !!pipeline && sameRepo(pipeline.repo) === sameRepo(service.repository_url);
  const platformTargets = targets.filter((target) => target.cloud !== "gcp");
  const validTargets =
    targets.length > 0 &&
    targets.length <= 2 &&
    targets.every((target) => {
      const option = options.find((item) => item.cloud === target.cloud);
      return (
        target.cloud !== source?.cloud &&
        option?.profiles.some((profile) => profile.id === target.profile_id) &&
        (target.cloud !== "onprem" ||
          option.hosts.some((host) => host.id === target.host_id))
      );
    });
  const canStart =
    !!source &&
    linked &&
    !!release &&
    !!commit &&
    validTargets &&
    targets.some((target) => target.cloud === "gcp") &&
    (platformTargets.length === 0 || !!build);

  function choose(option: DeploymentOption, selected: boolean) {
    setTargets((current) =>
      selected
        ? current.filter((target) => target.cloud !== option.cloud)
        : current.length < 2 && option.cloud !== source?.cloud
          ? [
              ...current,
              {
                cloud: option.cloud,
                region: option.region,
                profile_id:
                  option.profiles.find(
                    (profile) => profile.id === defaultProfiles[option.cloud],
                  )?.id ??
                  option.profiles[0]?.id ??
                  "",
                ...(option.cloud === "onprem"
                  ? { host_id: option.hosts[0]?.id ?? "" }
                  : {}),
              },
            ]
          : current,
    );
  }
  async function start(event: React.FormEvent) {
    event.preventDefault();
    if (!canStart || pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    const body = {
      branch: service.source_ref,
      commit: commit!,
      cutover: !!pipeline?.cutover_configured,
      release_target: release!.id,
      project_id: service.id,
    };
    const serialized = JSON.stringify({ body, targets, build: build?.id });
    if (requestKey.current?.body !== serialized)
      requestKey.current = { body: serialized, key: crypto.randomUUID() };
    const launch = () =>
      api<{ run_id: string }>("/pipeline/runs", body, requestKey.current!.key);
    let runId = "";
    try {
      let started;
      try {
        started = await launch();
      } catch (reason) {
        // A lost response can leave a running pipeline. Retry only with the same idempotency key.
        if (reason instanceof ApiError && reason.status < 500) throw reason;
        started = await launch();
      }
      if (
        typeof started.run_id !== "string" ||
        !/^[A-Za-z0-9]{1,128}$/.test(started.run_id)
      )
        throw new ApiError(
          502,
          "invalid_run",
          "실행 기록을 확인할 수 없습니다.",
        );
      runId = started.run_id;
      if (platformTargets.length > 0) {
        const plan = await api<DeploymentPlan>(
          `/services/${service.id}/deployment-plans`,
          {
            kind: "migration",
            build_id: build!.id,
            source_environment_id: source!.id,
            targets: platformTargets,
          },
          crypto.randomUUID(),
        );
        if (!plan.executable)
          throw new ApiError(
            409,
            "plan_blocked",
            plan.reason || "On-Premise 배포 연결을 확인하세요.",
          );
        await api(
          `/deployment-plans/${plan.id}/execute`,
          { input_hash: plan.input_hash },
          crypto.randomUUID(),
        );
      }
      openPipelineRun(runId);
    } catch (reason) {
      if (runId)
        openPipelineRun(
          runId,
          `GCP 파이프라인은 시작했지만 On-Premise 배포를 접수하지 못했습니다: ${message(reason)}`,
        );
      else setError(message(reason));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  return (
    <section className="panel migration-launcher" aria-label="이관 환경 선택">
      {error && (
        <div className="error" role="alert">
          {error}
          <button
            type="button"
            disabled={busy}
            onClick={() => setReload((value) => value + 1)}
          >
            다시 불러오기
          </button>
        </div>
      )}
      {loading ? (
        <p role="status">이관 환경을 불러오는 중입니다.</p>
      ) : !error && options.length === 0 ? (
        <p role="status">선택할 수 있는 CSP 환경이 없습니다.</p>
      ) : (
        options.length > 0 && (
          <form className="migration-launch-form" onSubmit={start}>
            <fieldset disabled={busy}>
              <legend>이관 대상 CSP 선택</legend>
              <div className="migration-cloud-cards">
                {options.map((option) => {
                  const target = targets.find(
                    (item) => item.cloud === option.cloud,
                  );
                  const current = option.cloud === source?.cloud;
                  return (
                    <article
                      className={`migration-cloud-card cloud-${option.cloud}${target ? " selected" : ""}`}
                      key={option.cloud}
                      aria-label={`${names[option.cloud]} 이관 환경`}
                    >
                      <button
                        type="button"
                        className="migration-cloud-choice"
                        aria-label={names[option.cloud]}
                        aria-pressed={!!target}
                        disabled={current || (!target && targets.length >= 2)}
                        onClick={() => choose(option, !!target)}
                      >
                        <img
                          src={`/cloud-logos/${option.cloud}.svg`}
                          width="48"
                          height="48"
                          alt=""
                          aria-hidden="true"
                        />
                        <strong>{names[option.cloud]}</strong>
                        <small>
                          {option.runtime} / {option.region}
                        </small>
                        {current && (
                          <span className="migration-current">현재 환경</span>
                        )}
                        {!!target && (
                          <span
                            className="migration-selected"
                            aria-hidden="true"
                          >
                            선택됨
                          </span>
                        )}
                      </button>
                    </article>
                  );
                })}
              </div>
            </fieldset>
            <div className="migration-launch-summary" aria-live="polite">
              <strong>GCP 보안 파이프라인</strong>
              <span>
                빌드 → SAST → 스테이징 → DAST 및 AI 수정 → 차분 → 운영 배포 승인
                → 도메인 전환
              </span>
              {commit && (
                <span>
                  실행 커밋 <code>{commit.slice(0, 7)}</code> /{" "}
                  {service.source_ref} 최신
                </span>
              )}
              {platformTargets.length > 0 && (
                <span>On-Premise 플랫폼 배포 함께 실행</span>
              )}
              {!source ? (
                <span>등록된 출발 환경이 없습니다.</span>
              ) : !linked || !release || !commit ? (
                <span>이 서비스의 GCP 파이프라인 연결을 확인하세요.</span>
              ) : !targets.some((target) => target.cloud === "gcp") ? (
                <span>
                  GCP를 선택하세요. On-Premise를 함께 선택할 수 있습니다.
                </span>
              ) : platformTargets.length > 0 && !build ? (
                <span>On-Premise 배포에 사용할 성공한 빌드가 필요합니다.</span>
              ) : platformTargets.some(
                  (target) => target.cloud === "onprem" && !target.host_id,
                ) ? (
                <span>On-Premise 기본 등록 호스트를 확인하세요.</span>
              ) : null}
            </div>
            <button
              className="primary migration-start"
              type="submit"
              disabled={busy || !canStart}
            >
              {busy ? "시작 중…" : "파이프라인 시작"}
            </button>
          </form>
        )
      )}
    </section>
  );
}
