'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="jarvis-token"]').content;
let state = null, busy = false, requestId = null, pullRequestId = null, oldest = null;
// 열린 화면과 작업 중인 방은 별개다. 스트림은 방을 이동해도 같은 요청에 붙어 있다.
const roomRequests = new Map(), roomDrafts = new Map(), unreadRooms = new Set();
const roomViews = new Map();  // 명령 결과·diff 버튼도 방을 오갈 때 보존한다(기존 DB 저장 범위는 유지).
let serverRequests = new Map(), auxiliaryBusy = false, desiredSession = null;
let snapshotRequest = 0, navigationVersion = 0, navigating = false, polling = false;
function composerKey() { return openProject ? `project:${openProject}` : state ? `session:${state.session_id}` : null; }
function saveComposer() {
  const key = composerKey();
  if (key) roomDrafts.set(key, {text: $('input').value, attachments: pendingAttachments.slice()});
}
function restoreComposer() {
  const draft = roomDrafts.get(composerKey());
  $('input').value = draft?.text || '';
  pendingAttachments = draft?.attachments?.slice() || [];
  renderAttachments(); fitInput();
}
function saveRoomView() {
  if (!state || openProject) return;
  roomViews.set(state.session_id, {nodes: Array.from($('messages').children), oldest,
    hasMore: !$('older').hidden, latestMessageId: state.messages.at(-1)?.id || 0});
  if (roomViews.size > 20) {
    const oldestRoom = Array.from(roomViews.keys()).find(sid => sid !== state.session_id && !roomRequests.has(sid));
    if (oldestRoom) roomViews.delete(oldestRoom);
  }
}
function roomRequest(sid) { return roomRequests.get(sid) || serverRequests.get(sid); }
function viewedRequest() { return openProject ? null : roomRequest(state?.session_id); }
function anyWork() { return auxiliaryBusy || roomRequests.size > 0 || serverRequests.size > 0 || Boolean(state?.busy); }
function requestBadge(sid) {
  const job = roomRequest(sid);
  if (job) return job.finished ? (unreadRooms.has(sid) ? '새 답변' : '') : job.status === 'running' ? '생성 중' : '대기 중';
  return unreadRooms.has(sid) ? '새 답변' : '';
}
function paintRequestBadges() {
  document.querySelectorAll('[data-room-status]').forEach(el => {
    const label = requestBadge(el.dataset.roomStatus);
    el.textContent = label; el.hidden = !label;
    el.classList.toggle('unread', label === '새 답변');
  });
}
let settingsModelsRequest = 0;
let pendingAttachments = [];
// 여러 사람이 쓰기·프로젝트(2026-10-11): 열려 있는 프로젝트 화면과 그 내용
let openProject = null, projectData = null, editingProject = null;
const headers = {'Content-Type': 'application/json', 'X-Jarvis-Token': token};
// 로그인·사용자 관리 화면은 정식 버전에서 연다(2026-10-11 사용자: "지금은 내 컴퓨터만"). 서버의 사람별 구분은 그대로 둔다.
const ACCOUNTS_UI = false;
// 화면 테마: 밝게(기본)·어둡게·시스템 따라. 이 브라우저에만 기억한다.
function applyTheme(choice) {
  const theme = ['light', 'dark', 'system'].includes(choice) ? choice : 'light';
  if (theme === 'light') document.documentElement.removeAttribute('data-theme'); else document.documentElement.dataset.theme = theme;
  const dark = theme === 'dark' || (theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.querySelector('meta[name="theme-color"]').content = dark ? '#121212' : '#ffffff';
  document.querySelectorAll('[data-theme-choice]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.themeChoice === theme)));
  return theme;
}
function savedTheme() { try { return localStorage.getItem('alex-theme') || 'light'; } catch { return 'light'; } }
applyTheme(savedTheme());
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => applyTheme(savedTheme()));
document.querySelectorAll('[data-theme-choice]').forEach(b => b.addEventListener('click', () => {
  const theme = applyTheme(b.dataset.themeChoice);
  try { localStorage.setItem('alex-theme', theme); } catch { /* 기억하지 못해도 지금 화면에는 적용됐다 */ }
}));
if (ACCOUNTS_UI) document.querySelectorAll('.accounts-ui').forEach(el => { el.hidden = false; });
const notice = text => { $('notice').textContent = text; };

// 토큰은 프로세스마다 새로 만들어져 페이지에 구워진다(web.py:50). 앱이 재시작되면
// 열려 있던 탭은 옛 토큰을 계속 보내고 서버는 403 을 준다. 예전에는 화면에 아무 일도
// 일어나지 않은 것처럼 보여서 "대답이 없다" 로만 보였다. 원인과 해결을 같이 보여준다.
let tokenExpired = false, appStopped = false;
function showTokenExpired() {
  if (tokenExpired) return;
  tokenExpired = true;
  const box = $('notice');
  box.replaceChildren();
  box.append(node('span', '', '앱이 다시 시작되어 이 페이지의 연결이 만료되었습니다. 새로고침하면 이어서 쓸 수 있습니다. 대화는 저장되어 있습니다.'));
  const button = node('button', 'primary', '새로고침');
  button.type = 'button';
  button.addEventListener('click', () => location.reload());
  box.append(button);
  // 죽은 페이지에 계속 입력하지 않도록 막는다.
  $('send').disabled = true;
  $('input').disabled = true;
  $('media-prompt-open').disabled = true;
  $('screenshot').disabled = true;
}
function showLogin(canUseOwner) {
  if (canUseOwner !== undefined) $('login-owner').hidden = !canUseOwner;
  $('login-screen').hidden = false;
  setTimeout(() => $('login-id').focus(), 0);
}

async function api(path, body) {
  if (appStopped) throw new Error('ALEX가 종료되었습니다. START_ALEX.vbs로 다시 실행한 뒤 새로고침하세요.');
  const r = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers, body: body === undefined ? undefined : JSON.stringify(body)});
  let result;
  try { result = await r.json(); } catch { if (r.status === 403) showTokenExpired(); throw new Error('서버 응답을 읽지 못했습니다. 실행 창을 확인하세요.'); }
  if (r.status === 403 && !result.admin_only) { showTokenExpired(); throw new Error('연결이 만료되었습니다.'); }
  if (r.status === 401 && path !== '/api/login') { showLogin(); throw new Error('로그인이 필요합니다.'); }
  if (!r.ok) throw new Error(result.error || `HTTP ${r.status}`);
  return result;
}
function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}
// HTML 을 해석하지 않고 아이콘을 만든다(index.html 의 아이콘과 같은 모양).
function icon(...paths) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('class', 'i'); svg.setAttribute('aria-hidden', 'true');
  for (const d of paths) { const p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.setAttribute('d', d); svg.append(p); }
  return svg;
}
const folderIcon = () => icon('M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z');
function backendLabel(backend) {
  return {strata: 'Strata', ollama: 'Ollama', mock: '데모'}[backend] || '추론 엔진 확인 필요';
}
function liveBackend(snapshot, kind = 'general') {
  if (snapshot?.backends?.[kind]) return snapshot.backends[kind];
  // A busy server may still run the previous API until its next safe restart.
  // Use its live provider name, never the unapplied settings file.
  const name = snapshot?.models?.[kind === 'coder' ? 1 : 0] || '';
  if (name.startsWith('strata:')) return 'strata';
  if (name.startsWith('ollama:')) return 'ollama';
  return snapshot?.mock ? 'mock' : 'unknown';
}
function scrollBottom() { const el = $('chat-scroll'); el.scrollTop = el.scrollHeight; }
// 주소를 링크로 (2026-09-25: 검색 답의 출처가 글자로만 나와서 누를 수 없었다).
// HTML 을 해석하지 않고 DOM 으로만 만든다. http/https 만 링크가 되고, 새 탭으로 열며,
// noopener noreferrer 로 열린 페이지가 ALEX 탭을 바꾸거나 이 주소(127.0.0.1)를 보지 못하게 한다.
const LINK_RE = /\[([^\]\n]{1,200})\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[A-Za-z0-9\-._~:/?#[\]@!$&'()*+,;=%]+)/g;
// core._compose_web_answer 가 붙이는 출처 줄: "[1] 제목 — https://..."
const SOURCE_LINE = /^\[(\d+)\] (.+) — (https?:\/\/\S+)$/;
function safeHref(raw) {
  try { const u = new URL(raw); return (u.protocol === 'http:' || u.protocol === 'https:') ? u.href : null; }
  catch { return null; }
}
function extLink(text, href) {
  const a = node('a', 'ext-link', text);
  a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
  return a;
}
function appendLinked(el, text) {
  const lines = text.split('\n');
  lines.forEach((line, i) => {
    const src = line.match(SOURCE_LINE);
    const srcHref = src && safeHref(src[3]);
    if (srcHref) {
      // 제목을 누르면 열리고, 어디로 가는지는 옆에 사이트 이름으로 보인다.
      el.append(document.createTextNode(`[${src[1]}] `), extLink(src[2], srcHref),
                node('span', 'link-host', ' · ' + new URL(srcHref).host));
    } else {
      let last = 0;
      for (const m of line.matchAll(LINK_RE)) {
        const label = m[1];
        let raw = m[2] || m[3];
        let end = m.index + m[0].length;
        if (!label) {  // 문장 끝 구두점은 주소가 아니다
          const trimmed = raw.replace(/[.,;:!?'")\]}]+$/, '');
          end -= raw.length - trimmed.length; raw = trimmed;
        }
        const href = safeHref(raw);
        if (!href) continue;
        el.append(document.createTextNode(line.slice(last, m.index)), extLink(label || raw, href));
        last = end;
      }
      el.append(document.createTextNode(line.slice(last)));
    }
    if (i < lines.length - 1) el.append(document.createTextNode('\n'));
  });
}
// 앱이 그린 그림(ADR-073): "![설명](alex-image:20261003-112233-42.png)" 한 줄. 이름 모양을 확인하고,
// 토큰을 붙여 받아 blob 주소로만 보인다(CSP img-src blob:) — 다른 주소의 그림은 그리지 않는다.
const IMAGE_LINE = /^!\[([^\]\n]{0,200})\]\(alex-image:(\d{8}-\d{6}-\d{1,20}\.(?:png|jpg|webp))\)$/;
const ATTACHMENT_LINE = /^!\[([^\]\n]{0,120})\]\(alex-attachment:([a-f0-9]{24}\.(?:png|jpg|webp))\)$/;
function imageFigure(caption, name, attachment = false, sessionId = state?.session_id) {
  const figure = node('figure', 'gen-image');
  const img = node('img'); img.alt = caption; img.title = '클릭하면 새 탭에서 크게 봅니다';
  const cap = node('figcaption', '', caption);
  figure.append(img, cap);
  const path = attachment ? '/api/attachment?name=' + encodeURIComponent(name) + '&session_id=' + encodeURIComponent(sessionId) : '/api/image?name=' + encodeURIComponent(name);
  fetch(path, {headers})
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.blob(); })
    .then(blob => { const url = URL.createObjectURL(blob); img.src = url; img.addEventListener('click', () => window.open(url, '_blank', 'noopener')); })
    .catch(e => { cap.textContent = `${caption} — 그림을 불러오지 못했습니다(${e.message})`; });
  return figure;
}
const VIDEO_LINE = /^!\[([^\]\n]{0,200})\]\(alex-video:(\d{8}-\d{6}-\d{1,20}\.mp4)\)$/;
function videoFigure(caption, name) {
  const figure = node('figure', 'gen-image');
  const video = node('video'); video.controls = true; video.loop = true; video.playsInline = true;
  const cap = node('figcaption', '', caption);
  figure.append(video, cap);
  fetch('/api/video?name=' + encodeURIComponent(name), {headers})
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.blob(); })
    .then(blob => { video.src = URL.createObjectURL(blob); })
    .catch(e => { cap.textContent = `${caption} — 영상을 불러오지 못했습니다(${e.message})`; });
  return figure;
}
const DOCUMENT_LINE = /^\[문서 다운로드\]\(alex-document:(alex-document-\d{8}-\d{6}-[a-f0-9]{16}\.(?:xlsx|docx|pptx|pdf))\)$/;
function documentDownload(name) {
  const button = node('button', 'secondary', '문서 다운로드 · ' + name.split('.').pop().toUpperCase());
  button.type = 'button';
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const r = await fetch('/api/document?name=' + encodeURIComponent(name), {headers});
      if (r.status === 403) { showTokenExpired(); throw new Error('연결이 만료되었습니다.'); }
      if (!r.ok) throw new Error('문서를 찾을 수 없습니다. 다시 내보내 주세요.');
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement('a'); a.href = url; a.download = name;
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { notice(e.message); }
    finally { button.disabled = false; }
  });
  return button;
}
function appendWithImages(el, text, sessionId = state?.session_id) {
  let buffer = [];
  const flush = () => { if (buffer.length) { appendLinked(el, buffer.join('\n')); buffer = []; } };
  for (const line of text.split('\n')) {
    const m = line.match(IMAGE_LINE), a = line.match(ATTACHMENT_LINE), v = line.match(VIDEO_LINE), d = line.match(DOCUMENT_LINE);
    if (m) { flush(); el.append(imageFigure(m[1], m[2])); } else if (a) { flush(); el.append(imageFigure(a[1], a[2], true, sessionId)); } else if (v) { flush(); el.append(videoFigure(v[1], v[2])); } else if (d) { flush(); el.append(documentDownload(d[1])); } else buffer.push(line);
  }
  flush();
}
function mediaLine(line, sessionId = state?.session_id) {
  const m = line.match(IMAGE_LINE), a = line.match(ATTACHMENT_LINE), v = line.match(VIDEO_LINE), d = line.match(DOCUMENT_LINE);
  if (m) return imageFigure(m[1], m[2]);
  if (a) return imageFigure(a[1], a[2], true, sessionId);
  if (v) return videoFigure(v[1], v[2]);
  if (d) return documentDownload(d[1]);
  return null;
}
function renderContent(el, text, markdown = false, sessionId = state?.session_id) {
  el.replaceChildren();
  // ALEX 답은 굵게·목록·표·코드 블록을 꾸며 보인다(markdown.js — HTML 은 실행하지 않고 글자로만 그린다).
  if (markdown && window.AlexMarkdown) {
    el.classList.add('markdown');
    try { window.AlexMarkdown.render(el, text, line => mediaLine(line, sessionId)); return; } catch { el.replaceChildren(); }
  }
  el.classList.remove('markdown');
  // Deliberately small formatter: no HTML evaluation or external markdown scripts.
  const chunks = text.split(/(```[^\n]*\n[\s\S]*?```)/g);
  for (const chunk of chunks) {
    if (chunk.startsWith('```') && chunk.endsWith('```')) {
      const raw = chunk.slice(chunk.indexOf('\n') + 1, -3);
      const value = raw.endsWith('\n') ? raw.slice(0, -1) : raw;
      if (window.AlexMarkdown?.codeBlock) {
        el.append(window.AlexMarkdown.codeBlock(value, chunk.slice(3, chunk.indexOf('\n')).trim()));
        continue;
      }
      const pre = node('pre');
      const code = node('code', '', value);
      pre.append(code); el.append(pre);
    } else appendWithImages(el, chunk, sessionId);
  }
}
function imageLoadingCard(phase, prompt) {
  const card = node('div', 'image-loading-card');
  card.setAttribute('role', 'status');
  const frame = node('div', 'image-loading-frame');
  const center = node('div', 'image-loading-center');
  const spinner = node('span', 'image-loading-spinner');
  spinner.setAttribute('aria-hidden', 'true');
  center.append(spinner, node('strong', '', '이미지 생성 중'));
  frame.append(center);
  card.append(frame, node('div', 'image-loading-phase', phase));
  if (prompt) card.append(node('div', 'image-loading-prompt', prompt));
  return card;
}
function imageProgressInfo(partial, requested) {
  const marker = '[그리는 중: ';
  const start = partial.lastIndexOf(marker);
  if (start >= 0) {
    const rest = partial.slice(start + marker.length);
    const end = rest.lastIndexOf(']\n(');
    return {phase: '그림 모델이 그리고 있습니다…', prompt: (end >= 0 ? rest.slice(0, end) : rest).trim()};
  }
  if (partial.includes('[그림 설명을 영어로 옮기는 중')) {
    return {phase: '프롬프트를 준비하고 있습니다…', prompt: requested};
  }
  return null;
}
// 대화 한 줄: 내 말은 오른쪽 말풍선, ALEX 답은 말풍선 없이. 복사 등은 아래 줄에(마우스를 올리면 보인다).
function message(role, text, parent = $('messages')) {
  const sessionId = state?.session_id;
  const el = node('article', 'message ' + (role === 'user' ? 'user' : 'assistant'));
  const content = node('div', 'message-content');
  const actions = node('div', 'message-actions');
  const copy = node('button', 'copy', '복사'); copy.type = 'button';
  let value = text;
  copy.addEventListener('click', async () => { try { await navigator.clipboard.writeText(value); copy.textContent = '복사됨'; setTimeout(() => { copy.textContent = '복사'; }, 1500); } catch { notice('복사할 텍스트를 직접 선택해 주세요.'); } });
  actions.append(copy); el.append(content, actions); parent.append(el);
  const result = {el, content, actions, update(t, formatted = false) {
    value = t;
    if (formatted) renderContent(content, t, role === 'assistant', sessionId); else content.textContent = t;
    el.querySelector('.learned-actions')?.remove();
    if (formatted && role === 'assistant') {
      const ids = [...t.matchAll(/^— (?:바로잡아 주신 내용을|찾은 내용을) 배웠습니다\(([0-9a-f]{8})/gm)]
        .map(match => match[1]);
      if (ids.length) {
        const row = node('div', 'row learned-actions');
        for (const id of ids) {
          const undo = node('button', 'secondary', `배운 것 취소 (${id})`);
          undo.type = 'button';
          undo.addEventListener('click', () => { if (!busy) { submit(`/undo-learning ${id}`); undo.disabled = true; } });
          row.append(undo);
        }
        el.insertBefore(row, actions);
      }
    }
  }, status(textValue) { content.replaceChildren(node('span', 'queue-note', textValue)); }};
  result.update(text, true); return result;
}
function lock(value) { auxiliaryBusy = value; updateControls(); }
function updateControls() {
  const job = viewedRequest();
  busy = auxiliaryBusy || navigating || Boolean(job);
  requestId = job?.request_id || null;
  if (appStopped) {
    document.querySelectorAll('button, input, textarea, select').forEach(el => { el.disabled = true; });
    return;
  }
  // 토큰이 만료된 뒤에는 lock(false) 가 전송 버튼을 되살리면 안 된다.
  // 새로고침 전까지 어떤 요청도 성공하지 않는다.
  $('send').disabled = busy || tokenExpired || appStopped;
  $('send').hidden = Boolean(job);
  $('input').disabled = tokenExpired || appStopped;
  $('new-chat').disabled = auxiliaryBusy || tokenExpired;
  $('attach').disabled = busy || tokenExpired || appStopped;
  $('screenshot').disabled = busy || tokenExpired || appStopped;
  $('knowledge-upload').disabled = busy || tokenExpired || appStopped;
  $('knowledge-form').querySelectorAll('input,button').forEach(b => { b.disabled = busy || tokenExpired || appStopped; });
  $('attachments').querySelectorAll('button').forEach(button => { button.disabled = busy || tokenExpired || appStopped; });
  $('media-prompt-open').disabled = busy || tokenExpired || appStopped;
  $('stop').hidden = !job;
  $('stop').disabled = !requestId || Boolean(job?.cancelRequested);
  $('stop').title = job?.cancelRequested ? '중단 요청 중…' : '이 대화의 생성 중단';
  document.querySelectorAll('#sessions .session-item, #projects button').forEach(b => { b.disabled = auxiliaryBusy || tokenExpired; });
  document.querySelectorAll('#sessions .session-delete').forEach(b => { b.disabled = auxiliaryBusy || Boolean(roomRequest(b.dataset.sessionId)); });
  document.querySelectorAll('#file-form button, [data-command]').forEach(b => { b.disabled = busy || tokenExpired; });
  paintRequestBadges();
}
// 대화 목록은 날짜별로 묶는다(Claude·ChatGPT 처럼).
function dayGroup(iso) {
  const d = new Date(iso), now = new Date();
  if (Number.isNaN(d.getTime())) return '이전';
  const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  if (days <= 0) return '오늘';
  if (days === 1) return '어제';
  if (days < 7) return '지난 7일';
  if (days < 30) return '지난 30일';
  return `${d.getFullYear()}년 ${d.getMonth() + 1}월`;
}
function paintState(s, history = false) {
  const changed = state && state.session_id !== s.session_id;
  if (changed) { saveComposer(); saveRoomView(); }
  const previousRequests = serverRequests;
  serverRequests = new Map((s.queue?.requests || []).map(job => [job.session_id, job]));
  for (const sid of previousRequests.keys()) {
    if (!serverRequests.has(sid) && !roomRequests.has(sid)) {
      if (sid === s.session_id && !openProject) history = true;
      else unreadRooms.add(sid);
    }
  }
  state = s;
  if (changed) restoreComposer();
  document.body.classList.toggle('member', !s.is_admin);
  $('account-name').textContent = ACCOUNTS_UI ? s.user.name : '이 PC';
  $('account-role').textContent = ACCOUNTS_UI ? `${s.is_admin ? '관리자' : '사용자'} · ${s.user.login}` : '기억 · 할 일 · 설정';
  $('account-avatar').textContent = ACCOUNTS_UI ? (s.user.name || '?').slice(0, 1).toUpperCase() : 'A';
  if (!openProject) {
    $('title').textContent = s.title || '새 대화';
    $('title-project').hidden = !s.project;
    $('title-project').textContent = s.project?.name || '';
  }
  $('session-count').textContent = s.sessions.length || '';
  $('sessions').replaceChildren();
  let group = null;
  for (const item of s.sessions) {
    if (!item.message_count && item.session_id !== s.session_id && !roomRequest(item.session_id)) continue;
    const title = item.title || '새 대화';
    const label = dayGroup(item.updated_at);
    if (label !== group) { group = label; const g = node('div', 'session-group', label); g.dataset.group = label; $('sessions').append(g); }
    const row = node('div', 'session-row');
    row.dataset.group = label;
    const b = node('button', 'session-item' + (item.session_id === s.session_id && !openProject ? ' active' : ''), title);
    b.type = 'button';
    b.title = `${title} · ${item.message_count}개 메시지`;
    b.addEventListener('click', () => openSession(item.session_id));
    b.addEventListener('dblclick', () => renameSession(item.session_id, title));
    const badge = node('small', 'room-status'); badge.dataset.roomStatus = item.session_id;
    const remove = node('button', 'session-delete', '×');
    remove.type = 'button'; remove.title = '이 대화 삭제'; remove.setAttribute('aria-label', `${title} 대화 삭제`);
    remove.addEventListener('click', () => deleteSession(item));
    remove.dataset.sessionId = item.session_id;
    row.append(b, badge, remove);
    $('sessions').append(row);
  }
  if (!s.sessions.length) $('sessions').append(node('div', 'side-empty', '아직 대화가 없습니다.'));
  filterSessions();
  $('projects').replaceChildren();
  for (const p of s.projects || []) {
    const b = node('button', 'project-item' + ((openProject || s.project?.id) === p.id ? ' active' : ''));
    b.type = 'button';
    b.title = `${p.name} · 대화 ${p.session_count}개`;
    b.append(folderIcon(), node('span', '', p.name));
    b.addEventListener('click', () => showProject(p.id));
    $('projects').append(b);
  }
  if (!(s.projects || []).length) $('projects').append(node('div', 'side-empty', '지침·자료를 묶어 두는 공간'));
  $('model-label').textContent = s.mock ? 'DEMO · 실제 AI 아님' : backendLabel(liveBackend(s)) + ' · ' + s.models[0].replace(/^(ollama|strata):/, '');
  if (s.is_admin) {
    $('hardware').textContent = `${s.profile} · ${s.gpu || 'GPU 미확인'}`;
    $('project-root').textContent = s.project_root;
  }
  paintMemories(s.memories || {});
  paintTasks(s.tasks || []);
  if (history) {
    $('messages').replaceChildren();
    const job = roomRequests.get(s.session_id);
    const cached = roomViews.get(s.session_id);
    if (cached && cached.latestMessageId === (s.messages.at(-1)?.id || 0)) {
      $('messages').append(...cached.nodes);
      oldest = cached.oldest;
      $('older').hidden = !cached.hasMore;
    } else {
      roomViews.delete(s.session_id);
      for (const m of s.messages) {
        if (!job || m.id <= job.beforeMessageId) message(m.role, m.content);
      }
      oldest = s.messages[0]?.id;
      $('older').hidden = !s.has_more;
    }
    if (job) $('messages').append(job.user.el, job.answer.el);
    $('welcome').hidden = $('messages').children.length > 0;
    scrollBottom();
  }
  updateControls();
}
async function refresh(history = false, sid = desiredSession || state?.session_id) {
  const revision = ++snapshotRequest;
  const s = await api('/api/state' + (sid ? '?session_id=' + encodeURIComponent(sid) : ''));
  if (revision !== snapshotRequest || (desiredSession && sid !== desiredSession)) return;
  paintState(s, history);
}
async function refreshVisible(history = false) {
  if (navigating || appStopped || tokenExpired) return;
  await refresh(history);
  const id = openProject;
  if (id) {
    const data = await api('/api/project?id=' + encodeURIComponent(id));
    if (openProject === id) paintProject(data);
  }
}
// 대화 고르기·새 대화는 모델을 쓰지 않는다 — 다른 사람의 답을 기다리지 않고 바로 바뀐다.
async function openSession(sid) {
  if (auxiliaryBusy || tokenExpired || appStopped) return;
  saveComposer();
  saveRoomView();
  closeProject();
  setSidebar(false);
  desiredSession = sid;
  const version = ++navigationVersion;
  navigating = true; updateControls();
  try {
    await refresh(true, sid);
    if (version === navigationVersion) { unreadRooms.delete(sid); paintRequestBadges(); }
  } catch (e) { if (version === navigationVersion) { desiredSession = state?.session_id; notice(e.message); } }
  finally { if (version === navigationVersion) { navigating = false; updateControls(); } }
}
// 제목 직접 바꾸기: 위쪽 제목을 누르거나 목록에서 두 번 누른다. 모델을 쓰지 않으니 바로 바뀐다.
async function renameSession(sid, current) {
  if (!sid) return;
  const title = prompt('대화 제목', current || '');
  if (title === null || !title.trim() || title.trim() === current) return;
  try { await api('/api/session/rename', {session_id: sid, title: title.trim()}); await refresh(false); } catch (e) { notice(e.message); }
}
$('title').addEventListener('click', () => { if (!openProject && state) renameSession(state.session_id, state.title); });
async function newChat(projectId = '') {
  saveComposer();
  const version = ++navigationVersion;
  navigating = true; updateControls();
  try {
    const r = await api('/api/session/new', projectId ? {project_id: projectId} : {});
    if (version !== navigationVersion) return null;
    await openSession(r.session_id);
    return r.session_id;
  } finally { if (version === navigationVersion) { navigating = false; updateControls(); } }
}
// 대화 목록의 [×] (2026-09-26 사용자: "대화목록 제거 기능도 필요할거 같은데"). /delete-session 명령을 치지 않아도
// 되게 하고, 확인 창 없이는 보내지 않는다. 보던 대화를 지우면 서버가 새 대화를 만들어 그 ID 를 돌려준다.
async function deleteSession(item) {
  if (auxiliaryBusy || roomRequest(item.session_id) || !state) return;
  const title = item.title || '새 대화';
  if (!confirm(`“${title}” 대화를 삭제할까요?\n메시지 ${item.message_count}개가 지워지고 되돌릴 수 없습니다.`)) return;
  const current = state.session_id;
  lock(true);
  try {
    const r = await api('/api/session/delete', {session_id: current, target: item.session_id, confirmed: true});
    roomDrafts.delete(`session:${item.session_id}`); roomViews.delete(item.session_id); unreadRooms.delete(item.session_id);
    if (state.session_id === current && r.session_id !== current) {
      desiredSession = r.session_id;
      await refresh(true, r.session_id);
    }
    else await refresh(false);
    if (openProject) await showProject(openProject);
    notice(`“${title}” 대화를 삭제했습니다.`);
  } catch (e) { notice(e.message); } finally { lock(false); }
}
async function models() {
  try {
    const x = await api('/api/models');
    // Using x.selected_general instead of x.ready to just check if something is selected and installed
    const isReady = x.selected_general && x.installed.includes(x.selected_general);
    $('connection').classList.toggle('offline', !x.mock && !isReady);
    const engine = backendLabel(x.backend || liveBackend(state));
    const name = (x.selected_general || '').replace(/^(ollama|strata):/, '');
    $('connection-text').textContent = x.mock ? '데모 모드' : isReady ? name : `${engine} 확인 필요`;
    $('connection').title = x.mock ? '데모 모드입니다. start_web.bat으로 실제 모델을 실행하세요.'
      : isReady ? `${engine} 사용 중 · ${x.selected_general} — 누르면 다시 확인` : `${engine} 연결 확인 필요 · 모델 서버를 준비한 뒤 다시 누르세요.`;
    return x.installed;
  } catch (e) { $('connection-text').textContent = '연결 확인 필요'; $('connection').title = e.message; return []; }
}
async function submit(text = $('input').value.trim()) {
  if (busy || !state) return;
  if (roomRequests.size >= 4) { notice('동시에 최대 4개 대화의 요청을 받을 수 있습니다. 하나가 끝난 뒤 보내 주세요.'); return; }
  // 프로젝트 화면에서 쓰면 그 프로젝트 안에 새 대화를 만들고 보낸다(Claude 프로젝트처럼).
  if (openProject && !text.trim().startsWith('/') && text.trim()) {
    try { if (!await newChat(openProject)) return; } catch (e) { notice(e.message); return; }
  }
  const selected = text.trim().startsWith('/') ? [] : pendingAttachments.slice();
  if (!text.trim() && selected.length) text = '첨부한 이미지의 내용을 설명해 주세요.';
  if (!text.trim()) return;
  const body = {message: text, session_id: state.session_id, attachments: selected.map(image => image.name)};
  if (new TextEncoder().encode(JSON.stringify(body)).length > 64000) { notice('요청이 너무 큽니다. 64KB 이하로 줄여 주세요.'); return; }
  const oldSession = state.session_id;
  notice(''); $('input').value = ''; fitInput(); $('welcome').hidden = true;
  if (openProject) closeProject();
  const displayed = text + (selected.length ? '\n' + selected.map(image => `![첨부 이미지 ${image.width}×${image.height}](alex-attachment:${image.name})`).join('\n') : '');
  const user = message('user', displayed);
  clearAttachments(new Set(selected.map(image => image.name)));
  const answer = message('assistant', '');
  const job = {session_id: oldSession, request_id: null, status: 'queued', finished: false,
    beforeMessageId: state.messages.at(-1)?.id || 0, user, answer};
  roomRequests.set(oldSession, job);
  unreadRooms.delete(oldSession);
  saveComposer(); updateControls();
  answer.status('생각하는 중…');
  answer.actions.hidden = true;  // 답이 오기 전에는 복사할 것이 없다
  const imageCommand = text.match(/^\/(?:image-prompt|image)\s+([\s\S]+)/);
  const requestedImage = imageCommand ? imageCommand[1].trim() : text;
  if (imageCommand) answer.content.replaceChildren(imageLoadingCard('요청을 준비하고 있습니다…', requestedImage));
  let imageProgressActive = Boolean(imageCommand);
  let partial = '', finished = false;
  scrollBottom();
  try {
    const r = await fetch('/api/chat/stream', {method: 'POST', headers, body: JSON.stringify(body)});
    if (r.status === 403) { showTokenExpired(); throw new Error('연결이 만료되었습니다.'); }
    if (r.status === 401) { showLogin(); throw new Error('로그인이 필요합니다.'); }
    if (!r.ok) { const x = await r.json(); throw new Error(x.error || `HTTP ${r.status}`); }
    const reader = r.body.getReader(), decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const {value, done} = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, {stream: true});
      const lines = buffer.split('\n'); buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === 'start') { job.request_id = event.request_id; updateControls(); }
        // 여러 사람이 쓸 때: 모델은 한 번에 한 사람의 답만 만든다. 기다리는 동안 순번을 보여 준다.
        if (event.type === 'queue' && !partial) {
          job.status = 'queued'; job.position = event.position;
          answer.status(`차례를 기다리는 중 · 앞에 ${event.position}명`); paintRequestBadges();
        }
        if (event.type === 'running') { job.status = 'running'; answer.status('생각하는 중…'); paintRequestBadges(); }
        if (event.type === 'chunk') {
          job.status = 'running';
          const atBottom = state?.session_id === oldSession && !openProject
            && $('chat-scroll').scrollHeight - $('chat-scroll').scrollTop - $('chat-scroll').clientHeight < 130;
          if (event.text.startsWith('[그림 설명을 영어로 옮기는 중') || event.text.startsWith('[그리는 중: ')) imageProgressActive = true;
          partial += event.text;
          answer.update(partial);
          const imageProgress = imageProgressActive ? imageProgressInfo(partial, requestedImage) : null;
          if (imageProgress) answer.content.replaceChildren(imageLoadingCard(imageProgress.phase, imageProgress.prompt));
          if (atBottom) scrollBottom();
          paintRequestBadges();
        }
        if (event.type === 'error') throw new Error(event.error);
        // 첫 답 뒤에 ALEX 가 지은 대화 제목(첫 메시지 그대로가 아니라 주제로)
        if (event.type === 'title') {
          if (state?.session_id === event.session_id && !openProject) $('title').textContent = event.title;
          await refreshVisible(false);
        }
        if (event.type === 'done') {
          finished = true; job.finished = true;
          const cached = roomViews.get(oldSession);
          if (cached && event.session_id === oldSession && event.last_message_id !== undefined) cached.latestMessageId = event.last_message_id;
          answer.update(event.response, true); answer.actions.hidden = false;
          if (state?.session_id !== oldSession || openProject || navigating || desiredSession !== oldSession) unreadRooms.add(oldSession);
          paintRequestBadges();
          const m = event.metrics || {}, meta = [];
          if (event.waited > 1) meta.push(`대기 ${event.waited}초`);
          if (event.elapsed !== undefined) meta.push(`${event.elapsed}초`);
          if (m.tokens_per_second) meta.push(`${m.tokens_per_second} tokens/s`);
          if (m.first_token_seconds !== null && m.first_token_seconds !== undefined) meta.push(`첫 응답 ${m.first_token_seconds}초`);
          if (m.done_reason === 'length') meta.push('최대 출력 길이에 도달했습니다');
          if (meta.length) answer.actions.append(node('span', 'message-meta', meta.join(' · ')));
          // Only show apply buttons for commands that return real edit proposals.
          if (text.startsWith('/edit-file ') || text.startsWith('/edit-files ') || text === '/dev-next' || text === '/dev-propose' || text === '/dev-auto') {
            const id = event.response.match(/^편집 제안 ID: ([a-f0-9]{12})/m)?.[1];
            if (id) {
              const row = node('div', 'row'), apply = node('button', 'primary', '이 수정안 적용'), discard = node('button', 'secondary', '폐기');
              apply.addEventListener('click', () => { if (!busy && confirm('위 diff를 확인했나요? 원본을 백업하고 이 수정안을 적용합니다.')) { submit(`/apply-edit ${id} --yes`); apply.disabled = true; discard.disabled = true; } });
              discard.addEventListener('click', () => { if (!busy) { submit(`/discard-edit ${id}`); apply.disabled = true; discard.disabled = true; } });
              row.append(apply, discard); answer.el.append(row);
            }
          }
          if (event.session_id !== oldSession && state?.session_id === oldSession && !openProject && !navigating) {
            await openSession(event.session_id);
          } else await refreshVisible(false);
        }
      }
      if (done) break;
    }
    if (!finished) throw new Error('연결이 끊겼습니다. 새로고침해 저장된 응답을 확인하세요.');
  } catch (e) {
    answer.update((partial ? partial + '\n\n' : '') + '오류: ' + e.message); answer.actions.hidden = false;
    if (state?.session_id === oldSession && !openProject) notice('전송한 요청: ' + text.slice(0, 120) + '\n' + e.message);
    else unreadRooms.add(oldSession);
    // Keep the original request available for manual retry, never resend automatically.
    const draftKey = `session:${oldSession}`;
    const draft = roomDrafts.get(draftKey) || {text: '', attachments: []};
    if (!draft.text) { draft.text = text; roomDrafts.set(draftKey, draft); }
    if (state?.session_id === oldSession && !openProject && !$('input').value) restoreComposer();
  } finally {
    roomRequests.delete(oldSession); serverRequests.delete(oldSession);
    updateControls();
    if (state?.session_id === oldSession && !openProject && !navigating) $('input').focus();
    refreshVisible(false).catch(() => {});
  }
}
// 입력창은 글이 길어지면 늘어난다(최대 240px).
function fitInput() { const el = $('input'); el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 240) + 'px'; }
$('input').addEventListener('input', () => { fitInput(); saveComposer(); });
$('chat-form').addEventListener('submit', e => { e.preventDefault(); submit(); });
// Direct mode sends the prompt exactly as entered to ComfyUI; the chat model does not rewrite it.
const mediaPromptDialog = $('media-prompt-dialog');
$('media-prompt-open').addEventListener('click', () => {
  if (busy || tokenExpired || !state) return;
  mediaPromptDialog.showModal();
  $('media-prompt-text').focus();
});
$('media-prompt-close').addEventListener('click', () => mediaPromptDialog.close());
$('media-prompt-cancel').addEventListener('click', () => mediaPromptDialog.close());
$('media-prompt-form').addEventListener('submit', e => {
  e.preventDefault();
  if (busy || tokenExpired || !state) return;
  const prompt = $('media-prompt-text').value.trim();
  if (!prompt) return;
  const video = $('media-prompt-kind').value === 'video', model = $(video ? 'media-prompt-video-model' : 'media-prompt-model').value;
  mediaPromptDialog.close();
  submit(`${video ? '/video-prompt' : '/image-prompt'}${model ? ' --model=' + model : ''} ${prompt}`);
});
$('media-prompt-kind').addEventListener('change', () => {
  const video = $('media-prompt-kind').value === 'video';
  $('media-prompt-model-row').hidden = video;
  $('media-prompt-video-model-row').hidden = !video;
});
function renderAttachments() {
  const list = $('attachments');
  list.replaceChildren();
  list.hidden = !pendingAttachments.length;
  for (const image of pendingAttachments) {
    const card = node('div', 'attachment-preview');
    const preview = document.createElement('img');
    preview.src = image.url; preview.alt = image.filename || '첨부 스크린샷';
    const remove = node('button', 'text-button', '✕');
    remove.type = 'button'; remove.title = '첨부 취소';
    remove.disabled = busy || tokenExpired || appStopped;
    remove.addEventListener('click', () => { if (!busy) clearAttachments(new Set([image.name])); });
    card.append(preview, node('span', '', `${image.width}×${image.height}`), remove);
    list.append(card);
  }
}
function clearAttachments(names = null) {
  pendingAttachments = pendingAttachments.filter(image => {
    if (names && !names.has(image.name)) return true;
    URL.revokeObjectURL(image.url);
    return false;
  });
  renderAttachments();
  saveComposer();
}
async function attachScreenshots(files) {
  if (busy || tokenExpired || appStopped || !state) return;
  const selected = Array.from(files);
  if (!selected.length) return;
  if (pendingAttachments.length + selected.length > 3) { notice('이미지는 한 번에 최대 3개까지 첨부할 수 있습니다.'); return; }
  if (selected.some(file => file.size > 10000000)) { notice('이미지는 파일당 10MB 이하로 보내 주세요.'); return; }
  if (openProject) {  // 첨부는 대화에 붙는다 — 프로젝트 화면이면 그 안에 대화를 먼저 연다
    try { await newChat(openProject); } catch (e) { notice(e.message); return; }
  }
  const sessionId = state.session_id;
  lock(true);
  notice('이미지를 첨부하고 있습니다…');
  try {
    for (const file of selected) {
      const mime = file.type || ({png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp'}[file.name.split('.').pop().toLowerCase()]);
      if (!['image/png', 'image/jpeg', 'image/webp'].includes(mime)) throw new Error('PNG·JPEG·WebP 이미지만 첨부할 수 있습니다.');
      const response = await fetch('/api/attachment/upload', {method: 'POST', body: file,
        headers: {'X-Jarvis-Token': token, 'X-Jarvis-Session': sessionId, 'Content-Type': mime}});
      if (response.status === 403) { showTokenExpired(); throw new Error('연결이 만료되었습니다.'); }
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      if (state.session_id !== sessionId) throw new Error('대화가 바뀌었습니다. 다시 첨부해 주세요.');
      pendingAttachments.push({...result.attachment, filename: file.name, url: URL.createObjectURL(file)});
    }
    notice('');
  } catch (error) { notice('이미지 첨부: ' + error.message); }
  finally { lock(false); renderAttachments(); $('input').focus(); }
}
$('screenshot').addEventListener('click', () => { if (!busy) $('screenshot-file').click(); });
$('screenshot-file').addEventListener('change', event => {
  const files = Array.from(event.target.files); event.target.value = '';
  attachScreenshots(files);
});
$('input').addEventListener('paste', event => {
  const files = Array.from(event.clipboardData?.items || []).filter(item => item.kind === 'file' && item.type.startsWith('image/')).map(item => item.getAsFile()).filter(Boolean);
  if (files.length) { event.preventDefault(); attachScreenshots(files); }
});
const composer = document.querySelector('.composer');
composer.addEventListener('dragover', event => {
  if (Array.from(event.dataTransfer?.types || []).includes('Files')) { event.preventDefault(); composer.classList.add('drag-over'); }
});
composer.addEventListener('dragleave', () => composer.classList.remove('drag-over'));
composer.addEventListener('drop', event => {
  composer.classList.remove('drag-over');
  if (event.dataTransfer?.files.length) { event.preventDefault(); attachScreenshots(event.dataTransfer.files); }
});
// 참고 그림 올리기(ADR-074 보충): 그림 바이트를 토큰과 함께 보내고, 받은 답이 들어간 대화를 다시 그린다.
$('attach').addEventListener('click', () => { if (!busy) $('attach-file').click(); });
$('attach-file').addEventListener('change', async e => {
  const file = e.target.files[0]; e.target.value = '';
  if (!file || busy) return;
  if (file.size > 10000000) { notice('그림이 너무 큽니다(10MB 이하).'); return; }
  if (openProject) { try { await newChat(openProject); } catch (err) { notice(err.message); return; } }
  lock(true);
  try {
    const r = await fetch('/api/image/upload', {method: 'POST', body: file, headers: {'X-Jarvis-Token': token,
      'Content-Type': file.type || 'application/octet-stream', 'X-Jarvis-Session': state.session_id,
      'X-File-Name': encodeURIComponent(file.name)}});
    if (r.status === 403) { showTokenExpired(); return; }
    const result = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(result.error || `HTTP ${r.status}`);
    await refresh(true);
    scrollBottom();
    notice('그림을 올렸습니다. 이어서 "이 캐릭터를 ○○로 저장해줘"라고 해 보세요.');
  } catch (err) { notice('그림을 올리지 못했습니다: ' + err.message); }
  finally { lock(false); }
});
$('input').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } });
$('new-chat').addEventListener('click', async () => {
  if (auxiliaryBusy || tokenExpired || appStopped) return;
  setSidebar(false);
  try { await newChat(); $('input').focus(); } catch (e) { notice(e.message); }
});
$('connection').addEventListener('click', models);
$('stop').addEventListener('click', async () => {
  const job = viewedRequest();
  if (!job?.request_id) return;
  job.cancelRequested = true; updateControls();
  try { await api('/api/cancel', {request_id: job.request_id}); }
  catch (e) { job.cancelRequested = false; updateControls(); notice(e.message); }
});
document.querySelectorAll('[data-prompt]').forEach(b => b.addEventListener('click', () => submit(b.dataset.prompt)));
document.querySelectorAll('[data-command]').forEach(b => b.addEventListener('click', () => submit(b.dataset.command)));
$('developer-form').addEventListener('submit', e => { e.preventDefault(); if (busy) return; const source = $('developer-file').value.trim(), test = $('developer-test').value.trim(), goal = $('developer-goal').value.trim(); if (source && test && goal && !source.includes('::') && !test.includes('::')) submit(`/dev-start ${source} :: ${test} :: ${goal}`); });
$('file-form').addEventListener('submit', e => { e.preventDefault(); const p = $('file-path').value.trim(), q = $('file-question').value.trim(); if (!busy && p && q) submit(`${e.submitter?.value === 'edit' ? '/edit-file' : '/ask-file'} ${p} :: ${q}`); });
$('knowledge-form').addEventListener('submit', e => {
  e.preventDefault();
  const path = $('knowledge-path').value.trim();
  if (!busy && path) submit('/learn-file ' + path);
});
$('knowledge-upload').addEventListener('click', () => { if (!busy && !tokenExpired && !appStopped) $('knowledge-file').click(); });
$('knowledge-file').addEventListener('change', async e => {
  const file = e.target.files[0]; e.target.value = '';
  if (!file || busy || tokenExpired || appStopped) return;
  if (file.size > 20000000 || !file.size) { notice('자료는 빈 파일이 아니어야 하고 20MB 이하여야 합니다.'); return; }
  lock(true); notice('자료 본문을 읽고 로컬 지식으로 저장하고 있습니다…');
  try {
    const r = await fetch('/api/knowledge/upload', {method: 'POST', body: file,
      headers: {'Content-Type': file.type || 'application/octet-stream', 'X-Jarvis-Token': token,
        'X-Jarvis-Session': state.session_id, 'X-File-Name': encodeURIComponent(file.name)}});
    if (r.status === 403) { showTokenExpired(); throw new Error('연결이 만료되었습니다.'); }
    const result = await r.json();
    if (!r.ok) throw new Error(result.error || `HTTP ${r.status}`);
    notice(result.response);
  } catch (error) { notice('자료 저장: ' + error.message); }
  finally { lock(false); }
});
$('older').addEventListener('click', async () => {
  if (!oldest || busy) return;
  const button = $('older'); button.disabled = true;
  const sid = state.session_id, version = navigationVersion;
  try {
    const page = await api(`/api/history?session_id=${encodeURIComponent(sid)}&before=${oldest}`);
    if (state.session_id !== sid || version !== navigationVersion) return;
    const fragment = document.createDocumentFragment();
    for (const m of page.messages) message(m.role, m.content, fragment);
    $('messages').prepend(fragment); oldest = page.messages[0]?.id; button.hidden = !page.has_more;
  } catch (e) { notice(e.message); } finally { button.disabled = appStopped || busy; }
});

// ── 기억·할 일: 나만 보는 것. 모델을 쓰지 않으니 바로 저장된다.
function paintMemories(memories) {
  $('memory-count').textContent = Object.keys(memories).length || '';
  $('memories').replaceChildren();
  for (const [key, value] of Object.entries(memories)) {
    const row = node('div', 'memory'), text = node('div');
    text.append(node('strong', '', key), node('span', '', value));
    const remove = node('button', 'text-button', '삭제'); remove.type = 'button';
    remove.addEventListener('click', async () => {
      if (!confirm(`“${key}” 기억을 삭제할까요?`)) return;
      try { paintMemories((await api('/api/memory', {action: 'delete', key})).memories); } catch (e) { notice(e.message); }
    });
    row.append(text, remove); $('memories').append(row);
  }
  if (!Object.keys(memories).length) $('memories').append(node('small', '', '저장한 기억이 없습니다. 예: 선호언어 = 한국어'));
}
function paintTasks(tasks) {
  $('task-count').textContent = tasks.filter(t => t.status === 'open').length || '';
  $('tasks').replaceChildren();
  for (const task of tasks) {
    const row = node('div', 'task' + (task.status === 'done' ? ' done' : ''));
    const cb = node('input'); cb.type = 'checkbox'; cb.checked = task.status === 'done'; cb.setAttribute('aria-label', task.text);
    cb.addEventListener('change', async () => {
      try { paintTasks((await api('/api/task', {action: cb.checked ? 'done' : 'open', id: task.task_id})).tasks); } catch (e) { notice(e.message); }
    });
    const remove = node('button', 'text-button', '삭제'); remove.type = 'button';
    remove.addEventListener('click', async () => {
      try { paintTasks((await api('/api/task', {action: 'delete', id: task.task_id})).tasks); } catch (e) { notice(e.message); }
    });
    row.append(cb, node('span', '', task.text), remove); $('tasks').append(row);
  }
  if (!tasks.length) $('tasks').append(node('small', '', '할 일이 없습니다.'));
}
$('memory-form').addEventListener('submit', async e => {
  e.preventDefault();
  const key = $('memory-key').value.trim(), value = $('memory-value').value.trim();
  if (!key || !value) return;
  if (key.includes('=')) { notice('기억 이름에는 = 를 쓸 수 없습니다.'); return; }
  try { paintMemories((await api('/api/memory', {action: 'add', key, value})).memories); $('memory-key').value = ''; $('memory-value').value = ''; }
  catch (err) { notice(err.message); }
});
$('task-form').addEventListener('submit', async e => {
  e.preventDefault();
  const text = $('task-text').value.trim();
  if (!text) return;
  try { paintTasks((await api('/api/task', {action: 'add', text})).tasks); $('task-text').value = ''; } catch (err) { notice(err.message); }
});

// ── 프로젝트(Gemini Gems·Claude Projects 처럼): 이름 + 지침 + 자료 + 그 안의 대화
async function showProject(id) {
  if (auxiliaryBusy || tokenExpired || appStopped) return;
  saveComposer();
  saveRoomView();
  const version = ++navigationVersion;
  navigating = true; updateControls();
  try {
    const data = await api('/api/project?id=' + encodeURIComponent(id));
    if (version !== navigationVersion) return;
    openProject = id;
    restoreComposer();
    paintProject(data);
    $('project-view').hidden = false; $('chat-view').hidden = true;
    $('title').textContent = data.project.name; $('title-project').hidden = true;
    $('input').placeholder = `“${data.project.name}” 안에서 새 대화 시작`;
    setSidebar(false);
    if (state) paintState(state);
  } catch (e) { if (version === navigationVersion) notice(e.message); }
  finally { if (version === navigationVersion) { navigating = false; updateControls(); } }
}
function closeProject() {
  if (!openProject) return;
  saveComposer();
  openProject = null; projectData = null;
  restoreComposer();
  $('project-view').hidden = true; $('chat-view').hidden = false;
  $('input').placeholder = 'ALEX 에게 메시지 보내기';
  if (state) paintState(state);
}
function paintProject(data) {
  projectData = data;
  $('project-name').textContent = data.project.name;
  const instructions = $('project-instructions');
  instructions.textContent = data.project.instructions || '아직 지침이 없습니다. “지침 편집”에서 ALEX 가 이 프로젝트에서 어떻게 답할지 적어 두세요.';
  instructions.classList.toggle('empty', !data.project.instructions);
  $('project-file-count').textContent = data.files.length || '';
  $('project-files').replaceChildren();
  for (const file of data.files) {
    const row = node('div', 'file-row');
    const remove = node('button', 'text-button', '×'); remove.type = 'button'; remove.title = '이 프로젝트에서 빼기';
    remove.addEventListener('click', async () => {
      if (!confirm(`“${file.title}” 자료를 이 프로젝트에서 뺄까요?`)) return;
      try { paintProject(await api('/api/project', {action: 'unlink_file', id: openProject, document_id: file.id})); } catch (e) { notice(e.message); }
    });
    row.append(node('span', '', file.title), node('small', 'muted', `${file.pages}쪽`), remove);
    $('project-files').append(row);
  }
  if (!data.files.length) $('project-files').append(node('small', 'muted', '올린 자료가 없습니다.'));
  $('project-session-count').textContent = data.sessions.length || '';
  $('project-sessions').replaceChildren();
  for (const item of data.sessions) {
    const row = node('div', 'psession-row');
    row.tabIndex = 0;
    row.append(node('span', '', item.title || '새 대화'), node('small', 'muted', dayGroup(item.updated_at)));
    const badge = node('small', 'room-status'); badge.dataset.roomStatus = item.session_id;
    row.append(badge);
    row.addEventListener('click', () => openSession(item.session_id));
    row.addEventListener('keydown', e => { if (e.key === 'Enter') openSession(item.session_id); });
    $('project-sessions').append(row);
  }
  if (!data.sessions.length) $('project-sessions').append(node('small', 'muted', '아직 대화가 없습니다. 아래 입력창에 쓰면 시작됩니다.'));
  paintRequestBadges();
}
$('title-project').addEventListener('click', () => { if (state?.project) showProject(state.project.id); });
$('project-new').addEventListener('click', () => {
  editingProject = null;
  $('project-dialog-title').textContent = '새 프로젝트';
  $('project-form-name').value = ''; $('project-form-instructions').value = ''; $('project-error').textContent = '';
  $('project-dialog').showModal(); $('project-form-name').focus();
});
$('project-edit').addEventListener('click', () => {
  if (!projectData) return;
  editingProject = projectData.project.id;
  $('project-dialog-title').textContent = '프로젝트 편집';
  $('project-form-name').value = projectData.project.name; $('project-form-instructions').value = projectData.project.instructions;
  $('project-error').textContent = '';
  $('project-dialog').showModal();
});
$('project-form').addEventListener('submit', async e => {
  e.preventDefault();
  const name = $('project-form-name').value.trim(), instructions = $('project-form-instructions').value;
  try {
    const data = await api('/api/project', editingProject ? {action: 'update', id: editingProject, name, instructions} : {action: 'create', name, instructions});
    $('project-dialog').close();
    await refresh();
    await showProject(data.project.id);
  } catch (err) { $('project-error').textContent = err.message; }
});
$('project-delete').addEventListener('click', async () => {
  if (!projectData || anyWork()) return;
  const {project, sessions} = projectData;
  if (!confirm(`“${project.name}” 프로젝트를 삭제할까요?\n안의 대화 ${sessions.length}개와 이 프로젝트에만 올린 자료도 지워지고 되돌릴 수 없습니다.`)) return;
  try {
    const r = await api('/api/project', {action: 'delete', id: project.id, confirmed: true});
    closeProject();
    desiredSession = r.session_id;
    await refresh(true, r.session_id);
    notice(`“${project.name}” 프로젝트를 삭제했습니다.`);
  } catch (e) { notice(e.message); }
});
$('project-upload').addEventListener('click', () => { if (openProject) $('project-file').click(); });
$('project-file').addEventListener('change', async e => {
  const file = e.target.files[0]; e.target.value = '';
  if (!file || !openProject) return;
  if (file.size > 20000000 || !file.size) { notice('자료는 빈 파일이 아니어야 하고 20MB 이하여야 합니다.'); return; }
  notice('자료를 읽어 이 프로젝트에 저장하고 있습니다…');
  try {
    const r = await fetch('/api/project/upload', {method: 'POST', body: file, headers: {'X-Jarvis-Token': token,
      'Content-Type': file.type || 'application/octet-stream', 'X-Project-Id': openProject, 'X-File-Name': encodeURIComponent(file.name)}});
    if (r.status === 403) { showTokenExpired(); throw new Error('연결이 만료되었습니다.'); }
    const result = await r.json();
    if (!r.ok) throw new Error(result.error || `HTTP ${r.status}`);
    paintProject(result);
    notice(`“${result.document.title}”을(를) 저장했습니다 · ${result.document.pages}쪽.`);
  } catch (err) { notice('자료 저장: ' + err.message); }
});

// ── 설정(관리자는 모델·사용자까지, 사용자는 문서 저장만)
async function populateSettingsModels() {
  const request = ++settingsModelsRequest, backend = $('model-backend').value;
  $('settings-save').disabled = true;
  $('settings-error').textContent = '모델 목록을 확인하는 중입니다…';
  let data;
  try { data = await api('/api/models?backend=' + encodeURIComponent(backend)); }
  catch (e) { if (request === settingsModelsRequest) $('settings-error').textContent = e.message; return; }
  if (request !== settingsModelsRequest) return;
  const installed = data.installed, s = state.settings, strata = backend === 'strata';
  $('settings-error').textContent = '';
  $('settings-model-note').textContent = strata
    ? (data.can_start ? '설치된 Strata 서버가 꺼져 있습니다. 설정 저장을 누르면 시작합니다. 첫 모델 적재는 1~3분 걸릴 수 있습니다. ' : '') + 'Strata 서버의 모델 하나를 대화·코딩에 공유합니다. 다른 모델을 쓰려면 위에서 Ollama를 선택하세요. 엔진은 설정 저장 시 전환합니다.'
    : (data.offline_store ? '저장된 Ollama 모델입니다. 설정 저장 시 Ollama 서버를 준비합니다. ' : '') + '대화·코딩 모델을 선택하세요. 엔진 전환 전에 기존 모델의 GPU 반환을 확인합니다.';
  for (const [id, key] of [['general-model', s.auto_profile_models && state.profile === 'DESKTOP' ? 'desktop_general_model' : 'general_model'], ['coder-model', s.auto_profile_models && state.profile === 'DESKTOP' ? 'desktop_coder_model' : 'coder_model']]) {
    let selected = strata ? data.selected_general || s.strata_model : s[key];
    if (!strata && backend !== liveBackend(state) && !installed.includes(selected)) {
      const saved = s[id === 'general-model' ? 'general_model' : 'coder_model'];
      selected = installed.includes(saved) ? saved : '';
    }
    $(id).replaceChildren();
    if (!selected) { const o = node('option', '', '모델을 선택하세요'); o.value = ''; $(id).append(o); }
    for (const name of installed) { const o = node('option', '', name); o.value = name; $(id).append(o); }
    // Preserve an unavailable configured model; opening settings must not replace it.
    if (selected && !installed.includes(selected)) {
      const o = node('option', '', selected + (data.can_start ? ' (서버 시작 후 확인)' : ' (서버 확인 필요)'));
      o.value = selected; $(id).append(o);
    }
    $(id).value = selected || '';
    $(id).disabled = strata || appStopped;
  }
  $('single-model').checked = strata || s.single_model_on_laptop;
  $('single-model').disabled = strata || state.profile === 'DESKTOP';
  updateSharedModel();
  $('settings-save').disabled = appStopped || (installed.length === 0 && !data.can_start);
  if (!installed.length && !data.can_start) $('settings-error').textContent = strata
    ? data.startup_error || 'Strata 서버에 연결하지 못했습니다. ALEX를 다시 실행한 뒤 확인하세요.'
    : '사용 가능한 모델이 없습니다. setup_local.bat으로 Ollama와 모델을 먼저 준비하세요.';
}
$('model-backend').addEventListener('change', populateSettingsModels);
async function openSettings() {
  if (!state) return;
  closeMenu();
  $('settings-dialog').showModal();
  if (!state.is_admin) return;
  $('settings-error').textContent = '';
  $('settings-save').disabled = true;
  try { await refresh(); } catch (e) { $('settings-error').textContent = e.message; return; }
  if (ACCOUNTS_UI) loadUsers();
  const s = state.settings;
  $('model-backend').value = liveBackend(state) === 'strata' ? 'strata' : 'ollama';
  // 설정 파일의 값이 목록에 없으면 select 가 조용히 비고, 저장하면 Number('') = 0 이 들어간다.
  // 실측으로 정한 6144 가 목록(4096까지)에 없어서 실제로 그 상태가 나왔다. 없으면 만들어 넣는다.
  syncEngineLimits();
  $('think').checked = s.ollama_think;
  const effective = state.effective_settings;
  $('effective-settings').textContent = effective ? [
    '현재 대화 모델: ' + backendLabel(liveBackend(state)) + ' · ' + effective.general.model,
    '현재 코딩 모델: ' + backendLabel(liveBackend(state, 'coder')) + ' · ' + effective.coder.model + (effective.shared ? ' (대화 모델 공유)' : ''),
    ...(state.context_limits ? [`입력 한도: ${state.context_limits.chars.toLocaleString()}자 · 최근 대화 ${state.context_limits.history}개`] : []),
    ...['general', 'coder'].filter(k => k === 'general' || !effective.shared).flatMap(k => {
      const p = effective[k];
      return p.num_predict === undefined ? [] : [
        `${k === 'general' ? '대화' : '코딩'}: 실제 출력 한도 ${p.num_predict} 토큰 · Thinking ${p.think ? '켜짐' : '꺼짐'} · 시간 제한 ${p.timeout}초`
      ];
    }),
  ].join('\n') : '실제 적용 설정은 앱을 다시 시작한 뒤 확인할 수 있습니다.';
  if (anyWork()) { $('settings-error').textContent = '모든 대화의 요청이 끝난 뒤 모델 설정을 바꿀 수 있습니다.'; return; }
  await populateSettingsModels();
}
$('settings-open').addEventListener('click', openSettings);
$('settings-open-menu').addEventListener('click', openSettings);
// 엔진을 고르면 그 엔진의 출력 길이와 입력 한도를 보여 준다(2026-10-11 "선택에 따라서 자동으로 설정을 바뀌게끔").
// Ollama 는 8192 토큰까지(16K 문맥 기준), Strata 는 문맥 창의 절반까지.
function syncEngineLimits() {
  const s = state.settings, strata = $('model-backend').value === 'strata', desktop = state.profile === 'DESKTOP';
  for (const o of $('output-limit').options) o.disabled = strata ? Number(o.value) > (s.strata_num_ctx || 65536) / 2 : Number(o.value) > 8192;
  setOutputLimit(strata ? s.strata_max_output_tokens || 16384 : (desktop && s.desktop_max_output_tokens) || s.local_max_output_tokens || 1024);
  const limits = (state.engine_limits || {})[strata ? 'strata' : 'ollama'];
  $('output-scope').textContent = (strata ? `Strata(문맥 ${Math.round((s.strata_num_ctx || 65536) / 1024)}K)` : desktop ? 'Ollama · 이 데스크톱' : 'Ollama · 노트북')
    + '의 출력 길이입니다.' + (limits ? ` 이 엔진을 고르면 입력 ${limits.chars.toLocaleString()}자, 최근 대화 ${limits.history}개로 자동으로 맞춥니다.` : '');
}
$('model-backend').addEventListener('change', syncEngineLimits);
function updateSharedModel() {
  const strata = $('model-backend').value === 'strata';
  const shared = state.profile !== 'DESKTOP' && $('single-model').checked;
  $('coder-model').disabled = strata || shared || appStopped;
  $('coder-model-note').textContent = strata ? 'Strata 모델을 대화·코딩에 함께 사용합니다.' : shared ? '일반 대화 모델을 코딩에도 사용합니다. 별도 코딩 모델 선택은 적용되지 않습니다.' : '코딩 요청과 OpenCode 작업에 사용할 모델을 선택하세요.';
}
$('single-model').addEventListener('change', updateSharedModel);
function setOutputLimit(value) {
  const select = $('output-limit'), wanted = String(value);
  if (![...select.options].some(o => o.value === wanted)) {
    const o = node('option', '', wanted + ' (설정 파일)');
    o.value = wanted;
    select.append(o);
    [...select.options].sort((a, b) => Number(a.value) - Number(b.value)).forEach(x => select.append(x));
  }
  select.value = wanted;
}
$('settings-close').addEventListener('click', () => $('settings-dialog').close());
$('settings-form').addEventListener('submit', async e => {
  e.preventDefault(); $('settings-save').disabled = true;
  const desktop = state.profile === 'DESKTOP';
  const profileModels = desktop && state.settings.auto_profile_models;
  const outputKey = $('model-backend').value === 'strata' ? 'strata_max_output_tokens' : desktop ? 'desktop_max_output_tokens' : 'local_max_output_tokens';
  const changes = {
    local_backend: $('model-backend').value,
    [outputKey]: Number($('output-limit').value),
    ollama_think: $('think').checked,
  };
  if (changes.local_backend !== 'strata') {
    changes[profileModels ? 'desktop_general_model' : 'general_model'] = $('general-model').value;
    if (!desktop) changes.single_model_on_laptop = $('single-model').checked;
    if (!$('coder-model').disabled) changes[profileModels ? 'desktop_coder_model' : 'coder_model'] = $('coder-model').value;
  }
  lock(true); $('model-backend').disabled = true; $('settings-close').disabled = true;
  $('settings-error').textContent = changes.local_backend !== liveBackend(state) ? '이전 모델의 GPU 반환을 확인하고 엔진을 준비하는 중입니다. Strata 첫 실행은 1~3분 걸릴 수 있습니다.' : '설정을 저장하는 중입니다…';
  try { await api('/api/settings', {session_id: state.session_id, settings: changes}); await refresh(); await models(); $('settings-dialog').close(); notice('모델 설정을 저장했습니다. 다음 메시지부터 적용됩니다.'); }
  catch (e) { $('settings-error').textContent = e.message; }
  finally { lock(false); $('settings-save').disabled = appStopped; $('model-backend').disabled = appStopped; $('settings-close').disabled = appStopped; }
});
// 설정 창 안의 명령(문서 저장·백업·모델 내리기)은 창을 닫고 대화에서 실행한다.
document.querySelectorAll('#settings-dialog [data-command]').forEach(b => b.addEventListener('click', () => $('settings-dialog').close()));

// ── 사용자 관리(관리자)
async function loadUsers() {
  try { paintUsers((await api('/api/users')).users); } catch (e) { $('users').replaceChildren(node('small', 'muted', e.message)); }
}
function paintUsers(users) {
  $('users').replaceChildren();
  for (const user of users) {
    const row = node('div', 'user-row'), info = node('div'), actions = node('div', 'row-actions');
    const facts = [user.login, user.role === 'admin' ? '관리자' : '사용자'];
    if (user.tailscale_login) facts.push('Tailscale ' + user.tailscale_login);
    if (!user.has_password) facts.push('비밀번호 없음(이 PC 에서만)');
    if (user.disabled) facts.push('사용 중지됨');
    info.append(node('strong', '', user.name), node('small', 'muted', facts.join(' · ')));
    const act = (label, handler) => { const b = node('button', 'text-button', label); b.type = 'button'; b.addEventListener('click', handler); actions.append(b); };
    const run = async body => { try { paintUsers((await api('/api/users', {...body, id: user.id})).users); } catch (e) { alert(e.message); } };
    act('비밀번호', () => { const password = prompt(`${user.name}의 새 비밀번호(8자 이상)`); if (password) run({action: 'password', password}); });
    act('Tailscale', () => { const login = prompt('연결할 Tailscale 계정(메일). 비우면 연결을 끊습니다.', user.tailscale_login); if (login !== null) run({action: 'update', tailscale_login: login.trim()}); });
    if (user.id !== 'owner' && user.id !== state.user.id) act(user.disabled ? '다시 사용' : '사용 중지', () => { if (user.disabled || confirm(`${user.name} 계정을 사용 중지할까요? 대화는 지워지지 않습니다.`)) run({action: user.disabled ? 'enable' : 'disable'}); });
    row.append(info, actions); $('users').append(row);
  }
}
$('user-new').addEventListener('click', () => { $('user-form').reset(); $('user-error').textContent = ''; $('user-dialog').showModal(); $('user-name').focus(); });
$('user-form').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    const r = await api('/api/users', {action: 'create', name: $('user-name').value, login: $('user-login').value,
      password: $('user-password').value, role: $('user-role').value, tailscale_login: $('user-tailscale').value.trim()});
    paintUsers(r.users); $('user-dialog').close();
  } catch (err) { $('user-error').textContent = err.message; }
});

// ── 계정 메뉴·로그인
function closeMenu() { $('account-menu').hidden = true; $('account-button').setAttribute('aria-expanded', 'false'); }
$('account-button').addEventListener('click', e => {
  e.stopPropagation();
  const open = $('account-menu').hidden;
  $('account-menu').hidden = !open; $('account-button').setAttribute('aria-expanded', String(open));
});
document.addEventListener('click', e => { if (!$('account-menu').hidden && !e.target.closest('.account')) closeMenu(); });
document.querySelectorAll('[data-dialog]').forEach(b => b.addEventListener('click', () => { closeMenu(); $(b.dataset.dialog).showModal(); }));
document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));
$('password-open').addEventListener('click', () => {
  closeMenu();
  $('password-form').reset(); $('password-error').textContent = '';
  $('password-current-row').hidden = !state?.user.has_password;
  $('password-dialog').showModal();
});
$('password-form').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    await api('/api/password', {current: $('password-current').value, password: $('password-new').value});
    $('password-dialog').close(); await refresh(); notice('비밀번호를 바꿨습니다. 다른 기기에서는 다시 로그인해야 합니다.');
  } catch (err) { $('password-error').textContent = err.message; }
});
$('logout').addEventListener('click', async () => {
  closeMenu();
  if (anyWork()) { notice('모든 대화의 요청이 끝난 뒤 바꾸세요.'); return; }
  try { await api('/api/logout', {}); } catch { /* 이미 로그아웃 */ }
  location.reload();
});
$('login-form').addEventListener('submit', async e => {
  e.preventDefault();
  $('login-error').textContent = ''; $('login-submit').disabled = true;
  try { await api('/api/login', {login: $('login-id').value, password: $('login-password').value}); location.reload(); }
  catch (err) { $('login-error').textContent = err.message; $('login-submit').disabled = false; }
});
$('login-owner').addEventListener('click', async () => {
  try { await api('/api/local-owner', {}); location.reload(); } catch (err) { $('login-error').textContent = err.message; }
});

// ── 좁은 화면: 왼쪽 칸은 서랍
function setSidebar(open) { $('sidebar').classList.toggle('open', open); $('sidebar-backdrop').hidden = !open; }
$('sessions-toggle').addEventListener('click', () => setSidebar(true));
$('sidebar-close').addEventListener('click', () => setSidebar(false));
$('sidebar-backdrop').addEventListener('click', () => setSidebar(false));

function setContextPanel(open) {
  document.querySelector('.context-panel').classList.toggle('open', open);
  $('context-toggle').setAttribute('aria-expanded', String(open));
  $('context-toggle').classList.toggle('active', open);
}
$('context-toggle').addEventListener('click', () => setContextPanel(!document.querySelector('.context-panel').classList.contains('open')));
$('context-close').addEventListener('click', () => setContextPanel(false));
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape' || document.querySelector('dialog[open]')) return;
  if (document.querySelector('.context-panel.open')) setContextPanel(false);
  closeMenu(); setSidebar(false);
});
function filterSessions() {
  const query = $('session-search').value.trim().toLowerCase();
  const rows = [...$('sessions').querySelectorAll('.session-row')];
  for (const row of rows) row.hidden = !!query && !row.textContent.toLowerCase().includes(query);
  for (const label of $('sessions').querySelectorAll('.session-group')) label.hidden = !rows.some(row => row.dataset.group === label.dataset.group && !row.hidden);
}
$('session-search').addEventListener('input', filterSessions);
$('workspace-change').addEventListener('click', () => { if (anyWork()) { notice('모든 대화의 요청이 끝난 뒤 프로젝트 폴더를 바꾸세요.'); return; } $('workspace-path').value = state.project_root; $('workspace-error').textContent = ''; $('workspace-dialog').showModal(); });
$('workspace-close').addEventListener('click', () => $('workspace-dialog').close());
$('workspace-form').addEventListener('submit', async e => { e.preventDefault(); if (anyWork()) { $('workspace-error').textContent = '모든 대화의 요청이 끝난 뒤 바꾸세요.'; return; } try { await api('/api/workspace', {path: $('workspace-path').value, confirmed: true, session_id: state.session_id}); await refresh(); $('workspace-dialog').close(); notice('프로젝트 폴더를 변경했습니다. 기존의 미적용 수정안은 폐기됩니다.'); } catch (e) { $('workspace-error').textContent = e.message; } });
$('shutdown').addEventListener('click', async () => {
  if (anyWork()) { notice('모든 대화의 요청을 완료 또는 중단한 뒤 종료하세요.'); return; }
  if (appStopped) return;
  if (!confirm('ALEX를 종료할까요?\n대화와 작업 기록은 저장됩니다. ALEX 서버와 직접 시작한 이미지·영상 엔진을 종료합니다.\nOllama와 따로 실행한 이미지·영상 엔진은 별도 서비스로 계속 실행됩니다.')) return;
  lock(true);
  $('shutdown').disabled = true;
  notice('ALEX를 종료하고 있습니다…');
  let accepted = false;
  try {
    const result = await api('/api/shutdown', {});
    if (result.stopped !== true) throw new Error('서버가 종료 요청을 확인하지 않았습니다.');
    accepted = true;
    appStopped = true;
    document.querySelectorAll('button, input, textarea, select').forEach(el => { el.disabled = true; });
    const warnings = result.warnings?.length ? '\n' + result.warnings.join('\n') : '';
    let stopped = false;
    for (let attempt = 0; attempt < 20; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 250));
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 800);
      try { await fetch('/health', {cache: 'no-store', signal: controller.signal}); }
      catch (e) { if (e.name !== 'AbortError') { stopped = true; break; } }
      finally { clearTimeout(timeout); }
    }
    notice((stopped ? 'ALEX 종료를 확인했습니다.' : '종료 요청을 보냈지만 서버 종료를 아직 확인하지 못했습니다.')
      + ' 대화와 작업 기록은 저장되어 있습니다.\n다시 사용하려면 START_ALEX.vbs를 실행하고 이 창을 새로고침하세요.\nOllama는 별도 서비스로 유지됩니다.' + warnings);
  } catch (e) {
    notice(e.message);
  } finally {
    if (!accepted) {
      lock(false);
      $('shutdown').disabled = false;
    }
  }
});

$('catalog-open').addEventListener('click', async () => {
  if ($('settings-dialog').open) $('settings-dialog').close();
  if (!state || busy) return;
  $('catalog-dialog').showModal();
  try {
    const data = await api('/api/models');
    renderCatalog(data);
  } catch (e) {
    $('catalog-storage').textContent = '서버에 연결하지 못했습니다: ' + e.message;
  }
});

$('catalog-close').addEventListener('click', () => {
  if (!busy) $('catalog-dialog').close();
});

function renderCatalog(data) {
  const canDownload = data.backend === 'ollama';
  const st = data.storage;
  let stText = '저장 공간 확인 불가';
  if (st && st.free_gb !== null) {
    stText = `Ollama 저장 경로: ${st.path} (여유 공간: ${st.free_gb} GB)`;
  }
  $('catalog-storage').textContent = canDownload ? stText : '이 카탈로그는 Ollama용 모델 목록입니다. 현재 ' + backendLabel(data.backend) + ' 모드에서는 다운로드·모델 전환을 지원하지 않습니다.';

  const list = $('catalog-list');
  list.replaceChildren();

  data.catalog.forEach(cand => {
    const div = node('div', 'row catalog-item', '');

    let isInstalled = data.installed.includes(cand.tag) || data.installed.includes(cand.tag + ':latest');
    let isLoaded = data.loaded.includes(cand.tag) || data.loaded.includes(cand.tag + ':latest');
    let isSelected = (data.selected_general === cand.tag || data.selected_coder === cand.tag || data.selected_general === cand.tag + ':latest' || data.selected_coder === cand.tag + ':latest');

    const infoParts = [
      `모델 본체: ${cand.params_b ?? '확인 불가'}B`,
      `활성: ${cand.active_params_b ?? '확인 불가'}B`,
      ...(cand.ngram_embedding_params_b == null ? [] : [`추가 n-gram embedding: ${cand.ngram_embedding_params_b}B`]),
      `구조: ${cand.architecture || '확인 불가'}`,
      `양자화: ${cand.quantization || '확인 불가'}`,
      `예상 다운로드: ${cand.download_size_gb ?? '확인 불가'}GB`,
    ];

    const info = node('div');
    const name = node('strong', '', cand.tag);
    const href = safeHref(cand.source_url);
    if (href) name.append(extLink('출처', href));

    const statuses = [isInstalled ? '설치됨' : '카탈로그 후보'];
    if (isSelected) statuses.push('현재 선택됨');
    if (isLoaded) statuses.push('현재 로드됨');
    const memory = node('small', 'muted', `메모리: ${cand.memory_estimate?.label || '추정 불가'} (OS·런타임·KV 캐시 여유를 반영한 추정, 실행 보장 아님)`);
    if (cand.memory_estimate?.status === 'insufficient_estimated') memory.style.color = 'var(--danger)';
    info.append(name, node('br'), node('small', 'muted', infoParts.join(' | ')), node('br'),
                memory, node('br'), node('small', 'muted', `검증일: ${cand.last_verified} | 상태: ${statuses.join(' · ')}`));

    const actions = node('div');
    if (!isInstalled) {
        const btn = node('button', 'primary', '다운로드');
        btn.disabled = !canDownload;
        let downloading = false;
        btn.addEventListener('click', async () => {
            if (downloading) {
                if (confirm('다운로드를 중단하시겠습니까?')) {
                    if (!pullRequestId) { notice('다운로드 시작 응답을 기다리는 중입니다. 잠시 후 다시 누르세요.'); return; }
                    btn.textContent = '취소 요청 중...';
                    try { await api('/api/cancel', {request_id: pullRequestId}); }
                    catch(e) { notice('취소 요청 실패: ' + e.message); }
                }
                return;
            }
            if (anyWork()) { notice('모든 대화의 요청이 끝난 뒤 모델을 다운로드하세요.'); return; }
            const free = data.storage?.free_gb == null ? '확인 불가' : `${data.storage.free_gb}GiB`;
            if (!confirm(`예상 크기 ${cand.download_size_gb ?? '확인 불가'}GB · 저장 공간 여유 ${free}\n${cand.memory_estimate?.label || '메모리 추정 불가'}입니다. 다운로드하시겠습니까?`)) return;
            downloading = true;
            pullModel(cand.tag, btn).finally(() => downloading = false);
        });
        actions.append(btn);
    }

    div.append(info, actions);
    list.append(div);
  });
}

async function pullModel(tag, btn) {
    lock(true);
    $('stop').hidden = true;
    pullRequestId = null;
    let originalText = btn.textContent;
    btn.textContent = '다운로드 중... (클릭하여 취소)';
    $('catalog-close').disabled = true;
    try {
        const r = await fetch('/api/pull', {method: 'POST', headers, body: JSON.stringify({model: tag, confirmed: true})});
        if (r.status === 403) { showTokenExpired(); throw new Error('연결이 만료되었습니다.'); }
        if (!r.ok) { const x = await r.json(); throw new Error(x.error || `HTTP ${r.status}`); }
        const reader = r.body.getReader(), decoder = new TextDecoder();
        let buffer = '';
        let success = false;
        while (true) {
            const {value, done} = await reader.read();
            buffer += done ? decoder.decode() : decoder.decode(value, {stream: true});
            const lines = buffer.split('\n'); buffer = lines.pop();
            for (const line of lines) {
                if (!line.trim()) continue;
                const event = JSON.parse(line);
                if (event.type === 'start') { pullRequestId = event.request_id; continue; }
                if (event.error) throw new Error(event.error);
                if (event.status === 'cancelled') throw new Error('다운로드가 취소되었습니다.');
                let text = event.status || '';
                if (event.status === 'success') success = true;
                if (event.total) text += ` ${Math.floor(100 * event.completed / event.total)}%`;
                if (!btn.disabled) btn.textContent = text + ' (클릭하여 취소)';
            }
            if (done) {
                if (buffer.trim()) {
                    const event = JSON.parse(buffer);
                    if (event.error) throw new Error(event.error);
                    if (event.status === 'success') success = true;
                }
                break;
            }
        }
        if (!success) throw new Error('다운로드가 완료되지 않았거나 취소되었습니다.');
        btn.textContent = '다운로드 완료';
        btn.className = 'secondary';
        const data = await api('/api/models');
        renderCatalog(data);
        models();
    } catch (e) {
        alert('다운로드 오류: ' + e.message);
        btn.textContent = originalText;
        btn.disabled = appStopped;
    } finally {
        $('catalog-close').disabled = appStopped;
        pullRequestId = null;
        lock(false);
    }
}

// 시작: 누구인지 먼저 확인한다(이 PC 면 주인으로 바로, 밖이면 로그인 화면).
(async () => {
  try {
    const who = await api('/api/me');
    if (!who.user) { showLogin(who.can_use_owner); return; }
    await refresh(true); desiredSession = state.session_id; await models();
    if (serverRequests.size) notice('처리 중인 대화가 있습니다. 다른 방으로 이동할 수 있고, 완료되면 저장된 답변을 표시합니다.');
  } catch (e) { if (!tokenExpired && $('login-screen').hidden) notice('서버에 연결하지 못했습니다: ' + e.message); }
})();
// 다른 탭의 요청과 대기열 순서를 확인한다. 모델을 추가로 호출하지 않는다.
setInterval(async () => {
  if (polling || navigating || auxiliaryBusy || !state || appStopped || tokenExpired
      || !roomRequests.size && !serverRequests.size) return;
  polling = true;
  try { await refreshVisible(false); } catch { /* 스트림 자체가 오류를 안내한다 */ }
  finally { polling = false; }
}, 1500);
// 홈 화면에 설치하는 앱(PWA, 2026-10-11): HTTPS(휴대폰·노트북의 Tailscale 주소)에서만 서비스 워커를 쓴다.
// 워커는 페이지·API 를 저장하지 않고, 연결이 끊겼을 때 안내만 보여 준다(sw.js).
if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('/sw.js').catch(() => {});
