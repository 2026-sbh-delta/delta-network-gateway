import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, message } from "./api";
import {
  OUTCOME,
  STATE_LABEL,
  activeStage,
  currentNode,
  duration,
  stagesOf,
  waitingDecision,
  type Board,
  type BoardNode,
  type Gate,
  type GateId,
  type RunSummary,
  type Stage,
  type StageId,
  type StageState,
} from "./pipeline-model";
import {
  DastLive,
  DastPip,
  DiffView,
  FixModal,
  FixProposal,
} from "./PipelineDast";
import "./pipeline.css";

type Selection = StageId | GateId;

// Summaries carry digests, URLs and hashes; the screen shows only what happened.
function clean(text: unknown) {
  return String(text ?? "")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/(sha256:)?[0-9a-f]{12,}/gi, "")
    .replace(/이미지\s*(?=·|$)/g, "")
    .replace(/(\s*·\s*){2,}/g, " · ")
    .replace(/^[\s·,]+|[\s·,]+$/g, "")
    .trim();
}

function short(sha: unknown) {
  return typeof sha === "string" ? sha.slice(0, 7) : "";
}
function when(ts: number) {
  return new Date(ts * 1000).toLocaleString("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function runLabel(r: RunSummary) {
  const branch = String(r.options.branch || "main");
  const state = r.outcome
    ? (OUTCOME[r.outcome]?.label ?? r.outcome)
    : "진행 중";
  return `${when(r.started_at)} · ${branch} @ ${short(r.options.commit)} · ${state}`;
}

export function StatePill({ state }: { state: StageState }) {
  return <span className={`pl-pill s-${state}`}>{STATE_LABEL[state]}</span>;
}

// Latest run summary for the top bar. Polls slowly; the pipeline screen polls the board itself.
export function useLatestRun() {
  const [latest, setLatest] = useState<Board | null>(null);
  useEffect(() => {
    let stop = false;
    async function tick() {
      try {
        const { runs } = await api<{ runs: RunSummary[] }>("/pipeline/runs");
        const board = runs[0]
          ? await api<Board>(`/pipeline/runs/${runs[0].run_id}`)
          : null;
        if (!stop) setLatest(board);
      } catch {
        if (!stop) setLatest(null);
      }
    }
    tick();
    const t = setInterval(tick, 5000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, []);
  return latest;
}

export function RunStatus({
  board,
  onOpen,
}: {
  board: Board | null;
  onOpen: () => void;
}) {
  if (!board) return null;
  const node = currentNode(board);
  const waiting = waitingDecision(board);
  const outcome = board.outcome ? OUTCOME[board.outcome] : null;
  return (
    <button
      className="pl-topstatus"
      onClick={onOpen}
      title="파이프라인 화면 열기"
    >
      <span
        className={`pl-dot s-${waiting ? "waiting" : node ? "running" : (outcome?.tone ?? "pending")}`}
      />
      <span>
        {waiting
          ? `승인 대기 · ${waiting.node.label}`
          : node
            ? `${node.label} 진행 중 · ${duration(node.started_at, null)}`
            : `최근 실행 ${outcome?.label ?? "대기"}`}
      </span>
    </button>
  );
}

// Overview: the latest run in one line plus the five stage states.
export function LatestRunCard({
  board,
  onOpen,
}: {
  board: Board;
  onOpen: () => void;
}) {
  const { stages } = stagesOf(board);
  const outcome = board.outcome ? OUTCOME[board.outcome] : null;
  const node = currentNode(board);
  return (
    <section className="panel pl-latest">
      <div className="pl-latest-head">
        <h2>최근 파이프라인 실행</h2>
        <span className="pl-muted">
          {String(board.options.branch || "main")} @{" "}
          {short(board.options.commit)} · {when(board.started_at)}
        </span>
        <StatePill
          state={
            board.outcome
              ? (outcome?.tone ?? "pending")
              : (node?.state ?? "running")
          }
        />
        <button onClick={onOpen}>열기</button>
      </div>
      <ol className="pl-latest-stages">
        {stages.map((s) => (
          <li key={s.id} className={`s-${s.state}`}>
            <i />
            {s.title}
            <small>{STATE_LABEL[s.state]}</small>
          </li>
        ))}
      </ol>
    </section>
  );
}

// Flow canvas modelled on Langflow's node editor (white version): nodes with field rows,
// ports on the node edge, an output row, message-style approval nodes and curved wires.
// Positions are canvas pixels; wires are measured from the rendered ports.
type Box = { x: number; y: number; w: number };
const LAYOUT: Record<StageId | GateId, Box> = {
  build: { x: 40, y: 76, w: 224 },
  verify: { x: 300, y: 76, w: 224 },
  diff: { x: 40, y: 392, w: 484 },
  "release-gate": { x: 570, y: 300, w: 158 },
  release: { x: 766, y: 214, w: 224 },
  "cutover-gate": { x: 1028, y: 300, w: 158 },
  cutover: { x: 1224, y: 214, w: 224 },
};
const CANVAS = { w: 1488, h: 620 };
const WIRES: [string, string][] = [
  ["build", "verify"],
  ["verify", "release-gate"],
  ["diff", "release-gate"],
  ["release-gate", "release"],
  ["release", "cutover-gate"],
  ["cutover-gate", "cutover"],
];
const OUTPUT: Record<StageId, string> = {
  build: "이미지 · 검사 결과",
  verify: "검증 결과",
  diff: "매핑표",
  release: "운영 환경",
  cutover: "서비스 도메인",
};

const done = (s: StageState) => s === "pass" || s === "skipped" || s === "warn";
const reached = (s: StageState) =>
  s !== "pending" && s !== "off" && s !== "mock";
const ICON: Record<StageId | "person", string> = {
  build: "M4 7l8-4 8 4-8 4z M4 7v10l8 4 8-4V7 M12 11v10",
  verify: "M3 5h18v12H3z M3 9h18 M9 21h6 M12 13a2 2 0 1 0 0 .1",
  diff: "M4 6h7v5H4z M13 13h7v5h-7z M11 8.5h3a2 2 0 0 1 2 2V13",
  release:
    "M7 18a4 4 0 0 1 .5-8A6 6 0 0 1 19 11a3.5 3.5 0 0 1 0 7z M12 17v-6 M9.5 13.5 12 11l2.5 2.5",
  cutover:
    "M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18z M3 12h18 M12 3c3 3 3 15 0 18 M12 3c-3 3-3 15 0 18",
  person: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M4 21a8 8 0 0 1 16 0",
};

function Glyph({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

function span(nodes: BoardNode[]): BoardNode | undefined {
  const started = nodes
    .map((n) => n.started_at)
    .filter((t): t is number => !!t);
  if (!started.length) return undefined;
  const ended = nodes.map((n) => n.ended_at).filter((t): t is number => !!t);
  const open = nodes.some((n) => n.started_at && !n.ended_at);
  return {
    ...nodes[0],
    started_at: Math.min(...started),
    ended_at: open || !ended.length ? null : Math.max(...ended),
  };
}

function Chip({ state, node }: { state: StageState; node?: BoardNode }) {
  const time = node ? duration(node.started_at, node.ended_at) : "";
  if (state === "pass")
    return <span className="lf-chip s-pass">✓ {time || "완료"}</span>;
  if (state === "running") return null; // the floating "실행 중" label carries it
  if (state === "pending") return null;
  return <span className={`lf-chip s-${state}`}>{STATE_LABEL[state]}</span>;
}

function FlowNode({
  stage,
  selected,
  onSelect,
  top,
}: {
  stage: Stage;
  selected: boolean;
  onSelect: () => void;
  top?: number;
}) {
  const pos = { ...LAYOUT[stage.id], ...(top ? { y: top } : {}) };
  const rows = stage.steps.filter((s) => s.state !== "mock");
  const mock = stage.steps.filter((s) => s.state === "mock");
  const time = (id: string) => {
    const n = stage.nodes.find((x) => x.id === id);
    return n ? duration(n.started_at, n.ended_at) : "";
  };
  return (
    <div
      className={`lf-node s-${stage.state} lane-${stage.lane}${selected ? " selected" : ""}`}
      data-stage={stage.id}
      style={{ left: pos.x, top: pos.y, width: pos.w }}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={onSelect}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect()}
    >
      {stage.state === "running" && (
        <span className="lf-responding">
          <span className="lf-spin" /> 실행 중
        </span>
      )}
      {stage.state === "waiting" && (
        <span className="lf-responding wait">승인 대기</span>
      )}
      <div className="lf-head">
        <span className="lf-icon">
          <Glyph d={ICON[stage.id]} />
        </span>
        <strong>{stage.title}</strong>
        <Chip state={stage.state} node={span(stage.nodes)} />
      </div>
      <p className="lf-desc">{stage.caption}</p>
      <div className={`lf-fields${stage.id === "diff" ? " row" : ""}`}>
        {rows.map((s, i) => (
          <div key={s.id} className={`lf-field s-${s.state}`}>
            {i === 0 && (
              <span
                className={`lf-port in${reached(stage.state) ? " on" : ""}`}
                data-port={`${stage.id}:in`}
              />
            )}
            <span className="lf-label">
              {s.label}
              {s.id === "dast" && stage.round > 1 && <em>{stage.round}차</em>}
            </span>
            <span className="lf-input">
              <i />
              {STATE_LABEL[s.state]}
              <small>{time(s.id)}</small>
            </span>
          </div>
        ))}
      </div>
      {mock.length > 0 && (
        <div className="lf-toolset">
          <span>다음</span>
          <b>{mock.map((s) => s.label).join(" → ")}</b>
          <em>목업</em>
        </div>
      )}
      {stage.diagnosed && <p className="lf-foot">진단 있음</p>}
      <div className="lf-output">
        <span>{OUTPUT[stage.id]}</span>
        <span
          className={`lf-port out${done(stage.state) ? " on" : ""}`}
          data-port={`${stage.id}:out`}
        />
      </div>
    </div>
  );
}

function GateNode({
  gate,
  selected,
  onSelect,
}: {
  gate: Gate;
  selected: boolean;
  onSelect: () => void;
}) {
  const pos = LAYOUT[gate.id];
  const text =
    gate.state === "waiting"
      ? "대기"
      : gate.state === "pass"
        ? "승인됨"
        : gate.state === "off"
          ? "꺼짐"
          : "예정";
  return (
    <div
      className={`lf-msg s-${gate.state}${selected ? " selected" : ""}`}
      style={{ left: pos.x, top: pos.y, width: pos.w }}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={onSelect}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect()}
    >
      <span
        className={`lf-port in${reached(gate.state) ? " on" : ""}`}
        data-port={`${gate.id}:in`}
      />
      <span className="lf-avatar">
        <Glyph d={ICON.person} />
      </span>
      <span className="lf-msg-text">
        <strong>{gate.title}</strong>
        <small>{text}</small>
      </span>
      <span
        className={`lf-port out${gate.state === "pass" ? " on" : ""}`}
        data-port={`${gate.id}:out`}
      />
    </div>
  );
}

function wire(x1: number, y1: number, x2: number, y2: number) {
  const dx = Math.max(48, Math.abs(x2 - x1) / 2);
  return `M${x1} ${y1} C${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

function FlowCanvas({
  board,
  selected,
  onSelect,
  zoom,
  onFit,
}: {
  board: Board;
  selected: Selection | null;
  onSelect: (s: Selection) => void;
  zoom: number;
  onFit: (scale: number) => void;
}) {
  const { stages, gates } = stagesOf(board);
  const state: Record<string, StageState> = Object.fromEntries([
    ...stages.map((s) => [s.id, s.state]),
    ...gates.map((g) => [g.id, g.state]),
  ]);
  const view = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const [ports, setPorts] = useState<Record<string, [number, number]>>({});
  const [pan, setPan] = useState({ x: 0, y: 0 });
  // The web lane grows while DAST/fix rows fill in; the diff lane sits below its tallest node.
  const [diffY, setDiffY] = useState(LAYOUT.diff.y);
  const [height, setHeight] = useState(CANVAS.h);
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(
    null,
  );
  // Measure ports after layout so wires land exactly on the dots.
  useLayoutEffect(() => {
    const root = stage.current;
    if (!root) return;
    const bottom = (id: string) => {
      const el = root.querySelector<HTMLElement>(`[data-stage="${id}"]`);
      return el ? el.offsetTop + el.offsetHeight : 0;
    };
    const nextDiff = Math.max(
      LAYOUT.diff.y,
      Math.max(bottom("build"), bottom("verify")) + 72,
    );
    if (nextDiff !== diffY) return setDiffY(nextDiff);
    const nextHeight = Math.max(CANVAS.h, bottom("diff") + 40);
    if (nextHeight !== height) setHeight(nextHeight);
    const base = root.getBoundingClientRect();
    const found: Record<string, [number, number]> = {};
    root.querySelectorAll<HTMLElement>("[data-port]").forEach((el) => {
      const r = el.getBoundingClientRect();
      found[el.dataset.port!] = [
        (r.left + r.width / 2 - base.left) / zoom,
        (r.top + r.height / 2 - base.top) / zoom,
      ];
    });
    setPorts((old) =>
      JSON.stringify(old) === JSON.stringify(found) ? old : found,
    );
  }, [board, zoom, diffY, height]);
  // Fit the flow into the visible area when the window size changes.
  useEffect(() => {
    const el = view.current;
    if (!el) return;
    const fit = () =>
      onFit(
        Math.min(
          1.1,
          (el.clientWidth - 48) / CANVAS.w,
          (el.clientHeight - 96) / height,
        ),
      );
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [height]);
  const edgeClass = (from: string, to: string) => {
    const a = state[from];
    const b = state[to];
    if (a === "off" || b === "off") return "off";
    return done(a)
      ? b === "running" || b === "waiting"
        ? "active"
        : "done"
      : "";
  };
  return (
    <div
      className="lf-canvas"
      ref={view}
      onPointerDown={(e) => {
        if ((e.target as HTMLElement).closest(".lf-node, .lf-msg")) return;
        drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (d) setPan({ x: d.px + e.clientX - d.x, y: d.py + e.clientY - d.y });
      }}
      onPointerUp={() => (drag.current = null)}
    >
      <div
        className="lf-stage"
        ref={stage}
        style={{
          width: CANVAS.w,
          height,
          transform: `translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px)) scale(${zoom})`,
        }}
      >
        <span className="lf-lane web" style={{ left: 40, top: 14 }}>
          웹 배포 에이전트
        </span>
        <span className="lf-lane infra" style={{ left: 40, top: diffY - 58 }}>
          차분 분석 에이전트
        </span>
        <svg
          className="lf-wires"
          width={CANVAS.w}
          height={height}
          aria-hidden="true"
        >
          {WIRES.map(([a, b]) => {
            const p = ports[`${a}:out`];
            const q = ports[`${b}:in`];
            return p && q ? (
              <path
                key={`${a}-${b}`}
                d={wire(p[0], p[1], q[0], q[1])}
                className={`lf-wire ${edgeClass(a, b)}`}
              />
            ) : null;
          })}
        </svg>
        {stages.map((s) => (
          <FlowNode
            key={s.id}
            stage={s}
            selected={selected === s.id}
            onSelect={() => onSelect(s.id)}
            top={s.id === "diff" ? diffY : undefined}
          />
        ))}
        {gates.map((g) => (
          <GateNode
            key={g.id}
            gate={g}
            selected={selected === g.id}
            onSelect={() => onSelect(g.id)}
          />
        ))}
      </div>
    </div>
  );
}

// While a run is live: what is happening now, the step history and the running Jenkins console tail.
const JENKINS_NODE = new Set(["build", "stage", "release"]);
function Activity({ board }: { board: Board }) {
  const [, tick] = useState(0);
  const [tail, setTail] = useState("");
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const running = board.nodes.filter(
    (n) => n.state === "running" || n.state === "waiting",
  );
  const logNode = running.find((n) => JENKINS_NODE.has(n.id))?.id;
  useEffect(() => {
    setTail("");
    if (!logNode) return;
    let stop = false;
    const load = () =>
      api<{ text: string }>(`/pipeline/runs/${board.run_id}/log/${logNode}`)
        .then((r) => {
          if (!stop)
            setTail(
              r.text
                .split("\n")
                .filter((l) => l.trim())
                .slice(-8)
                .join("\n"),
            );
        })
        .catch(() => undefined);
    load();
    const t = setInterval(load, 4000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [board.run_id, logNode]);
  const history = board.nodes
    .filter((n) => n.started_at && n.state !== "skipped")
    .sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0));
  return (
    <aside className="flow-activity" aria-label="진행 상황">
      <header>
        <strong>진행 상황</strong>
        <span>{duration(board.started_at, null)}</span>
      </header>
      {running.map((n) => (
        <div key={n.id} className={`fa-now s-${n.state}`}>
          <span className="lf-spin" />
          <div>
            <b>
              {n.label}
              <small>{duration(n.started_at, null)}</small>
            </b>
            <p>
              {n.state === "waiting" ? "승인 대기" : n.summary || "진행 중"}
            </p>
          </div>
        </div>
      ))}
      {tail && (
        <pre className="fa-tail" aria-label="Jenkins 로그">
          {tail}
        </pre>
      )}
      <ol className="fa-history">
        {history.map((n) => (
          <li key={n.id} className={`s-${n.state}`}>
            <i />
            <span>
              <b>{n.label}</b>
              {n.summary && <small>{n.summary}</small>}
            </span>
            <time>{duration(n.started_at, n.ended_at)}</time>
          </li>
        ))}
      </ol>
    </aside>
  );
}

// Bottom drawer with the selected step's Jenkins console (build, staging deploy, release).
const LOG_NODE: Partial<Record<Selection, string>> = {
  build: "build",
  verify: "stage",
  release: "release",
};

function LogDrawer({
  board,
  selection,
  live,
}: {
  board: Board;
  selection: Selection | null;
  live: boolean;
}) {
  const node = selection ? LOG_NODE[selection] : undefined;
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const pre = useRef<HTMLPreElement>(null);
  useEffect(() => {
    setText("");
    setError("");
    if (!node) return;
    let stop = false;
    async function load() {
      try {
        const r = await api<{ text: string; job: string; build: number }>(
          `/pipeline/runs/${board.run_id}/log/${node}`,
        );
        if (!stop) setText(r.text);
      } catch (e) {
        if (!stop) setError(message(e));
      }
    }
    load();
    if (!live) return () => void (stop = true);
    const t = setInterval(load, 4000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [board.run_id, node, live]);
  useEffect(() => {
    pre.current?.scrollTo({ top: pre.current.scrollHeight });
  }, [text]);
  return (
    <section className="lf-logs" aria-label="실행 로그">
      <header>
        <strong>로그</strong>
        <span>
          {node
            ? `${board.nodes.find((n) => n.id === node)?.label ?? node} · Jenkins`
            : "단계 선택"}
        </span>
      </header>
      <pre ref={pre}>
        {!node
          ? "Jenkins 로그 · 빌드 · 스테이징 · 운영 배포"
          : error || text || "불러오는 중…"}
      </pre>
    </section>
  );
}

function Approval({
  board,
  node,
  onAnswered,
}: {
  board: Board;
  node: BoardNode;
  onAnswered: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const decision = node.decision!;
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
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="pl-approval" role="region" aria-label="승인 요청">
      <strong>{decision.prompt}</strong>
      {node.id === "dast" && <FixProposal board={board} />}
      <div>
        {decision.options.map((o) => (
          <button
            key={o.value}
            className={
              o.value ===
              (decision.options.some((x) => x.value === "fix")
                ? "fix"
                : "approve")
                ? "primary"
                : ""
            }
            disabled={busy}
            onClick={() => answer(o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
      {error && <p className="pl-error">{error}</p>}
    </div>
  );
}

function NodeRow({ node }: { node: BoardNode }) {
  const pr =
    node.id === "fix"
      ? (node.detail.pr as { url?: string; number?: number } | undefined)
      : undefined;
  return (
    <li className={`pl-node s-${node.state}`}>
      <span className="pl-node-head">
        <i />
        <strong>{node.label}</strong>
        {node.round > 1 && (
          <span className="pl-badge loop">{node.round}차</span>
        )}
        <StatePill state={node.state} />
        <small>{duration(node.started_at, node.ended_at)}</small>
      </span>
      {clean(node.summary) && <p>{clean(node.summary)}</p>}
      {node.history.length > 0 && (
        <ol className="pl-history">
          {node.history.map((h, i) => (
            <li key={i}>
              {String(h.round ?? i + 1)}차 ·{" "}
              {STATE_LABEL[h.state as StageState] ?? String(h.state)} ·{" "}
              {clean(h.summary)}
            </li>
          ))}
        </ol>
      )}
      {pr?.url && (
        <a href={pr.url} target="_blank" rel="noreferrer">
          수정 PR #{pr.number} 열기
        </a>
      )}
    </li>
  );
}

type MapRow = {
  source: string;
  target: string;
  grade: string;
  resource: string;
  differences: string[];
};
const GRADE: Record<string, string> = {
  match: "일치",
  approximate: "근사",
  gap: "공백",
  unknown: "확인 필요",
};

type TraceStep = { role: string; file: string; line?: number };
type DastResult = { finding_id: string; status: string };
const VERDICT: Record<string, { label: string; tone: StageState }> = {
  REPRODUCED: { label: "재현됨", tone: "fail" },
  NOT_REPRODUCED: { label: "재현 안 됨", tone: "pass" },
};
const ROLE: Record<string, string> = {
  source: "입력",
  propagation: "전달",
  sink: "출력",
};

// Finding → reproduction → AI fix → recheck, from the DAST rounds and the fix step of the same run.
function VulnStory({ board }: { board: Board }) {
  const byId = new Map(board.nodes.map((n) => [n.id, n]));
  const dast = byId.get("dast");
  const fix = byId.get("fix");
  if (!dast || !fix || fix.state === "skipped" || fix.state === "pending")
    return null;
  const d = fix.detail as {
    finding_id?: string;
    trace?: TraceStep[];
    changes?: { file: string; reason: string }[];
    pr?: { url?: string; number?: number };
    patch?: string;
  };
  const rounds = [
    ...dast.history,
    { round: dast.round, detail: dast.detail },
  ].map((h) => {
    const results = (
      (h.detail as { results?: DastResult[] })?.results ?? []
    ).filter((r) => !d.finding_id || r.finding_id === d.finding_id);
    return { round: Number(h.round ?? 1), status: results[0]?.status };
  });
  const first = rounds.find((r) => r.status);
  const last = [...rounds].reverse().find((r) => r.status && r !== first);
  const verdict = (status?: string) => {
    const v = status ? VERDICT[status] : undefined;
    return v ? (
      <span className={`pl-pill s-${v.tone}`}>{v.label}</span>
    ) : (
      <span className="pl-pill s-pending">대기</span>
    );
  };
  return (
    <section className="pl-vuln" aria-label="취약점과 수정">
      <h4>취약점 · 수정</h4>
      <ol>
        <li>
          <b>발견</b>
          <span className="pl-vuln-tag">SAST</span>
          <div className="pl-trace">
            {(d.trace ?? []).map((t, i) => (
              <span key={i} className={`role-${t.role}`}>
                {ROLE[t.role] ?? t.role} ·{" "}
                {t.file.split("/").slice(-2).join("/")}
                {t.line ? `:${t.line}` : ""}
              </span>
            ))}
          </div>
        </li>
        <li>
          <b>재현</b>
          <span className="pl-vuln-tag">DAST {first?.round ?? 1}차</span>
          {verdict(first?.status)}
        </li>
        <li>
          <b>수정</b>
          <span className="pl-vuln-tag">AI</span>
          <div className="pl-trace">
            {(d.changes ?? []).map((c, i) => (
              <span key={i}>{c.file.split("/").slice(-2).join("/")}</span>
            ))}
            {d.pr?.url && (
              <a href={d.pr.url} target="_blank" rel="noreferrer">
                PR #{d.pr.number}
              </a>
            )}
          </div>
        </li>
        <li>
          <b>재검증</b>
          <span className="pl-vuln-tag">DAST {last?.round ?? 2}차</span>
          {verdict(last?.status)}
        </li>
      </ol>
      {d.patch && <DiffView patch={d.patch} title="AI 수정 내용" />}
    </section>
  );
}

function MappingTable({ node }: { node: BoardNode }) {
  const rows = (node.detail.rows as MapRow[] | undefined) ?? [];
  if (!rows.length) return null;
  const count = (g: string) => rows.filter((r) => r.grade === g).length;
  return (
    <div className="pl-mapping">
      <div className="pl-mapping-sum">
        <span>매핑 {rows.length}행</span>
        {["match", "approximate", "gap", "unknown"].map((g) => (
          <span key={g} className={`grade g-${g}`}>
            {GRADE[g]} {count(g)}
          </span>
        ))}
      </div>
      <table>
        <thead>
          <tr>
            <th>AWS 구성</th>
            <th>GCP 대응</th>
            <th>판정</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>{r.source}</td>
              <td>{r.target || "—"}</td>
              <td>
                <span className={`grade g-${r.grade}`}>
                  {GRADE[r.grade] ?? r.grade}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Detail({
  board,
  selection,
  onAnswered,
}: {
  board: Board;
  selection: Selection;
  onAnswered: () => void;
}) {
  const { stages, gates } = stagesOf(board);
  const byId = new Map(board.nodes.map((n) => [n.id, n]));
  const gate = gates.find((g) => g.id === selection);
  if (gate) {
    const node = byId.get(gate.id === "release-gate" ? "release" : "cutover");
    return (
      <section className="pl-detail">
        <header>
          <h3>{gate.title}</h3>
          <StatePill state={gate.state} />
        </header>
        {node?.decision && node.state === "waiting" ? (
          <Approval board={board} node={node} onAnswered={onAnswered} />
        ) : (
          <p className="pl-muted">
            {gate.state === "off"
              ? "꺼짐"
              : gate.state === "pass"
                ? "승인됨"
                : "앞 단계 완료 후 요청"}
          </p>
        )}
      </section>
    );
  }
  const stage = stages.find((s) => s.id === selection)!;
  const diagnosis = stage.diagnosed
    ? byId.get("exception-analysis")
    : undefined;
  const report = byId.get("exception-report");
  const waiting = stage.nodes.find((n) => n.state === "waiting" && n.decision);
  const map = byId.get("map");
  return (
    <section className="pl-detail">
      <header>
        <h3>
          {stage.index}. {stage.title}
        </h3>
        <StatePill state={stage.state} />
        <small>{stage.caption}</small>
      </header>
      {waiting && (
        <Approval board={board} node={waiting} onAnswered={onAnswered} />
      )}
      {stage.id === "verify" && <DastLive board={board} />}
      {diagnosis && (
        <div className="pl-diagnosis">
          <strong>원인 진단 · delta-ai</strong>
          <p>{diagnosis.summary.split(/(?<=다\.)\s/)[0]}</p>
          {diagnosis.summary.split(/(?<=다\.)\s/).length > 1 && (
            <details>
              <summary>자세히</summary>
              <ul>
                {diagnosis.summary
                  .split(/(?<=다\.)\s/)
                  .slice(1)
                  .map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
              </ul>
            </details>
          )}
        </div>
      )}
      <ul className="pl-nodes">
        {stage.nodes
          .filter((n) => !(n.id === "fix" && n.state === "skipped"))
          .map((n) => (
            <NodeRow key={n.id} node={n} />
          ))}
        {stage.steps
          .filter((s) => s.state === "off")
          .map((s) => (
            <li key={s.id} className={`pl-node s-${s.state}`}>
              <span className="pl-node-head">
                <i />
                <strong>{s.label}</strong>
                <StatePill state={s.state} />
              </span>
              <p>{s.summary}</p>
            </li>
          ))}
      </ul>
      {stage.id === "verify" && <VulnStory board={board} />}
      {stage.id === "diff" && map && <MappingTable node={map} />}
      {stage.steps.some((s) => s.state === "mock") && (
        <div className="pl-mockstrip">
          <span className="pl-pill s-mock">목업</span>
          <span>다음 단계</span>
          <ol>
            {stage.steps
              .filter((s) => s.state === "mock")
              .map((s) => (
                <li key={s.id}>{s.label}</li>
              ))}
          </ol>
        </div>
      )}
    </section>
  );
}

type StartOptions = {
  repo: string;
  branch: string;
  branches: string[];
  commits: { sha: string; message: string; author: string; date: string }[];
  release_targets: { id: string; cloud?: string; label: string }[];
  cutover_configured: boolean;
};

// One click to run the full chain on the latest commit; the defaults are the usual demo run.
function StartRun({
  onCancel,
  onStarted,
}: {
  onCancel: () => void;
  onStarted: (runId: string) => void;
}) {
  const [opts, setOpts] = useState<StartOptions | null>(null);
  const [branch, setBranch] = useState("");
  const [commit, setCommit] = useState("");
  const [release, setRelease] = useState("");
  const [cutover, setCutover] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const key = useRef(crypto.randomUUID());
  async function load(b?: string) {
    setError("");
    try {
      const o = await api<StartOptions>(
        "/pipeline/options" + (b ? `?branch=${encodeURIComponent(b)}` : ""),
      );
      setOpts(o);
      setBranch(o.branch);
      setCommit(o.commits[0]?.sha ?? "");
      if (!b) {
        setRelease(o.release_targets[0]?.id ?? "");
        setCutover(o.cutover_configured && !!o.release_targets.length);
      }
    } catch (e) {
      setError(message(e));
    }
  }
  useEffect(() => {
    load();
  }, []);
  async function start() {
    setBusy(true);
    setError("");
    try {
      const r = await api<{ run_id: string }>(
        "/pipeline/runs",
        {
          branch,
          commit,
          cutover: cutover && !!release,
          release_target: release || null,
        },
        key.current,
      );
      onStarted(r.run_id);
    } catch (e) {
      setError(message(e));
      key.current = crypto.randomUUID();
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="pl-start" aria-label="새 실행">
      {!opts ? (
        <p className="pl-muted">{error || "불러오는 중…"}</p>
      ) : (
        <>
          <div className="pl-start-row">
            <span className="pl-start-repo">
              {opts.repo.replace("https://github.com/", "")}
            </span>
            <label>
              브랜치
              <select
                value={branch}
                onChange={(e) => {
                  setBranch(e.target.value);
                  load(e.target.value);
                }}
              >
                {opts.branches.map((b) => (
                  <option key={b}>{b}</option>
                ))}
              </select>
            </label>
            <label className="grow">
              커밋
              <select
                value={commit}
                onChange={(e) => setCommit(e.target.value)}
              >
                {opts.commits.map((c) => (
                  <option key={c.sha} value={c.sha}>
                    {c.sha.slice(0, 7)} · {c.message}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="pl-start-row">
            <label>
              운영 배포
              <select
                value={release}
                onChange={(e) => setRelease(e.target.value)}
              >
                <option value="">스테이징까지만</option>
                {opts.release_targets.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="pl-check">
              <input
                type="checkbox"
                checked={cutover && !!release}
                disabled={!opts.cutover_configured || !release}
                onChange={(e) => setCutover(e.target.checked)}
              />
              도메인 전환
            </label>
            <span className="grow" />
            <button onClick={onCancel} disabled={busy}>
              취소
            </button>
            <button
              className="primary"
              onClick={start}
              disabled={busy || !commit}
            >
              {busy ? "시작 중…" : "실행"}
            </button>
          </div>
          {error && <p className="pl-error">{error}</p>}
        </>
      )}
    </section>
  );
}

// `#/pipeline/<run_id>` opens that run; other screens link here right after starting one.
export function PipelinePanel({ runId: linked }: { runId?: string | null }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runId, setRunId] = useState<string | null>(linked ?? null);
  const [board, setBoard] = useState<Board | null>(null);
  const [picked, setPicked] = useState<Selection | null>(null);
  const [panel, setPanel] = useState(false);
  const [logs, setLogs] = useState(false);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [closedFix, setClosedFix] = useState("");
  const follow = useRef(true);

  async function loadRuns() {
    try {
      const data = await api<{ runs: RunSummary[] }>("/pipeline/runs");
      setRuns(data.runs);
      setRunId((id) => id ?? data.runs[0]?.run_id ?? null);
      setError("");
    } catch (e) {
      setError(message(e));
    }
  }
  async function loadBoard(id: string) {
    try {
      setBoard(await api<Board>(`/pipeline/runs/${id}`));
      setError("");
    } catch (e) {
      setError(message(e));
    }
  }
  useEffect(() => {
    loadRuns();
    const t = setInterval(loadRuns, 15000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (linked) setRunId(linked);
  }, [linked]);
  useEffect(() => {
    if (!runId) return;
    setBoard(null);
    setPicked(null);
    setPanel(false);
    follow.current = true;
    loadBoard(runId);
  }, [runId]);
  const live = !!board && !board.outcome;
  useEffect(() => {
    if (!runId || !live) return;
    const t = setInterval(() => loadBoard(runId), 3000);
    return () => clearInterval(t);
  }, [runId, live]);

  const stages = board ? stagesOf(board).stages : [];
  const gates = board ? stagesOf(board).gates : [];
  // Follow the live step; for a finished run point at what needs attention, else the last stage reached.
  const reachedStages = stages.filter(
    (s) => s.state !== "off" && s.state !== "pending",
  );
  const waitingGate = gates.find((g) => g.state === "waiting")?.id;
  const auto: Selection | null =
    waitingGate ??
    activeStage(stages) ??
    stages.find((s) => s.state === "warn")?.id ??
    reachedStages[reachedStages.length - 1]?.id ??
    null;
  const selection = follow.current ? auto : picked;
  const node = board ? currentNode(board) : null;
  const result = board?.nodes.find((n) => n.id === "result");
  const outcome = board?.outcome ? OUTCOME[board.outcome] : null;
  // An approval request opens the side panel by itself; everything else waits for a click.
  // Approvals and the live DAST screen open the side panel by themselves; the rest waits for a click.
  // Approvals open the side panel by themselves; the live DAST screen and the AI fix approval get
  // their own large overlays because the panel is too narrow to read them.
  const needsEyes = !!board?.nodes.some(
    (n) => n.state === "waiting" && n.decision,
  );
  const showPanel = !!selection && (panel || (follow.current && needsEyes));
  const dastRunning =
    board?.nodes.find((n) => n.id === "dast")?.state === "running";
  const fixAsk = board?.nodes.find(
    (n) =>
      n.id === "dast" &&
      n.state === "waiting" &&
      n.decision?.options.some((o) => o.value === "fix"),
  );
  const fixKey = fixAsk
    ? `${board!.run_id}:${fixAsk.decision?.id ?? "dast"}`
    : "";

  return (
    <div className={`flow-page${live && !showPanel ? " with-activity" : ""}`}>
      {board ? (
        <FlowCanvas
          board={board}
          selected={selection}
          zoom={zoom}
          onFit={setZoom}
          onSelect={(s) => {
            follow.current = false;
            setPicked(s);
            setPanel(true);
          }}
        />
      ) : (
        <div className="lf-canvas lf-empty">
          <p>
            {error ||
              (runs.length ? "불러오는 중…" : "실행 기록 없음 · 새 실행")}
          </p>
        </div>
      )}

      <div className="flow-float top-left">
        <span className="flow-crumb">파이프라인</span>
        <span className="flow-sep">/</span>
        <select
          aria-label="실행 기록"
          value={runId ?? ""}
          onChange={(e) => setRunId(e.target.value)}
        >
          {runs.map((r) => (
            <option key={r.run_id} value={r.run_id}>
              {runLabel(r)}
            </option>
          ))}
        </select>
        {board && (
          <span className="flow-status">
            <span
              className={`pl-dot s-${live ? (node?.state === "waiting" ? "waiting" : "running") : (outcome?.tone ?? "pending")}`}
            />
            {live
              ? node
                ? `${node.label} ${STATE_LABEL[node.state]}`
                : "진행 중"
              : (outcome?.label ?? board.outcome)}
            <small>
              {duration(
                board.started_at,
                live
                  ? null
                  : (result?.ended_at ??
                      Math.max(...board.nodes.map((n) => n.ended_at ?? 0))),
              )}
            </small>
          </span>
        )}
      </div>

      <div className="flow-float top-right">
        {!follow.current && live && (
          <button
            onClick={() => {
              follow.current = true;
              setPicked(null);
              setPanel(false);
            }}
          >
            진행 단계 따라가기
          </button>
        )}
        <button className="flow-dark" onClick={() => setStarting((v) => !v)}>
          새 실행
        </button>
      </div>
      {starting && (
        <div className="flow-popover">
          <StartRun
            onCancel={() => setStarting(false)}
            onStarted={(id) => {
              setStarting(false);
              setRuns((old) => [
                {
                  run_id: id,
                  options: {},
                  outcome: null,
                  started_at: Date.now() / 1000,
                },
                ...old,
              ]);
              setRunId(id);
              loadRuns();
            }}
          />
        </div>
      )}

      {board && logs && (
        <div className={`flow-drawer${showPanel ? " with-panel" : ""}`}>
          <LogDrawer board={board} selection={selection} live={live} />
        </div>
      )}
      <div className="flow-float bottom-left">
        <button aria-pressed={logs} onClick={() => setLogs((v) => !v)}>
          <span className="flow-term">&gt;_</span> 로그
        </button>
      </div>
      <div
        className={`flow-float bottom-right${showPanel ? " with-panel" : ""}`}
      >
        <button
          aria-label="축소"
          onClick={() => setZoom((z) => Math.max(0.5, +(z - 0.1).toFixed(2)))}
        >
          −
        </button>
        <span>{Math.round(zoom * 100)}%</span>
        <button
          aria-label="확대"
          onClick={() => setZoom((z) => Math.min(1.6, +(z + 0.1).toFixed(2)))}
        >
          +
        </button>
      </div>

      {board && dastRunning && !(showPanel && selection === "verify") && (
        <DastPip
          board={board}
          onOpen={() => {
            follow.current = false;
            setPicked("verify");
            setPanel(true);
          }}
        />
      )}
      {board && fixAsk?.decision && fixKey !== closedFix && (
        <FixModal
          board={board}
          decision={fixAsk.decision}
          onAnswered={() => loadBoard(board.run_id)}
          onClose={() => setClosedFix(fixKey)}
        />
      )}
      {board && live && !showPanel && <Activity board={board} />}
      {board && showPanel && selection && (
        <aside className="flow-panel" aria-label="단계 상세">
          <button
            className="flow-close"
            aria-label="상세 닫기"
            onClick={() => setPanel(false)}
          >
            ×
          </button>
          <Detail
            board={board}
            selection={selection}
            onAnswered={() => loadBoard(board.run_id)}
          />
        </aside>
      )}
    </div>
  );
}
