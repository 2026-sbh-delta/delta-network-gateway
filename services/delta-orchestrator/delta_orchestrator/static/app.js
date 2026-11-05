(() => {
  const q = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const LABEL = {up:'정상', placeholder:'임시 서버', down:'응답 없음', error:'오류', unknown:'확인 불가'};
  const DEPLOY = {ready:'준비됨', deploying:'배포 중', failed:'실패', stopped:'내림'};
  const RUN = {done:'완료', running:'점검 중', error:'오류', queued:'대기'};
  if (location.protocol === 'https:') q('logout').hidden = false;

  function services(list) {
    q('services').innerHTML = list.map(s => {
      const st = s.state === 'up' && s.placeholder ? 'placeholder' : (s.state || 'unknown');
      const meta = s.ms != null ? s.ms + 'ms' : (s.error ? esc(s.error) : '');
      return `<div class="card ${st}"><div class="name">${esc(s.label)}<span class="tag ${st}">${LABEL[st] || st}</span></div>
        <div class="role">${esc(s.role)}</div><div class="meta">${meta}</div></div>`;
    }).join('');
    const up = list.filter(s => s.state === 'up' && !s.placeholder).length;
    const ph = list.filter(s => s.state === 'up' && s.placeholder).length;
    const bad = list.length - up - ph;
    q('summary').innerHTML = `<span class="pill">정상 <b>${up}</b></span><span class="pill">임시 <b>${ph}</b></span>` +
      `<span class="pill">문제 <b>${bad}</b></span>`;
  }

  function staging(s) {
    if (s.error) { q('staging').innerHTML = `<p class="empty">조회 실패: ${esc(s.error)}</p>`; return; }
    const targets = (s.targets || []).map(t => `<div class="row"><span>${esc(t.cloud.toUpperCase())}</span>
      <span>${t.url ? `<a href="${esc(t.url)}" target="_blank" rel="noopener noreferrer">${esc(t.url)}</a>` : `주소 없음 · ${esc(t.error)}`}</span></div>`).join('');
    const d = (s.recent || [])[0];
    const last = d ? `<div class="row"><span>최근 배포</span><span>${esc(d.run_id)} · <b>${DEPLOY[d.state] || esc(d.state)}</b></span></div>
      <div class="row"><span>이미지</span><span><code>${esc((d.image_digest || '').slice(0, 19))}</code></span></div>
      <div class="row"><span>스캐너 허용 IP</span><span><code>${esc(d.scanner_ip || '-')}</code></span></div>
      ${d.error ? `<div class="row"><span>오류</span><span>${esc(d.error)}</span></div>` : ''}` : '<p class="empty">배포 기록 없음</p>';
    q('staging').innerHTML = targets + last;
  }

  function jenkins(j) {
    if (j.error) { q('jenkins').innerHTML = `<p class="empty">조회 실패: ${esc(j.error)}</p>`; return; }
    const agents = (j.agents || []).map(a => `<div class="row"><span>에이전트</span><span>${esc(a.name)} · <b>${a.online ? '연결됨' : '끊김'}</b></span></div>`).join('')
      || '<div class="row"><span>에이전트</span><span>없음</span></div>';
    const b = j.last_build;
    const build = b ? `<div class="row"><span>최근 빌드</span><span>#${esc(b.number)} · <b>${b.building ? '진행 중' : esc(b.result)}</b> · ${new Date(b.timestamp).toLocaleString('ko-KR')}</span></div>`
      : '<div class="row"><span>최근 빌드</span><span>없음</span></div>';
    q('jenkins').innerHTML = agents + build;
  }

  function dast(d) {
    if (d.error) { q('dast').innerHTML = `<p class="empty">조회 실패: ${esc(d.error)}</p>`; return; }
    const runs = d.recent || [];
    if (!runs.length) { q('dast').innerHTML = '<p class="empty">점검 기록 없음</p>'; return; }
    q('dast').innerHTML = `<table><thead><tr><th>회차</th><th>상태</th><th>재현</th><th>차단</th><th>안전</th><th>판정 불가</th><th>소요</th></tr></thead><tbody>` +
      runs.map(r => { const c = r.counts || {}; return `<tr><td><code>${esc((r.run_id || '').slice(0, 8))}</code></td>
        <td>${RUN[r.status] || esc(r.status)}${r.error_kind ? ' · ' + esc(r.error_kind) : ''}</td>
        <td>${c.REPRODUCED ?? 0}</td><td>${c.BLOCKED ?? 0}</td><td>${c.NOT_REPRODUCED ?? 0}</td><td>${c.INCONCLUSIVE ?? 0}</td>
        <td>${r.duration_ms ? Math.round(r.duration_ms / 1000) + '초' : '-'}</td></tr>`; }).join('') + '</tbody></table>';
  }

  function urls(u, nat) {
    const names = {orchestrator: '플랫폼(이 화면)', dast: 'DAST 작업실', jenkins: 'Jenkins 웹훅', log: '로그 수집'};
    q('urls').innerHTML = Object.entries(u || {}).map(([k, v]) => `<div class="row"><span>${names[k] || esc(k)}</span><span><a href="${esc(v)}" target="_blank" rel="noopener noreferrer">${esc(v)}</a></span></div>`).join('') +
      `<div class="row"><span>출구 고정 IP</span><span><code>${esc(nat || '-')}</code></span></div>`;
    if (u && u.dast) q('dast-link').href = u.dast;
  }

  const NODE_STATE = {pending:'대기', running:'진행 중', waiting:'확인 필요', pass:'완료', fail:'실패', skipped:'건너뜀'};
  const OUTCOME = {verified:'검증 완료', stopped:'멈춤'};

  async function pipeline() {
    try {
      const list = await (await fetch('/api/pipeline/runs')).json();
      const latest = (list.runs || [])[0];
      if (!latest) { q('pipeline').innerHTML = '<p class="empty">실행 기록 없음</p>'; return; }
      const run = await (await fetch('/api/pipeline/runs/' + encodeURIComponent(latest.run_id))).json();
      const opt = run.options || {};
      const head = `<div class="row"><span>최근 실행</span><span><code>${esc(run.run_id.slice(0, 8))}</code> · <code>${esc((opt.commit || '').slice(0, 7))}</code> · <b>${run.outcome ? (OUTCOME[run.outcome] || esc(run.outcome)) : '진행 중'}</b></span></div>`;
      const nodes = run.nodes.map(n => {
        const ask = n.decision ? `<div class="row"><span></span><span>${esc(n.decision.prompt)} ` +
          n.decision.options.map(o => `<button type="button" data-answer="${esc(o.value)}" data-run="${esc(run.run_id)}">${esc(o.label)}</button>`).join(' ') + '</span></div>' : '';
        return `<div class="row"><span>${esc(n.label)}</span><span><b>${NODE_STATE[n.state] || esc(n.state)}</b>${n.summary ? ' · ' + esc(n.summary) : ''}</span></div>` + ask;
      }).join('');
      q('pipeline').innerHTML = head + nodes;
    } catch (err) { q('pipeline').innerHTML = '<p class="empty">파이프라인 상태를 읽지 못했습니다</p>'; }
  }

  q('pipeline').addEventListener('click', async ev => {
    const b = ev.target.closest('button[data-answer]');
    if (!b) return;
    b.disabled = true;
    await fetch('/api/pipeline/runs/' + encodeURIComponent(b.dataset.run) + '/answer', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({value: b.dataset.answer})});
    pipeline();
  });

  q('run-form').addEventListener('submit', async ev => {
    ev.preventDefault();
    const r = await fetch('/api/pipeline/runs', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({repo: q('run-repo').value.trim(), commit: q('run-commit').value.trim()})});
    const body = await r.json().catch(() => ({}));
    q('run-msg').textContent = r.ok ? '시작: ' + body.run_id : '거부: ' + (body.detail || r.status);
    pipeline();
  });

  async function refresh() {
    pipeline();
    try {
      const r = await fetch('/api/status');
      if (r.status === 401) { location.href = '/login'; return; }
      const s = await r.json();
      services(s.services); staging(s.staging); jenkins(s.jenkins); dast(s.dast); urls(s.public_urls, s.nat_ip);
      q('checked').textContent = '마지막 확인 ' + new Date(s.checked_at).toLocaleTimeString('ko-KR');
    } catch (err) { q('checked').textContent = '상태를 읽지 못했습니다'; }
  }
  refresh();
  setInterval(refresh, 10000);
})();
