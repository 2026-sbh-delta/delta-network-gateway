(() => {
  const q = id => document.getElementById(id);
  const labels = {PENDING:'',RUNNING:'점검 중',REPRODUCED:'취약',NOT_REPRODUCED:'안전',BLOCKED:'차단',INCONCLUSIVE:'불가'};
  let config, report, sample, stream, timer, started = 0, currentRun, viewing, projectId, projectList=[], latestFrame = {width:960,height:640}, cursorPoint, busy = false;
  const cards = new Map();
  function modelState(model) {
    if(!model.credential_present) return ['인증 필요','unavailable'];
    if(model.state==='available') return ['사용 가능','available'];
    if(model.state==='failed') return ['호출 실패','failed'];
    return ['자격증명 설정 · 미검증','unknown'];
  }
  function renderModels(models) {
    const grid=q('model-status-grid');grid.replaceChildren();
    (models || []).forEach(model=>{
      const [label,state]=modelState(model);
      const card=document.createElement('article');card.className='model-card';card.dataset.state=state;
      const head=document.createElement('div');head.className='model-card-head';
      const title=document.createElement('strong');title.textContent=model.label;
      const badge=document.createElement('span');badge.className='model-state';badge.textContent=label;
      head.append(title,badge);
      const name=document.createElement('code');name.textContent=model.model;
      const details=document.createElement('dl');
      const keyText=model.key_present?(model.state==='available'?'있음 · 실호출 확인':'있음 · 미검증'):'없음';
      const sessionText=model.login_present?(model.login_scope || '별도 CLI 세션')+' · '+(model.login_method || '확인됨'):'없음';
      const rows=[
        ['API 키',keyText],
        ['별도 로그인',sessionText],
        ...(Object.hasOwn(model,'oauth_present')?[['ChatGPT OAuth',model.oauth_present?(model.state==='available'?'실호출 확인':'토큰 있음 · 미검증'):'없음']]:[]),
        ['마지막 확인',model.checked_at?(model.detail || model.state):'실제 호출 기록 없음']
      ];
      rows.forEach(([term,value])=>{const dt=document.createElement('dt');dt.textContent=term;const dd=document.createElement('dd');dd.textContent=value;details.append(dt,dd);});
      card.append(head,name,details);grid.append(card);
    });
  }
  async function refreshModels() {
    const button=q('refresh-models');button.disabled=true;button.textContent='확인 중';
    try{const response=await fetch('/api/models');if(!response.ok)throw new Error('모델 상태를 읽지 못했습니다.');const data=await response.json();renderModels(data.models);}
    catch(err){q('model-status-grid').textContent=err.message;}
    finally{button.disabled=false;button.textContent='새로고침';}
  }
  // 취약점마다 화면·커서·로그·경고를 따로 보관하고, 선택한 항목만 화면에 그린다.
  const views=new Map();let allLogs=[],selected=null,pinned=false;
  function viewOf(id){if(!views.has(id))views.set(id,{frame:null,cursor:null,alert:false,activity:''});return views.get(id);}
  function logItem(entry) {
    const li = document.createElement('li');
    const time = document.createElement('time');time.textContent = entry.time;
    li.append(time, document.createTextNode(entry.text));
    li.dataset.kind=entry.text.startsWith('💥')?'exec':entry.text.startsWith('🧪')?'probe':entry.text.startsWith('🤖')?'agent':'';
    return li;
  }
  const visible=entry=>!entry.fid || entry.fid===selected;
  function log(message,fid) {
    const entry={time:new Date().toLocaleTimeString('en-GB',{hour12:false}),text:String(message),fid:fid || null};
    allLogs.push(entry);if(allLogs.length>600) allLogs.shift();
    if(!visible(entry)) return;
    q('logs').append(logItem(entry));
    while(q('logs').children.length > 200) q('logs').firstChild.remove();
    q('logs').scrollTop = q('logs').scrollHeight;
  }
  function select(id,byUser) {
    selected=id;if(byUser!==undefined) pinned=byUser;
    cards.forEach((card,key)=>card.classList.toggle('selected',key===id));
    const v=id?viewOf(id):null;
    if(v && v.frame){q('screen').src=v.frame;q('screen').hidden=false;q('empty').hidden=true;}
    else{q('screen').hidden=true;q('empty').hidden=false;}
    cursorPoint=v && v.cursor;q('cursor').hidden=!cursorPoint;if(cursorPoint) renderCursor(true);
    q('execution-alert').hidden=!(v && v.alert);
    if(v && v.activity) q('activity').textContent=v.activity;
    const card=id && cards.get(id);
    q('url-label').textContent=card?(pinned?'📌 ':'▶ ')+card.querySelector('.qi-body strong').textContent:'허용된 스테이징만 접근';
    q('logs').replaceChildren(...allLogs.filter(visible).slice(-200).map(logItem));
    if(id && cards.get(id) && FINAL.includes(cards.get(id).dataset.state)) showFixFor(id);
    q('logs').scrollTop=q('logs').scrollHeight;
  }
  const FINAL=['REPRODUCED','NOT_REPRODUCED','BLOCKED','INCONCLUSIVE'];
  const STAGES=[['analyze','분석'],['explore','탐색'],['reflect','필터링'],['detect','탐지'],['judge','판정'],['impact','영향']];
  const STAGE_LABEL=Object.fromEntries(STAGES);
  // 상세 보기: 6개 단계를 원형으로 나눠 현재 단계가 돌아가며 보이게 한다.
  const RING_R=46,RING_C=2*Math.PI*RING_R,SEG=RING_C/6;
  function buildDetail() {
    const wrap=document.createElement('div');wrap.className='qi-detail';
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');
    svg.setAttribute('viewBox','0 0 120 120');svg.setAttribute('class','ring');
    STAGES.forEach(([key],i)=>{
      const c=document.createElementNS(ns,'circle');
      Object.entries({cx:60,cy:60,r:RING_R,'stroke-dasharray':`${SEG-5} ${RING_C-SEG+5}`,'stroke-dashoffset':-i*SEG,class:'seg'}).forEach(([k,v])=>c.setAttribute(k,v));
      c.dataset.stage=key;c.dataset.s='pending';svg.append(c);
    });
    const spin=document.createElementNS(ns,'circle');
    Object.entries({cx:60,cy:60,r:55,'stroke-dasharray':`${2*Math.PI*55*0.18} ${2*Math.PI*55}`,class:'spin'}).forEach(([k,v])=>spin.setAttribute(k,v));
    svg.append(spin);
    const center=document.createElement('div');center.className='ring-center';
    center.innerHTML='<strong>대기</strong><small>0 / 6</small>';
    const ringBox=document.createElement('div');ringBox.className='ring-box';ringBox.append(svg,center);
    const list=document.createElement('ol');list.className='stage-list';
    STAGES.forEach(([key,label])=>{const li=document.createElement('li');li.dataset.stage=key;li.dataset.s='pending';li.innerHTML='<b></b><span></span>';li.querySelector('b').textContent=label;list.append(li);});
    const foot=document.createElement('div');foot.className='qi-foot';
    foot.innerHTML='<code class="agent-sid">에이전트 세션 —</code><span class="foot-btns"><button type="button" class="fix" hidden>수정안 만들기</button><button type="button" class="reverify" hidden>같은 에이전트로 재검증</button></span>';
    wrap.append(ringBox,list,foot);return wrap;
  }
  function renderRing(card) {
    const segs=[...card.querySelectorAll('.ring .seg')];
    const done=segs.filter(s=>['done','skipped','failed'].includes(s.dataset.s)).length;
    const running=segs.find(s=>s.dataset.s==='running');
    const current=running || [...segs].reverse().find(s=>s.dataset.s!=='pending');
    const center=card.querySelector('.ring-center');
    center.querySelector('strong').textContent=current?STAGE_LABEL[current.dataset.stage]:'대기';
    center.querySelector('small').textContent=(running?'진행 중 · ':'')+done+' / 6';
    const spin=card.querySelector('.ring .spin');
    // 진행 중인 단계 위치에서 바깥 고리가 돈다.
    const index=running?STAGES.findIndex(([k])=>k===running.dataset.stage):-1;
    spin.style.setProperty('--start',(index*60)+'deg');spin.classList.toggle('on',index>=0);
  }
  function setStage(id,stage,status,detail) {
    const card=cards.get(id);if(!card) return;
    card.querySelectorAll(`[data-stage="${stage}"]`).forEach(el=>{el.dataset.s=status;if(detail) el.title=detail;});
    const li=card.querySelector(`.stage-list [data-stage="${stage}"] span`);if(li && detail) li.textContent=detail;
    renderRing(card);
  }
  function updateQueue() {
    const items=[...cards.values()];
    const finished=items.filter(c=>FINAL.includes(c.dataset.state)).length;
    q('queue-progress').textContent=finished+' / '+items.length;
    q('queue-bar-fill').style.width=(items.length?finished/items.length*100:0)+'%';
    const count=state=>items.filter(c=>c.dataset.state===state).length;
    const kpi={'kpi-total':items.length,'kpi-running':count('RUNNING'),'kpi-vuln':count('REPRODUCED'),
      'kpi-safe':count('NOT_REPRODUCED')+count('BLOCKED'),'kpi-unknown':count('INCONCLUSIVE')};
    Object.entries(kpi).forEach(([id,value])=>{if(q(id))q(id).textContent=value;});
    // 진행 중 다음 항목은 대기 항목보다 조금 밝게 보인다.
    const running=items.findIndex(c=>c.dataset.state==='RUNNING');
    items.forEach((c,i)=>c.classList.toggle('next',running>=0 && i===running+1 && c.dataset.state==='PENDING'));
  }
  function focusItem(card) {
    const list=q('findings');
    list.scrollTo({top:card.offsetTop-list.offsetTop-list.clientHeight/2+card.offsetHeight/2,behavior:'smooth'});
  }
  function setStatus(id,status,message) {
    status=String(status).toUpperCase();
    const card=cards.get(id);
    if(!card) return;
    const changed=card.dataset.state!==status;
    card.dataset.state=status;
    card.querySelector('.qi-badge').textContent=labels[status] ?? status;
    if(message) card.title=message;
    if(changed && status==='RUNNING' && !pinned) focusItem(card);
    updateQueue();
  }
  const MODES={agent:'AI 에이전트 · Agent SDK',computer_use:'AI 에이전트 · Computer Use',claude:'Claude 매핑',reviewed:'수동 검토'};
  function modeLabel(value) {
    return value==='reviewed'?MODES.reviewed:MODES[value]+' · '+((config&&config.claude_model) || '모델 지정');
  }
  const ACTIONS={plan:'가설',type_marker:'필터링 확인 표식',type_canary:'검사 문자열',type_impact:'영향 시연',think:'생각',click:'클릭',left_click:'클릭',double_click:'더블클릭',triple_click:'트리플클릭',type:'입력',key:'키',type_text:'입력',type_probe:'검사 문자열 입력',press_key:'키',scroll:'스크롤',navigate:'이동',new_session:'새 세션',finish:'종료'};
  function setMapping(id,mapping) {
    const card=cards.get(id);if(!card || !mapping) return;
    card.dataset.mode=mapping.planner_mode || '';
  }
  function renderFindings() {
    cards.clear();views.clear();selected=null;pinned=false;q('findings').replaceChildren();
    const findings=Array.isArray(report.findings)?report.findings:[];
    findings.forEach(f=>{
      const id=f.id || f.findingId;
      const card=document.createElement('li');card.className='qi';card.dataset.state='PENDING';
      card.title=f.description || '';
      card.dataset.file=f.source_file || f.codeLocations?.[0]?.filePath || '';card.dataset.line=f.line || f.codeLocations?.[0]?.lineStart || '';
      const icon=document.createElement('span');icon.className='qi-icon';
      const body=document.createElement('div');body.className='qi-body';
      const title=document.createElement('strong');title.textContent=f.title || f.name || id;
      const where=document.createElement('small');
      where.textContent=(f.cwe || (f.riskType==='CROSS_SITE_SCRIPTING'?'CWE-79':f.riskType) || '—')+' · '+(f.source_file || f.codeLocations?.[0]?.filePath || '?')+':'+(f.line || f.codeLocations?.[0]?.lineStart || '?');
      body.append(title,where);
      const badge=document.createElement('span');badge.className='qi-badge';
      const track=document.createElement('div');track.className='qi-stages';
      STAGES.forEach(([key,label])=>{const st=document.createElement('span');st.dataset.stage=key;st.dataset.s='pending';st.textContent=label;track.append(st);});
      body.append(track);
      const more=document.createElement('button');more.type='button';more.className='qi-more';more.textContent='상세';
      more.addEventListener('click',ev=>{ev.stopPropagation();card.classList.toggle('open');more.textContent=card.classList.contains('open')?'접기':'상세';});
      const head=document.createElement('div');head.className='qi-head';head.append(icon,body,badge,more);
      card.append(head,buildDetail());q('findings').append(card);cards.set(id,card);viewOf(id);
      card.addEventListener('click',()=>{if(pinned && selected===id){pinned=false;select(id,false);}else select(id,true);});
    });
    q('finding-count').textContent=findings.length+'개';
    q('source-label').textContent='SAST 출처: '+(report.source_label || report.source || '출처 미지정');
    q('findings').scrollTop=0;updateQueue();
  }
  function useReport(value) {
    if(!value || !Array.isArray(value.findings)) throw new Error('findings 배열이 있는 JSON이 필요합니다.');
    report=value;q('report-input').value=JSON.stringify(value,null,2);renderFindings();
  }
  function phase(name,message) {
    const order=['planning','control','saving','revisiting','observing','done'];
    const alias={validate:'planning',plan:'planning',browser:'control',input:'saving',revisit:'revisiting',observe:'observing',opening:'control',typing:'saving',submitting:'saving',persisting:'saving',evidence:'observing',finished:'done',complete:'done'};
    name=alias[name] || name;
    const index=order.indexOf(name);
    if(index>=0) document.querySelectorAll('[data-step]').forEach((el,i)=>{el.classList.toggle('done',i<index || name==='done');el.classList.toggle('active',i===index && name!=='done');});
    if(message) q('activity').textContent=message;
    q('session-status').textContent=name==='done'?'검사 종료':name==='error'?'실행 오류':'검사 중';
  }
  function renderCursor(instant) {
    if(!cursorPoint) return;
    q('cursor').classList.toggle('instant',!!instant);
    const host=q('screen-host');const scale=Math.min(host.clientWidth/latestFrame.width,host.clientHeight/latestFrame.height);
    const left=(host.clientWidth-latestFrame.width*scale)/2+cursorPoint.x*scale;
    const top=(host.clientHeight-latestFrame.height*scale)/2+cursorPoint.y*scale;
    q('cursor').style.left=Math.max(0,Math.min(host.clientWidth-30,left))+'px';
    q('cursor').style.top=Math.max(0,Math.min(host.clientHeight-30,top))+'px';
    q('cursor').hidden=false;
  }
  function setAgentSession(id,evidence) {
    const card=cards.get(id);if(!card || !evidence) return;
    const sid=evidence.agent_session_id;if(!sid) return;
    card.querySelector('.agent-sid').textContent='에이전트 세션 '+sid.slice(0,8)+(evidence.resumed_from?' · 이어받음 '+evidence.resumed_from.slice(0,8):'');
    const button=card.querySelector('.reverify');button.hidden=false;
    button.onclick=async ev=>{
      ev.stopPropagation();button.disabled=true;
      try{
        const response=await fetch('/api/runs/'+encodeURIComponent(currentRun)+'/findings/'+encodeURIComponent(id)+'/reverify',
          {method:'POST',headers:{'Content-Type':'application/json','X-DAST-Token':config.csrf_token},
           body:JSON.stringify({scenario:q('scenario').value,impact:q('impact').checked})});
        const data=await response.json();if(!response.ok) throw new Error(data.detail || '재검증을 시작하지 못했습니다.');
        log('재검증 시작: '+shortId(data.run_id)+' (같은 에이전트 맥락 이어받음)');loadSessions();await view(data.run_id);
      }catch(err){log(err.message);}finally{button.disabled=false;}
    };
  }
  // 조치 권장 코드: 선택한 취약 항목의 "취약한 코드 → AI 제안 코드"를 나란히 보여주고 PR 버튼을 둔다.
  function setFix(id,status) {
    const card=cards.get(id);if(!card) return;
    const button=card.querySelector('.fix');
    button.hidden=!['REPRODUCED','BLOCKED'].includes(status);
    button.textContent='조치 권장 코드';
    button.onclick=ev=>{ev.stopPropagation();select(id,true);q('fix-panel').scrollIntoView({behavior:'smooth',block:'start'});};
  }
  let fixWatch=0;
  async function showFixFor(id,start=false) {
    showChatFor(id);
    const card=cards.get(id);const token=++fixWatch;
    // 항목 점검이 끝나면 전체 점검을 기다리지 않고 바로 코드 확인을 연다(AI 호출 없음).
    if(!card || !currentRun || !FINAL.includes(card.dataset.state)){q('fix-panel').hidden=true;return;}
    const runId=currentRun,url='/api/runs/'+encodeURIComponent(runId)+'/findings/'+encodeURIComponent(id);
    q('fix-panel').hidden=false;q('fix-title').textContent=card.querySelector('.qi-body strong').textContent;
    q('fix-split').replaceChildren();q('fix-summary').replaceChildren();q('fix-pr').hidden=true;q('fix-make').hidden=true;q('fix-result').textContent='';
    renderFlow(id,null);
    const patchable=['REPRODUCED','BLOCKED'].includes(card.dataset.state);
    document.querySelector('#fix-panel .patch').hidden=!patchable;
    if(!patchable) return;
    let state;
    try{state=await (await fetch(url+'/fix')).json();}catch{return;}
    if(start && ['none','failed'].includes(state.status)){
      const r=await fetch(url+'/fix',{method:'POST',headers:{'X-DAST-Token':config.csrf_token}});
      if(!r.ok){q('fix-state').textContent=(await r.json()).detail || '수정안을 시작하지 못했습니다.';return;}
      state={status:'generating'};
    }
    while(state.status==='generating'){
      q('fix-state').textContent='AI가 SAST·DAST 근거로 안전한 코드를 작성하는 중…';
      await new Promise(r=>setTimeout(r,2000));
      if(token!==fixWatch || runId!==currentRun) return;
      state=await (await fetch(url+'/fix')).json();
    }
    if(token!==fixWatch) return;
    if(state.status!=='ready'){
      q('fix-state').textContent=state.status==='failed'?'수정안 생성 실패: '+(state.error || ''):'아직 수정안이 없습니다.';
      q('fix-make').hidden=false;q('fix-make').onclick=()=>showFixFor(id,true);return;
    }
    q('fix-state').textContent='';
    renderFlow(id,state.trace);
    renderSplit(state.patch || '');
    if(state.summary){const p=document.createElement('p');p.textContent=state.summary;q('fix-summary').append(p);}
    (state.changes || []).forEach(c=>{const li=document.createElement('li');li.textContent=c.file+' — '+c.reason;q('fix-summary').append(li);});
    const pr=q('fix-pr');pr.hidden=false;pr.disabled=false;pr.textContent='PR 생성';
    if(state.pr) showPr(state.pr);
    pr.onclick=async()=>{
      if(!confirm('이 수정안으로 브랜치를 커밋하고 PR을 생성할까요?')) return;
      pr.disabled=true;q('fix-result').textContent='PR 생성 중…';
      const r=await fetch(url+'/pr',{method:'POST',headers:{'Content-Type':'application/json','X-DAST-Token':config.csrf_token},body:JSON.stringify({approve:true})});
      const data=await r.json();
      if(!r.ok){q('fix-result').textContent='실패: '+(data.detail || r.status);pr.disabled=false;return;}
      showPr(data);loadVulns();
    };
  }
  function renderSplit(patch) {
    // unified diff를 파일별 좌(현재·취약)/우(AI 제안) 두 칸으로 나눈다.
    const host=q('fix-split');host.replaceChildren();
    let file=null,left=[],right=[],ln=0,rn=0;
    const flush=()=>{
      if(!file) return;
      const box=document.createElement('div');box.className='split-file';
      const head=document.createElement('div');head.className='split-name';head.textContent=file;box.append(head);
      const cols=document.createElement('div');cols.className='split-cols';
      [['취약한 코드 (현재)',left,'old'],['AI 제안 코드 (안전)',right,'new']].forEach(([title,rows,kind])=>{
        const col=document.createElement('div');col.className='split-col '+kind;
        const h=document.createElement('div');h.className='split-title';h.textContent=title;col.append(h);
        rows.forEach(r=>{const row=document.createElement('div');row.className='split-row '+r.t;
          const n=document.createElement('span');n.className='ln';n.textContent=r.n ?? '';
          const c=document.createElement('code');c.textContent=r.s;row.append(n,c);col.append(row);});
        cols.append(col);
      });
      box.append(cols);host.append(box);left=[];right=[];
    };
    const pad=()=>{while(left.length<right.length)left.push({t:'pad',s:''});while(right.length<left.length)right.push({t:'pad',s:''});};
    patch.split('\n').forEach(line=>{
      if(line.startsWith('diff --git')){flush();file=line.split(' b/')[1] || line;return;}
      if(line.startsWith('---') || line.startsWith('+++') || line.startsWith('index ')) return;
      const m=line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if(m){pad();ln=+m[1];rn=+m[2];left.push({t:'hunk',s:'…'});right.push({t:'hunk',s:'…'});return;}
      if(line.startsWith('-')){left.push({t:'del',n:ln++,s:line.slice(1)});return;}
      if(line.startsWith('+')){right.push({t:'add',n:rn++,s:line.slice(1)});return;}
      if(line.startsWith(' ')){pad();left.push({t:'ctx',n:ln++,s:line.slice(1)});right.push({t:'ctx',n:rn++,s:line.slice(1)});}
    });
    pad();flush();
  }
  function showPr(data) {
    const box=q('fix-result');box.replaceChildren();
    if(data.url){const a=document.createElement('a');a.href=data.url;a.target='_blank';a.rel='noopener noreferrer';a.textContent='PR #'+data.number+' 열기 ↗';box.append(a);}
    else box.textContent='로컬 커밋 '+data.commit+' · '+data.branch+' (Git 주소 미설정)';
    q('fix-pr').disabled=true;q('fix-pr').textContent=data.url?'PR 생성됨':'커밋 완료';
  }
  function evidenceShot(id,evidence) {
    // 끝난 점검은 실시간 화면이 없으므로 마지막 증거 화면을 보여준다.
    const shots=(evidence && evidence.screenshots) || [];
    if(!shots.length) return;
    const v=viewOf(id);if(v.frame) return;
    v.frame=shots.find(s=>s.includes('executed')) || shots[shots.length-1];
    if(selected===id){q('screen').src=v.frame;q('screen').hidden=false;q('empty').hidden=true;}
  }
  const CTX={html_unescaped:'HTML 본문 · 인코딩 없음',html_escaped:'HTML 본문 · 인코딩됨',attribute:'HTML 속성값',script:'스크립트 내부'};
  function flowNode(role,label,main,sub) {
    const n=document.createElement('div');n.className='node '+role;
    const tag=document.createElement('span');tag.className='tag';tag.textContent=label;
    const m=document.createElement('code');m.textContent=main || '—';
    n.append(tag,m);
    if(sub){const s=document.createElement('small');s.textContent=sub;n.append(s);}
    return n;
  }
  const CODE_CONTEXT=10; // 선택한 줄 위아래로 함께 보여줄 줄 수
  let codeWatch=0;
  async function showCode(id,t,node) {
    // 코드레이처럼 흐름 상자를 누르면 왼쪽에 그 파일의 해당 줄 ±N줄을 띄운다.
    if(!node) node=[...document.querySelectorAll('#flow-code .node')].find(n=>n.querySelector('code').textContent===t.file+':'+t.line) || null;
    document.querySelectorAll('#flow-code .node').forEach(n=>n.classList.toggle('active',n===node));
    const token=++codeWatch,role=node?node.querySelector('.tag').textContent:'';
    q('cv-file').textContent=t.file+':'+t.line;q('cv-role').textContent=role;q('cv-role').className='cv-role '+(node?node.classList[1]:'');
    const url='/api/runs/'+encodeURIComponent(currentRun)+'/findings/'+encodeURIComponent(id)+'/source?file='+encodeURIComponent(t.file)+'&line='+t.line+'&context='+CODE_CONTEXT;
    let data;
    try{const r=await fetch(url);data=await r.json();if(!r.ok) throw new Error(data.detail || r.status);}
    catch(err){if(token===codeWatch){const e=document.createElement('p');e.className='flow-empty';e.textContent='코드를 불러오지 못했습니다: '+err.message;q('cv-body').replaceChildren(e);}return;}
    if(token!==codeWatch) return;
    const rows=data.lines.map(l=>{
      const row=document.createElement('div');row.className='cv-row'+(l.n===data.line?' hit':'');
      const n=document.createElement('span');n.className='ln';n.textContent=l.n;
      const c=document.createElement('code');c.textContent=l.text;row.append(n,c);return row;
    });
    q('cv-body').replaceChildren(...rows);
    const hit=q('cv-body').querySelector('.hit');
    if(hit) q('cv-body').scrollTop=hit.offsetTop-q('cv-body').clientHeight/2+hit.offsetHeight/2;
  }
  function renderFlow(id,trace) {
    // 실측 흐름(입력→저장→실행)은 위에 가로로, 코드 흐름(원인→연관→탐지)은 오른쪽 세로로 둔다.
    const ev=viewOf(id).evidence || {};
    const points=ev.input_points || [];const p=points.find(x=>x.kind==='probe') || points[0];
    const ex=(ev.executed || []).find(e=>e.full_url);const rd=(ev.rendered || []).find(e=>e.full_url);
    const fresh=(ev.executed || []).some(e=>e.session>(ev.probe_session || 1));
    const ctx=(ev.reflection_contexts || []).map(c=>CTX[c] || c).join(', ');
    const dast=[
      flowNode('source','입력 지점',p?p.method+' '+p.pattern.replace(/^https?:\/\/[^/]+/,''):'—',p?'파라미터 '+p.params.join(', '):''),
      flowNode('prop','저장·전달',fresh?'서버 저장 → 다른 사용자 조회':'저장 후 다시 출력',fresh?'새 세션 재방문에서 실행 (저장형)':''),
      flowNode('sink',ex?'실행 지점':'출력 지점',ex?'GET '+ex.full_url.replace(/^https?:\/\/[^/]+/,''):rd?'GET '+rd.full_url.replace(/^https?:\/\/[^/]+/,''):'—',ctx+(ev.csp_blocked?' · CSP 차단':'')),
    ];
    q('flow-dast').replaceChildren(...dast.flatMap((n,i)=>i?[arrowEl('▶'),n]:[n]));
    const ROLE={source:['source','원인 라인'],propagation:['prop','연관 라인'],sink:['sink','탐지 라인']};
    let items=(trace || []).filter(t=>t.file && t.line);
    if(!items.length){
      // 수정안(코드 추적)이 아직 없으면 SAST가 알려준 위치 하나만 보여준다.
      const card=cards.get(id);
      if(card && card.dataset.file && card.dataset.line) items=[{role:'sink',file:card.dataset.file,line:+card.dataset.line,code:'',note:'SAST 탐지 위치 · AI 추적 전'}];
    }
    const nodes=items.map(t=>{
      const [cls,label]=ROLE[t.role] || ROLE.propagation;
      const n=flowNode(cls,label,t.file+':'+t.line,(t.code || '').trim()+(t.note?(t.code?'  · ':'')+t.note:''));
      n.tabIndex=0;n.setAttribute('role','button');
      n.addEventListener('click',()=>showCode(id,t,n));
      n.addEventListener('keydown',e=>{if(e.key==='Enter' || e.key===' '){e.preventDefault();showCode(id,t,n);}});
      return n;
    });
    if(nodes.length){
      q('flow-code').replaceChildren(...nodes.flatMap((n,i)=>i?[arrowEl(),n]:[n]));
      // 기본은 탐지 라인(sink)을 연다.
      const i=Math.max(0,items.map(t=>t.role).lastIndexOf('sink'));
      showCode(id,items[i],nodes[i]);
    }else{
      const e=document.createElement('p');e.className='flow-empty';e.textContent='코드 위치 정보가 없습니다.';q('flow-code').replaceChildren(e);
      q('cv-file').textContent='—';q('cv-role').textContent='';q('cv-body').replaceChildren();
    }
  }
  // 개발자 대화: 점검이 끝난 항목마다 점검 기록·원본 코드를 근거로 묻고 답한다.
  let chatKey='',chatBusy=false;
  function inlineInto(el,text,id) {
    // `코드`, **강조**, 경로:줄 을 안전하게 DOM으로 만든다. 경로:줄은 위 코드 뷰어로 이동한다.
    const re=/(`[^`\n]+`|\*\*[^*\n]+\*\*|[A-Za-z0-9_./-]+\.[A-Za-z]{1,5}:\d+)/g;let last=0,m;
    while((m=re.exec(text))){
      if(m.index>last) el.append(document.createTextNode(text.slice(last,m.index)));
      const tok=m[0];
      if(tok.startsWith('**')){const b=document.createElement('strong');b.textContent=tok.slice(2,-2);el.append(b);}
      else{
        const raw=tok.startsWith('`')?tok.slice(1,-1):tok;
        const ref=raw.match(/^([A-Za-z0-9_./-]+\.[A-Za-z]{1,5}):(\d+)$/);
        if(ref && !q('fix-panel').hidden){
          const a=document.createElement('button');a.type='button';a.className='code-ref';a.textContent=raw;
          a.title='위 코드 확인에서 열기';
          a.onclick=()=>{showCode(id,{file:ref[1],line:+ref[2]},null);q('cv-file').closest('.code-check').scrollIntoView({behavior:'smooth',block:'center'});};
          el.append(a);
        }else{const c=document.createElement('code');c.textContent=raw;el.append(c);}
      }
      last=m.index+tok.length;
    }
    if(last<text.length) el.append(document.createTextNode(text.slice(last)));
  }
  function renderAnswer(host,text,id) {
    host.replaceChildren();
    text.split(/```[^\n]*\n?/).forEach((part,i)=>{
      if(i%2){const pre=document.createElement('pre');const c=document.createElement('code');c.textContent=part.replace(/\n$/,'');pre.append(c);host.append(pre);return;}
      part.split(/\n{2,}/).forEach(block=>{
        if(!block.trim()) return;
        const lines=block.split('\n');
        if(lines.every(l=>/^\s*([-*]|\d+\.)\s/.test(l))){
          const ul=document.createElement('ul');
          lines.forEach(l=>{const li=document.createElement('li');inlineInto(li,l.replace(/^\s*([-*]|\d+\.)\s/,''),id);ul.append(li);});host.append(ul);return;
        }
        const p=document.createElement('p');
        lines.forEach((l,j)=>{if(j) p.append(document.createElement('br'));
          const h=l.match(/^#{1,4}\s+(.*)$/);if(h){const b=document.createElement('strong');b.textContent=h[1];p.append(b);}else inlineInto(p,l,id);});
        host.append(p);
      });
    });
  }
  function chatBubble(role,text,id) {
    const row=document.createElement('div');row.className='msg '+role;
    const who=document.createElement('span');who.className='who';who.textContent=role==='user'?'개발자':'AI';
    const body=document.createElement('div');body.className='body';
    if(role==='user') body.textContent=text; else renderAnswer(body,text,id);
    row.append(who,body);q('chat-log').append(row);return body;
  }
  function chatEmpty(){const p=document.createElement('p');p.className='chat-empty';p.textContent='점검 결과를 보고 궁금한 점을 물어보세요. 아래 예시 질문을 눌러도 됩니다.';q('chat-log').replaceChildren(p);}
  async function showChatFor(id) {
    const card=cards.get(id);
    if(!card || !currentRun || !FINAL.includes(card.dataset.state)){q('chat-panel').hidden=true;return;}
    const key=currentRun+'/'+id;q('chat-panel').hidden=false;
    q('chat-title').textContent=card.querySelector('.qi-body strong').textContent;
    if(key===chatKey) return;
    chatKey=key;chatEmpty();
    try{
      const data=await (await fetch('/api/runs/'+encodeURIComponent(currentRun)+'/findings/'+encodeURIComponent(id)+'/chat')).json();
      if(chatKey!==key) return;
      if(data.messages?.length){q('chat-log').replaceChildren();data.messages.forEach(m=>chatBubble(m.role,m.text,id));q('chat-log').scrollTop=q('chat-log').scrollHeight;}
    }catch{}
  }
  async function sendChat(text) {
    text=text.trim();if(!text || chatBusy || !selected || !currentRun) return;
    const id=selected,key=currentRun+'/'+id;
    if(q('chat-log').querySelector('.chat-empty')) q('chat-log').replaceChildren();
    chatBubble('user',text,id);q('chat-input').value='';
    const body=chatBubble('assistant','',id);const status=document.createElement('small');status.className='chat-status';status.textContent='점검 기록을 읽는 중…';body.append(status);
    q('chat-log').scrollTop=q('chat-log').scrollHeight;
    chatBusy=true;q('chat-send').disabled=true;let answer='';
    try{
      const r=await fetch('/api/runs/'+encodeURIComponent(currentRun)+'/findings/'+encodeURIComponent(id)+'/chat',
        {method:'POST',headers:{'Content-Type':'application/json','X-DAST-Token':config.csrf_token},body:JSON.stringify({message:text})});
      if(!r.ok) throw new Error((await r.json()).detail || r.status);
      const reader=r.body.getReader(),dec=new TextDecoder();let buf='';
      for(;;){
        const {value,done}=await reader.read();if(done) break;
        buf+=dec.decode(value,{stream:true});const lines=buf.split('\n');buf=lines.pop();
        for(const line of lines){
          if(!line.trim()) continue;const ev=JSON.parse(line);
          if(chatKey!==key) continue;
          if(ev.type==='text'){answer+=ev.text;renderAnswer(body,answer,id);}
          else if(ev.type==='tool'){status.textContent='📄 코드 확인 중 · '+(ev.target || ev.name);if(!body.contains(status)) body.append(status);}
          else if(ev.type==='error') throw new Error(ev.message);
          if(ev.type!=='tool' && body.contains(status) && answer) status.remove();
          q('chat-log').scrollTop=q('chat-log').scrollHeight;
        }
      }
      if(!answer){status.textContent='답변이 비어 있습니다.';}
    }catch(err){status.textContent='답변 실패: '+err.message;if(!body.contains(status)) body.append(status);}
    finally{chatBusy=false;q('chat-send').disabled=false;}
  }
  q('chat-form').addEventListener('submit',e=>{e.preventDefault();sendChat(q('chat-input').value);});
  q('chat-input').addEventListener('keydown',e=>{if(e.key==='Enter' && !e.shiftKey && !e.isComposing){e.preventDefault();sendChat(q('chat-input').value);}});
  q('chat-suggest').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>sendChat(b.textContent)));
  function arrowEl(ch='▼'){const a=document.createElement('div');a.className='arrow';a.textContent=ch;return a;}
  function renderResult(r) {
    viewOf(r.finding_id || r.id).evidence=r.evidence;
    setAgentSession(r.finding_id || r.id,r.evidence);
    evidenceShot(r.finding_id || r.id,r.evidence);
    setFix(r.finding_id || r.id,r.status || r.verdict);
    const status=r.status || r.verdict || 'INCONCLUSIVE';
    const id=r.finding_id || r.id;
    setStatus(id,status,r.message || r.reason);
    setMapping(id,r.mapping);
    const positive=status==='REPRODUCED',negative=status==='NOT_REPRODUCED',blocked=status==='BLOCKED';
    q('result').dataset.tone=positive?'positive':negative?'negative':blocked?'blocked':'error';
    q('result-title').textContent=positive?'양성 · 저장형 XSS 실행 재현':negative?'음성 · 해당 입력에서 실행 미관측':status==='BLOCKED'?'차단 근거 확인':'판정 불가';
    q('result-description').textContent=r.message || r.reason || (positive?'작성·저장 후 재방문에서 실행 사건이 확인됐습니다.':negative?'정상 입력·저장·재방문·관측을 완료한 이번 테스트의 결과입니다.':'검사 실패를 안전 판정으로 처리하지 않습니다.');
    const e=r.evidence || {};
    q('evidence').textContent=typeof e==='string'?e:[
      '정상 입력·저장 확인: '+(e.control_passed?'완료':'미완료'),
      '새 컨텍스트 재방문: '+(e.fresh_context?'완료':'미완료'),
      '실행 표식: '+(e.proof_marker?'일치':'미관측'),
      '브라우저 실행 로그: '+(e.console_proof?'일치':'미관측'),
      '화면 기록: '+(e.screenshots?.length || 0)+'장',
      e.error_type?'오류 유형: '+e.error_type:''
    ].filter(Boolean).join('\n');
    q('checks').replaceChildren();
    const checklist=r.checks || [
      {label:'정상 입력 저장·재방문',status:e.control_passed?'done':'미완료'},
      {label:'SAST 페이로드 저장',status:e.post_path?'done':'미완료'},
      {label:'새 컨텍스트 상세 열기',status:e.fresh_context&&e.detail_url?'done':'미완료'},
      {label:'실행 여부 관찰',status:status!=='INCONCLUSIVE'?'done':'판정 불가'},
      {label:'화면 증거 저장',status:e.screenshots?.length>=4?'done':'미완료'}
    ];
    checklist.forEach(c=>{
      const li=document.createElement('li');const status=String(c.status || '');
      const ok=['done','pass','passed','completed','success','ok'].includes(status.toLowerCase());
      li.className=ok?'ok':'';
      li.textContent=(ok?'✓ ':'○ ')+(c.label || c.name || c.stage || c.id || '검사')+' · '+status;
      q('checks').append(li);
    });
    if(currentRun){q('report-link').href='/api/artifacts/'+encodeURIComponent(currentRun)+'/report.json';q('report-link').hidden=false;}
  }
  function done(message,failed=false) {
    busy=false;clearInterval(timer);q('run').disabled=false;q('run').textContent='새 점검';loadSessions();loadVulns();
    q('scenario').disabled=false;q('planner').disabled=false;q('cursor').hidden=true;
    phase(failed?'error':'done',message || '검사 결과를 확인하세요.');
    // 완료 이벤트 직후에 오는 마지막 화면 프레임까지 받은 뒤 닫는다.
    if(stream){const finished=stream;stream=null;setTimeout(()=>finished.close(),800);}
  }
  function event(e) {
    const fid=e.finding_id || null;
    const shown=!fid || fid===selected;
    const activity=text=>{if(fid) viewOf(fid).activity=text;if(shown) q('activity').textContent=text;};
    if(e.type==='frame'){
      const src=e.image || e.data_url || e.screenshot;
      if(typeof src!=='string' || !/^data:image\/(jpeg|png);base64,/.test(src)) return;
      if(fid) viewOf(fid).frame=src;
      if(shown){q('screen').src=src;q('screen').hidden=false;q('empty').hidden=true;}
      latestFrame={width:Number(e.width)||960,height:Number(e.height)||640};
    }else if(e.type==='cursor'){
      const point={x:Number(e.x)||0,y:Number(e.y)||0};
      if(fid) viewOf(fid).cursor=point;
      if(shown){cursorPoint=point;renderCursor();}
    }else if(e.type==='stage'){
      if(fid) setStage(fid,e.stage,e.status,e.detail);
      if(e.status!=='running' || e.stage==='analyze') log('🧭 '+(STAGE_LABEL[e.stage] || e.stage)+' · '+({running:'시작',done:'완료',failed:'실패',skipped:'생략'}[e.status] || e.status)+(e.detail?' — '+e.detail:''),fid);
    }else if(e.type==='click'){
      if(shown) ripple();
    }else if(e.type==='state'){
      if(!fid) phase(e.phase,e.message);if(e.message) log(e.message,fid);
    }else if(e.type==='log'){
      log(e.message,fid);
    }else if(e.type==='probe'){
      log('🧪 '+(e.message || '검사 문자열 입력')+' ('+(e.payload_id || '')+')',fid);activity('검사 문자열 입력 중');
    }else if(e.type==='execution'){
      log('💥 '+(e.message || 'XSS 실행 감지'),fid);activity('XSS 실행 감지 — 실제 화면 효과를 확인하세요');
      // 실제 페이지의 XSS 효과가 먼저 보이도록 경고창은 늦게 띄운다.
      setTimeout(()=>{if(fid) viewOf(fid).alert=true;if(!fid || fid===selected) q('execution-alert').hidden=false;},2000);
    }else if(e.type==='agent'){
      log('🤖 '+(ACTIONS[e.action] || e.action)+(e.detail?' '+e.detail:'')+' — '+(e.message || ''),fid);
      activity((ACTIONS[e.action] || e.action)+': '+(e.message || ''));
    }else if(e.type==='finding'){
      setStatus(e.finding_id || e.id,e.status || 'RUNNING',e.message);
      setMapping(e.finding_id || e.id,e.mapping);
      // 사용자가 항목을 고정하지 않았으면 새로 시작한 점검을 자동으로 따라간다.
      if(String(e.status).toUpperCase()==='RUNNING' && !pinned) select(e.finding_id,false);
    }else if(e.type==='result'){
      renderResult(e.result || e);
      const rid=(e.result || e).finding_id;
      if(rid && rid===selected) showFixFor(rid);
    }else if(e.type==='done'){
      if(e.results?.length) e.results.forEach(renderResult);
      if(!selected && cards.size) select([...cards.keys()][0],false);
      else if(selected) showFixFor(selected);
      if(e.plan){
        const cost=e.plan.cost_usd==null?'비용 보고 없음':'$'+Number(e.plan.cost_usd).toFixed(4);
        log('계획 결과: '+e.plan.model_name+' · '+(e.plan.duration_ms/1000).toFixed(1)+'초 · '+cost);
      }
      if(e.status==='error'){
        q('result').dataset.tone='error';
        q('result-title').textContent='검사 일부 판정 불가';
        q('result-description').textContent=e.error || '하나 이상의 항목에서 검증을 완료하지 못했습니다.';
      }
      const counts={};cards.forEach(c=>{counts[c.dataset.state]=(counts[c.dataset.state]||0)+1;});
      const parts=[['REPRODUCED','취약'],['NOT_REPRODUCED','안전'],['BLOCKED','차단'],['INCONCLUSIVE','불가']].filter(([k])=>counts[k]).map(([k,v])=>v+' '+counts[k]);
      if(cards.size>1){q('result-title').textContent=parts.join(' · ');q('result').dataset.tone=counts.REPRODUCED?'error':counts.INCONCLUSIVE?'idle':'negative';}
      done(e.error || e.message,e.status==='error');
      if(e.duration_ms) q('elapsed').textContent=(e.duration_ms/1000).toFixed(1)+'초';
      if(currentRun) watchReport(currentRun);
      refreshModels();
    }else if(e.type==='error'){
      q('result').dataset.tone='error';q('result-title').textContent='실행 오류 · 판정 불가';q('result-description').textContent=e.message || '실행을 완료하지 못했습니다.';
      cards.forEach((_,id)=>setStatus(id,'INCONCLUSIVE',e.message));
      log(e.message || '실행 오류');done('검사에 실패했습니다. 실행 로그를 확인하세요.',true);q('session-status').textContent='실행 오류';
    }
  }
  function resetView() {
      busy=true;currentRun=null;q('run').textContent='새 점검';q('chat-panel').hidden=true;chatKey='';
      if(stream) stream.close();
      q('logs').replaceChildren();allLogs=[];document.querySelectorAll('.qi [data-stage]').forEach(s=>{s.dataset.s='pending';s.removeAttribute('title');});document.querySelectorAll('.stage-list span').forEach(s=>s.textContent='');cards.forEach(renderRing);views.forEach(v=>Object.assign(v,{frame:null,cursor:null,alert:false,activity:''}));selected=null;pinned=false;cards.forEach(c=>c.classList.remove('selected'));q('execution-alert').hidden=true;cards.forEach(c=>{c.dataset.state='PENDING';c.querySelector('.qi-badge').textContent='';});updateQueue();q('report-link').hidden=true;clearTimeout(reportTimer);showReport({status:''});q('screen').hidden=true;q('empty').hidden=false;q('cursor').hidden=true;
      q('evidence').textContent='실행 증거를 기다리고 있습니다.';q('checks').replaceChildren();
      q('result').dataset.tone='idle';q('result-title').textContent='검사 진행 중';q('result-description').textContent='실행이 관측되기 전에는 양성으로 판정하지 않습니다.';
      q('plan-mode').textContent=modeLabel(q('planner').value);
      clearInterval(timer);started=Date.now();timer=setInterval(()=>q('elapsed').textContent=((Date.now()-started)/1000).toFixed(1)+'초',100);
  }
  function attach(runId) {
      currentRun=runId;showViewing(runId);
      stream=new EventSource('/api/runs/'+encodeURIComponent(currentRun)+'/events');
      stream.onmessage=message=>{try{event(JSON.parse(message.data));}catch(err){log('이벤트 처리 오류: '+err.message);}};
      stream.onerror=async()=>{
        if(!busy) return;
        try{
          const r=await fetch('/api/runs/'+encodeURIComponent(currentRun));const s=await r.json();
          if(s.status==='done'){(s.results || []).forEach(renderResult);done();}
          else if(s.status==='error') event({type:'error',message:s.error || '검사 오류'});
          else q('activity').textContent='화면 스트림 연결을 재시도하고 있습니다.';
        }catch{q('activity').textContent='서버 연결을 확인하세요. 검사 결과는 아직 확정되지 않았습니다.';}
      };
  }
  function ripple() {
    const host=q('screen-host'),dot=document.createElement('span');dot.className='click-ripple';
    const scale=Math.min(host.clientWidth/latestFrame.width,host.clientHeight/latestFrame.height);
    dot.style.left=((host.clientWidth-latestFrame.width*scale)/2+cursorPoint.x*scale)+'px';
    dot.style.top=((host.clientHeight-latestFrame.height*scale)/2+cursorPoint.y*scale)+'px';
    host.append(dot);setTimeout(()=>dot.remove(),700);
  }
  let reportTimer=null;
  function showReport(state) {
    const ready=state.status==='ready';
    q('report-html').hidden=!ready;q('report-pdf').hidden=!ready;q('report-make').hidden=!['none','failed'].includes(state.status);
    if(ready){q('report-html').href=state.html;q('report-pdf').href=state.pdf;}
    q('report-status').textContent={generating:'보고서 작성 중…',failed:'보고서 생성 실패',none:'',ready:''}[state.status] || '';
  }
  async function watchReport(runId) {
    // 점검이 끝나면 서버가 보고서를 만든다. 준비될 때까지 확인한다.
    clearTimeout(reportTimer);
    const tick=async tries=>{
      if(runId!==currentRun) return;
      try{
        const state=await (await fetch('/api/runs/'+encodeURIComponent(runId)+'/report')).json();
        showReport(state);
        if((state.status==='generating' || (state.status==='none' && tries<3)) && tries<90) reportTimer=setTimeout(()=>tick(tries+1),2000);
      }catch{}
    };
    tick(0);
  }
  const shortId=id=>String(id || '').slice(0,8);
  function showViewing(runId) {
    viewing=runId;q('viewing-label').textContent=runId?'보는 중 · '+shortId(runId):'—';
    q('rerun').disabled=!runId;
    if(runId && location.hash!=='#run='+runId) history.replaceState(null,'','#run='+runId);
    document.querySelectorAll('#session-rows tr[data-id], #scan-strip .scan').forEach(el=>el.classList.toggle('viewing',el.dataset.id===runId));
  }
  async function view(runId) {
    showView('project');
    // 세션 하나를 처음부터 다시 그린다. 진행 중이면 실시간으로 이어서 본다.
    const response=await fetch('/api/runs/'+encodeURIComponent(runId)+'/view');
    if(!response.ok) throw new Error('점검 세션을 찾을 수 없습니다.');
    const data=await response.json();
    const findings=data.report?.findings || (data.results || []).map(r=>({id:r.finding_id,title:r.title || r.finding_id}));
    useReport(data.report || {source_label:data.source_label,findings});
    if(data.project_id && data.project_id!==projectId) selectProject(data.project_id,false);
    if(data.scenario) q('scenario').value=data.scenario;
    if(data.planner) q('planner').value=data.planner;
    resetView();
    q('plan-mode').textContent=modeLabel(q('planner').value);
    started=(data.started_at || Date.now()/1000)*1000;
    if(data.live){
      log(data.status==='running'?'진행 중인 점검에 연결했습니다 · '+shortId(runId):'점검 기록을 불러왔습니다 · '+shortId(runId));
      attach(runId);
    }else{
      currentRun=runId;showViewing(runId);log('저장된 점검 기록을 불러왔습니다 · '+shortId(runId));
      event({type:'done',...data});
    }
  }
  async function resume() {
    // 새로고침 뒤에도 주소의 세션(없으면 최근 세션)을 처음부터 다시 그려 이어서 보여준다.
    try{
      const hash=location.hash.match(/^#run=([0-9a-f-]{36})$/);
      if(hash){await view(hash[1]);return;}
      const response=await fetch('/api/runs/latest');if(!response.ok) return;
      const latest=await response.json();await view(latest.run_id);
    }catch(err){log(err.message);}
  }
  // 프로젝트(시스템) 단위로 점검 이력과 취약점 상태를 묶는다.
  async function loadProjects() {
    try{
      const data=await (await fetch('/api/projects')).json();projectList=data.projects;
      let saved=null;try{saved=localStorage.getItem('dast-project');}catch{}
      const select=q('project');select.replaceChildren();
      projectList.forEach(p=>{const o=document.createElement('option');o.value=p.id;o.textContent=p.name;select.append(o);});
      q('pf-target').replaceChildren(...data.targets.map(t=>{const o=document.createElement('option');o.value=t;o.textContent=t;return o;}));
      selectProject(projectList.some(p=>p.id===saved)?saved:data.default,false);
    }catch(err){log('프로젝트를 불러오지 못했습니다.');}
  }
  function selectProject(id,reload=true) {
    projectId=id;q('project').value=id;try{localStorage.setItem('dast-project',id);}catch{}
    const p=projectList.find(item=>item.id===id) || {};
    const meta=q('project-meta');meta.replaceChildren();
    const add=(label,value,href)=>{const span=document.createElement('span');const b=document.createElement('b');b.textContent=label;span.append(b);
      if(href){const a=document.createElement('a');a.href=href;a.target='_blank';a.rel='noopener noreferrer';a.textContent=value;span.append(a);}else span.append(document.createTextNode(value));meta.append(span);};
    add('Git',p.repo_url || '미설정',p.repo_url || null);add('운영',p.prod_url || '미설정',p.prod_url || null);add('점검 대상',p.target_id || '-');
    add('점검',(p.run_count ?? 0)+'회');add('미조치',String(p.open ?? 0));
    loadSessions();loadVulns();
  }
  const STATE_CLASS={open:'vuln',reopen:'vuln',warn:'warn',check:'na',fixed:'fixed',ok:'ok'};
  async function loadVulns() {
    if(!projectId) return;
    try{
      const data=await (await fetch('/api/projects/'+encodeURIComponent(projectId))).json();
      const rows=q('vuln-rows');rows.replaceChildren();
      const list=data.vulnerabilities || [];
      q('vulns-count').textContent='미조치 '+list.filter(v=>['open','reopen'].includes(v.state)).length+' · 전체 '+list.length;
      if(!list.length){rows.innerHTML='<tr><td colspan="6">점검 기록이 없습니다.</td></tr>';return;}
      list.forEach(v=>{
        const tr=document.createElement('tr');tr.dataset.id=v.latest.run_id;
        const fix=v.fix?(v.fix.pr?(v.fix.pr.url?'PR #'+v.fix.pr.number:'커밋 '+v.fix.pr.commit):'수정안 '+(v.fix.status==='ready'?'준비됨':v.fix.status)):'—';
        const cells=[v.title,v.state_label,({REPRODUCED:'취약',NOT_REPRODUCED:'양호',BLOCKED:'주의',INCONCLUSIVE:'판정 불가'})[v.latest.status] || v.latest.status,
          v.checks+'회',v.latest.at?new Date(v.latest.at*1000).toLocaleString('ko-KR',{hour12:false}):'—',fix];
        cells.forEach((text,i)=>{const td=document.createElement('td');td.textContent=text;if(i===1)td.className='state '+(STATE_CLASS[v.state] || '');tr.append(td);});
        tr.addEventListener('click',()=>view(v.latest.run_id).catch(err=>log(err.message)));
        rows.append(tr);
      });
    }catch{}
  }
  // 프로젝트 안의 점검 회차(#1, #2 …)와 회차별 취약 개수 추이.
  function renderScans(list) {
    const strip=q('scan-strip');strip.replaceChildren();
    const runs=[...list].sort((a,b)=>(a.started_at||0)-(b.started_at||0));
    const project=projectList.find(p=>p.id===projectId) || {};
    const offset=Math.max(0,(project.run_count||runs.length)-runs.length);
    if(!runs.length){strip.innerHTML='<span class="scan-empty">아직 점검이 없습니다.</span>';q('scan-trend').textContent='';return;}
    const max=Math.max(1,...runs.map(r=>r.finding_count||1));
    runs.forEach((r,i)=>{
      const c=r.counts||{},vuln=(c.REPRODUCED||0),safe=(c.NOT_REPRODUCED||0)+(c.BLOCKED||0),na=c.INCONCLUSIVE||0;
      const card=document.createElement('button');card.type='button';card.className='scan';card.dataset.id=r.run_id;card.dataset.status=r.status;
      card.classList.toggle('viewing',r.run_id===viewing);
      const bar=document.createElement('span');bar.className='scan-bar';
      const fill=document.createElement('i');fill.style.height=Math.round(vuln/max*100)+'%';bar.append(fill);
      const no=document.createElement('b');no.textContent='#'+(offset+i+1);
      const when=document.createElement('small');const d=r.started_at?new Date(r.started_at*1000):null;const two=n=>String(n).padStart(2,'0');
      when.textContent=d?`${two(d.getMonth()+1)}/${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`:'';
      const stat=document.createElement('span');stat.className='scan-stat';
      stat.innerHTML=r.status==='running'?'<em class="run">점검 중</em>':`<em class="v">취약 ${vuln}</em><em>안전 ${safe}</em>${na?`<em>불가 ${na}</em>`:''}`;
      card.append(bar,no,when,stat);
      if(r.reverify_of){const t=document.createElement('span');t.className='scan-tag';t.textContent='재검증';card.append(t);}
      card.addEventListener('click',()=>view(r.run_id).catch(err=>log(err.message)));
      strip.append(card);
    });
    const done=runs.filter(r=>r.status!=='running');
    if(done.length>1){const a=(done[done.length-2].counts||{}).REPRODUCED||0,b=(done[done.length-1].counts||{}).REPRODUCED||0;
      q('scan-trend').textContent=b<a?'직전 회차보다 취약 '+(a-b)+'개 감소':b>a?'직전 회차보다 취약 '+(b-a)+'개 증가':'직전 회차와 취약 개수 동일';}
    else q('scan-trend').textContent='';
    strip.scrollLeft=strip.scrollWidth;
  }
  // 통합 대시보드: 전체 프로젝트 현황. 프로젝트를 누르면 프로젝트 화면으로 들어간다.
  function showView(name) {
    q('dashboard-view').hidden=name!=='dashboard';q('project-view').hidden=name!=='project';
    q('tab-dashboard').classList.toggle('on',name==='dashboard');q('tab-project').classList.toggle('on',name==='project');
    if(name==='dashboard') loadDashboard();
    else requestAnimationFrame(()=>{const strip=q('scan-strip');const on=strip.querySelector('.scan.viewing');strip.scrollLeft=on?on.offsetLeft-strip.clientWidth/2:strip.scrollWidth;});
  }
  function hbar(label,parts,total,onclick) {
    const row=document.createElement('button');row.type='button';row.className='hbar';
    const name=document.createElement('span');name.className='hb-name';name.textContent=label;
    const track=document.createElement('span');track.className='hb-track';
    parts.forEach(([value,cls])=>{if(!value) return;const seg=document.createElement('i');seg.className=cls;seg.style.width=(value/Math.max(1,total)*100)+'%';seg.textContent=value;track.append(seg);});
    if(!parts.some(([v])=>v)){const e=document.createElement('em');e.textContent='점검 없음';track.append(e);}
    row.append(name,track);row.addEventListener('click',onclick);return row;
  }
  async function loadDashboard() {
    try{
      const d=await (await fetch('/api/dashboard')).json();const t=d.totals;
      q('dk-projects').textContent=t.projects;q('dk-scans').textContent=t.scans;q('dk-findings').textContent=t.findings;q('dk-open').textContent=t.open;
      q('dk-rate').textContent=t.fix_rate+'%';q('dk-ring').style.setProperty('--p',t.fix_rate);
      const open=id=>()=>{selectProject(id);showView('project');};
      const maxScan=Math.max(1,...d.projects.map(p=>Object.values(p.latest || {}).reduce((a,b)=>a+b,0)));
      q('dash-scan').replaceChildren(...d.projects.map(p=>{const c=p.latest || {};return hbar(p.name+' · '+p.scans+'회',[[c.REPRODUCED,'c-vuln'],[(c.NOT_REPRODUCED||0)+(c.BLOCKED||0),'c-safe'],[c.INCONCLUSIVE,'c-na']],maxScan,open(p.id));}));
      const maxFix=Math.max(1,...d.projects.map(p=>p.findings));
      q('dash-fix').replaceChildren(...d.projects.map(p=>hbar(p.name,[[p.open,'c-vuln'],[p.fixed,'c-fixed'],[p.ok,'c-safe'],[p.warn,'c-warn'],[p.check,'c-na']],maxFix,open(p.id))));
      q('dash-recent').replaceChildren(...d.recent.map(r=>{
        const tr=document.createElement('tr');const c=r.counts || {};
        [r.project,'#'+r.number+(r.reverify?' 재검증':''),r.started_at?new Date(r.started_at*1000).toLocaleString('ko-KR',{hour12:false}):'',
         '취약 '+(c.REPRODUCED||0)+' · 안전 '+((c.NOT_REPRODUCED||0)+(c.BLOCKED||0))].forEach((v,i)=>{const td=document.createElement('td');td.textContent=v;if(i===3&&c.REPRODUCED)td.className='v';tr.append(td);});
        tr.addEventListener('click',()=>{selectProject(r.project_id);showView('project');view(r.run_id).catch(err=>log(err.message));});
        return tr;}));
    }catch{}
  }
  const STATUS_LABEL={running:'진행 중',done:'완료',error:'일부 불가'};
  const MODE_SHORT={agent:'Agent SDK',computer_use:'Computer Use',claude:'Claude 매핑',reviewed:'수동'};
  async function loadSessions() {
    try{
      const response=await fetch('/api/runs?project='+encodeURIComponent(projectId || ''));if(!response.ok) return;
      const data=await response.json();renderScans(data.runs);const rows=q('session-rows');rows.replaceChildren();
      if(!data.runs.length){rows.innerHTML='<tr><td colspan="6">점검 기록이 없습니다.</td></tr>';return;}
      data.runs.forEach(r=>{
        const tr=document.createElement('tr');tr.dataset.id=r.run_id;tr.dataset.status=r.status;
        const c=r.counts || {};
        const cells=[
          shortId(r.run_id),
          r.started_at?new Date(r.started_at*1000).toLocaleTimeString('en-GB',{hour12:false}):'—',
          (r.reverify_of?'재검증 ← '+shortId(r.reverify_of.run_id)+' · ':'')+(MODE_SHORT[r.planner] || r.planner || '—'),
          (r.scenario || '—')+' · '+(r.finding_count ?? '?')+'건',
          r.status==='running'?((c.REPRODUCED||0)+(c.NOT_REPRODUCED||0)+(c.BLOCKED||0)+(c.INCONCLUSIVE||0))+' / '+(r.finding_count ?? '?'):
            [['REPRODUCED','취약'],['NOT_REPRODUCED','안전'],['BLOCKED','차단'],['INCONCLUSIVE','불가']].filter(([k])=>c[k]).map(([k,v])=>v+' '+c[k]).join(' · ') || '—',
          STATUS_LABEL[r.status] || r.status
        ];
        cells.forEach((text,i)=>{const td=document.createElement('td');td.textContent=text;if(i===0)td.className='sid';if(i===5)td.className='st';tr.append(td);});
        if(c.REPRODUCED) tr.classList.add('has-vuln');
        tr.addEventListener('click',()=>{view(r.run_id).catch(err=>log(err.message));});
        rows.append(tr);
      });
      showViewing(viewing);
    }catch{}
  }
  async function run(reportOverride) {
    try {
      if(reportOverride) useReport(reportOverride);
      useReport(JSON.parse(q('report-input').value));
      resetView();
      phase('planning','SAST 입력과 등록된 검사 경로를 확인합니다.');
      const response=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json','X-DAST-Token':config.csrf_token},body:JSON.stringify({scenario:q('scenario').value,planner:q('planner').value,report,concurrency:Number(q('concurrency').value),impact:q('impact').checked,project_id:projectId})});
      const data=await response.json();
      if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail || data));
      log('점검 세션: '+data.run_id);loadSessions();
      log('계획 모드: '+modeLabel(q('planner').value));
      attach(data.run_id);
    }catch(err){
      q('result').dataset.tone='error';q('result-title').textContent='검사를 시작하지 못했습니다';q('result-description').textContent=err.message;
      log(err.message);done('입력과 실행 로그를 확인하세요.',true);q('session-status').textContent='입력·실행 오류';
    }
  }
  try{const saved=localStorage.getItem('dast-concurrency');if(saved)q('concurrency').value=saved;}catch{}
  q('concurrency').addEventListener('change',()=>{try{localStorage.setItem('dast-concurrency',q('concurrency').value);}catch{}});
  q('run').addEventListener('click',()=>run());
  q('rerun').addEventListener('click',()=>{if(report)run(structuredClone(report));});
  q('project').addEventListener('change',()=>selectProject(q('project').value));
  q('tab-dashboard').addEventListener('click',()=>showView('dashboard'));
  q('tab-project').addEventListener('click',()=>showView('project'));
  q('project-new').addEventListener('click',()=>{q('project-form').hidden=false;q('pf-name').focus();});
  q('pf-cancel').addEventListener('click',()=>{q('project-form').hidden=true;});
  q('project-form').addEventListener('submit',async ev=>{
    ev.preventDefault();q('pf-msg').textContent='';
    const body={name:q('pf-name').value.trim(),repo_url:q('pf-repo').value.trim(),prod_url:q('pf-prod').value.trim(),target_id:q('pf-target').value,description:q('pf-desc').value.trim()};
    const r=await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json','X-DAST-Token':config.csrf_token},body:JSON.stringify(body)});
    const data=await r.json();
    if(!r.ok){q('pf-msg').textContent=typeof data.detail==='string'?data.detail:'입력을 확인하세요.';return;}
    q('project-form').hidden=true;q('project-form').reset();await loadProjects();selectProject(data.id);
  });
  q('fix-close').addEventListener('click',()=>{q('fix-panel').hidden=true;});
  q('report-make').addEventListener('click',async()=>{
    if(!currentRun) return;
    await fetch('/api/runs/'+encodeURIComponent(currentRun)+'/report',{method:'POST',headers:{'X-DAST-Token':config.csrf_token}});
    watchReport(currentRun);
  });
  setInterval(loadSessions,4000);
  q('refresh-models').addEventListener('click',refreshModels);
  q('dismiss-alert').addEventListener('click',()=>{q('execution-alert').hidden=true;if(selected) viewOf(selected).alert=false;});
  q('load-sample').addEventListener('click',()=>{if(!busy)useReport(structuredClone(sample));});
  q('load-multi').addEventListener('click',()=>{if(!busy && config.multi_sample_report)useReport(structuredClone(config.multi_sample_report));});
  q('import-file').addEventListener('change',async e=>{
    const file=e.target.files[0];if(!file || busy) return;
    try{if(file.size>128*1024)throw new Error('입력 JSON은 128KB 이하여야 합니다.');useReport(JSON.parse(await file.text()));}catch(err){q('activity').textContent=err.message;}
  });
  q('scenario').addEventListener('change',()=>{
    if(config)q('board-link').href=(config.target_origin || 'http://127.0.0.1:8766')+'/'+q('scenario').value+'/board';
  });
  q('planner').addEventListener('change',()=>{if(config)q('plan-mode').textContent=modeLabel(q('planner').value);});
  window.addEventListener('resize',renderCursor);
  fetch('/api/config').then(r=>{if(!r.ok)throw new Error('서버 설정을 읽지 못했습니다.');return r.json();}).then(c=>{
    config=c;sample=c.sample_report;useReport(structuredClone(sample));
    renderModels(c.models);
    q('planner').value=c.default_planner || 'agent';
    q('plan-mode').textContent=modeLabel(q('planner').value);
    q('target-origin').textContent=c.target_origin || 'http://127.0.0.1:8766';
    q('board-link').href=q('target-origin').textContent+'/vulnerable/board';
    q('run').disabled=false;q('activity').textContent='SAST 결과를 확인한 뒤 검사를 시작하세요.';
    if(c.browser_available===false)q('activity').textContent='브라우저 실행 파일을 찾지 못했습니다. 서버 실행 설정을 확인하세요.';
    loadProjects().then(()=>{if(/^#run=/.test(location.hash)){showView('project');resume();}else showView('dashboard');});
  }).catch(err=>{q('activity').textContent=err.message;q('source-label').textContent='서버 연결 실패';});
})();
