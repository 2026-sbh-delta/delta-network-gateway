import { useEffect, useRef, useState } from "react";
import {
  api,
  message,
  type Page,
  type Run,
  type RunEvent,
  type SandboxTarget,
} from "./api";

const active = (run: Run | null) =>
  run?.status === "queued" || run?.status === "running";
const labels = {
  queued: "실행 대기",
  running: "실행 확인 중",
  succeeded: "실행 확인 통과",
  failed: "실행 확인 실패",
};
const steps: Record<string, string> = {
  sandbox_queued: "샌드박스 실행을 준비하고 있습니다.",
  sandbox_prepare: "실행할 이미지와 샌드박스 개정판을 준비합니다.",
  sandbox_deploy: "샌드박스에서 앱을 실행합니다.",
  sandbox_verify: "실제 이미지와 앱의 헬스 체크를 확인합니다.",
  sandbox_cleanup: "샌드박스 실행 자원을 정리하고 있습니다.",
  sandbox_complete: "실행 확인과 정리를 마쳤습니다.",
};
export function SandboxPanel({
  serviceId,
  build,
}: {
  serviceId: string;
  build: Run | null;
}) {
  const [run, setRun] = useState<Run | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [targets, setTargets] = useState<SandboxTarget[]>([]);
  const [targetsLoaded, setTargetsLoaded] = useState(false);
  const [executionCloud, setExecutionCloud] =
    useState<SandboxTarget["cloud"]>("aws");
  const [refresh, setRefresh] = useState(0);
  const key = useRef<{ selection: string; key: string } | null>(null);
  const selectedTarget = targets.find(
    (target) => target.cloud === executionCloud,
  );
  const runCloud = (value: Run) =>
    value.result?.execution_cloud ?? value.execution_cloud ?? "aws";
  const targetLabel = (cloud?: string) => {
    if (cloud === "gcp") return "GCP Cloud Run";
    if (cloud === "onprem") return "온프레미스";
    return "AWS ECS";
  };
  useEffect(() => {
    let stopped = false;
    api<{ items: SandboxTarget[] }>("/sandbox-targets")
      .then(({ items }) => {
        if (!stopped) {
          setTargets(items);
          setTargetsLoaded(true);
        }
      })
      .catch((e) => {
        if (!stopped) {
          setError(message(e));
          setTargetsLoaded(true);
        }
      });
    return () => {
      stopped = true;
    };
  }, []);
  useEffect(() => {
    let stopped = false;
    setLoaded(false);
    api<Page<Run>>(`/services/${serviceId}/sandbox-checks`)
      .then((p) => {
        if (!stopped) {
          setHistory(p.items);
          setRun(p.items[0] ?? null);
          setCursor(p.next_cursor);
          setLoaded(true);
        }
      })
      .catch((e) => {
        if (!stopped) setError(message(e));
      });
    return () => {
      stopped = true;
    };
  }, [serviceId, refresh]);
  useEffect(() => {
    if (!run) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const id = run.id;
    setEvents([]);
    async function poll() {
      try {
        const [current, log] = await Promise.all([
          api<Run>(`/sandbox-checks/${id}`),
          api<{ items: RunEvent[] }>(`/sandbox-checks/${id}/events`),
        ]);
        if (stopped) return;
        const identified = current.execution_cloud
          ? current
          : { ...current, execution_cloud: run ? runCloud(run) : "aws" };
        setRun(identified);
        setHistory((h) => h.map((r) => (r.id === id ? identified : r)));
        setEvents(log.items);
        if (active(current)) timer = setTimeout(poll, 3000);
      } catch (e) {
        if (!stopped) {
          setError(message(e));
          timer = setTimeout(poll, 5000);
        }
      }
    }
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [run?.id]);
  async function start() {
    if (!build || !selectedTarget?.configured) return;
    setBusy(true);
    setError("");
    const selection = `${build.id}:${executionCloud}`;
    if (key.current?.selection !== selection)
      key.current = { selection, key: crypto.randomUUID() };
    try {
      const response = await api<Run>(
        `/services/${serviceId}/sandbox-checks`,
        { build_id: build.id, execution_cloud: executionCloud },
        key.current.key,
      );
      const current =
        response.execution_cloud || response.result?.execution_cloud
          ? response
          : { ...response, execution_cloud: executionCloud };
      key.current = null;
      setRun(current);
      setHistory((h) => [current, ...h.filter((r) => r.id !== current.id)]);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    if (!run) return;
    setBusy(true);
    setError("");
    try {
      const current = await api<Run>(`/sandbox-checks/${run.id}/cancel`, {});
      setRun(current);
      setHistory((h) => h.map((r) => (r.id === current.id ? current : r)));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function more() {
    if (!cursor) return;
    setBusy(true);
    try {
      const p = await api<Page<Run>>(
        `/services/${serviceId}/sandbox-checks?cursor=${encodeURIComponent(cursor)}`,
      );
      setHistory((h) => [...h, ...p.items]);
      setCursor(p.next_cursor);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  const pending = history.some(active);
  const status = (r: Run) =>
    r.error?.code === "sandbox_cancelled" ? "중단 완료" : labels[r.status];
  return (
    <section className="panel sandbox-panel" aria-label="샌드박스 실행 확인">
      <div className="panel-top">
        <div>
          <h2>샌드박스 실행 확인</h2>
          <p className="muted">
            선택한 빌드 이미지를 대상 샌드박스에서 실행하고, 같은 이미지인지와
            앱의 응답 상태를 확인합니다.
          </p>
        </div>
        <button
          className="primary"
          disabled={
            !loaded ||
            !targetsLoaded ||
            !selectedTarget?.configured ||
            busy ||
            pending ||
            build?.status !== "succeeded"
          }
          onClick={start}
        >
          {pending ? "실행 확인 진행 중…" : "이 이미지 실행 확인"}
        </button>
      </div>
      <p>
        선택한 빌드:{" "}
        <code>
          {build?.status === "succeeded"
            ? build.id.slice(0, 8)
            : "성공한 이미지 빌드가 필요합니다"}
        </code>
      </p>
      <label>
        실행 환경{" "}
        <select
          aria-label="샌드박스 실행 환경"
          value={executionCloud}
          disabled={!targetsLoaded || busy || pending}
          onChange={(event) =>
            setExecutionCloud(event.target.value as SandboxTarget["cloud"])
          }
        >
          {targets.map((target) => (
            <option
              key={target.cloud}
              value={target.cloud}
              disabled={!target.configured}
            >
              {target.label} · {target.runtime}
              {target.configured ? "" : " · 미연결"}
            </option>
          ))}
        </select>
      </label>
      {selectedTarget && !selectedTarget.configured && (
        <p className="muted" role="status">
          {selectedTarget.label} 실행 환경은 아직 연결되지 않아 실행할 수
          없습니다.
        </p>
      )}
      {targets
        .filter((target) => !target.configured)
        .map((target) => (
          <p className="muted" key={`${target.cloud}-unavailable`}>
            {target.label}: 아직 실행 환경이 연결되지 않았습니다.
          </p>
        ))}
      {executionCloud === "gcp" && selectedTarget?.configured && (
        <p className="muted">
          Cloud Run 샌드박스 성공은 최종 VM 네트워크·권한 검증을 인증하지
          않습니다.
        </p>
      )}
      <p className="sandbox-boundary">
        실행 확인은 보안 검사와 다릅니다. SAST는 아래 코드 보안 검사에서 별도로
        실행합니다. DAST는 아래에서 별도로 실행합니다. AI 수정안과 운영 배포는
        아직 연결하지 않았습니다.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}{" "}
          <button
            onClick={() => {
              setError("");
              setRefresh((n) => n + 1);
            }}
          >
            다시 조회
          </button>
        </p>
      )}
      {run ? (
        <>
          <span className={`status ${run.status}`}>{status(run)}</span>
          <p className="muted">실행 위치: {targetLabel(runCloud(run))}</p>
          <p className="muted">
            커밋 {run.resolved_commit_sha?.slice(0, 12)} ·{" "}
            {new Date(run.created_at).toLocaleString("ko-KR")}
          </p>
          {active(run) && (
            <>
              <p>
                {run.cancel_requested
                  ? "중단 요청을 받았습니다. 실행 자원의 정리를 기다리고 있습니다."
                  : (steps[run.current_step ?? ""] ??
                    "실행 상태를 확인하고 있습니다.")}
              </p>
              <button onClick={cancel} disabled={busy || run.cancel_requested}>
                {run.cancel_requested ? "중단 요청됨" : "실행 중단"}
              </button>
            </>
          )}
          {run.error && (
            <div className="failure">
              <strong>{run.error.message}</strong>
              <small>{run.error.code}</small>
            </div>
          )}
          {run.result?.runtime_check === "passed" && (
            <div className="build-result sandbox-result">
              <strong>
                {targetLabel(runCloud(run))} · 실제 실행 이미지 일치 · 헬스 체크
                통과
              </strong>
              <code>{run.result.image}</code>
              {run.result.revision && (
                <small>개정판: {run.result.revision}</small>
              )}
              {run.result.url && <small>앱 주소: {run.result.url}</small>}
              <small>
                확인한 빌드: {run.result.build_id?.slice(0, 8)} · 앱 포트:{" "}
                {run.result.container_port} · 헬스 체크:{" "}
                {run.result.healthcheck_path}
              </small>
              <small>샌드박스 자원 정리 확인 · 보안 검사 미실행</small>
            </div>
          )}
          {events.length > 0 && (
            <details className="sandbox-events">
              <summary>진행 기록 보기</summary>
              <ol>
                {events.map((e) => (
                  <li key={e.sequence}>{e.message}</li>
                ))}
              </ol>
            </details>
          )}
        </>
      ) : (
        <p className="muted">아직 샌드박스 실행 이력이 없습니다.</p>
      )}
      {history.length > 1 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>접수 시각</th>
                <th>커밋</th>
                <th>결과</th>
                <th>실행 위치</th>
                <th>상세</th>
              </tr>
            </thead>
            <tbody>
              {history.map((r) => (
                <tr key={r.id}>
                  <td>{new Date(r.created_at).toLocaleString("ko-KR")}</td>
                  <td>
                    <code>{r.resolved_commit_sha?.slice(0, 12)}</code>
                  </td>
                  <td>{status(r)}</td>
                  <td>{targetLabel(runCloud(r))}</td>
                  <td>
                    <button
                      onClick={() => setRun(r)}
                      aria-pressed={run?.id === r.id}
                    >
                      실행 보기
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {cursor && (
        <button disabled={busy} onClick={more}>
          실행 이력 더 보기
        </button>
      )}
    </section>
  );
}
