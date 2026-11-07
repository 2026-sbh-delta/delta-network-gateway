import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Service } from "./api";

export type View =
  "overview" | "build" | "migration" | "monitoring" | "pipeline";
export type WorkspaceRoute = { view: View; serviceId: string | null };
export const sections: { view: View; title: string; description: string }[] = [
  {
    view: "overview",
    title: "서비스 개요",
    description: "서비스와 다음 작업 확인",
  },
  {
    view: "build",
    title: "빌드·검사",
    description: "소스부터 실행·보안 검사까지",
  },
  {
    view: "migration",
    title: "이전 계획",
    description: "대상 환경 선택과 계획 승인",
  },
  {
    view: "monitoring",
    title: "모니터링",
    description: "실행 환경의 상태와 지표",
  },
  {
    view: "pipeline",
    title: "파이프라인",
    description: "웹 검증·차분 분석·운영 반영",
  },
];
function readRoute(): WorkspaceRoute {
  const match = location.hash.match(
    /^#\/(overview|build|migration|monitoring|pipeline)(?:\/([^/]+))?$/,
  );
  if (!match) return { view: "overview", serviceId: null };
  try {
    const serviceId = match[2] ? decodeURIComponent(match[2]) : null;
    // A route ID is one API path segment, never a relative path or query.
    if (serviceId && !/^[a-zA-Z0-9_-]+$/.test(serviceId))
      return { view: "overview", serviceId: null };
    return { view: match[1] as View, serviceId };
  } catch {
    return { view: "overview", serviceId: null };
  }
}
export function useWorkspaceRoute() {
  const [route, setRoute] = useState(readRoute);
  useEffect(() => {
    const changed = () => setRoute(readRoute());
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  function navigate(view: View, serviceId: string | null = route.serviceId) {
    location.hash = `/${view}${serviceId ? `/${encodeURIComponent(serviceId)}` : ""}`;
  }
  return { route, navigate };
}
export function Icon({ name }: { name: View | "menu" | "arrow" }) {
  const paths = {
    overview: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
    pipeline:
      "M5 6a2 2 0 1 0 0 .1 M5 18a2 2 0 1 0 0 .1 M19 12a2 2 0 1 0 0 .1 M7 6h4l4 6M7 18h4l4-6",
    build: "m8 5-6 7 6 7 M16 5l6 7-6 7 M14 3l-4 18",
    migration: "M3 7h16m-5-5 5 5-5 5 M21 17H5m5-5-5 5 5 5",
    monitoring: "M3 3v18h18 M5 14l4-5 4 7 6-11",
    menu: "M4 6h16 M4 12h16 M4 18h16",
    arrow: "M4 12h16 M14 6l6 6-6 6",
  };
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}

// Authentication belongs to the caller; the shell only renders an optional account slot.
export function AppShell({
  route,
  navigate,
  services,
  account,
  children,
}: {
  route: WorkspaceRoute;
  navigate: (view: View, serviceId?: string | null) => void;
  services: Service[];
  account?: ReactNode;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const main = useRef<HTMLElement>(null);
  const firstRender = useRef(true);
  const section = sections.find((s) => s.view === route.view)!;
  const service = services.find((s) => s.id === route.serviceId);
  const sectionTitle =
    service?.application?.status === "succeeded" &&
    (route.view === "overview" || route.view === "build")
      ? "배포 환경"
      : section.title;
  function closeDrawer() {
    dialog.current?.close();
  }
  function go(view: View, id: string | null = route.serviceId) {
    closeDrawer();
    navigate(view, id);
  }
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    main.current?.focus();
    window.scrollTo({ top: 0 });
  }, [route.view, route.serviceId]);
  useEffect(() => {
    if (!open) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = overflow;
    };
  }, [open]);
  function sidebar() {
    return (
      <>
        <button
          className="shell-brand"
          onClick={() => go("overview", null)}
          aria-label="DELTA 서비스 목록"
        >
          <span className="brand-symbol">Δ</span>
          <span>
            DELTA<small>CLOUD PLATFORM</small>
          </span>
        </button>
        <div className="sidebar-context">
          <label>
            작업 서비스
            <select
              aria-label="작업 서비스 선택"
              value={route.serviceId ?? ""}
              onChange={(e) => go(route.view, e.target.value || null)}
            >
              <option value="">전체 서비스</option>
              {route.serviceId && !service && (
                <option value={route.serviceId}>선택한 서비스</option>
              )}
              {services.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="sidebar-label">WORKSPACE</div>
        <nav aria-label="주요 메뉴" className="sidebar-nav">
          {sections.map((s, i) => (
            <button
              key={s.view}
              aria-label={s.title}
              onClick={() => go(s.view)}
              aria-current={route.view === s.view ? "page" : undefined}
            >
              <Icon name={s.view} />
              <span>{s.title}</span>
              <small>{String(i + 1).padStart(2, "0")}</small>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-guide">
            <span>서비스의 다음 목적지</span>
            <strong>
              준비부터 이전까지,
              <br />
              하나의 작업 공간.
            </strong>
            <p>
              확인한 결과를 바탕으로
              <br />
              다음 단계를 진행하세요.
            </p>
          </div>
          {account && <div className="shell-account">{account}</div>}
        </div>
      </>
    );
  }
  return (
    <div className="app-shell">
      <a
        className="skip-link"
        href="#workspace-content"
        onClick={(e) => {
          e.preventDefault();
          main.current?.focus();
        }}
      >
        본문으로 이동
      </a>
      <dialog
        id="workspace-drawer"
        ref={dialog}
        className="workspace-drawer"
        aria-label="작업 공간 메뉴"
        onClose={() => setOpen(false)}
        onKeyDown={(event) => {
          if (event.key !== "Tab") return;
          const elements = event.currentTarget.querySelectorAll<HTMLElement>(
            "button:not([disabled]), select:not([disabled]), a[href]",
          );
          const first = elements[0];
          const last = elements[elements.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last?.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
          }
        }}
        onClick={(e) => {
          if (e.target === e.currentTarget) closeDrawer();
        }}
      >
        <div className="drawer-content">
          <button
            className="drawer-close"
            onClick={closeDrawer}
            aria-label="메뉴 닫기"
          >
            ×
          </button>
          {sidebar()}
        </div>
      </dialog>
      <div className="shell-body">
        <header className="shell-topbar">
          <button
            className="drawer-trigger"
            aria-label="메뉴 열기"
            aria-controls="workspace-drawer"
            aria-haspopup="dialog"
            aria-expanded={open}
            onClick={() => {
              dialog.current?.showModal();
              setOpen(true);
            }}
          >
            <Icon name="menu" />
          </button>
          <div className="breadcrumbs">
            <span>워크스페이스</span>
            <span aria-hidden="true">/</span>
            <strong>{sectionTitle}</strong>
          </div>
          <span className="shell-context-label">
            {service?.name ?? "공통 작업 공간"}
          </span>
        </header>
        <main
          id="workspace-content"
          ref={main}
          tabIndex={-1}
          className={`workspace presentation-workspace${route.view === "pipeline" ? " flow-workspace" : ""}`}
        >
          {children}
        </main>
        <footer className="shell-footer">
          <strong>DELTA</strong>
          <span>서비스의 다음 환경을 준비합니다.</span>
        </footer>
      </div>
    </div>
  );
}
