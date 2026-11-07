import { useEffect, useState } from "react";
import { api, message, type Service } from "./api";
import "./application-flow.css";
const labels: Record<string, string> = {
  queued: "대기",
  running: "진행 중",
  succeeded: "완료",
  failed: "실패",
  blocked: "설정 대기",
  review: "검토 필요",
  skipped: "미실행",
};
// Registration stands up the current production; SAST/DAST run later in the migration pipeline.
const stages = [
  ["source", "소스 확인"],
  ["build", "Jenkins 빌드"],
  ["sandbox", "샌드박스"],
  ["deploy", "VM·DB 준비 및 배포"],
];
export function ApplicationProgress({
  service,
  onUpdate,
}: {
  service: Service;
  onUpdate: (service: Service) => void;
}) {
  const [current, setCurrent] = useState(service);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function refresh() {
      try {
        const result = await api<Service>(
          `/services/${service.id}`,
          undefined,
          undefined,
          controller.signal,
        );
        if (!stopped) {
          setCurrent(result);
          onUpdate(result);
          setError("");
          if (result.application?.status === "succeeded") return;
        }
      } catch (e) {
        if (!stopped) setError(message(e));
      }
      if (!stopped) timer = setTimeout(refresh, 3000);
    }
    refresh();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [service.id, onUpdate]);
  const app = current.application;
  if (!app || app.status === "succeeded") return null;
  async function resume(approve = false) {
    setBusy(true);
    try {
      const result = await api<Service>(
        `/applications/${service.id}/${approve ? "approve-review" : "resume"}`,
        {},
      );
      setCurrent(result);
      onUpdate(result);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel application-progress" aria-label="자동 배포 진행">
      <div className="panel-top">
        <h2>자동 배포 진행</h2>
        <strong role="status">{labels[app.status]}</strong>
      </div>
      <p>프론트엔드 + 백엔드 · PostgreSQL · {app.target.cloud.toUpperCase()}</p>
      <ol className="application-progress-line">
        {stages.map(([key, label]) => {
          const steps = app.steps.filter(
            (s) => s.key === key || s.key.startsWith(key + "_"),
          );
          const total = key === "deploy" ? 1 : 2;
          const paused =
            ["blocked", "review"].includes(app.status) &&
            app.steps.at(-1)?.key.startsWith(key);
          const status = paused
            ? app.status
            : steps.some((s) => s.status === "failed")
              ? "failed"
              : steps.some((s) => s.status === "skipped")
                ? "skipped"
                : steps.length === total &&
                    steps.every((s) => s.status === "succeeded")
                  ? "succeeded"
                  : steps.length
                    ? "running"
                    : "queued";
          return (
            <li key={key} data-status={status}>
              <strong>{label}</strong>
              <span>{labels[status]}</span>
            </li>
          );
        })}
      </ol>
      <p className="muted">
        샌드박스는 구성 요소별 실행 검사입니다. 마지막 배포 단계에서 FE → BE →
        PostgreSQL 연결을 확인합니다.
      </p>
      {app.error && <p role="alert">{app.error}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <details open={app.status === "failed" || app.status === "review"}>
        <summary>단계별 진행 보기</summary>
        <ul>
          {app.steps.map((s) => (
            <li key={s.key}>
              {s.label}: {labels[s.status]}{" "}
              {s.detail && <small>({s.detail})</small>}
              {s.report && (
                <details>
                  <summary>검사 결과</summary>
                  <pre>{JSON.stringify(s.report, null, 2)}</pre>
                </details>
              )}
            </li>
          ))}
        </ul>
        {app.status === "review" && (
          <p>
            빌드·검사의 상세 결과에서 발견 사항을 확인하세요. DAST 미실행은 보안
            통과를 뜻하지 않습니다.
          </p>
        )}
      </details>
      {app.status === "failed" &&
        app.steps.some(
          (s) => s.status === "failed" && /^(security|dast)_/.test(s.key),
        ) && (
          <div className="application-retired">
            <p>
              이전 방식(플랫폼 SAST·DAST)에서 멈춘 등록입니다. 보안 검사는 이전
              파이프라인이 맡으므로 샌드박스 → 배포로 이어서 진행할 수 있습니다.
            </p>
            <button
              className="primary"
              disabled={busy}
              onClick={() => resume()}
            >
              이어서 진행
            </button>
          </div>
        )}
      {app.status === "blocked" && (
        <button disabled={busy} onClick={() => resume()}>
          설정 확인 후 계속
        </button>
      )}
      {app.status === "review" && (
        <button disabled={busy} onClick={() => resume(true)}>
          검사 결과를 검토했습니다 · 배포 계속
        </button>
      )}
      {app.status === "succeeded" && (
        <p>
          FE·BE 및 PostgreSQL 연결 확인 완료. DB 데이터는 앱 삭제 시에도
          보존합니다.
        </p>
      )}
    </section>
  );
}
