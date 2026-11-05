import { useEffect, useRef, useState } from "react";
import { api, message, type Run } from "./api";
import type { Environment } from "./observability-api";
import "./project-delete.css";

type Preview = {
  configured: boolean;
  reason: string | null;
  resources: { kind: string; name: string }[];
  binding_hash: string | null;
  state: string;
  run: Run | null;
};

export function RemoveRuntime({
  serviceId,
  serviceName,
  environment,
  onState,
}: {
  serviceId: string;
  serviceName: string;
  environment: Environment;
  onState: (state: string) => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [plan, setPlan] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const submitting = useRef(false);
  const requestKey = useRef<string | null>(null);
  const path = `/services/${encodeURIComponent(serviceId)}/environments/${encodeURIComponent(environment.id)}/removal`;
  useEffect(() => {
    if (!environment.runtime_registered) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const next = await api<Preview>(
          path,
          undefined,
          undefined,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setPreview(next);
          setLoadError("");
          onState(next.state);
        }
      } catch (e) {
        if (!controller.signal.aborted) setLoadError(message(e));
      }
      if (!controller.signal.aborted) timer = setTimeout(read, 2000);
    }
    void read();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [path, environment.runtime_registered, refresh, onState]);
  useEffect(() => {
    if (!open) return;
    const element = dialog.current!;
    const focus = document.activeElement;
    element.showModal();
    return () => {
      element.close();
      if (focus instanceof HTMLElement && focus.isConnected) focus.focus();
    };
  }, [open]);
  const active =
    preview?.run && ["queued", "running"].includes(preview.run.status);
  async function remove(event: React.FormEvent) {
    event.preventDefault();
    if (submitting.current || name !== serviceName || !plan?.binding_hash)
      return;
    submitting.current = true;
    setBusy(true);
    setError("");
    requestKey.current ??= crypto.randomUUID();
    try {
      const run = await api<Run>(
        path,
        { confirmation_name: name, binding_hash: plan.binding_hash },
        requestKey.current,
      );
      setPreview({
        ...plan,
        run,
        state: run.status === "succeeded" ? "removed" : "removing",
      });
      onState(run.status === "succeeded" ? "removed" : "removing");
      setOpen(false);
      setRefresh((n) => n + 1);
      requestKey.current = null;
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
      submitting.current = false;
    }
  }
  if (!environment.runtime_registered)
    return <p className="muted">실행 앱 삭제 대상 미등록</p>;
  return (
    <div className="runtime-removal">
      {preview?.state === "removed" ? (
        <p role="status">앱 삭제 완료 · DB·볼륨·서버와 이력은 보존했습니다.</p>
      ) : (
        <>
          {active && (
            <p role="status">
              {preview?.run?.status === "queued"
                ? "앱 삭제 대기 중"
                : "앱 삭제 중 · 종료 확인 중"}
            </p>
          )}
          {preview?.run?.status === "failed" && (
            <p role="alert">
              삭제 실패 · {preview.run.error?.message} 다시 시도하면 남은 자원을
              확인합니다.
            </p>
          )}
          {!preview?.configured && (
            <p className="muted">{preview?.reason ?? "삭제 연결 확인 중"}</p>
          )}
          <button
            type="button"
            disabled={!preview?.configured || !!active || busy || !!loadError}
            onClick={() => {
              setName("");
              setError("");
              setPlan(preview);
              requestKey.current = null;
              setOpen(true);
            }}
          >
            {preview?.run?.status === "failed"
              ? "앱 삭제 다시 시도"
              : "실행 앱 삭제"}
          </button>
        </>
      )}
      {error && !open && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loadError && (
        <p className="error" role="alert">
          {loadError}
        </p>
      )}
      {open && (
        <dialog
          ref={dialog}
          className="project-delete-dialog"
          aria-labelledby={`remove-${environment.id}`}
          onCancel={(e) => {
            e.preventDefault();
            if (!submitting.current) setOpen(false);
          }}
        >
          <form onSubmit={remove} aria-busy={busy}>
            <h2 id={`remove-${environment.id}`}>
              온프레미스 실행 앱을 삭제할까요?
            </h2>
            <p>
              {serviceName} · {environment.name}
            </p>
            <ul>
              {plan?.resources.map((r) => (
                <li key={`${r.kind}/${r.name}`}>
                  {r.kind} · {r.name}
                </li>
              ))}
            </ul>
            <p>
              앱이 종료되고 접속할 수 없게 됩니다. 앱 메모리 안의 데이터는
              사라집니다.
            </p>
            <p>DB·저장 볼륨·VM·k3s와 플랫폼 등록 정보·이력은 보존합니다.</p>
            <label>
              확인을 위해 서비스 이름을 입력하세요
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={busy}
                autoComplete="off"
                maxLength={80}
              />
            </label>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <div className="delete-project-actions">
              <button
                type="button"
                disabled={busy}
                onClick={() => setOpen(false)}
              >
                취소
              </button>
              <button
                type="submit"
                className="project-delete-confirm"
                disabled={busy || name !== serviceName}
              >
                {busy ? "요청 중…" : "실행 앱 삭제 확인"}
              </button>
            </div>
          </form>
        </dialog>
      )}
    </div>
  );
}
