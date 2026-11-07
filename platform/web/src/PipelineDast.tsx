import { useEffect, useRef, useState } from "react";
import { api, message } from "./api";
import type { Board } from "./pipeline-model";

// ── Before/after code (unified patch → side-by-side) ─────────────────────────
type Row = {
  a?: number;
  b?: number;
  left?: string;
  right?: string;
  kind: "same" | "del" | "add" | "mod";
};
type FileDiff = { file: string; rows: Row[] };

export function parsePatch(patch: string): FileDiff[] {
  const files: FileDiff[] = [];
  let cur: FileDiff | null = null;
  let a = 0;
  let b = 0;
  let dels: { n: number; text: string }[] = [];
  let adds: { n: number; text: string }[] = [];
  const flush = () => {
    if (!cur) return;
    const n = Math.max(dels.length, adds.length);
    for (let i = 0; i < n; i++) {
      const d = dels[i];
      const p = adds[i];
      cur.rows.push({
        a: d?.n,
        b: p?.n,
        left: d?.text,
        right: p?.text,
        kind: d && p ? "mod" : d ? "del" : "add",
      });
    }
    dels = [];
    adds = [];
  };
  for (const line of patch.split("\n")) {
    if (line.startsWith("diff --git")) {
      flush();
      cur = {
        file: line.replace(/^diff --git a\/(\S+) b\/.*$/, "$1"),
        rows: [],
      };
      files.push(cur);
    } else if (line.startsWith("@@")) {
      flush();
      const m = line.match(/-(\d+)(?:,\d+)? \+(\d+)/);
      a = m ? Number(m[1]) : 1;
      b = m ? Number(m[2]) : 1;
      if (!cur) files.push((cur = { file: "", rows: [] }));
      if (cur.rows.length)
        cur.rows.push({ kind: "same", left: "⋯", right: "⋯" });
    } else if (
      !cur ||
      line.startsWith("---") ||
      line.startsWith("+++") ||
      line.startsWith("index ")
    ) {
      continue;
    } else if (line.startsWith("-")) {
      dels.push({ n: a++, text: line.slice(1) });
    } else if (line.startsWith("+")) {
      adds.push({ n: b++, text: line.slice(1) });
    } else if (line.startsWith(" ")) {
      flush();
      cur.rows.push({
        a: a++,
        b: b++,
        left: line.slice(1),
        right: line.slice(1),
        kind: "same",
      });
    }
  }
  flush();
  return files.filter((f) => f.rows.length);
}

export function DiffView({ patch, title }: { patch: string; title?: string }) {
  const files = parsePatch(patch);
  if (!files.length) return null;
  return (
    <div className="pd-diff">
      {title && <div className="pd-diff-title">{title}</div>}
      {files.map((f) => (
        <div key={f.file} className="pd-file">
          <div className="pd-file-name">
            <span>{f.file}</span>
            <span className="pd-legend">
              <i className="del" /> 수정 전 <i className="add" /> 수정 후
            </span>
          </div>
          <div className="pd-grid">
            <div className="pd-head">수정 전</div>
            <div className="pd-head">수정 후</div>
            {f.rows.map((r, i) => (
              <div key={i} className={`pd-row k-${r.kind}`}>
                <span className="pd-ln">{r.a ?? ""}</span>
                <code
                  className={
                    r.left !== undefined && r.kind !== "same" ? "del" : ""
                  }
                >
                  {r.left ?? ""}
                </code>
                <span className="pd-ln">{r.b ?? ""}</span>
                <code
                  className={
                    r.right !== undefined && r.kind !== "same" ? "add" : ""
                  }
                >
                  {r.right ?? ""}
                </code>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// AI fix proposal for the reproduced finding, shown while the person decides.
type Proposal = {
  status?: string;
  summary?: string;
  patch?: string;
  finding_id?: string;
};
export function FixProposal({ board }: { board: Board }) {
  const [fix, setFix] = useState<Proposal | null>(null);
  const [note, setNote] = useState("");
  useEffect(() => {
    let stop = false;
    async function load() {
      try {
        const f = await api<Proposal>(
          `/pipeline/runs/${board.run_id}/dast/fix`,
        );
        if (stop) return;
        setFix(f);
        if (f.status !== "ready") t = window.setTimeout(load, 3000);
      } catch (e) {
        if (!stop) setNote(message(e));
      }
    }
    let t: number | undefined;
    load();
    return () => {
      stop = true;
      window.clearTimeout(t);
    };
  }, [board.run_id]);
  if (fix?.status === "ready" && fix.patch)
    return (
      <DiffView patch={fix.patch} title="AI 수정안 · 승인 시 PR 로 반영" />
    );
  if (fix && fix.status !== "ready")
    return <p className="pd-note">AI 수정안 생성 중…</p>;
  return note ? <p className="pd-note">수정안 미리보기 없음</p> : null;
}

// ── DAST live screen (frames, stage, AI actions, verdicts, evidence) ─────────
type DastResult = {
  finding_id: string;
  status: string;
  message?: string;
  evidence?: { screenshots?: string[] };
};
type DastView = {
  status?: string;
  results?: DastResult[];
  findings?: { id: string; title: string }[];
};
type Ev = { type: string; [k: string]: unknown };
const STAGE: Record<string, string> = {
  prepare: "준비",
  login: "로그인",
  plan: "계획",
  probe: "검사 문자열 입력",
  verify: "실행 확인",
  impact: "영향 시연",
  report: "보고",
};
const ACT: Record<string, string> = {
  plan: "계획",
  navigate: "이동",
  type: "입력",
  click: "클릭",
  observe: "관찰",
};
const VERDICT: Record<string, [string, string]> = {
  REPRODUCED: ["재현됨", "bad"],
  BLOCKED: ["차단됨", "warn"],
  NOT_REPRODUCED: ["재현 안 됨", "ok"],
};

export function DastLive({ board }: { board: Board }) {
  const dast = board.nodes.find((n) => n.id === "dast");
  const started =
    !!(dast?.detail as { run_id?: string } | undefined)?.run_id ||
    dast?.state === "running";
  const running = dast?.state === "running";
  const [frame, setFrame] = useState("");
  const [live, setLive] = useState(false);
  const [now, setNow] = useState("");
  const [feed, setFeed] = useState<
    { tag: string; text: string; tone: string }[]
  >([]);
  const [view, setView] = useState<DastView | null>(null);
  const [zoom, setZoom] = useState("");
  const seen = useRef(0);

  async function loadView() {
    try {
      setView(await api<DastView>(`/pipeline/runs/${board.run_id}/dast`));
    } catch {
      /* not started yet */
    }
  }
  // A new run starts with a clean screen; a state change of the same run keeps the last frame.
  useEffect(() => {
    setFrame("");
    setFeed([]);
    setView(null);
    seen.current = 0;
  }, [board.run_id]);
  useEffect(() => {
    if (!started) return;
    loadView();
    let es: EventSource | null = null;
    let timer: number | undefined;
    let closed = false;
    const add = (tag: string, text: string, tone = "") =>
      setFeed((f) => [...f.slice(-7), { tag, text, tone }]);
    function connect() {
      if (closed) return;
      es = new EventSource(`/api/v1/pipeline/runs/${board.run_id}/dast/events`);
      es.onmessage = (m) => {
        let ev: Ev;
        try {
          ev = JSON.parse(m.data);
        } catch {
          return;
        }
        if (ev.type !== "frame") {
          const n = Number(m.lastEventId || 0);
          if (n && n <= seen.current) return;
          if (n) seen.current = n;
        }
        if (ev.type === "frame" && typeof ev.image === "string") {
          setFrame(ev.image);
          setLive(true);
        } else if (ev.type === "stage" && ev.status === "running") {
          setNow(STAGE[String(ev.stage)] ?? String(ev.stage));
        } else if (ev.type === "agent") {
          add(
            ACT[String(ev.action)] ?? String(ev.action),
            String(ev.message ?? ""),
          );
        } else if (ev.type === "probe") {
          add("입력", String(ev.payload_id ?? ev.message ?? ""), "warn");
        } else if (ev.type === "execution") {
          add("실행 탐지", "검사 문자열이 브라우저에서 실행됨", "bad");
        } else if (ev.type === "result") {
          const r = (ev.result ?? {}) as { status?: string };
          const v = VERDICT[r.status ?? ""];
          add("판정", v ? v[0] : String(r.status ?? ""), v ? v[1] : "");
          loadView();
        } else if (ev.type === "done" || ev.type === "error") {
          setLive(false);
          closed = true;
          es?.close();
          loadView();
        }
      };
      es.addEventListener("gone", () => {
        es?.close();
        setLive(false);
        if (!closed) timer = window.setTimeout(connect, 3000);
      });
      es.onerror = () => {
        es?.close();
        setLive(false);
        if (!closed) timer = window.setTimeout(connect, 3000);
      };
    }
    if (running) connect();
    const poll = running ? window.setInterval(loadView, 5000) : undefined;
    return () => {
      closed = true;
      es?.close();
      window.clearTimeout(timer);
      window.clearInterval(poll);
    };
  }, [board.run_id, started, running]);

  if (!started) return null;
  const titles = Object.fromEntries(
    (view?.findings ?? []).map((f) => [f.id, f.title]),
  );
  const shots = (view?.results ?? []).flatMap(
    (r) => r.evidence?.screenshots ?? [],
  );
  const screen = frame || shots[shots.length - 1] || "";
  return (
    <section className="pd-live" aria-label="DAST 화면">
      <div className="pd-live-head">
        <strong>DAST 화면</strong>
        {live ? (
          <span className="pd-badge live">● LIVE</span>
        ) : (
          <span className="pd-badge">기록</span>
        )}
        {now && running && <span className="pd-now">{now}</span>}
      </div>
      <button
        className="pd-screen"
        onClick={() => screen && setZoom(screen)}
        disabled={!screen}
      >
        {screen ? (
          <img src={screen} alt="DAST 가 조작 중인 스테이징 화면" />
        ) : (
          <span>화면 대기 중…</span>
        )}
      </button>
      {feed.length > 0 && (
        <ol className="pd-feed">
          {feed.map((f, i) => (
            <li key={i} className={f.tone}>
              <b>{f.tag}</b> {f.text}
            </li>
          ))}
        </ol>
      )}
      {(view?.results ?? []).length > 0 && (
        <ul className="pd-results">
          {(view?.results ?? []).map((r) => {
            const v = VERDICT[r.status];
            return (
              <li key={r.finding_id}>
                <span className={`pd-verdict ${v?.[1] ?? ""}`}>
                  {v?.[0] ?? r.status}
                </span>
                <span>{titles[r.finding_id] ?? r.finding_id}</span>
                <span className="pd-shots">
                  {(r.evidence?.screenshots ?? []).map((s) => (
                    <button key={s} onClick={() => setZoom(s)}>
                      <img src={s} alt="증거 스크린샷" loading="lazy" />
                    </button>
                  ))}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {zoom && (
        <div
          className="pd-zoom"
          role="dialog"
          aria-label="화면 크게 보기"
          onClick={() => setZoom("")}
        >
          <img src={zoom} alt="DAST 화면 크게 보기" />
        </div>
      )}
    </section>
  );
}

// ── Big overlays: live DAST picture-in-picture and the AI fix approval notice ──
export function DastPip({
  board,
  onOpen,
}: {
  board: Board;
  onOpen: () => void;
}) {
  return (
    <div className="pd-pip" role="region" aria-label="DAST 실시간 화면">
      <div className="pd-pip-bar">
        <span>DAST 실시간</span>
        <button onClick={onOpen}>상세</button>
      </div>
      <DastLive board={board} />
    </div>
  );
}

type Decision = {
  id?: string;
  prompt: string;
  options: { value: string; label: string }[];
  case_id?: string;
};
type FixDetail = Proposal & {
  changes?: { file: string; reason: string }[];
  trace?: { role: string; file: string; line?: number }[];
};

export function FixModal({
  board,
  decision,
  onAnswered,
  onClose,
}: {
  board: Board;
  decision: Decision;
  onAnswered: () => void;
  onClose: () => void;
}) {
  const [fix, setFix] = useState<FixDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let stop = false;
    let t: number | undefined;
    async function load() {
      try {
        const f = await api<FixDetail>(
          `/pipeline/runs/${board.run_id}/dast/fix`,
        );
        if (stop) return;
        setFix(f);
        if (f.status !== "ready") t = window.setTimeout(load, 3000);
      } catch {
        if (!stop) setFix({ status: "none" });
      }
    }
    load();
    return () => {
      stop = true;
      window.clearTimeout(t);
    };
  }, [board.run_id]);
  async function answer(value: string) {
    setBusy(true);
    setError("");
    try {
      await api(`/pipeline/runs/${board.run_id}/answer`, {
        value,
        decision_id: decision.id ?? null,
        case_id: decision.case_id ?? null,
      });
      onAnswered();
      onClose();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  const sink = fix?.trace?.find((t) => t.role === "sink");
  const source = fix?.trace?.find((t) => t.role === "source");
  return (
    <div
      className="pd-modal-back"
      role="dialog"
      aria-modal="true"
      aria-label="AI 코드 수정 승인"
    >
      <div className="pd-modal">
        <header>
          <span className="pd-modal-tag">AI 코드 수정</span>
          <h2>취약점 재현 · 수정안 확인</h2>
          <button
            className="pd-modal-close"
            aria-label="닫기"
            onClick={onClose}
          >
            ×
          </button>
        </header>
        <ul className="pd-modal-keys">
          <li>
            <b>발견</b> SAST{" "}
            {source
              ? `· 입력 ${source.file.split("/").pop()}:${source.line ?? ""}`
              : ""}
            {sink
              ? ` → 출력 ${sink.file.split("/").pop()}:${sink.line ?? ""}`
              : ""}
          </li>
          <li>
            <b>재현</b> DAST · <span className="pd-verdict bad">재현됨</span>
          </li>
          <li>
            <b>수정</b>{" "}
            {(fix?.changes ?? [])
              .map((c) => c.file.split("/").pop())
              .join(", ") || "AI 수정안"}
          </li>
        </ul>
        <div className="pd-modal-body">
          {fix?.status === "ready" && fix.patch ? (
            <DiffView patch={fix.patch} />
          ) : (
            <p className="pd-note">
              {fix?.status === "none"
                ? "수정안 미리보기 없음"
                : "AI 수정안 생성 중…"}
            </p>
          )}
        </div>
        <footer>
          {error && <span className="pl-error">{error}</span>}
          {decision.options.map((o) => (
            <button
              key={o.value}
              className={o.value === "fix" ? "pd-primary" : ""}
              disabled={busy}
              onClick={() => answer(o.value)}
            >
              {o.label}
            </button>
          ))}
        </footer>
      </div>
    </div>
  );
}
