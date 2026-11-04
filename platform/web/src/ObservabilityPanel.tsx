import { useEffect, useState } from "react";
import { api, message, type Service } from "./api";
import {
  observabilityApi,
  type Environment,
  type Metric,
  type Observability,
  type State,
} from "./observability-api";

const stateLabel: Record<State, string> = {
  ok: "정상 수집",
  no_data: "데이터 없음",
  stale: "오래된 데이터",
  error: "조회 오류",
  unsupported: "미지원",
};
const statusText = (status: State, detail?: string | null) =>
  status === "ok"
    ? "정상 수집"
    : `${stateLabel[status]}${detail ? ` · ${detail}` : ""}`;
const metricText = (metric: Metric) =>
  metric.status === "ok" && metric.value === null
    ? "값 없음"
    : statusText(metric.status, metric.message);
const migrationStage: Record<
  NonNullable<Observability["migration"]>["stage"],
  string
> = {
  planned: "계획 생성",
  preparing: "준비 중",
  copying: "초기 복사 중",
  synchronizing: "변경 동기화 중",
  fencing_source: "원본 쓰기 차단 중",
  source_fenced: "원본 쓰기 차단 완료",
  validated: "대상 검증 완료",
  promotion_pending: "대상 승격 대기",
  promoted: "DB 대상 전환 완료",
  cleaned: "DB 전환·복제 정리 완료",
  aborted: "이전 작업 중단",
};
const migrationStatus: Record<
  NonNullable<Observability["migration"]>["status"],
  string
> = {
  ok: "상태 확인됨",
  stale: "오래된 상태",
  blocked: "차단됨",
  completed: "실행 단계 완료",
};
const stamp = (value: string | null) =>
  value ? new Date(value).toLocaleString("ko-KR") : "시각 정보 없음";
function MetricValue({
  metric,
  suffix = "",
}: {
  metric: Metric;
  suffix?: string;
}) {
  return (
    <div className="obs-value">
      {metric.status === "ok" && metric.value !== null ? (
        <>
          {metric.value.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}
          {suffix}
        </>
      ) : (
        <span className="obs-unknown">{metricText(metric)}</span>
      )}
    </div>
  );
}
function MetricCard({
  title,
  metric,
  suffix = "",
}: {
  title: string;
  metric: Metric;
  suffix?: string;
}) {
  return (
    <article className={`obs-metric state-${metric.status}`}>
      <div className="dashboard-metric-heading">
        <span>{title}</span>
        <span className={`obs-pill state-${metric.status}`}>
          {stateLabel[metric.status]}
        </span>
      </div>
      {metric.status === "ok" && metric.value !== null ? (
        <MetricValue metric={metric} suffix={suffix} />
      ) : (
        <>
          <div className="obs-value">—</div>
          <span className="dashboard-metric-reason">{metricText(metric)}</span>
        </>
      )}
      <Sparkline metric={metric} title={title} />
      {metric.status === "ok" && (
        <small>
          {metric.value === null ? "값 없음" : stamp(metric.collected_at)}
        </small>
      )}
    </article>
  );
}
function Sparkline({ metric, title }: { metric: Metric; title: string }) {
  const series = metric.series
    .filter(
      (point) =>
        Number.isFinite(point.value) && Number.isFinite(Date.parse(point.at)),
    )
    .slice()
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  if (metric.status !== "ok" || series.length < 2)
    return <div className="dashboard-chart-empty">추이 데이터 없음</div>;
  const values = series.map((point) => point.value);
  const low = Math.min(...values);
  const span = Math.max(...values) - low || 1;
  const start = Date.parse(series[0].at);
  const duration = Date.parse(series[series.length - 1].at) - start;
  if (!duration)
    return <div className="dashboard-chart-empty">추이 데이터 없음</div>;
  const points = series
    .map(
      (point) =>
        `${((Date.parse(point.at) - start) / duration) * 300},${72 - ((point.value - low) / span) * 56}`,
    )
    .join(" ");
  return (
    <div className="dashboard-chart">
      <svg
        viewBox="0 0 300 88"
        preserveAspectRatio="none"
        role="img"
        aria-label={`${title} 선택 기간 추이`}
      >
        {[16, 44, 72].map((y) => (
          <line key={y} x1="0" x2="300" y1={y} y2={y} />
        ))}
        <polygon points={`0,88 ${points} 300,88`} />
        <polyline points={points} />
      </svg>
      <div>
        <span>
          {new Date(start).toLocaleTimeString("ko-KR", {
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          })}
        </span>
        <span>
          {new Date(start + duration).toLocaleTimeString("ko-KR", {
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          })}
        </span>
      </div>
    </div>
  );
}
export function ObservabilityPanel({ serviceId }: { serviceId: string }) {
  const [service, setService] = useState<Service | null>(null);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [environmentId, setEnvironmentId] = useState("");
  const [minutes, setMinutes] = useState(15);
  const [snapshot, setSnapshot] = useState<Observability | null>(null);
  const [environmentState, setEnvironmentState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [snapshotState, setSnapshotState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [serviceError, setServiceError] = useState("");
  const [environmentError, setEnvironmentError] = useState("");
  const [snapshotError, setSnapshotError] = useState("");
  const [retry, setRetry] = useState(0);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setServiceError("");
    api<Service>(
      `/services/${encodeURIComponent(serviceId)}`,
      undefined,
      undefined,
      controller.signal,
    )
      .then((data) => {
        if (!controller.signal.aborted) setService(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setServiceError(message(e));
      });
    return () => controller.abort();
  }, [serviceId, retry]);
  useEffect(() => {
    const controller = new AbortController();
    setEnvironmentState("loading");
    setEnvironmentError("");
    observabilityApi
      .environments(serviceId, controller.signal)
      .then(({ items }) => {
        if (controller.signal.aborted) return;
        setEnvironments(items);
        setEnvironmentId((current) =>
          items.some((item) => item.id === current)
            ? current
            : (items[0]?.id ?? ""),
        );
        setEnvironmentState("ready");
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setEnvironmentState("error");
          setEnvironmentError(message(e));
        }
      });
    return () => controller.abort();
  }, [serviceId, retry]);
  useEffect(() => {
    if (!environmentId) {
      setSnapshot(null);
      setSnapshotState("idle");
      return;
    }
    const controller = new AbortController();
    setSnapshotState("loading");
    setSnapshotError("");
    observabilityApi
      .snapshot(serviceId, environmentId, minutes, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) {
          setSnapshot(data);
          setSnapshotState("ready");
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setSnapshotState("error");
          setSnapshotError(message(e));
        }
      });
    return () => controller.abort();
  }, [serviceId, environmentId, minutes, reload]);
  useEffect(() => {
    const timer = window.setInterval(() => setReload((n) => n + 1), 30000);
    return () => window.clearInterval(timer);
  }, []);
  const selectedEnvironment = environments.find(
    (item) => item.id === environmentId,
  );
  const error = serviceError || environmentError || snapshotError;
  const busy = environmentState === "loading" || snapshotState === "loading";
  const noEnvironment =
    environmentState === "ready" && environments.length === 0;
  const selectionMatches =
    snapshot?.service_id === serviceId &&
    snapshot.environment.id === environmentId &&
    snapshot.window.minutes === minutes;
  const logs =
    snapshot?.logs.items
      .filter((item) => /warn|error|fatal|critical/i.test(item.severity))
      .slice(0, 5) ?? [];
  const appState = snapshot?.metrics.app_up;
  const dbState = snapshot?.metrics.db_up;
  return (
    <section
      className="observability developer-dashboard"
      aria-label="관측 대시보드"
    >
      <header className="dashboard-heading">
        <div>
          <a
            className="dashboard-project"
            href={`#/overview/${encodeURIComponent(serviceId)}`}
          >
            {service?.name ?? "서비스 개요"}
          </a>
          <h1>대시보드</h1>
          <p>트래픽, 실행 상태와 최근 오류를 한눈에 확인하세요.</p>
        </div>
        <div className="dashboard-refresh">
          <span className="dashboard-live">
            {environmentId ? "30초마다 갱신" : "환경 연결 대기"}
          </span>
          <button
            onClick={() => {
              setRetry((n) => n + 1);
              setReload((n) => n + 1);
            }}
            disabled={busy}
          >
            {busy ? "조회 중…" : "지금 새로고침"}
          </button>
        </div>
      </header>
      <div className="dashboard-toolbar">
        <div
          className="dashboard-environments"
          role="group"
          aria-label="실행 환경"
        >
          {environments.map((item) => (
            <button
              key={item.id}
              aria-pressed={environmentId === item.id}
              onClick={() => setEnvironmentId(item.id)}
            >
              {item.name}
            </button>
          ))}
          {!environments.length && (
            <span>
              {environmentState === "loading"
                ? "환경 조회 중…"
                : environmentState === "error"
                  ? "환경 조회 실패"
                  : "연결된 환경 없음"}
            </span>
          )}
        </div>
        <div className="dashboard-period" role="group" aria-label="조회 기간">
          {[15, 30, 60, 120].map((value) => (
            <button
              key={value}
              aria-pressed={minutes === value}
              onClick={() => setMinutes(value)}
            >
              {value < 60 ? `${value}분` : `${value / 60}시간`}
            </button>
          ))}
        </div>
      </div>
      <div className="dashboard-context">
        <span>
          {selectedEnvironment
            ? `${selectedEnvironment.cloud.toUpperCase()} · ${selectedEnvironment.region}${selectedEnvironment.version ? ` · ${selectedEnvironment.version}` : ""}`
            : "실행 환경을 연결하면 관측이 시작됩니다."}
        </span>
        <span>
          {selectionMatches && snapshot
            ? `마지막 관측 ${stamp(snapshot.observed_at)}`
            : "수집된 데이터 없음"}
        </span>
      </div>
      {error && (
        <div className="error" role="alert">
          {error}
          {selectionMatches && " · 이전 관측 결과를 표시하고 있습니다."}
        </div>
      )}
      {noEnvironment && (
        <div className="dashboard-notice">
          <span className="dashboard-notice-icon" aria-hidden="true">
            ◎
          </span>
          <div>
            <strong>실행 환경 연결이 필요합니다</strong>
            <p>
              이 서비스에 연결된 환경이 없습니다. 실행 환경과 관측 수집을
              연결하면 지표가 표시됩니다.
            </p>
          </div>
          <a href={`#/overview/${encodeURIComponent(serviceId)}`}>
            서비스 개요로 이동 →
          </a>
        </div>
      )}
      {!selectionMatches && (
        <>
          {busy && (
            <p className="dashboard-loading" role="status">
              관측 데이터를 불러오는 중입니다.
            </p>
          )}
          <div className="obs-metrics">
            {["요청 수", "오류율", "p95 지연"].map((title) => (
              <MetricCard
                key={title}
                title={title}
                metric={{
                  status: "no_data",
                  value: null,
                  unit: "",
                  collected_at: null,
                  message: error
                    ? "다시 조회해 주세요"
                    : busy
                      ? "불러오는 중"
                      : "환경 연결 후 수집",
                  series: [],
                }}
              />
            ))}
          </div>
          <div className="obs-columns dashboard-placeholders">
            {[
              ["앱 · DB", "앱 상태와 DB 연결 정보를 표시합니다."],
              ["가상 머신", "서버별 CPU, 메모리, 디스크 사용량을 표시합니다."],
              ["최근 경고 · 오류", "수집된 경고와 오류 로그를 표시합니다."],
              [
                "대표 요청 경로",
                "요청이 각 서비스를 통과한 시간과 경로를 표시합니다.",
              ],
            ].map(([title, text]) => (
              <section key={title} className="panel">
                <div className="panel-top">
                  <h2>{title}</h2>
                  <span className="obs-pill">
                    {error ? "조회 확인 필요" : busy ? "조회 중" : "연결 대기"}
                  </span>
                </div>
                <div className="dashboard-panel-empty">
                  <span aria-hidden="true">—</span>
                  <p>{text}</p>
                </div>
              </section>
            ))}
          </div>
        </>
      )}
      {snapshot && selectionMatches && (
        <>
          <div className="obs-metrics">
            <MetricCard title="요청 수" metric={snapshot.metrics.requests} />
            <MetricCard
              title="오류율"
              metric={snapshot.metrics.error_rate}
              suffix="%"
            />
            <MetricCard
              title="p95 지연"
              metric={snapshot.metrics.p95_ms}
              suffix=" ms"
            />
          </div>
          <div className="obs-columns">
            <section className="panel">
              <div className="panel-top">
                <h2>앱 · DB</h2>
                <span
                  className={`obs-pill state-${appState?.status ?? "no_data"}`}
                >
                  {appState?.status === "ok" && appState.value !== null
                    ? appState.value === 1
                      ? "앱 실행 중"
                      : "앱 중지"
                    : appState
                      ? metricText(appState)
                      : stateLabel.no_data}
                </span>
              </div>
              <p className="obs-line">
                앱 상태{" "}
                <b>
                  {appState?.status === "ok" && appState.value !== null
                    ? appState.value === 1
                      ? "실행 중"
                      : "중지"
                    : appState?.status === "ok" && appState.value === null
                      ? "값 없음"
                      : stateLabel[appState?.status ?? "no_data"]}
                </b>
              </p>
              <p className="obs-line">
                DB 연결{" "}
                <b>
                  {dbState?.status === "ok" && dbState.value !== null
                    ? dbState.value === 1
                      ? "연결됨"
                      : "연결 안 됨"
                    : dbState
                      ? metricText(dbState)
                      : stateLabel.no_data}
                </b>
              </p>
              {snapshot.migration && (
                <div className="obs-migration">
                  <div className="obs-migration-head">
                    <b>DB 이전</b>
                    <span
                      className={`obs-pill state-${snapshot.migration.status}`}
                    >
                      {migrationStatus[snapshot.migration.status]}
                    </span>
                  </div>
                  <p>단계: {migrationStage[snapshot.migration.stage]}</p>
                  <p>
                    검증:{" "}
                    {snapshot.migration.validation === "passed"
                      ? "통과"
                      : "대기"}
                    {" · "}
                    미반영 WAL:{" "}
                    {snapshot.migration.lag_bytes === null
                      ? "정보 없음"
                      : `${snapshot.migration.lag_bytes.toLocaleString("ko-KR")} 바이트`}
                  </p>
                  <small>
                    마지막 갱신 {stamp(snapshot.migration.observed_at)}
                  </small>
                </div>
              )}
              {!snapshot.migration && (
                <p className="obs-migration-none">DB 이전 실행 없음</p>
              )}
            </section>
            <section className="panel">
              <div className="panel-top">
                <h2>가상 머신</h2>
                <span className={`obs-pill state-${snapshot.infra.status}`}>
                  {statusText(snapshot.infra.status, snapshot.infra.message)}
                </span>
              </div>
              {snapshot.environment.cloud === "onprem" &&
                snapshot.infra.instances.length > 0 && (
                  <p className="muted">
                    수신 상태는 Collector의 보고 여부이며 VM 전원 상태와
                    다릅니다.
                  </p>
                )}
              {snapshot.infra.instances.length ? (
                snapshot.infra.instances.map((instance) => (
                  <div className="obs-instance" key={instance.id}>
                    <strong>{instance.name}</strong>
                    <span>
                      상태{" "}
                      {(
                        {
                          reporting: "수집 중",
                          stale: "수집 지연",
                          unknown: "확인 필요",
                        } as Record<string, string>
                      )[instance.state] ?? instance.state}
                    </span>
                    <span>
                      CPU{" "}
                      <MetricValue metric={instance.cpu_percent} suffix="%" />
                    </span>
                    <span>
                      메모리{" "}
                      <MetricValue
                        metric={instance.memory_percent}
                        suffix="%"
                      />
                    </span>
                    {instance.disk_percent && (
                      <span>
                        디스크{" "}
                        <MetricValue
                          metric={instance.disk_percent}
                          suffix="%"
                        />
                      </span>
                    )}
                  </div>
                ))
              ) : (
                <p className="obs-unknown">
                  {snapshot.infra.message || stateLabel[snapshot.infra.status]}
                </p>
              )}
            </section>
          </div>
          <div className="obs-columns">
            <section className="panel">
              <div className="panel-top">
                <h2>최근 경고 · 오류</h2>
                <span className={`obs-pill state-${snapshot.logs.status}`}>
                  {statusText(snapshot.logs.status, snapshot.logs.message)}
                </span>
              </div>
              {logs.length ? (
                <ul className="obs-logs">
                  {logs.map((item, index) => (
                    <li key={`${item.timestamp}-${index}`}>
                      <time>{stamp(item.timestamp)}</time>
                      <b>{item.severity}</b>
                      <span>{item.message}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="obs-unknown">
                  {snapshot.logs.status === "ok"
                    ? "표시할 경고·오류 로그가 없습니다."
                    : statusText(snapshot.logs.status, snapshot.logs.message)}
                </p>
              )}
            </section>
            <section className="panel">
              <div className="panel-top">
                <h2>대표 요청 경로</h2>
                <span className={`obs-pill state-${snapshot.trace.status}`}>
                  {statusText(snapshot.trace.status, snapshot.trace.message)}
                </span>
              </div>
              {snapshot.trace.spans.length ? (
                <>
                  <p className="obs-trace-title">
                    Trace {snapshot.trace.id || "식별자 없음"} ·{" "}
                    {snapshot.trace.duration_ms === null
                      ? "총 시간 정보 없음"
                      : `${snapshot.trace.duration_ms} ms`}
                  </p>
                  <ol className="obs-spans">
                    {snapshot.trace.spans.map((span) => (
                      <li
                        key={span.span_id}
                        style={{
                          marginLeft: `${Math.min(depth(span, snapshot.trace.spans), 4) * 14}px`,
                        }}
                      >
                        <span>{span.name}</span>
                        <small>
                          {span.service} · {span.duration_ms} ms
                        </small>
                      </li>
                    ))}
                  </ol>
                </>
              ) : (
                <p className="obs-unknown">
                  {snapshot.trace.status === "ok"
                    ? "표시할 대표 trace가 없습니다."
                    : statusText(snapshot.trace.status, snapshot.trace.message)}
                </p>
              )}
            </section>
          </div>
        </>
      )}
    </section>
  );
}
function depth(
  span: Observability["trace"]["spans"][number],
  spans: Observability["trace"]["spans"],
) {
  let level = 0;
  let parent = span.parent_id;
  while (parent && level < 5) {
    const found = spans.find((item) => item.span_id === parent);
    if (!found) break;
    parent = found.parent_id;
    level++;
  }
  return level;
}
