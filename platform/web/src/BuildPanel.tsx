import { useEffect, useRef, useState } from "react";
import { SandboxPanel } from "./SandboxPanel";
import { SecurityPanel } from "./SecurityPanel";
import { api, message, type Page, type Run } from "./api";

const labels = {
  queued: "빌드 대기",
  running: "빌드 중",
  succeeded: "빌드 완료",
  failed: "빌드 실패",
};
const stepLabels: Record<string, string> = {
  build_queued: "빌드 순서를 기다리고 있습니다.",
  build_submitted: "빌드를 접수했습니다.",
  build_provisioning: "빌드 환경을 준비하고 있습니다.",
  build_download_source: "확인한 커밋의 소스를 가져오고 있습니다.",
  build_install: "빌드 도구를 준비하고 있습니다.",
  build_pre_build: "빌드 전 준비를 확인하고 있습니다.",
  build_build: "Dockerfile로 이미지를 만들고 저장하고 있습니다.",
  build_post_build: "빌드 종료 처리를 진행하고 있습니다.",
  build_upload_artifacts: "빌드 결과를 정리하고 있습니다.",
  build_finalizing: "빌드 결과를 정리하고 있습니다.",
  image_verify: "저장된 이미지 digest를 확인하고 있습니다.",
};
const active = (run: Run | null) =>
  run?.status === "queued" || run?.status === "running";
export function BuildPanel({
  serviceId,
  source,
}: {
  serviceId: string;
  source: Run | null;
}) {
  const [stage, setStage] = useState<"image" | "runtime" | "security">("image");
  const [run, setRun] = useState<Run | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState("");
  const [showLogs, setShowLogs] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const key = useRef<{ source: string; key: string } | null>(null);
  useEffect(() => {
    let stopped = false;
    api<Page<Run>>(`/services/${serviceId}/builds`)
      .then((p) => {
        if (!stopped) {
          setHistory(p.items);
          setCursor(p.next_cursor);
          setRun(p.items[0] ?? null);
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
    const runId = run.id;
    async function poll() {
      try {
        const current = await api<Run>(`/builds/${runId}`);
        if (stopped) return;
        setRun(current);
        setHistory((h) => h.map((r) => (r.id === current.id ? current : r)));
        if (active(current)) timer = setTimeout(poll, 3000);
      } catch (e) {
        if (!stopped) {
          setError(message(e));
          timer = setTimeout(poll, 5000);
        }
      }
    }
    setShowLogs(false);
    setLogs("");
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [run?.id]);
  async function build() {
    if (!source) return;
    setBusy(true);
    setError("");
    if (key.current?.source !== source.id)
      key.current = { source: source.id, key: crypto.randomUUID() };
    try {
      const r = await api<Run>(
        `/services/${serviceId}/builds`,
        { source_check_id: source.id },
        key.current.key,
      );
      key.current = null;
      setRun(r);
      setHistory((h) => [r, ...h.filter((x) => x.id !== r.id)]);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function readLogs() {
    if (!run) return;
    setBusy(true);
    setError("");
    try {
      const value = await api<{
        text: string;
        available: boolean;
        truncated: boolean;
      }>(`/builds/${run.id}/logs`);
      setLogs(
        value.available
          ? `${value.truncated ? "(최근 로그 일부)\n" : ""}${value.text}`
          : "아직 생성된 빌드 로그가 없습니다.",
      );
      setShowLogs(true);
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
        `/services/${serviceId}/builds?cursor=${encodeURIComponent(cursor)}`,
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
  return (
    <>
      <div className="work-stage-heading">
        <span>02</span>
        <div>
          <h2>빌드와 검증</h2>
          <p>이미지를 만든 뒤 실행 환경과 코드의 검사 결과를 확인합니다.</p>
        </div>
      </div>
      <nav className="build-tabs" aria-label="빌드와 검사 단계">
        {(
          [
            ["image", "이미지 빌드"],
            ["runtime", "실행 확인"],
            ["security", "보안 검사"],
          ] as const
        ).map(([value, title], i) => (
          <button
            key={value}
            aria-pressed={stage === value}
            onClick={() => setStage(value)}
          >
            <span>0{i + 1}</span>
            {title}
          </button>
        ))}
      </nav>
      <div hidden={stage !== "image"}>
        <section className="panel build-panel" aria-label="이미지 빌드">
          <div className="panel-top">
            <div>
              <h2>이미지 빌드</h2>
              <p className="muted">
                선택한 소스 검사에서 확인한 커밋으로 이미지를 만듭니다.
              </p>
            </div>
            <button
              className="primary"
              onClick={build}
              disabled={busy || pending || source?.status !== "succeeded"}
            >
              {pending ? "빌드 진행 중…" : "이 커밋 빌드"}
            </button>
          </div>
          <p>
            선택한 커밋:{" "}
            <code>
              {source?.resolved_commit_sha?.slice(0, 12) ??
                "소스 확인이 필요합니다"}
            </code>
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
              <span className={`status ${run.status}`}>
                {labels[run.status]}
              </span>
              {active(run) && (
                <p>
                  {stepLabels[run.current_step ?? ""] ??
                    "빌드 상태를 확인하고 있습니다."}
                </p>
              )}
              <p className="muted">
                커밋 {run.resolved_commit_sha?.slice(0, 12)} ·{" "}
                {new Date(run.created_at).toLocaleString("ko-KR")}
              </p>
              {run.error && (
                <div className="failure">
                  <strong>{run.error.message}</strong>
                  <small>{run.error.code}</small>
                </div>
              )}
              {run.result?.image && (
                <div className="build-result">
                  <strong>빌드된 이미지</strong>
                  <code>{run.result.image}</code>
                  <small>
                    {run.result.platform} · 샌드박스 실행 확인은 아래에서 별도로
                    진행합니다.
                  </small>
                </div>
              )}
              {run.external_id && (
                <>
                  <p className="muted">
                    {run.external_id.startsWith("jenkins:")
                      ? "Jenkins"
                      : "CodeBuild"}{" "}
                    실행: <code>{run.external_id}</code>
                  </p>
                  <button disabled={busy} onClick={readLogs}>
                    {showLogs ? "로그 새로고침" : "빌드 로그 보기"}
                  </button>
                </>
              )}
              {showLogs && (
                <pre className="build-logs" tabIndex={0}>
                  {logs}
                </pre>
              )}
            </>
          ) : (
            <p className="muted">아직 빌드 이력이 없습니다.</p>
          )}
          {history.length > 1 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>접수 시각</th>
                    <th>커밋</th>
                    <th>결과</th>
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
                      <td>{labels[r.status]}</td>
                      <td>
                        <button
                          onClick={() => setRun(r)}
                          aria-pressed={run?.id === r.id}
                        >
                          빌드 보기
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
              빌드 이력 더 보기
            </button>
          )}
        </section>
      </div>
      <div hidden={stage !== "runtime"}>
        <SandboxPanel key={serviceId} serviceId={serviceId} build={run} />
      </div>
      <div hidden={stage !== "security"}>
        <SecurityPanel
          key={`security-${serviceId}`}
          serviceId={serviceId}
          build={run}
        />
      </div>
    </>
  );
}
