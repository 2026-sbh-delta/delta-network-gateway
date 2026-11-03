import { useEffect, useState } from "react";
import { message, type Service } from "./api";
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
      <span>{title}</span>
      <MetricValue metric={metric} suffix={suffix} />
      <Sparkline metric={metric} />
      <small>
        {metric.status === "ok"
          ? metric.value === null
            ? "값 없음"
            : stamp(metric.collected_at)
          : metric.message || stateLabel[metric.status]}
      </small>
    </article>
  );
}
function Sparkline({ metric }: { metric: Metric }) {
  if (metric.status !== "ok" || metric.series.length < 2) return null;
  const values = metric.series.map((point) => point.value);
  const low = Math.min(...values);
  const span = Math.max(...values) - low || 1;
  const points = values
    .map(
      (value, index) =>
        `${(index / (values.length - 1)) * 100},${24 - ((value - low) / span) * 20}`,
    )
    .join(" ");
  return (
    <svg
      className="obs-sparkline"
      viewBox="0 0 100 26"
      role="img"
      aria-label="선택 기간 추이"
    >
      <polyline points={points} />
    </svg>
  );
}
export function ObservabilityPanel() {
  const [services, setServices] = useState<Service[]>([]);
  const [serviceId, setServiceId] = useState("");
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [environmentId, setEnvironmentId] = useState("");
  const [minutes, setMinutes] = useState(15);
  const [snapshot, setSnapshot] = useState<Observability | null>(null);
  const [serviceState, setServiceState] = useState<
    "loading" | "ready" | "error"
  >("loading");
  const [environmentState, setEnvironmentState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [snapshotState, setSnapshotState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let alive = true;
    observabilityApi
      .services()
      .then((page) => {
        if (!alive) return;
        setServices(page.items);
        setServiceId((id) => id || page.items[0]?.id || "");
        setServiceState("ready");
      })
      .catch((e) => {
        if (alive) {
          setServiceState("error");
          setError(message(e));
        }
      });
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    setEnvironments([]);
    setEnvironmentId("");
    setSnapshot(null);
    setSnapshotState("idle");
    if (!serviceId) return;
    const controller = new AbortController();
    setEnvironmentState("loading");
    setError("");
    observabilityApi
      .environments(serviceId, controller.signal)
      .then(({ items }) => {
        if (controller.signal.aborted) return;
        setEnvironments(items);
        setEnvironmentId(items[0]?.id ?? "");
        setEnvironmentState("ready");
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setEnvironmentState("error");
          setError(message(e));
        }
      });
    return () => controller.abort();
  }, [serviceId]);
  useEffect(() => {
    if (!serviceId || !environmentId) return;
    const controller = new AbortController();
    setSnapshotState("loading");
    setError("");
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
          setError(message(e));
        }
      });
    return () => controller.abort();
  }, [serviceId, environmentId, minutes, reload]);
  useEffect(() => {
    const timer = window.setInterval(() => setReload((n) => n + 1), 30000);
    return () => window.clearInterval(timer);
  }, []);
  const service = services.find((item) => item.id === serviceId);
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
    <section className="observability" aria-label="관측 대시보드">
      <div className="page-heading">
        <div>
          <div className="eyebrow">PLATFORM / OBSERVABILITY</div>
          <h1>서비스 대시보드</h1>
          <p>선택한 실행 환경의 최근 관측 결과입니다.</p>
        </div>
        <button
          onClick={() => setReload((n) => n + 1)}
          disabled={!serviceId || !environmentId}
        >
          지금 새로고침
        </button>
      </div>
      <section className="panel obs-controls">
        <label>
          서비스
          <select
            aria-label="서비스 선택"
            value={serviceId}
            onChange={(e) => setServiceId(e.target.value)}
            disabled={serviceState !== "ready" || !services.length}
          >
            <option value="">
              {serviceState === "loading"
                ? "불러오는 중…"
                : serviceState === "error"
                  ? "서비스 조회 오류"
                  : "서비스 없음"}
            </option>
            {services.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          환경
          <select
            aria-label="환경 선택"
            value={environmentId}
            onChange={(e) => setEnvironmentId(e.target.value)}
            disabled={environmentState !== "ready" || !environments.length}
          >
            <option value="">
              {environmentState === "loading"
                ? "불러오는 중…"
                : environmentState === "error"
                  ? "환경 조회 오류"
                  : "환경 없음"}
            </option>
            {environments.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} · {item.cloud.toUpperCase()} · {item.region}
              </option>
            ))}
          </select>
        </label>
        <label>
          기간
          <select
            aria-label="조회 기간"
            value={minutes}
            onChange={(e) => setMinutes(Number(e.target.value))}
          >
            <option value={15}>최근 15분</option>
            <option value={30}>최근 30분</option>
            <option value={60}>최근 1시간</option>
            <option value={120}>최근 2시간</option>
          </select>
        </label>
      </section>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {serviceState === "ready" && !services.length && (
        <div className="panel obs-empty">등록된 서비스가 없습니다.</div>
      )}
      {serviceId && environmentState === "ready" && !environments.length && (
        <div className="panel obs-empty">
          이 서비스에 연결된 환경이 없습니다.
        </div>
      )}
      {snapshotState === "loading" && !selectionMatches && (
        <div className="panel obs-empty" role="status">
          관측 데이터를 불러오는 중입니다.
        </div>
      )}
      {snapshotState === "error" && !selectionMatches && (
        <div className="panel obs-empty">
          관측 데이터를 가져오지 못했습니다. 다시 시도해 주세요.
        </div>
      )}
      {snapshot && selectionMatches && (
        <>
          <div className="obs-summary">
            <div>
              <strong>{service?.name}</strong>
              <span>
                {snapshot.environment.name} ·{" "}
                {snapshot.environment.cloud.toUpperCase()} ·{" "}
                {snapshot.environment.region}
                {snapshot.environment.version
                  ? ` · ${snapshot.environment.version}`
                  : ""}
              </span>
            </div>
            <small>
              마지막 관측 {stamp(snapshot.observed_at)} · 최근{" "}
              {snapshot.window.minutes}분 · 30초마다 자동 갱신
            </small>
          </div>
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
                  <b>DB 이전</b>
                  <span>
                    단계: {snapshot.migration.stage} · 검증:{" "}
                    {snapshot.migration.validation === "passed"
                      ? "통과"
                      : snapshot.migration.validation === "failed"
                        ? "실패"
                        : "대기"}
                    {snapshot.migration.lag_seconds !== null
                      ? ` · 지연 ${snapshot.migration.lag_seconds}초`
                      : ""}
                  </span>
                </div>
              )}
            </section>
            <section className="panel">
              <div className="panel-top">
                <h2>가상 머신</h2>
                <span className={`obs-pill state-${snapshot.infra.status}`}>
                  {statusText(snapshot.infra.status, snapshot.infra.message)}
                </span>
              </div>
              {snapshot.infra.instances.length ? (
                snapshot.infra.instances.map((instance) => (
                  <div className="obs-instance" key={instance.id}>
                    <strong>{instance.name}</strong>
                    <span>상태 {instance.state}</span>
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
