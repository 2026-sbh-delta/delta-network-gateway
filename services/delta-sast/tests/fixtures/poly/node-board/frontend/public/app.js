const $ = (id) => document.getElementById(id);
const form = $('post-form');
const views = { list: $('list-view'), write: $('write-view'), detail: $('detail-view') };
const providers = {
  default: { name: 'WORKSPACE', color: '#f3f5f8' },
  aws: { name: 'AWS', color: '#fff3e6' },
  gcp: { name: 'GCP', color: '#eaf2ff' },
  onprem: { name: 'ON-PREMISES', color: '#eaf5ee' },
};
const listDate = new Intl.DateTimeFormat('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' });
const fullDate = new Intl.DateTimeFormat('ko-KR', {
  year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
});
const updateTime = new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
let config;
let configured = false;
let initializing = false;
let busy = false;
let activeView = 'list';
let revision = 0;
let viewController;
let nextNotice = '';

function notice(message = '', tone = 'success') {
  $('notice').textContent = message;
  $('notice').dataset.tone = tone;
  $('notice').hidden = !message;
}

function setBusy(value) {
  busy = value;
  for (const input of form.elements) input.disabled = value || !configured;
  $('submit-label').textContent = value ? '등록 중' : '게시글 등록';
  form.setAttribute('aria-busy', String(value));
}

async function api(path, options = {}) {
  const timeout = AbortSignal.timeout(15000);
  const response = await fetch(`${config.apiEndpoint}${path}`, {
    ...options,
    headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    signal: options.signal ? AbortSignal.any([options.signal, timeout]) : timeout,
    cache: 'no-store',
  });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    throw Object.assign(new Error(data?.error?.message ?? '요청을 처리하지 못했어요.'), { status: response.status });
  }
  return data;
}

function errorMessage(error) {
  return error.name === 'TimeoutError' || error instanceof TypeError
    ? '서비스에 연결하지 못했어요. 잠시 후 다시 불러와 주세요.'
    : error instanceof SyntaxError ? '응답을 읽지 못했어요. 잠시 후 다시 불러와 주세요.' : error.message;
}

function connection(ok, health) {
  $('status-dot').dataset.state = ok ? 'ok' : 'error';
  $('connection-status').textContent = ok ? '서비스 정상 연결' : '서비스 연결을 확인해 주세요';
  $('be-version').textContent = health?.version ?? '-';
}

async function checkHealth(signal, current) {
  try {
    const health = await api('/health', { signal });
    if (revision === current && !signal.aborted) connection(true, health);
  } catch {
    if (revision === current && !signal.aborted) connection(false);
  }
}

function listState(kind, title, description) {
  $('posts-table').hidden = true;
  $('list-state').hidden = false;
  $('list-state-title').textContent = title;
  $('list-state-description').textContent = description;
  $('list-state-icon').dataset.state = kind;
  $('empty-write').hidden = kind !== 'empty';
  $('list-retry').hidden = kind !== 'error';
}

function renderPosts(notes) {
  $('posts').replaceChildren();
  $('post-count').textContent = notes.length.toLocaleString('ko-KR');
  if (!notes.length) {
    listState('empty', '아직 게시글이 없어요', '첫 이야기를 남겨 게시판을 채워 보세요.');
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const note of notes) {
    const row = document.createElement('tr');
    const titleCell = document.createElement('td');
    const link = document.createElement('a');
    link.className = 'post-link';
    link.href = `#/posts/${encodeURIComponent(note.id)}`;
    const icon = document.createElement('span');
    icon.className = 'post-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M6 3h8l4 4v14H6V3Z" stroke-linejoin="round"/><path d="M13 3v5h5M9 12h6M9 16h5" stroke-linecap="round"/></svg>';
    const title = document.createElement('span');
    title.className = 'post-link-title';
    title.textContent = note.title;
    link.append(icon, title);
    titleCell.append(link);
    const dateCell = document.createElement('td');
    dateCell.className = 'post-date';
    const time = document.createElement('time');
    time.dateTime = note.createdAt;
    time.textContent = listDate.format(new Date(note.createdAt));
    dateCell.append(time);
    row.append(titleCell, dateCell);
    fragment.append(row);
  }
  $('posts').append(fragment);
  $('posts-table').hidden = false;
  $('list-state').hidden = true;
}

function parseRoute() {
  const path = location.hash.slice(1) || '/posts';
  if (path === '/posts') return { view: 'list' };
  if (path === '/posts/new') return { view: 'write' };
  const match = path.match(/^\/posts\/([^/]+)$/);
  if (match) {
    try { return { view: 'detail', id: decodeURIComponent(match[1]) }; }
    catch { /* Malformed addresses are shown as a missing page. */ }
  }
  return { view: 'detail', missing: true };
}

function detailError(error, missingPage = false) {
  const missing = missingPage || error.status === 404;
  $('detail-loading').hidden = true;
  $('detail-content').hidden = true;
  $('detail-error').hidden = false;
  $('detail-error-title').textContent = missingPage ? '페이지를 찾지 못했어요'
    : missing ? '게시글을 찾지 못했어요' : '게시글을 불러오지 못했어요';
  $('detail-error-description').textContent = missing
    ? '주소가 올바른지 확인하거나 목록에서 다시 선택해 주세요.' : errorMessage(error);
  $('detail-retry').hidden = missing;
  document.title = `${$('detail-error-title').textContent} | ${config.appName}`;
}

async function renderRoute({ focus = true } = {}) {
  if (!configured) return;
  viewController?.abort();
  const controller = new AbortController();
  viewController = controller;
  const current = ++revision;
  const isCurrent = () => revision === current && !controller.signal.aborted;
  const route = parseRoute();
  activeView = route.view;
  for (const [name, element] of Object.entries(views)) element.hidden = name !== activeView;
  notice(nextNotice);
  nextNotice = '';
  if (focus) window.scrollTo({ top: 0, behavior: 'instant' });
  void checkHealth(controller.signal, current);

  if (route.view === 'write') {
    document.title = `새 게시글 | ${config.appName}`;
    setBusy(busy);
    if (focus) (busy ? $('write-heading') : $('title')).focus({ preventScroll: true });
    return;
  }
  if (route.view === 'list') {
    document.title = `게시판 | ${config.appName}`;
    $('refresh').disabled = true;
    $('posts-section').setAttribute('aria-busy', 'true');
    listState('loading', '게시글을 불러오고 있어요', '잠시만 기다려 주세요.');
    if (focus) $('page-title').focus({ preventScroll: true });
    try {
      const data = await api('/notes', { signal: controller.signal });
      if (!isCurrent()) return;
      renderPosts(data.notes);
      $('last-updated').textContent = `${updateTime.format(new Date())} 업데이트`;
    } catch (error) {
      if (!isCurrent()) return;
      $('post-count').textContent = '-';
      listState('error', '게시글을 불러오지 못했어요', errorMessage(error));
      $('last-updated').textContent = '목록을 다시 불러와 주세요';
    } finally {
      if (isCurrent()) {
        $('refresh').disabled = false;
        $('posts-section').setAttribute('aria-busy', 'false');
      }
    }
    return;
  }

  document.title = `게시글 | ${config.appName}`;
  $('detail-panel').setAttribute('aria-busy', 'true');
  $('detail-loading').hidden = false;
  $('detail-content').hidden = true;
  $('detail-error').hidden = true;
  try {
    if (route.missing) {
      detailError({}, true);
    } else {
      const { note } = await api(`/notes/${encodeURIComponent(route.id)}`, { signal: controller.signal });
      if (!isCurrent()) return;
      $('detail-title').textContent = note.title;
      $('detail-date').textContent = fullDate.format(new Date(note.createdAt));
      $('detail-date').dateTime = note.createdAt;
      $('detail-body').innerHTML = note.content || '본문이 없는 게시글이에요.';
      $('detail-body').classList.toggle('is-empty', !note.content);
      $('detail-loading').hidden = true;
      $('detail-content').hidden = false;
      document.title = `${note.title} | ${config.appName}`;
    }
  } catch (error) {
    if (!isCurrent()) return;
    detailError(error);
  } finally {
    if (isCurrent()) {
      $('detail-panel').setAttribute('aria-busy', 'false');
      if (focus) ($('detail-error').hidden ? $('detail-title') : $('detail-error-title')).focus({ preventScroll: true });
    }
  }
}

function countCharacters() {
  $('title-count').textContent = `${$('title').value.length.toLocaleString('ko-KR')} / 120`;
  $('content-count').textContent = `${$('content').value.length.toLocaleString('ko-KR')} / 2,000`;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy || !configured) return;
  if (!$('title').value.trim()) {
    notice('제목을 입력해 주세요.', 'error');
    $('title').focus();
    return;
  }
  const submittedRevision = revision;
  const body = { title: $('title').value, content: $('content').value };
  setBusy(true);
  notice();
  try {
    const { note } = await api('/notes', { method: 'POST', body: JSON.stringify(body) });
    form.reset();
    countCharacters();
    if (revision === submittedRevision && activeView === 'write') {
      nextNotice = '게시글을 등록했어요.';
      location.hash = `/posts/${encodeURIComponent(note.id)}`;
    } else if (activeView === 'list') {
      const refresh = renderRoute({ focus: false });
      const refreshedRevision = revision;
      await refresh;
      if (revision === refreshedRevision && activeView === 'list') notice('게시글을 등록했어요.');
    }
  } catch (error) {
    if (activeView === 'write') notice(errorMessage(error), 'error');
  } finally {
    setBusy(false);
  }
});

$('title').addEventListener('input', countCharacters);
$('content').addEventListener('input', countCharacters);
$('refresh').addEventListener('click', () => configured ? renderRoute({ focus: false }) : initialize());
$('list-retry').addEventListener('click', () => configured ? renderRoute({ focus: false }) : initialize());
$('detail-retry').addEventListener('click', () => renderRoute());
window.addEventListener('hashchange', () => { void renderRoute(); });
document.querySelector('.skip-link').addEventListener('click', (event) => {
  event.preventDefault();
  $('main').focus();
});

async function initialize() {
  if (initializing) return;
  initializing = true;
  $('refresh').disabled = true;
  $('list-retry').hidden = true;
  listState('loading', '게시판을 준비하고 있어요', '잠시만 기다려 주세요.');
  try {
    const response = await fetch('/runtime-config.json', { cache: 'no-store', signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error('화면 설정을 불러오지 못했어요.');
    config = await response.json();
    $('app-name').textContent = config.appName;
    $('environment').textContent = config.environment.toUpperCase();
    $('fe-version').textContent = config.version;
    const provider = Object.hasOwn(providers, config.cloudProvider) ? config.cloudProvider : 'default';
    document.documentElement.dataset.cloudProvider = provider;
    $('provider-name').textContent = providers[provider].name;
    $('provider-badge').setAttribute('aria-label', `배포 환경: ${providers[provider].name}`);
    document.querySelector('meta[name="theme-color"]').content = providers[provider].color;
    configured = true;
    setBusy(false);
    await renderRoute({ focus: false });
  } catch (error) {
    connection(false);
    $('posts-section').setAttribute('aria-busy', 'false');
    listState('error', '게시판을 준비하지 못했어요', errorMessage(error));
    $('refresh').disabled = false;
  } finally {
    initializing = false;
  }
}

void initialize();
