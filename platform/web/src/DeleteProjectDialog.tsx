import { useEffect, useRef, useState } from "react";
import { api, message, type Service, type Run } from "./api";
import "./project-delete.css";

export function DeleteProjectDialog({
  service,
  onCancel,
  onDeleted,
}: {
  service: Service;
  onCancel: () => void;
  onDeleted: (service: Service) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const submitting = useRef(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(Boolean(service.deletion_requested));
  const [pending, setPending] = useState(Boolean(service.deletion_requested));
  const [error, setError] = useState("");
  useEffect(() => {
    const element = dialog.current!;
    const overflow = document.body.style.overflow;
    const previousFocus = document.activeElement;
    element.showModal();
    cancelButton.current?.focus();
    document.body.style.overflow = "hidden";
    return () => {
      element.close();
      document.body.style.overflow = overflow;
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected)
        previousFocus.focus();
    };
  }, []);
  function completed() {
    try {
      sessionStorage.removeItem(`delta:environment:${service.id}`);
    } catch {
      /* Storage may be disabled. */
    }
    onDeleted(service);
  }
  useEffect(() => {
    if (!pending) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const state = await api<{ status: string; run: Run | null }>(
          `/services/${encodeURIComponent(service.id)}/deletion`,
        );
        if (!active) return;
        if (state.status === "deleted") {
          completed();
          return;
        }
        if (state.status === "failed") {
          setError(
            state.run?.error?.message ||
              "자원 삭제에 실패했습니다. 다시 시도하세요.",
          );
          setPending(false);
          setBusy(false);
          return;
        }
        timer = setTimeout(poll, 2000);
      } catch (e) {
        if (!active) return;
        setError(
          `상태 조회 실패: ${message(e)} 삭제 작업은 서버에서 계속됩니다.`,
        );
        timer = setTimeout(poll, 5000);
      }
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [pending, service.id]);
  async function remove(event: React.FormEvent) {
    event.preventDefault();
    if (name !== service.name || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api<{ status: string; run: Run } | undefined>(
        `/services/${encodeURIComponent(service.id)}/delete`,
        {
          confirmation_name: name,
        },
      );
      if (result) {
        setPending(true);
      } else {
        completed();
      }
    } catch (e) {
      setError(message(e));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  return (
    <dialog
      className="project-delete-dialog"
      ref={dialog}
      aria-labelledby="delete-project-title"
      aria-describedby="delete-project-description"
      onCancel={(event) => {
        event.preventDefault();
        if (!submitting.current) onCancel();
      }}
    >
      <form onSubmit={remove} aria-busy={busy}>
        <span className="delete-project-eyebrow">프로젝트 관리</span>
        <h2 id="delete-project-title">프로젝트를 삭제할까요?</h2>
        <p className="delete-project-name">{service.name}</p>
        <p id="delete-project-description">
          이 프로젝트가 배포한 VM·앱과 전용 이미지 태그를 정리한 뒤 등록 정보와
          이력을 삭제합니다. 삭제 후 복구할 수 없습니다.
        </p>
        <p className="delete-project-boundary">
          GitHub 저장소, 공용 서버·네트워크, DB 볼륨과 공유 이미지는 보존합니다.
          소유 정보를 확인할 수 없는 환경은 삭제하지 않고 알려드립니다.
        </p>
        <label htmlFor="delete-project-confirmation">
          확인을 위해 프로젝트 이름을 입력하세요
        </label>
        <input
          id="delete-project-confirmation"
          value={name}
          onChange={(event) => setName(event.target.value)}
          disabled={busy || pending}
          autoComplete="off"
          spellCheck={false}
          maxLength={80}
        />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {(busy || pending) && (
          <p className="muted" role="status">
            실제 자원을 정리하고 있습니다. 이 창을 닫아도 작업은 계속됩니다.
          </p>
        )}
        <div className="delete-project-actions">
          <button
            type="button"
            ref={cancelButton}
            disabled={submitting.current}
            onClick={onCancel}
          >
            {pending ? "닫기" : "취소"}
          </button>
          <button
            type="submit"
            className="project-delete-confirm"
            disabled={busy || pending || name !== service.name}
          >
            {busy || pending ? "삭제 중…" : "프로젝트 삭제"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
