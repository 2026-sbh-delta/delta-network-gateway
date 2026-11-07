// Reviewed incident knowledge. No model calls or pipeline decisions originate here.
(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const dialog = $("#cases"), detail = $("#case-detail");
  const outcomes = { success: "재검사 성공", failure: "재검사 실패", unknown: "결과 미확인", stopped: "중단" };
  const decisions = { approved: "검색 승인", rejected: "검색 제외" };
  let rows = [], current = null, offset = 0, next = null, busy = false, dirty = false, generation = 0;
  const message = (text, error = false) => { $("#cases-message").textContent = text; $("#cases-message").classList.toggle("error", error); };
  async function api(path = "", body) {
    const response = await fetch("/api/case-reviews" + path, { credentials: "same-origin", cache: "no-store",
      ...(body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const error = new Error(typeof data.detail === "string" ? data.detail : "요청을 처리하지 못했습니다");
      error.status = response.status;
      throw error;
    }
    return response;
  }
  function controls() {
    $("#cases-refresh").disabled = busy;
    $("#cases-prev").disabled = busy || offset === 0;
    $("#cases-next").disabled = busy || next === null;
    document.querySelectorAll(".case-row").forEach((el) => { el.disabled = busy; });
    const save = $("#case-save"), download = $("#case-export");
    if (save) save.disabled = busy;
    if (download) download.disabled = busy || dirty || !current?.exportable;
  }
  function listing() {
    $("#cases-list").innerHTML = rows.length ? rows.map((row) => `<button type="button" class="case-row ${current?.case_id === row.case_id ? "selected" : ""}" data-case="${esc(row.case_id)}" aria-pressed="${current?.case_id === row.case_id}">
      <span class="case-row-top"><strong>${esc(row.service_id)}</strong><span class="case-badge ${esc(row.outcome)}">${esc(outcomes[row.outcome] || row.outcome)}</span></span>
      <span class="case-row-summary">${esc(row.summary)}</span><span class="case-row-meta">${esc(row.environment || "환경 미확인")} · ${esc(row.node_id)} · ${esc(row.review ? decisions[row.review.decision] : "검토 대기")}</span>
      <span class="case-id mono">${esc(row.case_id)}</span></button>`).join("") : '<p class="case-empty">아직 기록된 사례가 없습니다. 장애 보고서가 저장되면 여기에 표시됩니다.</p>';
    $("#cases-page").textContent = rows.length ? `${offset + 1}–${offset + rows.length}` : "0건";
    controls();
  }
  async function loadList(page = 0) {
    const ticket = ++generation;
    busy = true; current = null; dirty = false; controls(); message("사례를 불러오는 중…");
    $("#cases-list").setAttribute("aria-busy", "true");
    detail.innerHTML = '<p class="case-empty">목록에서 검토할 사례를 선택하세요.</p>';
    try {
      const data = await (await api(`?limit=30&offset=${page}`)).json();
      if (ticket !== generation || !dialog.open) return;
      rows = data.items; offset = data.offset; next = data.next_offset; listing(); message("");
    } catch (error) {
      if (ticket !== generation || !dialog.open) return;
      rows = []; next = null; offset = 0; listing(); message(error.message, true);
    } finally {
      if (ticket === generation) { busy = false; controls(); $("#cases-list").setAttribute("aria-busy", "false"); }
    }
  }
  function render(item) {
    current = item; dirty = false;
    const c = item.case, review = item.review;
    const lines = (values) => esc((values || []).join("\n"));
    detail.innerHTML = `<div class="case-detail-title"><span class="case-badge ${esc(item.outcome)}">${esc(outcomes[item.outcome])}</span><h3>${esc(item.service_id)} <span class="hint">/ ${esc(item.node_id)}</span></h3><p>${esc(item.summary)}</p></div>
      <dl class="case-facts"><div><dt>사례</dt><dd class="mono">${esc(item.case_id)}</dd></div><div><dt>환경 · 조치</dt><dd>${esc(item.environment || "미확인")} · ${esc(c.resolution.action)} · ${esc(c.resolution.attempt)}회</dd></div><div><dt>검증 범위</dt><dd>${esc(c.resolution.scope)}</dd></div><div><dt>버전</dt><dd class="mono" title="${esc(item.revision)}">${esc(item.revision.slice(0, 16))}… · 순서 ${esc(item.sequence)}</dd></div></dl>
      <section class="case-evidence"><h4>재검사 근거</h4>${c.resolution.checks.length ? c.resolution.checks.map((check) => `<article><strong>${esc(check.check)} · ${esc(check.status)}</strong><p class="mono">${esc(check.reference)}</p>${check.details ? `<pre>${esc(check.details)}</pre>` : ""}<details><summary>근거 해시</summary><code>${esc(check.sha256)}</code></details></article>`).join("") : '<p class="hint">완료된 재검사 근거가 없습니다. 검색용으로 승인할 수 없습니다.</p>'}
      ${c.resolution.limitations.map((text) => `<p class="hint">${esc(text)}</p>`).join("")}</section>
      <details class="case-source"><summary>원래 진단 · 관측 · 버전 이력</summary><pre>${esc(JSON.stringify({ request: c.request, report: c.report, report_sha256: c.report_sha256, versions: item.versions }, null, 2))}</pre>${item.versions_truncated ? '<p class="hint">최근 20개 버전만 표시합니다.</p>' : ""}</details>
      <form id="case-review-form" class="case-review-form">
        <div class="case-section-title"><h4>검토 기록</h4><span class="case-badge">${esc(review ? decisions[review.decision] : "검토 대기")}</span></div>
        <label>검토자<input id="case-reviewer" name="reviewer" required maxlength="128" pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,127}" placeholder="예: yeonjae" value="${esc(review?.reviewer || "")}"><small>공유 관리자 계정의 감사 표기입니다. 영문·숫자·점·밑줄·하이픈을 사용하세요.</small></label>
        <label>검토 근거<textarea id="case-reason" name="reason" required maxlength="4000" rows="3" placeholder="어떤 재검사 결과를 확인했는지 작성하세요.">${esc(review?.reason || "")}</textarea></label>
        <div class="case-label-grid"><label>기대 확인 항목<textarea id="case-required" rows="3" placeholder="한 줄에 한 항목\n예: HTTP 503">${lines(review?.required_terms)}</textarea><small>평가셋 추출에 필요합니다. 모델 답변을 복사하지 않고 검토자가 작성합니다.</small></label>
        <label>금지 단정<textarea id="case-forbidden" rows="3" placeholder="한 줄에 한 항목\n예: 전체 이전 완료">${lines(review?.forbidden_terms)}</textarea><small>각 목록은 최대 20개, 항목당 200자입니다.</small></label></div>
        <label>검토 결정<select id="case-decision"><option value="approved" ${!item.approvable ? "disabled" : ""}>검색 승인</option><option value="rejected">거절 · 승인 취소</option></select></label>
        ${!item.approvable ? '<p class="case-warning">검증된 성공·실패와 환경 정보가 있어야 승인할 수 있습니다.</p>' : ""}
        <label class="case-confirm"><input id="case-confirm" type="checkbox"> 원본 진단과 재검사 근거를 확인했습니다.</label>
        <div class="case-actions"><button class="btn primary" id="case-save" type="submit">검토 저장</button><button class="btn" id="case-export" type="button">평가셋 JSON 다운로드</button></div>
        <p class="hint" id="case-export-hint">승인과 기대 확인 항목을 저장하면 현재 버전을 추출할 수 있습니다. 실제 모델 평가는 실행하지 않습니다.</p>
      </form>
      <details class="case-audit"><summary>검토 이력 (${item.review_events.length}${item.review_events_truncated ? "+" : ""})</summary>${item.review_events.length ? item.review_events.map((event) => `<article><strong>${esc(decisions[event.review.decision])} · ${esc(event.review.reviewer)}</strong><p>${esc(event.review.reason)}</p><small class="hint">${esc(event.created_at)} · ${esc(event.revision.slice(0, 12))}</small></article>`).join("") : '<p class="hint">저장된 검토가 없습니다.</p>'}</details>`;
    $("#case-decision").value = review?.decision || (item.approvable ? "approved" : "rejected");
    $("#case-confirm").required = $("#case-decision").value === "approved";
    $("#case-review-form").oninput = () => {
      dirty = true; $("#case-confirm").required = $("#case-decision").value === "approved";
      $("#case-export-hint").textContent = "변경사항을 저장한 뒤 평가셋을 내려받으세요."; controls();
    };
    $("#case-review-form").onsubmit = save;
    $("#case-export").onclick = download;
    rows = rows.map((row) => row.case_id === item.case_id ? item : row); listing(); controls();
  }
  async function select(id) {
    const ticket = ++generation;
    busy = true; current = null; controls(); message("사례 근거를 불러오는 중…"); detail.setAttribute("aria-busy", "true");
    detail.innerHTML = '<p class="case-empty">진단과 재검사 근거를 확인하는 중…</p>';
    try {
      const item = await (await api("/" + encodeURIComponent(id))).json();
      if (ticket !== generation || !dialog.open) return;
      render(item); message("");
    } catch (error) { if (ticket === generation && dialog.open) message(error.message, true); }
    finally { if (ticket === generation) { busy = false; detail.setAttribute("aria-busy", "false"); controls(); } }
  }
  function terms(id) {
    const values = $(id).value.split(/\r?\n/).map((v) => v.trim()).filter(Boolean);
    if (values.length > 20 || values.some((v) => v.length > 200)) throw new Error("각 목록은 최대 20개, 항목당 200자로 작성하세요.");
    return [...new Set(values)];
  }
  async function save(event) {
    event.preventDefault(); if (busy || !current) return;
    const item = current, ticket = generation;
    try {
      const review = { reviewer: $("#case-reviewer").value.trim(), reason: $("#case-reason").value.trim(), decision: $("#case-decision").value,
        required_terms: terms("#case-required"), forbidden_terms: terms("#case-forbidden") };
      busy = true; controls(); message("검토 기록을 저장하는 중…");
      await api(`/${encodeURIComponent(item.case_id)}/review`, { revision: item.revision, review });
      if (ticket !== generation || !dialog.open) return;
      const latest = await (await api("/" + encodeURIComponent(item.case_id))).json();
      if (ticket !== generation || !dialog.open) return;
      render(latest); message("검토 기록을 저장했습니다.");
    } catch (error) {
      if (ticket !== generation || !dialog.open) return;
      if (error.status === 409) { await select(item.case_id); message("사례 상태가 바뀌었거나 승인 조건이 충족되지 않았습니다. 최신 근거를 다시 확인하세요.", true); }
      else message(error.message, true);
    } finally { if (ticket === generation) { busy = false; controls(); } }
  }
  async function download() {
    if (busy || dirty || !current?.exportable) return;
    const item = current, ticket = generation;
    busy = true; controls(); message("평가셋을 추출하는 중…");
    try {
      const response = await api(`/${encodeURIComponent(item.case_id)}/export`, { revision: item.revision });
      const blob = await response.blob();
      if (ticket !== generation || !dialog.open) return;
      const link = document.createElement("a"), objectUrl = URL.createObjectURL(blob);
      link.href = objectUrl; link.download = `evaluation-${item.case_id}.json`; link.click();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 30000); message("평가셋 JSON을 내려받았습니다.");
    } catch (error) { if (ticket === generation && dialog.open) message(error.message, true); }
    finally { if (ticket === generation) { busy = false; controls(); } }
  }
  $("#open-cases").onclick = () => { dialog.showModal(); loadList(); };
  $("#close-cases").onclick = () => dialog.close();
  dialog.addEventListener("close", () => { generation++; busy = false; current = null; });
  $("#cases-refresh").onclick = () => loadList(offset);
  $("#cases-prev").onclick = () => loadList(Math.max(0, offset - 30));
  $("#cases-next").onclick = () => next !== null && loadList(next);
  $("#cases-list").onclick = (event) => { const row = event.target.closest("[data-case]"); if (row && !busy) select(row.dataset.case); };
})();
