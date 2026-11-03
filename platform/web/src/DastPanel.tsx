import { useEffect, useState } from "react";
import { api, message, type Page, type Run } from "./api";

const labels: Record<string, string> = {
  REPRODUCED: "취약점 재현",
  NOT_REPRODUCED: "이번 검사에서 미재현",
  BLOCKED: "브라우저 정책이 차단 · 확인 필요",
  INCONCLUSIVE: "판정 불가",
};

export function DastPanel({
  serviceId,
  build,
  security,
}: {
  serviceId: string;
  build: Run | null;
  security: Run | null;
}) {
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const active = run?.status === "queued" || run?.status === "running";
  useEffect(() => {
    const abort = new AbortController();
    api<Page<Run>>(
      `/services/${serviceId}/dast-checks`,
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
      api<Run>(`/dast-checks/${run.id}`, undefined, undefined, abort.signal)
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
    if (!build || !security || busy) return;
    setBusy(true);
    setError("");
    try {
      setRun(
        await api<Run>(
          `/services/${serviceId}/dast-checks`,
          { build_id: build.id, security_check_id: security.id },
          crypto.randomUUID(),
        ),
      );
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    if (!run || busy) return;
    setBusy(true);
    setError("");
    try {
      setRun(await api<Run>(`/dast-checks/${run.id}/cancel`, {}));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  const ready =
    build?.status === "succeeded" &&
    security?.status === "succeeded" &&
    security.result?.build_id === build.id;
  const result = run?.result;
  const resultMatchesCurrentSelection =
    !!result &&
    result.build_id === build?.id &&
    result.security_check_id === security?.id;
  return (
    <section className="panel dast-panel" aria-label="실행 중 보안 검사">
      <div className="panel-top">
        <div>
          <h2>실행 중 보안 검사 · DAST</h2>
          <p className="muted">
            같은 이미지를 샌드박스에서 실행하고 SAST의 XSS 후보를 확인합니다.
          </p>
        </div>
        <button
          className="primary"
          onClick={start}
          disabled={!loaded || !ready || active || busy}
        >
          이 빌드의 DAST 검사
        </button>
      </div>
      <p className="sandbox-boundary">
        검사한 XSS 후보의 결과입니다. 미재현이나 검사 대상 없음은 전체 보안
        통과가 아닙니다. AI 수정안·승인·운영 배포는 아직 연결하지 않았습니다.
      </p>
      <p className="dast-environment">
        현재 검사 환경: AWS ECS 샌드박스 · GCP·온프레미스 대상은 연결되지
        않았으며, 각 환경의 별도 샌드박스 검증이 필요합니다.
      </p>
      {!ready && (
        <p className="muted">선택한 빌드의 코드 보안 검사를 먼저 완료하세요.</p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {active && (
        <div>
          <p>
            {run?.current_step === "dast_cleanup_wait"
              ? "앱·검사 서버의 정리를 확인 중입니다. 다음 검사는 잠시 기다려 주세요."
              : run?.cancel_requested
                ? "검사 중단과 앱 정리를 진행하고 있습니다."
                : "샌드박스 준비 → 브라우저 검사 → 앱 정리 중입니다."}
          </p>
          <button onClick={cancel} disabled={busy || run?.cancel_requested}>
            검사 중단
          </button>
        </div>
      )}
      {run?.error && (
        <div className="failure">
          <strong>{run.error.message}</strong>
          <small>{run.error.code}</small>
        </div>
      )}
      {run?.status === "succeeded" && result && (
        <div className="dast-result">
          <p className="muted">
            {resultMatchesCurrentSelection
              ? "현재 선택한 빌드와 코드 검사 결과"
              : "이전 검사 결과 · 현재 선택한 빌드의 결과가 아닙니다"}
          </p>
          <strong>
            {result.dast_check === "not_run"
              ? "지원하는 XSS 후보 없음 · DAST 미실행"
              : result.requires_review
                ? "DAST 완료 · 조치 또는 확인 필요"
                : "DAST 완료 · 검사한 후보에서 미재현"}
          </strong>
          <p>샌드박스 앱 종료 확인 · 실행 위치 AWS ECS · 운영 배포 승인 아님</p>
          <p>
            검사한 빌드 <code>{result.build_id?.slice(0, 8)}</code> · 커밋{" "}
            <code>{run.resolved_commit_sha?.slice(0, 12)}</code>
          </p>
          {result.dast_findings?.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>SAST 후보</th>
                    <th>DAST 결과</th>
                  </tr>
                </thead>
                <tbody>
                  {result.dast_findings.map((f) => (
                    <tr key={f.finding_id}>
                      <td>
                        <code>{f.finding_id}</code>
                      </td>
                      <td>{labels[f.status] ?? "확인 필요"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {result.findings_truncated && (
            <p>
              후보 중 최대 10건만 검사했습니다. 나머지 항목은 추가 확인이
              필요합니다.
            </p>
          )}
          <details>
            <summary>DAST 검사 근거</summary>
            <p>
              검사 ID: <code>{result.scanner_run_id}</code>
            </p>
            <p>
              SAST 검사: <code>{result.security_check_id}</code>
            </p>
            <p>
              이미지: <code>{result.image}</code>
            </p>
            <p>
              DAST 근거 SHA-256: <code>{result.dast_evidence_sha256}</code>
            </p>
          </details>
        </div>
      )}
      {!run && loaded && (
        <p className="muted">아직 DAST 검사 이력이 없습니다.</p>
      )}
    </section>
  );
}
