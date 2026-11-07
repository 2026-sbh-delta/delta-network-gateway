import { useEffect, useState } from "react";
import { api, message } from "./api";
import { OUTCOME } from "./pipeline-model";
import type { View } from "./AppShell";
import { cloudName } from "./application-model";
import { observabilityApi, type Environment } from "./observability-api";
import "./deployed-environments.css";

const cloudLabel = { ...cloudName, onprem: "On-Premise" };
const cloudOrder = { aws: 0, gcp: 1, onprem: 2 };
const runtimeStates: Record<string, { label: string; tone: string }> = {
  active: { label: "활성화", tone: "good" },
  removing: { label: "앱 정리 중", tone: "" },
  removed: { label: "앱 삭제됨", tone: "" },
  deploying: { label: "배포 중", tone: "" },
  failed: { label: "배포 실패", tone: "bad" },
  stopped: { label: "중지됨", tone: "" },
  inactive: { label: "비활성화", tone: "" },
};

function DeploymentState({ status }: { status?: string | null }) {
  const state = runtimeStates[status ?? ""] ?? {
    label: "상태 확인 필요",
    tone: "",
  };
  return (
    <span className={`release-state ${state.tone}`}>
      {status === "active" && (
        <span className="deployment-state-indicator" aria-hidden="true" />
      )}
      {state.label}
    </span>
  );
}

/*!
 * Font Awesome Free 6.7.2 by @fontawesome - https://fontawesome.com
 * Icons: CC BY 4.0 - https://fontawesome.com/license/free
 * Copyright 2024 Fonticons, Inc. Original solid SVG path data, unmodified.
 */
const actionIconPaths = {
  "right-left":
    "M32 96l320 0 0-64c0-12.9 7.8-24.6 19.8-29.6s25.7-2.2 34.9 6.9l96 96c6 6 9.4 14.1 9.4 22.6s-3.4 16.6-9.4 22.6l-96 96c-9.2 9.2-22.9 11.9-34.9 6.9s-19.8-16.6-19.8-29.6l0-64L32 160c-17.7 0-32-14.3-32-32s14.3-32 32-32zM480 352c17.7 0 32 14.3 32 32s-14.3 32-32 32l-320 0 0 64c0 12.9-7.8 24.6-19.8 29.6s-25.7 2.2-34.9-6.9l-96-96c-6-6-9.4-14.1-9.4-22.6s3.4-16.6 9.4-22.6l96-96c9.2-9.2 22.9-11.9 34.9-6.9s19.8 16.6 19.8 29.6l0 64 320 0z",
  "chart-line":
    "M64 64c0-17.7-14.3-32-32-32S0 46.3 0 64L0 400c0 44.2 35.8 80 80 80l400 0c17.7 0 32-14.3 32-32s-14.3-32-32-32L80 416c-8.8 0-16-7.2-16-16L64 64zm406.6 86.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L320 210.7l-57.4-57.4c-12.5-12.5-32.8-12.5-45.3 0l-112 112c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0L240 221.3l57.4 57.4c12.5 12.5 32.8 12.5 45.3 0l128-128z",
};

function DeploymentActionIcon({
  name,
}: {
  name: keyof typeof actionIconPaths;
}) {
  return (
    <svg
      className="deployment-action-icon"
      data-icon={name}
      width="16"
      height="16"
      viewBox="0 0 512 512"
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      <path d={actionIconPaths[name]} />
    </svg>
  );
}

export function DeployedEnvironments({
  serviceId,
  onNavigate,
}: {
  serviceId: string;
  onNavigate: (view: View) => void;
}) {
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  // This project's latest pipeline run (started from its migration screen) and a way back to it.
  const [recent, setRecent] = useState<{
    run_id: string;
    outcome: string | null;
  } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setRecent(null);
    api<{
      runs: {
        run_id: string;
        outcome: string | null;
        options: { project_id?: string };
      }[];
    }>("/pipeline/runs", undefined, undefined, controller.signal)
      .then((data) => {
        const run = data.runs.find((r) => r.options?.project_id === serviceId);
        if (!controller.signal.aborted && run) setRecent(run);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [serviceId]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setEnvironments([]);
    setLoading(true);
    setError("");

    async function refresh() {
      try {
        const result = await observabilityApi.environments(
          serviceId,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setEnvironments(result.items);
          setError("");
        }
      } catch (e) {
        if (!controller.signal.aborted) setError(message(e));
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = setTimeout(refresh, 10000);
        }
      }
    }

    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [serviceId]);

  const orderedEnvironments = [...environments].sort(
    (left, right) => cloudOrder[left.cloud] - cloudOrder[right.cloud],
  );

  return (
    <section className="panel deployed-environments" aria-label="배포 환경">
      <div className="panel-top">
        <div>
          <h2>배포 환경</h2>
          <p>
            현재 서비스가 배포된 위치입니다. 실제 응답과 지표는 대시보드에서
            확인하세요.
          </p>
        </div>
        <div className="deployment-actions">
          {recent && (
            <button
              onClick={() => {
                location.hash = `/pipeline/${encodeURIComponent(recent.run_id)}`;
              }}
            >
              {recent.outcome
                ? `최근 파이프라인 · ${OUTCOME[recent.outcome]?.label ?? recent.outcome} →`
                : "파이프라인 진행 중 →"}
            </button>
          )}
          <button onClick={() => onNavigate("migration")}>
            <DeploymentActionIcon name="right-left" />
            이관 진행
          </button>
          <button className="primary" onClick={() => onNavigate("monitoring")}>
            <DeploymentActionIcon name="chart-line" />
            모니터링
          </button>
        </div>
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading && <p role="status">배포 환경을 확인하고 있습니다…</p>}
      {!loading && !error && environments.length === 0 && (
        <p className="muted">연결된 배포 환경이 없습니다.</p>
      )}

      <div className="deployment-environment-cards">
        {orderedEnvironments.map((environment) => (
          <article
            key={environment.id}
            className={`deployment-environment-card cloud-${environment.cloud}`}
            aria-label={`${cloudLabel[environment.cloud]} 배포 환경`}
          >
            <div className="release-line">
              <h3 className="deployment-cloud-brand">
                <img
                  className="deployment-cloud-logo"
                  src={`/cloud-logos/${environment.cloud}.svg`}
                  width="40"
                  height="40"
                  alt=""
                  aria-hidden="true"
                />
                {cloudLabel[environment.cloud]}
              </h3>
              <DeploymentState status={environment.runtime_status} />
            </div>
            <dl>
              <div>
                <dt>리전</dt>
                <dd>{environment.region}</dd>
              </div>
              <div>
                <dt>
                  {environment.cloud === "aws"
                    ? "계정"
                    : environment.cloud === "gcp"
                      ? "프로젝트"
                      : "호스트"}
                </dt>
                <dd>
                  {environment.cloud === "aws"
                    ? (environment.account_id ?? "미등록")
                    : environment.cloud === "gcp"
                      ? (environment.gcp_project ?? "미등록")
                      : "팀 온프레미스 서버"}
                </dd>
              </div>
              <div>
                <dt>배포 버전</dt>
                <dd>
                  <code>{environment.version ?? "미등록"}</code>
                </dd>
              </div>
              {environment.instances?.map((instance) => (
                <div key={instance.id}>
                  <dt>
                    {environment.cloud === "onprem" ? "실행 호스트" : "VM"}
                  </dt>
                  <dd>
                    {instance.name}
                    <code>{instance.id}</code>
                    {instance.zone && <small>{instance.zone}</small>}
                  </dd>
                </div>
              ))}
              {environment.database_profile_id && (
                <div>
                  <dt>DB 생성 설정</dt>
                  <dd>
                    {environment.database_profile_id}
                    <small>
                      DB 이름이 아닌 설정 ID입니다. 앱 DB는 환경별 고유 이름으로
                      생성됩니다.
                    </small>
                  </dd>
                </div>
              )}
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}
