/* Render only the saved assessment. No price calculation or credentials in the browser. */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
  const amount = (n) => typeof n === "number" && Number.isFinite(n) ? `$${n.toFixed(2)}` : "산정 불가";
  const change = (n) => typeof n !== "number" || !Number.isFinite(n) ? "산정 불가"
    : n === 0 ? "$0.00 · 동일" : `$${Math.abs(n).toFixed(2)} ${n > 0 ? "증가" : "감소"}`;
  const provider = (p) => ({aws:"AWS", gcp:"GCP", onprem:"온프레미스"}[p] || "출처 미확인");

  function render(d = {}) {
    if (d.error) return `<p class="mapping-error" role="alert">${esc(d.error)} · 가격 산정 불가. 설정을 확인한 뒤 새 실행으로 다시 시도하세요.</p>`;
    if (!d.mapping) return '<p class="hint">매핑 완료 후 이 실행의 월 예상 비용을 표시합니다.</p>';
    const m = d.mapping, cost = m.cost;
    const verdict = {pass:"통과", fix:"수정 필요", needs_human:"사람 확인 필요"}[d.verdict] || "미확인";
    const rows = (cost?.lines || []).map((r) => `<tr><th scope="row">${esc(r.server)}${r.notes?.length ? `<small>${r.notes.map(esc).join(" · ")}</small>` : ""}</th>
      <td>${esc(r.source_type || "미확인")}</td><td>${esc(r.target_type || "미확인")}</td><td>${amount(r.source_monthly)}</td><td>${amount(r.target_monthly)}</td><td>${change(r.difference)}</td></tr>`).join("");
    const table = cost ? `<p class="mapping-basis">${esc(provider(d.source_provider))} (${esc(cost.source_region)}) → ${esc(provider(d.target_provider))} (${esc(cost.target_region)})<br>
      월 ${esc(cost.hours_per_month)}시간 · ${esc(cost.currency)} · 단가 기준 ${esc(cost.priced_on)}</p>
      <div class="mapping-scroll" tabindex="0" role="region" aria-label="서버별 월 예상 비용 비교"><table class="res mapping-cost"><caption>서버 월 예상 비용 · 저장된 단가 기준</caption>
      <thead><tr><th scope="col">서버</th><th scope="col">출발 타입</th><th scope="col">대상 타입</th><th scope="col">출발 월 비용</th><th scope="col">대상 월 비용</th><th scope="col">증감</th></tr></thead>
      <tbody>${rows}</tbody><tfoot><tr><th scope="row" colspan="3">합계</th><td>${amount(cost.source_total)}</td><td>${amount(cost.target_total)}</td><td>${change(cost.difference_total)}</td></tr></tfoot></table></div>
      <p class="hint">실시간 가격·실제 청구액이 아닌 VM 예상 비용입니다. 일부 단가가 없으면 해당 금액과 합계는 산정 불가로 표시합니다.</p>
      <p class="hint">제외: ${(cost.excludes || []).map(esc).join(" · ")}</p>`
      : '<p class="hint">가격 산정 불가 · 이 매핑에는 비교 가능한 서버 비용이 없습니다.</p>';
    const mapped = (m.rows || []).map((r) => `<tr><td>${esc(r.resource)}</td><td>${esc(r.source)}</td><td>${esc(r.target || "미확인")}</td><td>${esc(r.grade)}</td></tr>`).join("");
    return `<section class="mapping-result"><h3>클라우드 가격 비교</h3>${table}
      <p>매핑 판정: <strong>${esc(verdict)}</strong></p><p class="hint">가격은 참고 자료이며 인프라 적용이나 배포 승인을 대신하지 않습니다.</p>
      ${m.assumptions?.length ? `<p class="hint">가정: ${m.assumptions.map(esc).join(" · ")}</p>` : ""}
      ${mapped ? `<details><summary>자원 매핑 ${m.rows.length}건</summary><div class="mapping-scroll"><table class="res"><thead><tr><th>자원</th><th>출발</th><th>대상</th><th>등급</th></tr></thead><tbody>${mapped}</tbody></table></div></details>` : ""}</section>`;
  }

  async function readRequest(file) {
    if (!file) return null;
    if (file.size > 60_000) throw new Error("매핑 JSON은 60KB 이하여야 합니다");
    let value;
    try { value = JSON.parse(await file.text()); } catch (_) { throw new Error("매핑 파일은 유효한 JSON이어야 합니다"); }
    if (value?.kind !== "migration_mapping" || !value?.evidence?.mapping) throw new Error("Terraform 원본 state 대신 추출기의 migration_mapping 요청 JSON을 선택하세요");
    return value;
  }
  window.DeltaMapping = {render, readRequest};
})();
