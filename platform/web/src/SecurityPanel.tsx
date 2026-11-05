import { useEffect, useState } from "react";
import { DastPanel } from "./DastPanel";
import { api, message, type Page, type Run } from "./api";

export function SecurityPanel({
  serviceId,
  build,
}: {
  serviceId: string;
  build: Run | null;
}) {
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const active = run?.status === "queued" || run?.status === "running";
  useEffect(() => {
    const abort = new AbortController();
    api<Page<Run>>(
      `/services/${serviceId}/security-checks`,
      undefined,
      undefined,
      abort.signal,
    )
      .then((data) => {
        if (!abort.signal.aborted) {
          setRun(data.items[0] ?? null);
          setLoaded(true);
        }
      })
      .catch((e) => {
        if (!abort.signal.aborted) setError(message(e));
      });
    return () => abort.abort();
  }, [serviceId]);
  useEffect(() => {
    if (!active || !run) return;
    const abort = new AbortController();
    const timer = window.setInterval(() => {
      api<Run>(`/security-checks/${run.id}`, undefined, undefined, abort.signal)
        .then((value) => {
          if (!abort.signal.aborted) {
            setRun(value);
            setError("");
          }
        })
        .catch((e) => {
          if (!abort.signal.aborted) setError(message(e));
        });
    }, 2000);
    return () => {
      abort.abort();
      window.clearInterval(timer);
    };
  }, [active, run?.id]);
  async function start() {
    if (!build || busy) return;
    setBusy(true);
    setError("");
    try {
      setRun(
        await api<Run>(
          `/services/${serviceId}/security-checks`,
          { build_id: build.id },
          crypto.randomUUID(),
        ),
      );
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  const result = run?.result;
  return (
    <>
      <section className="panel security-panel" aria-label="코드 보안 검사">
        <div className="panel-top">
          <div>
            <h2>코드 보안 검사</h2>
            <p className="muted">빌드한 커밋의 소스를 SAST로 검사합니다.</p>
          </div>
          <button
            className="primary"
            onClick={start}
            disabled={
              !loaded || busy || active || build?.status !== "succeeded"
            }
          >
            {active ? "검사 진행 중…" : "이 빌드의 코드 검사"}
          </button>
        </div>
        <p>
          선택한 빌드:{" "}
          <code>
            {build?.status === "succeeded"
              ? build.id.slice(0, 8)
              : "성공한 빌드가 필요합니다"}
          </code>
        </p>
        <p className="sandbox-boundary">
          연결된 검사기의 지원 언어·규칙 범위에서 검사합니다. 발견 사항 0건도
          전체 보안 통과나 운영 배포 승인을 뜻하지 않습니다. 실행 중 보안 검사는
          아래 DAST에서 별도로 진행합니다.
        </p>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {active && <p>검사 서비스의 결과와 커밋을 확인하고 있습니다.</p>}
        {run?.error && (
          <div className="failure">
            <strong>{run.error.message}</strong>
            <small>{run.error.code}</small>
          </div>
        )}
        {run?.status === "succeeded" && result && (
          <div className="security-result">
            <strong>
              {result.requires_review
                ? "검사 완료 · 조치 또는 확인 필요"
                : "검사 완료 · 해당 규칙에서 발견 사항 없음"}
            </strong>
            <p>
              조치 대상 {result.counts?.fail ?? 0}건 · 확인 필요{" "}
              {result.counts?.review ?? 0}건
            </p>
            <p className="muted">
              검사한 빌드 <code>{result.build_id?.slice(0, 8)}</code> · 커밋{" "}
              <code>{run.resolved_commit_sha?.slice(0, 12)}</code>
            </p>
            {(result.findings?.length ?? 0) > 0 && (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>구분</th>
                      <th>규칙</th>
                      <th>위치</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.findings?.map((f, i) => (
                      <tr key={i}>
                        <td>{f.kind === "fail" ? "조치 대상" : "확인 필요"}</td>
                        <td>{f.rule}</td>
                        <td>
                          {f.file}
                          {f.line ? `:${f.line}` : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {result.findings_truncated && (
              <p>
                상위 100건을 표시합니다. 전체 내용은 SAST 실행 기록을
                확인하세요.
              </p>
            )}
            <details>
              <summary>검사 근거</summary>
              <p>
                검사 ID: <code>{result.scanner_run_id}</code>
              </p>
              <p>
                이미지: <code>{result.image}</code>
              </p>
              <p>
                SARIF SHA-256: <code>{result.evidence_sha256}</code>
              </p>
            </details>
          </div>
        )}
        {!run && loaded && (
          <p className="muted">아직 코드 보안 검사 이력이 없습니다.</p>
        )}
      </section>
      <DastPanel
        key={serviceId}
        serviceId={serviceId}
        build={build}
        security={run}
      />
    </>
  );
}
