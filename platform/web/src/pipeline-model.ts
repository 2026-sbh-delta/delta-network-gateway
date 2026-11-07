// Orchestrator board → five stages and two approval gates. The board has ~20 nodes; the screen
// shows only what a viewer needs to follow the run. Node ids come from delta_pipeline.model.

export type NodeState =
  "pending" | "running" | "pass" | "waiting" | "fail" | "skipped" | "warn";
export type Decision = {
  id?: string;
  prompt: string;
  options: { value: string; label: string }[];
  case_id?: string;
};
export type BoardNode = {
  id: string;
  label: string;
  owner: string;
  state: NodeState;
  summary: string;
  started_at: number | null;
  ended_at: number | null;
  detail: Record<string, unknown>;
  decision: Decision | null;
  round: number;
  history: Record<string, unknown>[];
};
export type Board = {
  run_id: string;
  options: Record<string, unknown>;
  outcome: string | null;
  started_at: number;
  nodes: BoardNode[];
};
export type RunSummary = {
  run_id: string;
  options: Record<string, unknown>;
  outcome: string | null;
  started_at: number;
};

// "off" = not part of this run (option turned off); "mock" = not built yet.
export type StageState = NodeState | "off" | "mock";
export type StageId = "build" | "verify" | "diff" | "release" | "cutover";
export type GateId = "release-gate" | "cutover-gate";
export type Step = {
  id: string;
  label: string;
  state: StageState;
  summary: string;
};
export type Stage = {
  id: StageId;
  index: number;
  lane: "web" | "infra" | "joined";
  title: string;
  caption: string;
  state: StageState;
  steps: Step[];
  round: number;
  diagnosed: boolean;
  nodes: BoardNode[];
};
export type Gate = {
  id: GateId;
  title: string;
  state: StageState;
  decision: Decision | null;
};

const STAGES: {
  id: StageId;
  lane: Stage["lane"];
  title: string;
  caption: string;
  nodes: string[];
}[] = [
  {
    id: "build",
    lane: "web",
    title: "빌드·SAST",
    caption: "Jenkins · SAST",
    nodes: ["build", "sast"],
  },
  {
    id: "verify",
    lane: "web",
    title: "스테이징·DAST",
    caption: "스테이징 · DAST · AI 수정",
    nodes: ["stage", "dast", "fix"],
  },
  {
    id: "diff",
    lane: "infra",
    title: "인프라 차분",
    caption: "AWS → GCP · 매핑",
    nodes: ["map"],
  },
  {
    id: "release",
    lane: "joined",
    title: "운영 배포",
    caption: "GCP VM · DB",
    nodes: ["release"],
  },
  {
    id: "cutover",
    lane: "joined",
    title: "도메인 전환",
    caption: "Cloudflare · CNAME",
    nodes: ["cutover"],
  },
];

// Infra branch steps that do not exist yet are shown as mock (official requirement).
const DIFF_MOCK_STEPS = [
  { id: "gen_plan", label: "GCP Terraform plan" },
  { id: "infra_approve", label: "인프라 승인" },
  { id: "apply", label: "GCP 적용" },
  { id: "db_dump", label: "DB 덤프 이관" },
];

const DIAGNOSIS = [
  "exception-analysis",
  "exception-report",
  "exception-review",
];

export function combine(states: StageState[]): StageState {
  const real = states.filter((s) => s !== "off" && s !== "mock");
  if (real.length === 0) return states[0] ?? "off";
  for (const s of ["fail", "waiting", "running"] as const)
    if (real.includes(s)) return s;
  if (real.includes("warn")) return "warn";
  if (real.every((s) => s === "pending")) return "pending";
  if (real.every((s) => s === "pass" || s === "skipped" || s === "warn"))
    return real.every((s) => s === "skipped") ? "skipped" : "pass";
  return "running"; // some done, some still pending
}

export function stagesOf(board: Board): { stages: Stage[]; gates: Gate[] } {
  const byId = new Map(board.nodes.map((n) => [n.id, n]));
  const failed = board.nodes.some((n) => n.state === "fail");
  const diagnosed =
    failed || board.outcome === "unresolved"
      ? DIAGNOSIS.some((id) => byId.get(id)?.state === "pass")
      : false;
  const stages = STAGES.map((def, index): Stage => {
    const nodes = def.nodes
      .map((id) => byId.get(id))
      .filter((n): n is BoardNode => !!n);
    const steps: Step[] = nodes
      // An unused fix step is noise: hide it unless the loop actually ran.
      .filter((n) => !(n.id === "fix" && n.state === "skipped"))
      .map((n) => ({
        id: n.id,
        label: n.label,
        state: n.state,
        summary: n.summary,
      }));
    let state: StageState = nodes.length
      ? combine(nodes.map((n) => n.state))
      : "off";
    if (def.id === "diff") {
      if (!nodes.length) {
        steps.push({
          id: "map",
          label: "차분 매핑",
          state: "off",
          summary: "요청 없음",
        });
        state = "off";
      }
      for (const m of DIFF_MOCK_STEPS)
        steps.push({ ...m, state: "mock", summary: "목업" });
    }
    const round = Math.max(1, ...nodes.map((n) => n.round));
    return {
      ...def,
      index: index + 1,
      state,
      steps,
      round,
      nodes,
      diagnosed: diagnosed && (state === "fail" || state === "warn"),
    };
  });
  const gate = (id: GateId, title: string, nodeId: string): Gate => {
    const node = byId.get(nodeId);
    if (!node) return { id, title, state: "off", decision: null };
    const state: StageState =
      node.state === "waiting"
        ? "waiting"
        : node.state === "pending"
          ? "pending"
          : node.state === "skipped"
            ? "skipped"
            : "pass";
    return {
      id,
      title,
      state,
      decision: node.state === "waiting" ? node.decision : null,
    };
  };
  return {
    stages,
    gates: [
      gate("release-gate", "운영 배포 승인", "release"),
      gate("cutover-gate", "도메인 전환 승인", "cutover"),
    ],
  };
}

export function waitingDecision(
  board: Board,
): { node: BoardNode; decision: Decision } | null {
  const node = board.nodes.find((n) => n.state === "waiting" && n.decision);
  return node && node.decision ? { node, decision: node.decision } : null;
}

export function currentNode(board: Board): BoardNode | null {
  const node =
    board.nodes.find((n) => n.state === "waiting") ??
    board.nodes.find((n) => n.state === "running") ??
    null;
  // The orchestrator's closing node is internal; show it as wrap-up.
  return node && node.id === "result" ? { ...node, label: "마무리" } : node;
}

export function activeStage(stages: Stage[]): StageId | null {
  const order: StageState[] = ["waiting", "running", "fail"];
  for (const s of order) {
    const hit = stages.find((st) => st.state === s);
    if (hit) return hit.id;
  }
  return null;
}

export const OUTCOME: Record<string, { label: string; tone: StageState }> = {
  verified: { label: "검증 완료", tone: "pass" },
  unresolved: { label: "확인 필요", tone: "warn" },
  stopped: { label: "중단", tone: "fail" },
};

export const STATE_LABEL: Record<StageState, string> = {
  pending: "대기",
  running: "진행 중",
  pass: "완료",
  waiting: "승인 대기",
  fail: "실패",
  skipped: "건너뜀",
  warn: "확인 필요",
  off: "꺼짐",
  mock: "목업",
};

export function duration(from: number | null, to: number | null): string {
  if (!from) return "";
  const s = Math.max(0, Math.round((to ?? Date.now() / 1000) - from));
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  return m < 60
    ? `${m}분 ${s % 60}초`
    : `${Math.floor(m / 60)}시간 ${m % 60}분`;
}
