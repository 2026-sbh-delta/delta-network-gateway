// 파이프라인 그래프 화면. 실행 기록(/api/pipeline/runs)을 그대로 그리고, 승인 대기면 오른쪽 패널에서 답한다.
// 아직 없는 단계(수정 루프·인프라 갈래·운영 반영)는 점선 "추후" 노드로 함께 보여 전체 흐름을 한눈에 본다.
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (location.protocol === "https:") $("#logout").hidden = false;

  /* ── 그래프 정의: 실제 노드 id 는 실행 기록(Board)의 노드 id 와 같다 ── */
  const GROUPS = [
    { label: "웹 갈래", code: "build ∥ sast → stage → dast", x: -40, y: -200, w: 1520, h: 560 },
    { label: "인프라 갈래 · 추후", code: "차분 에이전트", x: -40, y: 410, w: 1820, h: 200 },
    { label: "운영 반영", code: "운영 배포 → 도메인 전환", x: 1530, y: 40, w: 1110, h: 300 },
  ];
  const T = (id, label, x, y, o = {}) => ({ id, label, x, y, w: 210, h: 98, kind: "task", ...o });
  const NODES = [
    { id: "start", label: "시작", x: 0, y: 140, w: 110, h: 50, kind: "pill", icon: "play" },
    T("prepare", "입력 확인", 150, 116, { icon: "play", tool: "오케스트레이터", desc: "허용된 저장소인지, 커밋이 40자리 SHA 인지 확인합니다. 빌드는 커밋으로 고정하고, 고른 브랜치는 수정 PR 의 대상으로만 씁니다." }),
    T("build", "빌드", 420, 30, { icon: "box", tool: "Jenkins delta-build", desc: "빌드 에이전트(별도 인스턴스)에서 고정한 커밋을 빌드해 스테이징 이미지 저장소에 푸시합니다. 결과는 digest 로 고정합니다." }),
    T("sast", "SAST", 420, 210, { icon: "search", ai: true, tool: "delta-sast", desc: "소스→싱크 경로를 찾아 SARIF 를 만듭니다. 실제로 점검한 커밋·파일 수·언어를 함께 확인해, 정상적인 0건과 잘못된 범위를 구분합니다." }),
    T("stage", "스테이징 배포", 690, 116, { icon: "cloud", tool: "Jenkins delta-deploy → 스테이징 도구", desc: "같은 digest 를 GCP 스테이징에 올립니다. 배포 직전에 같은 이미지로 DB 를 초기화(남은 행 0)하고, 이 실행이 스테이징을 점유합니다." }),
    T("dast", "DAST 점검", 960, 116, { icon: "bot", ai: true, tool: "delta-dast /api/platform/runs", desc: "SAST 가 넘긴 후보를 실제 브라우저로 스테이징에 재현해 봅니다. 모두 재현되지 않을 때만 자동 통과, 나머지는 사람이 봅니다." }),
    T("result", "웹 검증 완료", 1230, 116, { icon: "check", tool: "오케스트레이터", desc: "검증한 digest 를 확정하고 스테이징 점유를 풉니다." }),
    // 조치 루프(1-B): DAST 재현 → 사람이 '수정 적용' → 수정안 PR → 그 커밋으로 빌드부터 2차(재검증)
    T("fix", "AI 수정 · PR", 690, -150, { icon: "spark", ai: true, tool: "delta-dast 수정 에이전트", desc: "DAST 가 재현하자마자 SAST 코드 경로와 재현 증거로 수정안을 만들어 둡니다. 사람이 DAST 승인 단계에서 '수정 적용'을 고르면 수정 브랜치·PR 을 올리고, 그 커밋으로 빌드부터 다시 돌아 같은 항목을 재검증합니다(1회)." }),
    // 운영 반영
    T("release", "운영 배포 · 검증", 1580, 116, { icon: "cloud", tool: "Jenkins delta-release · 환경별 VM", desc: "사람이 승인하면(서명 토큰) 검증한 같은 FE·BE digest 로 운영 VM 을 새로 만들고 DB 를 준비해 Compose 로 띄운 뒤, 실제 실행 이미지·헬스·PostgreSQL 18 을 확인합니다." }),
    T("cutover", "서비스 도메인 전환", 1850, 116, { icon: "shift", mock: true, tool: "Cloudflare DNS 전용 CNAME", desc: "사람 승인 뒤 CNAME 을 AWS → GCP 로 바꾸고, 공개 DNS·실제 응답으로 확인한 뒤 TTL 만큼 AWS 를 유지합니다. 실패하면 되돌립니다." }),
    { id: "end", label: "이전 완료", x: 2130, y: 140, w: 120, h: 50, kind: "pill", icon: "flag", mock: true },
    // 추후: 인프라 갈래
    T("read_aws", "AWS 구성 읽기", 150, 470, { icon: "doc", mock: true, tool: "S3 Terraform 코드·state", desc: "A 기업 AWS Terraform 코드와 state 를 조회 역할로 읽습니다. (3단계)" }),
    T("map", "차분 매핑", 420, 470, { icon: "spark", ai: true, mock: true, tool: "차분 에이전트", desc: "AWS 자원·보안 설정(GuardDuty·WAF·ANF)을 GCP 대응과 등급(일치·근사·공백)으로 매핑합니다. (3단계)" }),
    T("gen_plan", "GCP 구성 · plan", 690, 470, { icon: "doc", mock: true, tool: "Terraform plan", desc: "매핑으로 GCP 구성을 만들고 plan 을 저장합니다. (3단계)" }),
    T("infra_approve", "인프라 승인", 960, 470, { icon: "pause", mock: true, tool: "interrupt()", desc: "매핑 표와 plan, 공백 항목을 승인합니다. (3단계)" }),
    T("apply", "GCP 적용", 1230, 470, { icon: "layers", mock: true, tool: "Terraform apply", desc: "plan 을 적용하고 다시 plan 해 변경이 없는지 확인합니다. (3단계)" }),
    T("db_dump", "DB 덤프 이관", 1500, 470, { icon: "db", mock: true, tool: "pg_dump → GCP", desc: "AWS DB 를 덤프해 GCP DB 로 옮기고 행 수·스키마가 같은지 확인합니다. (3단계)" }),
  ];
  const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));
  const EDGES = [
    ["start", "prepare"], ["prepare", "build"], ["prepare", "sast"], ["build", "stage"], ["sast", "stage", { label: "합류" }],
    ["stage", "dast"], ["dast", "result", { label: "재현 없음·승인" }],
    ["dast", "fix", { from: "top", to: "right", label: "재현 · 수정 적용" }], ["fix", "build", { from: "left", to: "top", label: "2차" }],
    ["result", "release"], ["release", "cutover"], ["cutover", "end"],
    ["start", "read_aws", { from: "bottom", to: "left", mock: true }], ["read_aws", "map", { mock: true }], ["map", "gen_plan", { mock: true }],
    ["gen_plan", "infra_approve", { mock: true }], ["infra_approve", "apply", { mock: true }], ["apply", "db_dump", { mock: true }],
    ["db_dump", "release", { from: "top", to: "bottom", mock: true }],
  ];
  const ICONS = {
    play: "M5.5 3.2 L12.8 8 L5.5 12.8 Z", box: "M2.5 5 L8 2 L13.5 5 V11 L8 14 L2.5 11 Z M2.5 5 L8 8 L13.5 5 M8 8 V14",
    search: "M2.5 7 a4.5 4.5 0 1 0 9 0 a4.5 4.5 0 1 0 -9 0 M10.4 10.4 L14 14",
    cloud: "M4.6 12.6 H11.8 a2.9 2.9 0 0 0 0.2 -5.8 a4 4 0 0 0 -7.6 -0.6 A3.2 3.2 0 0 0 4.6 12.6 Z",
    bot: "M3 5.5 H13 V12.8 H3 Z M8 2.5 V5.5 M6 9.2 V9.3 M10 9.2 V9.3 M1.5 8.5 V10 M14.5 8.5 V10",
    spark: "M8 1.8 L9.4 6.6 L14.2 8 L9.4 9.4 L8 14.2 L6.6 9.4 L1.8 8 L6.6 6.6 Z", pause: "M5.8 3.5 V12.5 M10.2 3.5 V12.5",
    branch: "M5 2.5 V13.5 M11 2.5 V5 C11 8.5 5 8 5 11.5", check: "M3 8.6 L6.5 12 L13 4.5",
    doc: "M4 2 H10 L13 5 V14 H4 Z M10 2 V5 H13 M6.2 8.5 H10.8 M6.2 11 H10.8", layers: "M8 2 L14 5 L8 8 L2 5 Z M2 8 L8 11 L14 8 M2 11 L8 14 L14 11",
    db: "M3 4 a5 2 0 1 0 10 0 a5 2 0 1 0 -10 0 M3 4 V12 a5 2 0 0 0 10 0 V4 M3 8 a5 2 0 0 0 10 0",
    shift: "M2 5 H12.5 M10 2.5 L12.5 5 L10 7.5 M14 11 H3.5 M6 8.5 L3.5 11 L6 13.5", flag: "M4 14 V2.5 H12 L10 5.5 L12 8.5 H4",
  };

  /* ── SVG 그리기 ── */
  const mk = (tag, attrs = {}, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  };
  for (const g of GROUPS) {
    const el = mk("g", { class: "grp" }, $("#groups"));
    mk("rect", { x: g.x, y: g.y, width: g.w, height: g.h, rx: 24 }, el);
    mk("text", { x: g.x + 20, y: g.y + 28 }, el).textContent = g.label;
    mk("text", { x: g.x + g.w - 20, y: g.y + 28, "text-anchor": "end", class: "code" }, el).textContent = g.code;
  }
  const port = (n, side) => side === "left" ? [n.x, n.y + n.h / 2] : side === "top" ? [n.x + n.w / 2, n.y]
    : side === "bottom" ? [n.x + n.w / 2, n.y + n.h] : [n.x + n.w, n.y + n.h / 2];
  function pathD(a, b, o = {}) {
    const fs = o.from || "right", ts = o.to || "left";
    const [x0, y0] = port(a, fs), [x3, y3] = port(b, ts);
    const vert = (s) => s === "top" || s === "bottom";
    const dy = Math.max(40, Math.abs(y3 - y0) / 2), dx = Math.max(48, Math.abs(x3 - x0) / 2);
    const c1 = vert(fs) ? [x0, y0 + (fs === "top" ? -dy : dy)] : [x0 + (fs === "left" ? -dx : dx), y0];
    const c2 = vert(ts) ? [x3, y3 + (ts === "top" ? -dy : dy)] : [x3 + (ts === "right" ? dx : -dx), y3];
    return `M${x0},${y0} C${c1} ${c2} ${x3},${y3}`;
  }
  function buildNode(n) {
    const g = mk("g", { class: `node ${n.kind}${n.mock ? " mock" : ""}`, transform: `translate(${n.x},${n.y})`, tabindex: n.mock ? -1 : 0,
      role: "button", "aria-label": n.label }, $("#nodes"));
    const ref = { g, handles: {} };
    const r = n.kind === "pill" ? 16 : 18;
    ref.glow = mk("rect", { class: "glow", x: -1, y: -1, width: n.w + 2, height: n.h + 2, rx: r + 1 }, g);
    mk("rect", { class: "sel", x: -7, y: -7, width: n.w + 14, height: n.h + 14, rx: r + 6 }, g);
    mk("rect", { class: "body", width: n.w, height: n.h, rx: r }, g);
    const iy = n.kind === "pill" ? (n.h - 28) / 2 : 12;
    mk("rect", { class: "ibox", x: 12, y: iy, width: 28, height: 28, rx: 8 }, g);
    mk("path", { class: "icon", d: ICONS[n.icon] || ICONS.check, transform: `translate(18,${iy + 6})` }, g);
    mk("text", { class: "title", x: 50, y: iy + 19 }, g).textContent = n.label;
    if (n.kind === "pill") return ref;
    mk("circle", { class: "sdot", cx: n.w - 16, cy: 26, r: 4 }, g);
    ref.time = mk("text", { class: "time", x: n.w - 26, y: 30, "text-anchor": "end" }, g);
    if (n.ai) {
      const t = mk("g", { class: "aitag", transform: `translate(28,${iy - 7})` }, g);
      mk("rect", { width: 22, height: 14, rx: 5 }, t);
      mk("text", { x: 11, y: 10.4, "text-anchor": "middle" }, t).textContent = "AI";
    }
    ref.resp = mk("g", { class: "resp", transform: `translate(${n.w - 104},-12)` }, g);
    for (let i = 0; i < 6; i++) mk("rect", { x: (i % 3) * 3.4, y: -6 + Math.floor(i / 3) * 3.4, width: 2.2, height: 2.2, rx: 0.6 }, ref.resp);
    ref.respT = mk("text", { x: 14 }, ref.resp);
    mk("text", { class: "lbl", x: 16, y: 64 }, g).textContent = n.mock ? "추후" : "도구";
    const tool = (n.tool || "").length > 17 ? n.tool.slice(0, 16) + "…" : n.tool || "";
    mk("text", { class: "val", x: n.w - 16, y: 64, "text-anchor": "end" }, g).textContent = tool;
    mk("rect", { class: "fieldbox", x: 12, y: 72, width: n.w - 24, height: 18, rx: 6 }, g);
    ref.out = mk("text", { class: "out", x: 20, y: 85 }, g);
    return ref;
  }
  NODES.forEach((n) => { n.ref = buildNode(n); });
  function handle(id, side, dir) {
    const n = byId[id], key = side + dir;
    if (!n.ref.handles[key]) { const [x, y] = port(n, side); n.ref.handles[key] = mk("circle", { class: `hd ${dir}`, cx: x - n.x, cy: y - n.y, r: 5.5 }, n.ref.g); }
    return n.ref.handles[key];
  }
  const edgeList = EDGES.map(([a, b, o = {}]) => {
    const g = mk("g", { class: `edge${o.mock ? " mock" : ""}` }, $("#edges"));
    const p = mk("path", { d: pathD(byId[a], byId[b], o) }, g);
    if (o.label) { const m = p.getPointAtLength(p.getTotalLength() / 2); mk("text", { x: m.x + 8, y: m.y - 8 }, g).textContent = o.label; }
    return { a, b, g, mock: !!o.mock, out: handle(a, o.from || "right", "out"), in: handle(b, o.to || "left", "in") };
  });

  /* ── 상태 ── */
  const STATE = { pending: "대기", running: "실행 중", pass: "완료", waiting: "승인 대기", fail: "실패", skipped: "건너뜀", warn: "보안 미해결" };
  const OUTCOME = { verified: "검증 완료", unresolved: "연결 확인 · 보안 미해결", stopped: "멈춤" };
  let board = null, runs = [], current = null, sel = null, publicUrls = {};
  const fmtDur = (s) => s < 60 ? s.toFixed(1) + "s" : Math.floor(s / 60) + "m " + Math.round(s % 60) + "s";
  const clock = (t) => t ? new Date(t * 1000).toLocaleTimeString("ko-KR", { hour12: false }) : "";
  const nodeOf = (id) => board && board.nodes.find((n) => n.id === id);
  const stateOf = (id) => {
    if (id === "start") return board ? "pass" : "pending";
    const n = nodeOf(id);
    return n ? n.state : "pending";
  };
  const isReal = (id) => !!nodeOf(id) || id === "start";

  function paint() {
    for (const n of NODES) {
      const real = isReal(n.id);
      const st = real ? stateOf(n.id) : "pending";
      const data = nodeOf(n.id);
      n.ref.g.setAttribute("class", `node ${n.kind}${real ? "" : " mock"} s-${st}${sel === n.id ? " selected" : ""}`);
      n.ref.g.setAttribute("tabindex", real ? 0 : -1);
      if (n.ref.out) {
        const tone = st === "pass" ? "ok" : st === "fail" ? "bad" : ["waiting", "warn"].includes(st) ? "warn" : st === "running" ? "run" : "";
        const round = data && data.round > 1 ? `${data.round}차 · ` : "";
        const text = !real ? (n.mock ? "아직 없음" : "—") : round + (st === "running" && Live.lastLine[n.id] ? "› " + Live.lastLine[n.id]
          : data && data.summary ? data.summary : STATE[st]);
        n.ref.out.textContent = text.length > 30 ? text.slice(0, 29) + "…" : text;
        n.ref.out.setAttribute("class", `out ${tone ? "t-" + tone : ""}`);
      }
      if (n.ref.time) {
        const s0 = data && data.started_at, s1 = data && data.ended_at;
        n.ref.time.textContent = s0 ? fmtDur(((s1 || Date.now() / 1000) - s0)) : "";
      }
      if (n.ref.respT) n.ref.respT.textContent = st === "waiting" ? "승인 대기..." : "실행 중...";
    }
    for (const e of edgeList) {
      const real = isReal(e.a) && isReal(e.b);
      const sa = stateOf(e.a), sb = stateOf(e.b);
      let cls = "edge";
      if (!real) cls += " mock";
      else if (["running", "waiting"].includes(sb) && ["pass", "skipped"].includes(sa)) cls += " active";
      else if (["pass", "skipped"].includes(sa) && sb !== "pending") cls += sb === "fail" ? " err" : " done";
      e.g.setAttribute("class", cls);
      e.out.setAttribute("class", `hd out${real && ["pass", "skipped"].includes(sa) ? " on" : ""}`);
      e.in.setAttribute("class", `hd in${real && sb !== "pending" ? " on" : ""}`);
    }
    const dn = nodeOf("dast"), dround = (dn && dn.round) || 1;
    if (board) Theater.reset(`${board.run_id}:${dround}`);
    const did = dn && dn.detail && dn.detail.run_id;
    Theater.label(did ? `점검 ${String(did).slice(0, 8)}${dround > 1 ? ` · ${dround}차` : ""}` : "");
    renderHeader();
    renderPanel();
  }

  function branchChip(label, t, text) { return `<span class="bchip ${t}"><i></i><b>${esc(label)}</b><span>${esc(text)}</span></span>`; }
  function renderHeader() {
    if (!board) { $("#meta").textContent = "실행 기록이 없습니다. '새 실행'으로 시작하세요."; $("#branches").innerHTML = ""; return; }
    const opt = board.options || {};
    const image = ((nodeOf("build") || {}).detail || {}).image || "";
    const digest = image.includes("@") ? image.split("@")[1].slice(0, 19) + "…" : "—";
    const outcome = OUTCOME[board.outcome] || "진행 중";
    $("#title").textContent = `◈ ${(opt.repo || "").split("/").pop() || "파이프라인"} · ${outcome}`;
    $("#meta").innerHTML = `<span class="mono">${esc(board.run_id.slice(0, 8))}</span> · 커밋 <span class="mono">${esc((opt.commit || "").slice(0, 7))}</span> · 이미지 <span class="mono">${esc(digest)}</span> · 시작 ${esc(clock(board.started_at))}`;
    const web = ["prepare", "build", "sast", "stage", "dast", "result"].map((id) => [id, stateOf(id)]);
    const wait = web.find(([, s]) => s === "waiting"), fail = web.find(([, s]) => s === "fail"), run = web.find(([, s]) => s === "running");
    const webChip = wait ? branchChip("웹", "t-wait", byId[wait[0]].label + " 승인 대기") : fail ? branchChip("웹", "t-bad", byId[fail[0]].label + " 실패")
      : run ? branchChip("웹", "t-run", byId[run[0]].label) : board.outcome === "verified" ? branchChip("웹", "t-ok", "검증 완료") : board.outcome === "unresolved" ? branchChip("웹", "t-wait", "연결 확인 · 보안 미해결")
      : branchChip("웹", "", board.outcome ? "멈춤" : "대기");
    const cut = nodeOf("cutover");
    const rollChip = cut ? branchChip("운영 반영", cut.state === "pass" ? "t-ok" : cut.state === "fail" ? "t-bad" : cut.state === "waiting" ? "t-wait" : cut.state === "running" ? "t-run" : "", STATE[cut.state])
      : branchChip("운영 반영", "t-mock", "추후(2단계)");
    const fix = nodeOf("fix");
    const fixChip = !fix ? branchChip("수정 루프", "t-mock", "이 실행에는 없음")
      : branchChip("수정 루프", fix.state === "pass" ? "t-ok" : fix.state === "fail" ? "t-bad" : fix.state === "running" ? "t-run" : "",
        fix.state === "pass" ? ((nodeOf("dast") || {}).round > 1 ? "2차 재검증" : "수정 PR") : fix.state === "pending" ? "재현 시 선택" : STATE[fix.state]);
    $("#branches").innerHTML = webChip + fixChip + branchChip("인프라", "t-mock", "추후(3단계)") + rollChip;
  }

  function autoTarget() {
    if (!board) return "start";
    for (const st of ["waiting", "running", "fail"]) { const n = board.nodes.find((x) => x.state === st && byId[x.id]); if (n) return n.id; }
    const done = board.nodes.filter((x) => x.ended_at && byId[x.id]).sort((a, b) => b.ended_at - a.ended_at)[0];
    return done ? done.id : "prepare";
  }
  const badge = (status) => {
    const m = { REPRODUCED: ["g-bad", "재현됨"], BLOCKED: ["g-warn", "차단됨(CSP 등)"], NOT_REPRODUCED: ["g-ok", "재현 안 됨"], INCONCLUSIVE: ["g-idle", "판정 불가"] };
    const [c, t] = m[status] || ["g-idle", status || "—"];
    return `<span class="g ${c}">${esc(t)}</span>`;
  };
  function special(id, d) {
    d = d || {};
    if (id === "build") return `<dl class="kv"><dt>이미지</dt><dd class="mono">${esc(d.image || "—")}</dd><dt>Jenkins</dt><dd>빌드 #${esc(d.build ?? "—")}</dd></dl>`;
    if (id === "sast") {
      const sc = d.scope || {}, langs = Object.entries(sc.languages || {}).map(([k, v]) => `${k} ${v}`).join(", ");
      const counts = Object.entries(d.counts || {}).map(([k, v]) => `${k} ${v}`).join(" · ");
      return `<dl class="kv"><dt>점검한 커밋</dt><dd class="mono">${esc((sc.commit || "—").slice(0, 12))}</dd><dt>파일</dt><dd>${esc(sc.files ?? "—")}개 (${esc(langs || "—")})</dd>
        <dt>DAST 대상</dt><dd>${esc(d.dast_targets ?? "—")}건</dd><dt>판정</dt><dd class="mono">${esc(counts || "—")}</dd><dt>점검 ID</dt><dd class="mono">${esc(d.run_id || "—")}</dd></dl>`;
    }
    if (id === "stage") return `<dl class="kv"><dt>주소</dt><dd class="mono">${esc(d.url || "—")}</dd><dt>DB 초기화</dt><dd>${d.rows_remaining === 0 ? '<span class="g g-ok">남은 행 0</span>' : "—"}</dd>
        <dt>개정판</dt><dd class="mono">${esc(d.revision || "—")}</dd><dt>스캐너 IP</dt><dd class="mono">${esc(d.scanner_ip || "—")}</dd><dt>점유</dt><dd>${esc({ released: "해제함", none: "없음", release_failed: "해제 실패(만료로 풀림)" }[d.lease] || "점유 중")}</dd></dl>`;
    if (id === "dast") {
      const rows = (d.results || []).map((r) => `<tr><td class="mono">${esc((r.finding_id || "").replace("sast-", "").slice(0, 10))}</td><td>${badge(r.status)}</td></tr>`).join("");
      const link = publicUrls.dast ? ` <a href="${esc(publicUrls.dast)}" target="_blank" rel="noopener noreferrer">DAST 작업실 열기 ↗</a>` : "";
      return `<dl class="kv"><dt>대상</dt><dd class="mono">${esc(d.target_url || "—")}</dd><dt>점검 ID</dt><dd class="mono">${esc(d.run_id || "—")}</dd></dl>
        ${rows ? `<table class="res"><thead><tr><th>항목(SAST 지문)</th><th>판정</th></tr></thead><tbody>${rows}</tbody></table>` : ""}
        <div class="callout">재현됨: 실제로 스크립트가 실행됨 · 차단됨: 저장은 됐지만 CSP 등으로 실행이 막힘 · 판정 불가는 통과로 보지 않습니다.${link}</div>`;
    }
    if (id === "fix") {
      const pr = d.pr || {};
      const trace = (d.trace || []).map((t) => `<li><span class="g ${t.role === "sink" ? "g-bad" : t.role === "source" ? "g-warn" : "g-idle"}">${esc({ source: "입력", propagation: "전달", sink: "출력" }[t.role] || t.role)}</span>
        <span class="mono">${esc(t.file)}:${esc(t.line)}</span> <code>${esc(t.code)}</code></li>`).join("");
      return `<dl class="kv"><dt>수정 요약</dt><dd>${esc(d.summary || "—")}</dd><dt>PR</dt><dd>${pr.url ? `<a href="${esc(pr.url)}" target="_blank" rel="noopener noreferrer">#${esc(pr.number)} ↗</a>` : "—"}</dd>
        <dt>수정 커밋</dt><dd class="mono">${esc((pr.commit || "—").slice(0, 12))}</dd><dt>브랜치</dt><dd class="mono">${esc(pr.branch || "—")}</dd></dl>
        ${trace ? `<h3>코드 흐름(수정 전)</h3><ol class="flow">${trace}</ol>` : ""}
        ${d.patch ? `<h3>패치</h3><pre class="console diff">${esc(d.patch)}</pre>` : ""}`;
    }
    if (id === "cutover") return `<dl class="kv"><dt>이전 대상</dt><dd class="mono">${esc(d.previous || "—")}</dd><dt>TTL</dt><dd>${esc(d.ttl ?? "—")}</dd><dt>확인</dt><dd class="mono">${esc(JSON.stringify(d.verified || "—"))}</dd></dl>`;
    return "";
  }
  function renderPanel() {
    const id = sel || autoTarget(), n = byId[id] || byId.prepare, data = nodeOf(id), real = isReal(id);
    const st = real ? stateOf(id) : "pending";
    const dur = data && data.started_at ? fmtDur(((data.ended_at || Date.now() / 1000) - data.started_at)) + (data.ended_at ? "" : " 경과") : "—";
    const ask = data && data.decision ? `<div class="ask"><b>${esc(data.decision.prompt)}</b><div class="actions">${data.decision.options.map((o) =>
      `<button type="button" class="btn ${o.value === "approve" ? "approve" : o.value === "fix" ? "primary" : ""}" data-answer="${esc(o.value)}">${esc(o.label)}</button>`).join("")}</div></div>` : "";
    Live.show(board && real ? board.run_id : "", id, st, (data && data.round) || 1);
    // 2차로 다시 돈 노드는 1차 결과를 접어서 함께 보여 준다
    const past = (data && data.history || []).map((h) => `<details class="past"><summary>${esc(h.round)}차 · ${esc(STATE[h.state] || h.state)} · ${esc(h.summary || "")}</summary>
      ${special(id, h.detail) || ""}</details>`).join("");
    const events = board ? board.nodes.filter((x) => byId[x.id]).flatMap((x) => [
      x.started_at ? { t: x.started_at, text: `${byId[x.id].label} 시작`, tone: "" } : null,
      x.ended_at ? { t: x.ended_at, text: `${byId[x.id].label}: ${x.summary || STATE[x.state]}`, tone: x.state === "pass" ? "ok" : x.state === "fail" ? "bad" : "warn" } : null,
    ]).filter(Boolean).sort((a, b) => b.t - a.t) : [];
    $("#p-head").innerHTML = `
      <div class="p-sec">
        <div class="eyebrow"><span>${sel ? "선택한 단계" : "현재 단계 · 자동으로 따라갑니다"}</span>${sel ? '<button type="button" id="unsel">자동으로</button>' : ""}</div>
        <div class="p-title"><h2>${esc(n.label)}</h2><span class="state s-${st}">${real ? STATE[st] : "추후"}</span>${n.ai ? '<span class="state s-running">AI</span>' : ""}</div>
        ${n.tool ? `<div class="tool">${esc(n.tool)}</div>` : ""}
        ${n.desc ? `<p class="desc">${esc(n.desc)}</p>` : ""}
        ${real ? `<dl class="kv"><dt>소요</dt><dd class="mono">${esc(dur)}</dd>${data && data.summary ? `<dt>결과</dt><dd>${esc(data.summary)}</dd>` : ""}</dl>` : ""}
        ${ask}
      </div>`;
    $("#p-rest").innerHTML = `
      ${real && data ? `<div class="p-sec"><h3>세부${data.round > 1 ? ` · ${esc(data.round)}차` : ""}</h3>${special(id, data.detail) || '<span class="hint">—</span>'}${past}</div>` : ""}
      <div class="p-sec"><h3>실행 기록</h3><ol class="log">${events.map((l) => `<li class="${l.tone ? "t-" + l.tone : ""}"><span class="t">${esc(clock(l.t))}</span><span>${esc(l.text)}</span></li>`).join("") || "<li><span></span><span>기록 없음</span></li>"}</ol></div>`;
  }
  $("#panel").addEventListener("click", async (ev) => {
    if (ev.target.id === "unsel") { sel = null; paint(); return; }
    const b = ev.target.closest("[data-answer]");
    if (!b || !board) return;
    b.disabled = true;
    const r = await fetch(`/api/pipeline/runs/${encodeURIComponent(board.run_id)}/answer`, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value: b.dataset.answer }) });
    if (!r.ok) { const j = await r.json().catch(() => ({})); alert(j.detail || "답을 보내지 못했습니다"); }
    await loadRun();
  });
  for (const n of NODES) {
    n.ref.g.addEventListener("click", (e) => { e.stopPropagation(); sel = n.id; paint(); });
    n.ref.g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sel = n.id; paint(); } });
  }

  /* ── 진행 보기: 고른 단계의 도구가 지금 하는 일(콘솔 로그·SAST 탐지·DAST 브라우저 화면) ── */
  const enc = encodeURIComponent;
  // DAST 노드 아래 캔버스에 띄우는 작은 실시간 화면
  const thumb = (() => {
    const n = byId.dast, g = mk("g", { class: "thumb", transform: `translate(${n.x},${n.y + n.h + 16})` }, $("#nodes"));
    mk("rect", { class: "tframe", width: n.w, height: 140, rx: 12 }, g);
    const img = mk("image", { x: 4, y: 4, width: n.w - 8, height: 132, preserveAspectRatio: "xMidYMid meet" }, g);
    const tag = mk("g", { transform: "translate(10,10)" }, g);
    const tagBox = mk("rect", { width: 44, height: 16, rx: 5, class: "tlive" }, tag);
    const tagText = mk("text", { x: 22, y: 11.5, "text-anchor": "middle" }, tag);
    const idText = mk("text", { class: "tid", x: n.w - 10, y: 21.5, "text-anchor": "end" }, g);
    g.style.display = "none";
    g.addEventListener("click", (e) => { e.stopPropagation(); sel = "dast"; paint(); });
    return { label(text) { idText.textContent = text; },
      clear() { img.removeAttribute("href"); g.style.display = "none"; g.classList.remove("on"); },
      set(src, on) {
      if (src) img.setAttribute("href", src);
      g.style.display = src || on ? "" : "none"; g.classList.toggle("on", !!on);
      tagText.textContent = on ? "● LIVE" : "마지막 화면"; tagBox.setAttribute("width", on ? 44 : 62); tagText.setAttribute("x", on ? 22 : 31);
    } };
  })();
  // DAST 화면 연출. 패널·캔버스 위 떠 있는 화면(PiP)·크게 보기에 같은 프레임과 커서·클릭·자막·탐지 알림을 그린다.
  // 화면 좌표는 DAST 브라우저 viewport(기본 960x640) 기준이고, 모든 화면을 같은 3:2 비율로 두어 % 로 옮긴다.
  const Theater = (() => {
    const screens = new Set();
    let size = { w: 960, h: 640 }, last = "", on = false, subTimer = null, alertTimer = null, run = "", pipClosed = "";
    const tv = (cls = "") => `<div class="tv ${cls}"><img class="tv-img" alt="DAST 가 조작 중인 스테이징 화면"><div class="tv-cursor" hidden></div>
      <div class="tv-sub" hidden></div><div class="tv-alert" hidden></div><span class="badge-live">● LIVE</span><span class="screen-empty">화면 기록을 기다리는 중…</span></div>`;
    const all = (fn) => screens.forEach((el) => { if (el.isConnected) fn(el); else screens.delete(el); });
    function add(el) { screens.add(el); if (last) paintFrame(el); }
    function paintFrame(el) { el.querySelector(".tv-img").src = last; el.classList.add("has"); el.classList.toggle("on", on); }
    function frame(src, live, w, h) {
      last = src; on = live; if (w && h) size = { w, h };
      all(paintFrame); thumb.set(src, live);
      if (live && run && pipClosed !== run) $("#pip").hidden = false;
    }
    function idle() { on = false; all((el) => el.classList.remove("on")); thumb.set(last, false); }
    function cursor(x, y) {
      all((el) => { const c = el.querySelector(".tv-cursor"); c.hidden = false;
        c.style.left = (x / size.w * 100) + "%"; c.style.top = (y / size.h * 100) + "%"; });
    }
    function click() {
      all((el) => { const c = el.querySelector(".tv-cursor"); if (c.hidden) return;
        const r = document.createElement("span"); r.className = "tv-ripple"; r.style.left = c.style.left; r.style.top = c.style.top;
        el.appendChild(r); setTimeout(() => r.remove(), 700); });
    }
    function sub(text, tone = "") {
      clearTimeout(subTimer);
      all((el) => { const s = el.querySelector(".tv-sub"); s.hidden = false; s.className = "tv-sub " + tone; s.textContent = text; });
      subTimer = setTimeout(() => all((el) => { el.querySelector(".tv-sub").hidden = true; }), 6000);
    }
    // keep: 알림을 지우지 않고 남긴다(재현 판정은 멈춘 블루스크린 위에 계속 보인다). 실행·회차가 바뀌면 reset 이 지운다
    function alert(text, tone = "bad", delay = 0, keep = false) {
      setTimeout(() => {
        clearTimeout(alertTimer);
        all((el) => { const a = el.querySelector(".tv-alert"); a.hidden = false; a.className = "tv-alert " + tone; a.textContent = text; el.classList.add("flash-" + tone); });
        if (!keep) alertTimer = setTimeout(() => all((el) => { el.querySelector(".tv-alert").hidden = true; el.classList.remove("flash-bad", "flash-ok", "flash-warn"); }), 3500);
      }, delay);
    }
    // 실행·회차(= DAST 점검)가 바뀌면 이전 점검의 화면을 모두 지운다. 화면마다 점검 ID 를 붙인다
    function reset(runId) {
      if (runId === run) return;
      run = runId; last = ""; on = false; $("#pip").hidden = true; thumb.clear();
      clearTimeout(subTimer); clearTimeout(alertTimer);
      all((el) => { el.classList.remove("has", "on", "flash-bad", "flash-ok", "flash-warn"); el.querySelector(".tv-img").removeAttribute("src");
        el.querySelector(".tv-cursor").hidden = true; el.querySelector(".tv-sub").hidden = true; el.querySelector(".tv-alert").hidden = true; });
    }
    function label(text) { $("#pip-id").textContent = text; thumb.label(text); }
    return { tv, add, frame, idle, cursor, click, sub, alert, reset, label, key: () => run, closePip() { pipClosed = run; $("#pip").hidden = true; } };
  })();
  Theater.add($("#pip .tv"));
  Theater.add($("#viewer-live .tv"));
  $("#pip-close").onclick = () => Theater.closePip();
  const openLive = () => { $("#viewer-img").hidden = true; $("#viewer-live").hidden = false; $("#viewer").showModal(); };
  $("#pip-big").onclick = openLive;
  const KIND = { fail: ["g-bad", "취약"], review: ["g-warn", "검토 필요"], open: ["g-warn", "열림"], pass: ["g-ok", "문제 없음"] };
  const STAGE = { analyze: "가설", explore: "탐색", load: "접속", detect: "탐지", judge: "판정", impact: "영향 확인", verify: "확인" };
  const ACT = { left_click: "클릭", double_click: "더블클릭", think: "생각", plan: "계획", click: "클릭", type: "입력", key: "키", navigate: "이동", scroll: "스크롤", screenshot: "화면 보기",
    probe: "검사 문자열", finish: "종료", mouse_move: "이동" };
  const Live = (() => {
    let key = "", timer = null, es = null, ctx = {}, cleanups = [];
    const lastLine = {};
    const box = () => $("#p-live");
    function stop() { clearTimeout(timer); timer = null; if (es) { es.close(); es = null; } cleanups.splice(0).forEach((f) => f()); }
    function show(run, node, st, round = 1) {
      // 회차까지 키에 넣는다: 2차는 다른 빌드 번호·점검 ID 를 본다
      const k = run && ["build", "stage", "sast", "dast"].includes(node) ? `${run}:${node}:${round}` : "";
      if (k === key) { ctx.state = st; return; }
      stop(); key = k; ctx = { run, node, state: st, round };
      $("#panel").scrollTop = 0;
      if (!k) { box().innerHTML = ""; return; }
      ({ build: logView, stage: logView, sast: sastView, dast: dastView })[node](ctx, k);
    }
    const live = (c) => ["running", "waiting"].includes(c.state);
    async function json(url) {
      const r = await fetch(url);
      if (r.status === 401) { location.href = "/login"; throw new Error("login"); }
      const j = await r.json().catch(() => ({}));
      return { ok: r.ok, status: r.status, j };
    }

    function logView(c, k) {
      box().innerHTML = `<div class="p-sec live"><div class="lv-head"><h3>Jenkins 콘솔 로그</h3><span class="lv-meta"></span></div>
        <pre class="console" tabindex="0">불러오는 중…</pre></div>`;
      const pre = box().querySelector("pre"), meta = box().querySelector(".lv-meta");
      let start = 0, first = true;
      async function tick() {
        if (key !== k) return;
        try {
          const { ok, status, j } = await json(`/api/pipeline/runs/${enc(c.run)}/live/log/${c.node}?start=${start}`);
          if (key !== k) return;
          if (!ok) {
            pre.textContent = j.detail || "로그를 읽지 못했습니다";
            if (!(status === 404 && live(c))) return;
          } else {
            const stick = first || pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 40;
            if (first) { pre.textContent = ""; first = false; }
            if (j.text) pre.textContent = (pre.textContent + j.text).slice(-400000);
            start = j.next;
            const link = publicUrls.jenkins ? ` · <a href="${esc(publicUrls.jenkins)}/job/${esc(j.job)}/${esc(j.build)}/console" target="_blank" rel="noopener noreferrer">Jenkins ↗</a>` : "";
            meta.innerHTML = `${esc(j.job)} #${esc(j.build)}${j.more ? ' · <span class="pulse">진행 중</span>' : ""}${link}`;
            const lines = pre.textContent.trimEnd().split("\n");
            lastLine[c.node] = lines[lines.length - 1] || "";
            if (stick) pre.scrollTop = pre.scrollHeight;
            if (!j.more && !live(c)) { delete lastLine[c.node]; return; }
          }
        } catch (e) { /* 다음 주기에 다시 */ }
        timer = setTimeout(tick, 2000);
      }
      tick();
    }

    function sastView(c, k) {
      box().innerHTML = `<div class="p-sec live"><div class="lv-head"><h3>SAST 탐지 항목</h3><span class="lv-meta"></span></div><div class="finds">불러오는 중…</div></div>`;
      const list = box().querySelector(".finds"), meta = box().querySelector(".lv-meta");
      const item = (f) => {
        const [cls, label] = KIND[f.kind] || ["g-idle", f.kind || "—"];
        const steps = (f.steps || []).map((s, i, all) => `<li><span class="role">${i === 0 ? "입력" : i === all.length - 1 ? "출력" : "전달"}</span>
          <span class="mono">${esc(s.file)}:${esc(s.line ?? "")}</span><span>${esc(s.note)}</span></li>`).join("");
        const entry = f.entry && f.entry.path ? ` · 진입 <span class="mono">${esc(f.entry.method || "")} ${esc(f.entry.path)}</span>` : "";
        const judge = f.judgment && f.judgment.verdict ? `<p class="hint">판정(${esc(f.judgment.by || "")}): ${esc(f.judgment.verdict)} — ${esc(f.judgment.reason || "")}</p>` : "";
        return `<details class="find"${f.kind === "fail" ? " open" : ""}><summary><span class="g ${cls}">${esc(label)}</span><b>${esc(f.rule.replace(/^delta\./, ""))}</b>
          <span class="mono loc">${esc(f.sink.file)}:${esc(f.sink.line ?? "")}</span>${f.dast ? '<span class="tag">DAST 대상</span>' : ""}</summary>
          <p>${esc(f.message)}${entry}${f.stored ? " · 저장형" : ""}</p>${steps ? `<ol class="flow">${steps}</ol>` : ""}${judge}</details>`;
      };
      async function tick() {
        if (key !== k) return;
        try {
          const { ok, status, j } = await json(`/api/pipeline/runs/${enc(c.run)}/live/sast`);
          if (key !== k) return;
          if (ok) {
            const main = j.findings.filter((f) => f.kind !== "pass"), quiet = j.findings.filter((f) => f.kind === "pass");
            meta.textContent = `${j.findings.length}건 · DAST 대상 ${j.findings.filter((f) => f.dast).length}건`;
            list.innerHTML = (main.map(item).join("") || '<p class="hint">취약·검토 항목이 없습니다.</p>')
              + (quiet.length ? `<details class="more"><summary>문제 없음 ${quiet.length}건 보기</summary>${quiet.map(item).join("")}</details>` : "");
            return;
          }
          list.textContent = j.detail || "SAST 결과를 읽지 못했습니다";
          if (!(status === 404 && live(c))) return;
        } catch (e) { /* 다음 주기에 다시 */ }
        timer = setTimeout(tick, 3000);
      }
      tick();
    }

    function dastView(c, k) {
      box().innerHTML = `
        <div class="p-sec live">
          <div class="lv-head"><h3>DAST 브라우저 화면</h3><span class="lv-meta"></span><button type="button" class="mini" id="lv-big">크게 보기</button></div>
          <div id="lv-screen">${Theater.tv()}</div>
          <div class="lv-now" id="lv-now"></div>
          <div class="stages" id="lv-stages"></div>
        </div>
        <div class="p-sec"><h3>에이전트 행동</h3><ol class="feed" id="lv-feed"><li class="hint">행동 기록을 기다리는 중…</li></ol></div>
        <div class="p-sec"><h3>판정 · 증거</h3><div id="lv-results" class="hint">불러오는 중…</div></div>`;
      const feed = $("#lv-feed"), stages = {}, meta = box().querySelector(".lv-meta");
      let titles = {}, fed = 0, results = [], hasFrame = false;
      Theater.reset(`${c.run}:${c.round}`);
      Theater.add($("#lv-screen .tv"));
      $("#lv-big").onclick = openLive;
      const short = (id) => titles[id] ? titles[id].slice(0, 26) : String(id || "").replace("sast-", "").slice(0, 8);
      function frame(src, on, w, h) { hasFrame = true; Theater.frame(src, on, w, h); }
      function add(text, tone, tag, fid, at) {
        if (!fed) feed.innerHTML = "";
        fed++;
        const li = document.createElement("li");
        li.className = tone ? "t-" + tone : "";
        li.innerHTML = `<span class="t">${esc(at ?? new Date().toLocaleTimeString("ko-KR", { hour12: false }))}</span>${tag ? `<span class="act">${esc(tag)}</span>` : ""}<span>${fid && Object.keys(titles).length > 1 ? `<i>${esc(short(fid))}</i> ` : ""}${esc(text)}</span>`;
        const stick = feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 30;
        feed.appendChild(li);
        while (feed.children.length > 300) feed.firstChild.remove();
        if (stick) feed.scrollTop = feed.scrollHeight;
      }
      function renderStages() {
        $("#lv-stages").innerHTML = Object.entries(stages).map(([name, s]) =>
          `<span class="stg s-${esc(s.status)}" title="${esc(s.detail || "")}">${esc(STAGE[name] || name)}</span>`).join("");
      }
      async function view() {
        try {
          const { ok, j } = await json(`/api/pipeline/runs/${enc(c.run)}/live/dast`);
          if (key !== k) return false;
          if (!ok) { $("#lv-results").textContent = j.detail || "DAST 결과를 읽지 못했습니다"; return false; }
          titles = Object.fromEntries((j.findings || []).map((f) => [f.id, f.title]));
          results = j.results || [];
          meta.textContent = `점검 ${String(j.run_id || "").slice(0, 8)}${c.round > 1 ? ` · ${c.round}차` : ""} · ${j.status === "running" ? "점검 중" : j.status === "done" ? "점검 끝" : j.status || ""} · ${(j.findings || []).length}건`;
          const rows = (j.results || []).map((r) => {
            const shots = ((r.evidence || {}).screenshots || []).map((s) =>
              `<button type="button" class="shot" data-src="${esc(s)}"><img src="${esc(s)}" alt="${esc(s.split("/").pop())}" loading="lazy"><span>${esc(s.split("/").pop().replace(/\.png$/, "").replace(r.finding_id + "-", ""))}</span></button>`).join("");
            return `<div class="verdict">${badge(r.status)}<b>${esc(r.title || short(r.finding_id))}</b><p>${esc(r.message || "")}</p>${shots ? `<div class="shots">${shots}</div>` : ""}</div>`;
          }).join("");
          // DAST 가 SAST 에 돌려준 결과(필터 판정 보정): ineffective=필터가 못 막음, effective=막음
          const OUT = { ineffective: "필터 효과 없음(재현됨)", effective: "필터 효과 있음(재현 안 됨)", conflict: "판정 충돌", skipped: "건너뜀", unknown: "판정 불가" };
          const reply = (j.sast_feedback || []).map((o) => `<li><span class="mono">${esc(String(o.fingerprint).slice(0, 10))}</span> ${esc(OUT[o.outcome] || o.outcome)}</li>`).join("");
          $("#lv-results").innerHTML = (rows || '<span class="hint">아직 판정이 없습니다.</span>')
            + (reply ? `<div class="reply"><b>SAST 회신</b><ul>${reply}</ul></div>` : j.status === "done" ? '<div class="reply hint">SAST 회신 없음</div>' : "");
          $("#lv-results").classList.remove("hint");
          if (!hasFrame) {
            const last = (j.results || []).flatMap((r) => (r.evidence || {}).screenshots || []).pop();
            if (last) frame(last, false);
          }
          return true;
        } catch (e) { return false; }
      }
      $("#lv-results").addEventListener("click", (e) => {
        const b = e.target.closest(".shot");
        if (b) { $("#viewer-img").src = b.dataset.src; $("#viewer-img").hidden = false; $("#viewer-live").hidden = true; $("#viewer").showModal(); }
      });
      function onEvent(ev) {
        const fid = ev.finding_id;
        if (ev.type === "frame") return frame(ev.image, true, Number(ev.width) || 0, Number(ev.height) || 0);
        if (ev.type === "cursor") return Theater.cursor(Number(ev.x) || 0, Number(ev.y) || 0);
        if (ev.type === "click") return fx.click();
        if (ev.type === "execution") { add(ev.message || "검사 문자열이 브라우저에서 실행됐습니다", "bad", "실행 탐지", fid);
          fx.sub("검사 문자열이 브라우저에서 실행됐습니다", "bad");
          return fx.alert("XSS 실행 탐지", "bad", 1500); }  // 실제 화면 효과(블루스크린)가 먼저 보이게 늦게 띄운다
        if (ev.type === "stage") { stages[ev.stage] = { status: ev.status, detail: ev.detail }; renderStages();
          if (ev.status === "running") { $("#lv-now").textContent = `${STAGE[ev.stage] || ev.stage} · ${ev.detail || ""}`;
            if (ev.stage === "impact") fx.sub("영향 시연: 재현된 취약점으로 실제 화면이 어떻게 장악되는지 보여 줍니다", "bad"); }
          return; }
        if (ev.type === "state") { $("#lv-now").textContent = ev.message || ev.phase || ""; return; }
        if (ev.type === "agent") { fx.sub(`AI · ${ACT[ev.action] || ev.action} — ${ev.message || ""}`);
          return add(ev.message + (ev.detail ? ` (${ev.detail})` : ""), ev.action === "plan" ? "run" : "", ACT[ev.action] || ev.action, fid); }
        if (ev.type === "probe") { fx.sub(`페이로드 입력 중 · ${ev.payload_id || ""}`, "warn");
          return add(ev.message + (ev.payload_id ? ` · ${ev.payload_id}` : ""), "warn", "검사 문자열", fid); }
        if (ev.type === "log") return add(ev.message, "", "브라우저", fid);
        if (ev.type === "finding") return add(ev.message || ev.status, "", "항목", fid);
        if (ev.type === "result") { const r = ev.result || {};
          fx.alert({ REPRODUCED: "XSS 재현됨 · 취약점 확인", BLOCKED: "차단됨 · CSP 가 실행을 막음", NOT_REPRODUCED: "재현 안 됨" }[r.status] || `판정 불가`,
            r.status === "REPRODUCED" ? "bad" : r.status === "NOT_REPRODUCED" ? "ok" : "warn", 0, true);
          add(`${r.status}: ${r.message || ""}`, r.status === "REPRODUCED" ? "bad" : r.status === "NOT_REPRODUCED" ? "ok" : "warn", "판정", r.finding_id); return view(); }
        if (ev.type === "done" || ev.type === "error") { finished = true; add(ev.message || (ev.type === "done" ? "점검을 마쳤습니다" : "점검 오류"), ev.type === "done" ? "ok" : "bad", "끝"); idle(); if (es) { es.close(); es = null; } return view(); }
      }
      // 연결 관리. DAST 시작 전에 열어 둔 패널도 점검이 시작되면 스스로 붙고, 잠깐 끊기면(중계·배포 재시작) 다시 붙는다.
      // 끝(done) 을 받았거나 DAST 가 이 점검을 모르면(404: 재시작 등) 더 붙지 않고 증거로 채운다.
      let finished = false, seen = 0, tries = 0, warm = false;
      const ended = () => finished || ["pass", "fail", "skipped"].includes(c.state);
      const idle = () => Theater.idle();
      const fx = { sub: (...a) => warm && Theater.sub(...a), alert: (...a) => warm && Theater.alert(...a), click: () => warm && Theater.click() };
      function retry() {
        if (key !== k || finished) return;
        clearTimeout(timer);
        timer = setTimeout(connect, Math.min(10000, 1500 * ++tries));
      }
      function connect() {
        if (key !== k || es || finished) return;
        es = new EventSource(`/api/pipeline/runs/${enc(c.run)}/live/dast/events`);
        es.onopen = () => { tries = 0; warm = false; setTimeout(() => { warm = true; }, 1200); };
        es.onmessage = (m) => {
          if (key !== k) return;
          let ev;
          try { ev = JSON.parse(m.data); } catch (e) { return; /* 깨진 줄은 건너뛴다 */ }
          // 다시 붙으면 처음부터 다시 오므로 이미 본 순번은 건너뛴다. 화면 프레임은 id 없이 오는데
          // EventSource 는 그때도 직전 id 를 lastEventId 로 붙이므로, 프레임은 순번을 보지 않고 항상 받는다.
          if (ev.type !== "frame") {
            const n = Number(m.lastEventId || 0);
            if (n && n <= seen) return;
            if (n) seen = n;
          }
          onEvent(ev);
        };
        es.addEventListener("gone", (m) => {
          if (es) { es.close(); es = null; }
          idle();
          if (m.data === "404") { finished = true; if (!fed) replay(); return; }
          retry();  // 중계 일시 오류: 다시 붙는다
        });
        es.onerror = () => {
          if (es) { es.close(); es = null; }
          idle();
          if (ended() && !live(c)) { finished = true; if (!fed) replay(); return; }
          retry();  // 아직 점검 ID 가 없거나(시작 전) 연결이 끊겼다
        };
      }
      // DAST 가 이 점검의 실시간 기록을 더는 갖고 있지 않으면(재시작 등) 증거에 남은 행동 목록으로 채운다
      async function replay() {
        if (!results.length) await view();
        for (const r of results) for (const a of ((r.evidence || {}).actions || []))
          add((a.reason || "") + (a.detail ? ` (${a.detail})` : ""), "", ACT[a.action] || a.action, r.finding_id, "기록");
        for (const r of results) add(`${r.status}: ${r.message || ""}`, r.status === "REPRODUCED" ? "bad" : r.status === "NOT_REPRODUCED" ? "ok" : "warn", "판정", r.finding_id, "기록");
        if (!fed) feed.innerHTML = '<li class="hint">남은 행동 기록이 없습니다. 아래 증거 스크린샷을 보세요.</li>';
      }
      // 판정·증거는 진행 중이면 주기적으로 다시 읽는다
      const vt = setInterval(() => { if (!ended() || live(c)) view(); }, 5000);
      cleanups.push(() => clearInterval(vt));
      view();
      connect();
    }
    return { show, lastLine, stop };
  })();
  $("#close-viewer").onclick = () => $("#viewer").close();

  /* ── 카메라 ── */
  const stageEl = $("#stage"), svg = $("#svg"), world = $("#world");
  const cam = { x: 0, y: 0, k: 0.5 }, tgt = { x: 0, y: 0, k: 0.5 };
  let follow = true, lastKey = "";
  const followOn = (v) => { follow = v; $("#follow").setAttribute("aria-pressed", String(v)); lastKey = ""; };
  function bounds(ids) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const id of ids) { const n = byId[id]; x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x + n.w); y1 = Math.max(y1, n.y + n.h); }
    return { x0, y0, x1, y1 };
  }
  function fitTo(b, pad, maxK, minK = 0.15) {
    const W = stageEl.clientWidth, H = stageEl.clientHeight, w = b.x1 - b.x0 + pad * 2, h = b.y1 - b.y0 + pad * 2;
    const k = Math.max(minK, Math.min(maxK, W / w, H / h));
    tgt.k = k; tgt.x = W / 2 - k * (b.x0 + b.x1) / 2; tgt.y = H / 2 - k * (b.y0 + b.y1) / 2;
  }
  const fitAll = () => { const b = bounds(NODES.map((n) => n.id)); for (const g of GROUPS) { b.x0 = Math.min(b.x0, g.x); b.y0 = Math.min(b.y0, g.y); b.x1 = Math.max(b.x1, g.x + g.w); b.y1 = Math.max(b.y1, g.y + g.h); } fitTo(b, 20, 1.1, 0.08); };
  const fitWeb = () => fitTo(bounds(["start", "prepare", "build", "sast", "stage", "dast", "result"]), 60, 1.0);
  function followTick() {
    if (!follow || !board) return;
    const act = board.nodes.filter((n) => ["running", "waiting"].includes(n.state) && byId[n.id]).map((n) => n.id).sort();
    const key = act.join(",") || "web";
    if (key === lastKey) return;
    lastKey = key;
    // 웹 갈래 전체는 늘 보이게 두고, 웹 밖(운영 반영 등)에서 진행 중이면 그 노드까지 넓힌다
    fitTo(bounds(["start", "prepare", "build", "sast", "stage", "dast", "result", ...act]), 60, 1.0);
  }
  let drag = null;
  svg.addEventListener("pointerdown", (e) => { if (e.target.closest(".node, .thumb")) return; drag = { x: e.clientX, y: e.clientY }; svg.classList.add("dragging"); svg.setPointerCapture(e.pointerId); followOn(false); });
  svg.addEventListener("pointermove", (e) => { if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag = { x: e.clientX, y: e.clientY }; cam.x += dx; cam.y += dy; tgt.x += dx; tgt.y += dy; });
  svg.addEventListener("pointerup", () => { drag = null; svg.classList.remove("dragging"); });
  svg.addEventListener("click", (e) => { if (!e.target.closest(".node") && sel) { sel = null; paint(); } });
  stageEl.addEventListener("wheel", (e) => {
    e.preventDefault(); followOn(false);
    const r = stageEl.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    const k = Math.max(0.08, Math.min(2, tgt.k * Math.exp(-e.deltaY * 0.0015)));
    tgt.x = mx - (mx - tgt.x) * k / tgt.k; tgt.y = my - (my - tgt.y) * k / tgt.k; tgt.k = k;
  }, { passive: false });
  const zoomBy = (f) => { followOn(false); const W = stageEl.clientWidth / 2, H = stageEl.clientHeight / 2, k = Math.max(0.08, Math.min(2, tgt.k * f)); tgt.x = W - (W - tgt.x) * k / tgt.k; tgt.y = H - (H - tgt.y) * k / tgt.k; tgt.k = k; };
  $("#zin").onclick = () => zoomBy(1.25); $("#zout").onclick = () => zoomBy(0.8);
  $("#fit").onclick = () => { followOn(false); fitAll(); };
  $("#follow").onclick = () => followOn(!follow);
  let lastT = performance.now(), tick = 0;
  function frame(t) {
    const dt = Math.min(64, t - lastT); lastT = t;
    followTick();
    const a = reduce ? 1 : 1 - Math.exp(-dt / 170);
    cam.x += (tgt.x - cam.x) * a; cam.y += (tgt.y - cam.y) * a; cam.k += (tgt.k - cam.k) * a;
    world.setAttribute("transform", `translate(${cam.x},${cam.y}) scale(${cam.k})`);
    if (t - tick > 1000 && board && !board.outcome) { tick = t; for (const n of NODES) { const d = nodeOf(n.id); if (d && d.started_at && !d.ended_at && n.ref.time) n.ref.time.textContent = fmtDur(Date.now() / 1000 - d.started_at); } }
    requestAnimationFrame(frame);
  }

  /* ── 데이터 ── */
  async function get(url) {
    const r = await fetch(url);
    if (r.status === 401) { location.href = "/login"; throw new Error("login"); }
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }
  async function loadRuns() {
    const data = await get("/api/pipeline/runs");
    runs = data.runs || [];
    const box = $("#runs");
    const want = current || data.active || (runs[0] && runs[0].run_id);
    box.innerHTML = runs.map((r) => {
      const o = { verified: "검증 완료", unresolved: "보안 미해결", stopped: "멈춤" }[r.outcome] || "진행 중";
      return `<option value="${esc(r.run_id)}">${esc(clock(r.started_at))} · ${esc(((r.options || {}).commit || "").slice(0, 7))} · ${esc(o)}</option>`;
    }).join("") || "<option value=''>실행 기록 없음</option>";
    if (want) box.value = want;
    if (want !== current) { current = want; sel = null; lastKey = ""; }
  }
  async function loadRun() {
    if (!current) { board = null; paint(); return; }
    board = await get(`/api/pipeline/runs/${encodeURIComponent(current)}`);
    paint();
  }
  async function loadHealth() {
    try {
      const s = await get("/api/status");
      publicUrls = s.public_urls || {};
      const up = s.services.filter((x) => x.state === "up").length, total = s.services.length;
      const chip = $("#health-chip");
      chip.textContent = `${up}/${total} 정상`; chip.className = up === total ? "ok" : "bad";
      $("#run-cutover-label").hidden = !s.cutover_configured;
      const rel = $("#run-release"), picked = rel.value, releases = s.release_targets || [];
      rel.innerHTML = `<option value="">스테이징까지만</option>` + releases.map((t) => `<option value="${esc(t.id)}">${esc(t.label)} (${esc((t.cloud || "").toUpperCase())})</option>`).join("");
      if (releases.some((t) => t.id === picked)) rel.value = picked;
      $("#run-release-label").hidden = !releases.length;
      if (!s.cutover_configured) $("#run-cutover").checked = false;
      const svc = s.services.map((x) => `<div><b>${esc(x.label)}</b><span>${esc(x.role)}</span><span>${x.state === "up" ? "정상" : esc(x.state)}${x.placeholder ? " · 임시 서버" : ""}${x.ms != null ? " · " + esc(x.ms) + "ms" : ""}</span></div>`).join("");
      const targets = ((s.staging || {}).targets || []).map((t) => `<dt>${esc((t.cloud || "").toUpperCase())} 스테이징</dt><dd class="mono">${esc(t.url || t.error || "—")}</dd>`).join("");
      const urls = Object.entries(publicUrls).map(([k, v]) => `<dt>${esc(k)}</dt><dd><a href="${esc(v)}" target="_blank" rel="noopener noreferrer">${esc(v)}</a></dd>`).join("");
      $("#health-body").innerHTML = `<div class="svc">${svc}</div><dl class="kv">${targets}<dt>출구 고정 IP</dt><dd class="mono">${esc(s.nat_ip || "—")}</dd>${urls}</dl>`;
    } catch (e) { $("#health-chip").textContent = "확인 실패"; }
  }
  $("#runs").addEventListener("change", async (e) => { current = e.target.value; sel = null; lastKey = ""; await loadRun(); });
  // 시작 폼: 저장소 주소만 넣으면 브랜치·최근 커밋을 불러와 고르게 한다(커밋 SHA 를 직접 넣지 않는다)
  // 기본 브랜치는 이 저장소에서 마지막으로 빌드까지 된 실행의 브랜치다(저장소 기본 브랜치가 빌드 대상이 아닐 수 있다)
  const lastBuiltBranch = () => {
    const repo = $("#run-repo").value.trim().replace(/\.git$|\/$/g, "");
    const prev = runs.find((r) => r.built && r.options && r.options.branch && r.options.repo === repo);
    return prev ? prev.options.branch : undefined;
  };
  let refsSeq = 0;
  // 저장소 주소를 바꾸면 이전 목록을 비운다. [확인]을 눌러야 그 주소의 브랜치·커밋을 불러온다
  function clearRefs(text = "저장소 확인 후 선택") {
    refsSeq++;
    $("#run-branch").innerHTML = `<option value="">${esc(text)}</option>`;
    $("#run-commit").innerHTML = `<option value="">${esc(text)}</option>`;
    $("#run-branch").disabled = $("#run-commit").disabled = $("#run-submit").disabled = true;
  }
  async function loadRefs(branch) {
    const repo = $("#run-repo").value.trim(), seq = ++refsSeq;
    const bsel = $("#run-branch"), csel = $("#run-commit"), btn = $("#run-load");
    bsel.disabled = csel.disabled = $("#run-submit").disabled = btn.disabled = true;
    csel.innerHTML = '<option value="">불러오는 중…</option>';
    $("#run-msg").textContent = "GitHub 에서 브랜치·커밋을 불러오는 중…";
    try {
      const q = new URLSearchParams({ repo, ...(branch ? { branch } : {}) });
      const r = await fetch(`/api/git/refs?${q}`);
      const j = await r.json().catch(() => ({}));
      if (seq !== refsSeq) return;
      btn.disabled = false;
      if (!r.ok) {
        clearRefs("불러오지 못함");
        const detail = typeof j.detail === "string" ? j.detail : `HTTP ${r.status}`;
        $("#run-msg").textContent = `불러오지 못했습니다: ${detail}. 주소를 확인하고 [확인]을 다시 누르세요`;
        return;
      }
      bsel.innerHTML = j.branches.map((b) => `<option value="${esc(b)}">${esc(b)}${b === j.default_branch ? " (기본)" : ""}</option>`).join("");
      bsel.value = j.branch;
      csel.innerHTML = j.commits.map((c, i) => `<option value="${esc(c.sha)}">${esc(c.sha.slice(0, 7))} · ${esc(c.message.slice(0, 48))}${i === 0 ? " (최신)" : ""} · ${esc(c.author)}${c.date ? " · " + esc(new Date(c.date).toLocaleString("ko-KR", { hour12: false, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })) : ""}</option>`).join("")
        || '<option value="">커밋 없음</option>';
      bsel.disabled = csel.disabled = false;
      $("#run-submit").disabled = !j.commits.length;
      $("#run-msg").textContent = `${j.branches.length}개 브랜치 · ${j.branch} 최근 커밋 ${j.commits.length}개`;
    } catch (e) {
      if (seq !== refsSeq) return;
      btn.disabled = false; clearRefs("불러오지 못함");
      $("#run-msg").textContent = "불러오지 못했습니다(연결 오류). [확인]을 다시 누르세요";
    }
  }
  $("#run-load").addEventListener("click", () => loadRefs(lastBuiltBranch()));
  $("#run-repo").addEventListener("input", () => { clearRefs(); $("#run-msg").textContent = ""; });
  // 주소 칸에서 Enter 는 실행이 아니라 [확인]
  $("#run-repo").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); loadRefs(lastBuiltBranch()); } });
  $("#run-branch").addEventListener("change", (e) => loadRefs(e.target.value));
  $("#open-start").onclick = () => {
    $("#start").hidden = !$("#start").hidden;
    if (!$("#start").hidden) $("#run-load").focus();
  };
  $("#open-health").onclick = () => $("#health").showModal();
  $("#close-health").onclick = () => $("#health").close();
  $("#start").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!/^[0-9a-f]{40}$/.test($("#run-commit").value)) { $("#run-msg").textContent = "커밋 목록을 불러온 뒤 커밋을 고르세요"; return; }
    const r = await fetch("/api/pipeline/runs", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo: $("#run-repo").value.trim().replace(/\.git$|\/$/g, ""), commit: $("#run-commit").value, branch: $("#run-branch").value || "", cutover: $("#run-cutover").checked, release_target: $("#run-release").value || null }) });
    const body = await r.json().catch(() => ({}));
    $("#run-msg").textContent = r.ok ? "시작했습니다" : "거부: " + (body.detail || r.status);
    if (r.ok) { current = body.run_id; sel = null; lastKey = ""; followOn(true); $("#start").hidden = true; await loadRuns(); await loadRun(); }
  });

  async function poll() {
    try { await loadRuns(); await loadRun(); } catch (e) { /* 다음 주기에 다시 */ }
    setTimeout(poll, board && !board.outcome ? 2000 : 8000);
  }
  new ResizeObserver(() => { lastKey = ""; }).observe(stageEl);
  fitWeb(); Object.assign(cam, tgt);
  requestAnimationFrame(frame);
  loadHealth(); setInterval(loadHealth, 15000);
  poll();
})();
