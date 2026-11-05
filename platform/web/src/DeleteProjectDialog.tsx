import { useEffect, useRef, useState } from "react";
import { api, message, type Service } from "./api";
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
  const [busy, setBusy] = useState(false);
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
  async function remove(event: React.FormEvent) {
    event.preventDefault();
    if (name !== service.name || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      await api(`/services/${encodeURIComponent(service.id)}/delete`, {
        confirmation_name: name,
      });
      try {
        sessionStorage.removeItem(`delta:environment:${service.id}`);
      } catch {
        /* Storage may be disabled. */
      }
      onDeleted(service);
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
          플랫폼에 등록한 서비스와 빌드·검사 이력이 영구 삭제됩니다. 삭제 후
          복구할 수 없습니다.
        </p>
        <p className="delete-project-boundary">
          GitHub 저장소와 실제 클라우드 자원은 삭제하지 않습니다. 실행 중인
          작업이나 연결된 환경·계획이 있으면 삭제할 수 없습니다.
        </p>
        <p className="muted">
          온프레미스 앱을 종료·제거하려면 개요의 환경 카드에서 ‘실행 앱 삭제’를
          사용하세요. DB 데이터와 플랫폼 이력은 보존합니다.
        </p>
        <label htmlFor="delete-project-confirmation">
          확인을 위해 프로젝트 이름을 입력하세요
        </label>
        <input
          id="delete-project-confirmation"
          value={name}
          onChange={(event) => setName(event.target.value)}
          disabled={busy}
          autoComplete="off"
          spellCheck={false}
          maxLength={80}
        />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {busy && (
          <p className="muted" role="status">
            삭제 중입니다…
          </p>
        )}
        <div className="delete-project-actions">
          <button
            type="button"
            ref={cancelButton}
            disabled={busy}
            onClick={onCancel}
          >
            취소
          </button>
          <button
            type="submit"
            className="project-delete-confirm"
            disabled={busy || name !== service.name}
          >
            {busy ? "삭제 중…" : "프로젝트 삭제"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
