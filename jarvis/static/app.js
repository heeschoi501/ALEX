'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="jarvis-token"]').content;
let state = null, busy = false, requestId = null, pullRequestId = null, oldest = null;
let settingsModelsRequest = 0;
let pendingAttachments = [];
const headers = {'Content-Type': 'application/json', 'X-Jarvis-Token': token};
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
  button.style.marginLeft = '10px';
  button.addEventListener('click', () => location.reload());
  box.append(button);
  // 죽은 페이지에 계속 입력하지 않도록 막는다.
  $('send').disabled = true;
  $('input').disabled = true;
  $('media-prompt-open').disabled = true;
  $('screenshot').disabled = true;
}

async function api(path, body) {
  if (appStopped) throw new Error('ALEX가 종료되었습니다. START_ALEX.vbs로 다시 실행한 뒤 새로고침하세요.');
  const r = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers, body: body === undefined ? undefined : JSON.stringify(body)});
  if (r.status === 403) { showTokenExpired(); throw new Error('연결이 만료되었습니다.'); }
  let result;
  try { result = await r.json(); } catch { throw new Error('서버 응답을 읽지 못했습니다. 실행 창을 확인하세요.'); }
  if (!r.ok) throw new Error(result.error || `HTTP ${r.status}`);
  return result;
}
function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}
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
function imageFigure(caption, name, attachment = false) {
  const figure = node('figure', 'gen-image');
  const img = node('img'); img.alt = caption; img.title = '클릭하면 새 탭에서 크게 봅니다';
  const cap = node('figcaption', '', caption);
  figure.append(img, cap);
  const path = attachment ? '/api/attachment?name=' + encodeURIComponent(name) + '&session_id=' + encodeURIComponent(state.session_id) : '/api/image?name=' + encodeURIComponent(name);
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
function appendWithImages(el, text) {
  let buffer = [];
  const flush = () => { if (buffer.length) { appendLinked(el, buffer.join('\n')); buffer = []; } };
  for (const line of text.split('\n')) {
    const m = line.match(IMAGE_LINE), a = line.match(ATTACHMENT_LINE), v = line.match(VIDEO_LINE), d = line.match(DOCUMENT_LINE);
    if (m) { flush(); el.append(imageFigure(m[1], m[2])); } else if (a) { flush(); el.append(imageFigure(a[1], a[2], true)); } else if (v) { flush(); el.append(videoFigure(v[1], v[2])); } else if (d) { flush(); el.append(documentDownload(d[1])); } else buffer.push(line);
  }
  flush();
}
function mediaLine(line) {
  const m = line.match(IMAGE_LINE), a = line.match(ATTACHMENT_LINE), v = line.match(VIDEO_LINE), d = line.match(DOCUMENT_LINE);
  if (m) return imageFigure(m[1], m[2]);
  if (a) return imageFigure(a[1], a[2], true);
  if (v) return videoFigure(v[1], v[2]);
  if (d) return documentDownload(d[1]);
  return null;
}
function renderContent(el, text, markdown = false) {
  el.replaceChildren();
  // ALEX 답은 굵게·목록·표·코드 블록을 꾸며 보인다(markdown.js — HTML 은 실행하지 않고 글자로만 그린다).
  if (markdown && window.AlexMarkdown) {
    el.classList.add('markdown');
    try { window.AlexMarkdown.render(el, text, mediaLine); return; } catch { el.replaceChildren(); }
  }
  el.classList.remove('markdown');
  // Deliberately small formatter: no HTML evaluation or external markdown scripts.
  const chunks = text.split(/(```[^\n]*\n[\s\S]*?```)/g);
  for (const chunk of chunks) {
    if (chunk.startsWith('```') && chunk.endsWith('```')) {
      const pre = node('pre');
      const code = node('code', '', chunk.slice(chunk.indexOf('\n') + 1, -3).trimEnd());
      pre.append(code); el.append(pre);
    } else appendWithImages(el, chunk);
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
function message(role, text, parent = $('messages')) {
  const el = node('article', 'message ' + (role === 'user' ? 'user' : 'assistant'));
  const label = node('div', 'message-label', role === 'user' ? 'YOU' : 'ALEX');
  const copy = node('button', 'copy', '복사'); copy.type = 'button';
  const content = node('div', 'message-content');
  let value = text;
  copy.addEventListener('click', async () => { try { await navigator.clipboard.writeText(value); copy.textContent = '복사됨'; } catch { notice('복사할 텍스트를 직접 선택해 주세요.'); } });
  label.append(copy); el.append(label, content); parent.append(el);
  const result = {el, content, update(t, formatted = false) {
    value = t;
    if (formatted) renderContent(content, t, role === 'assistant'); else content.textContent = t;
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
        el.append(row);
      }
    }
  }};
  result.update(text, true); return result;
}
function lock(value) {
  busy = value;
  if (appStopped) {
    document.querySelectorAll('button, input, textarea, select').forEach(el => { el.disabled = true; });
    return;
  }
  // 토큰이 만료된 뒤에는 lock(false) 가 전송 버튼을 되살리면 안 된다.
  // 새로고침 전까지 어떤 요청도 성공하지 않는다.
  $('send').disabled = value || tokenExpired || appStopped;
  $('input').disabled = tokenExpired || appStopped;
  $('new-chat').disabled = value;
  $('attach').disabled = value || tokenExpired || appStopped;
  $('screenshot').disabled = value || tokenExpired || appStopped;
  $('attachments').querySelectorAll('button').forEach(button => { button.disabled = value || tokenExpired || appStopped; });
  $('media-prompt-open').disabled = value || tokenExpired || appStopped;
  $('settings-open').disabled = value;
  $('stop').hidden = !value;
  $('stop').disabled = false;
  $('stop').textContent = '■ 생성 중단';
  document.querySelectorAll('#sessions button, #task-form button, #memory-form button, #file-form button, [data-command], #tasks input, #memories button').forEach(b => { b.disabled = value; });
}
function paintState(s, history = false) {
  if (state && state.session_id !== s.session_id) clearAttachments();
  state = s;
  const current = s.sessions.find(x => x.session_id === s.session_id);
  $('title').textContent = current?.title || '새 대화';
  $('session-count').textContent = s.sessions.length;
  $('sessions').replaceChildren();
  for (const item of s.sessions) {
    const title = item.title || '새 대화';
    const row = node('div', 'session-row');
    const b = node('button', 'session-item' + (item.session_id === s.session_id ? ' active' : ''), title);
    b.title = `${title} · ${item.message_count}개 메시지`;
    b.addEventListener('click', () => submit('/use ' + item.session_id));
    const remove = node('button', 'session-delete', '×');
    remove.type = 'button'; remove.title = '이 대화 삭제'; remove.setAttribute('aria-label', `${title} 대화 삭제`);
    remove.addEventListener('click', () => deleteSession(item));
    row.append(b, remove);
    $('sessions').append(row);
  }
  filterSessions();
  $('model-label').textContent = (s.mock ? 'DEMO · 실제 AI 아님 · ' : backendLabel(liveBackend(s)) + ' · ') + s.models[0].replace(/^(ollama|strata):/, '');
  $('badge-model').textContent = s.mock ? '데모' : s.models[0].replace(/^(ollama|strata):/, '');
  $('hardware').textContent = `${s.profile} · ${s.gpu || 'GPU 미확인'}`;
  $('memory-count').textContent = Object.keys(s.memories || {}).length;
  $('project-root').textContent = s.project_root;
  $('task-count').textContent = s.tasks.filter(t => t.status === 'open').length;
  $('tasks').replaceChildren();
  for (const task of s.tasks) {
    const row = node('div', 'task' + (task.status === 'done' ? ' done' : ''));
    const cb = node('input'); cb.type = 'checkbox'; cb.checked = task.status === 'done'; cb.setAttribute('aria-label', task.text);
    cb.addEventListener('change', () => submit((cb.checked ? '/task-done ' : '/task-open ') + task.task_id));
    row.append(cb, node('span', '', task.text)); $('tasks').append(row);
  }
  $('memories').replaceChildren();
  for (const [key, value] of Object.entries(s.memories)) {
    const row = node('div', 'memory');
    const remove = node('button', 'text-button', '삭제');
    remove.addEventListener('click', () => { if (confirm(`“${key}” 기억을 삭제할까요?`)) submit('/forget ' + key); });
    row.append(remove, node('strong', '', key), node('span', '', value)); $('memories').append(row);
  }
  if (!Object.keys(s.memories).length) $('memories').append(node('small', '', '저장한 기억이 없습니다.'));
  if (history) {
    $('messages').replaceChildren();
    for (const m of s.messages) message(m.role, m.content);
    oldest = s.messages[0]?.id;
    $('older').hidden = !s.has_more;
    $('welcome').hidden = s.messages.length > 0;
    scrollBottom();
  }
  lock(busy);
}
async function refresh(history = false) { paintState(await api('/api/state'), history); }
// 대화 목록의 [×] (2026-09-26 사용자: "대화목록 제거 기능도 필요할거 같은데"). /delete-session 명령을 치지 않아도
// 되게 하고, 확인 창 없이는 보내지 않는다. 보던 대화를 지우면 서버가 새 대화를 만들어 그 ID 를 돌려준다.
async function deleteSession(item) {
  if (busy || !state) return;
  const title = item.title || '새 대화';
  if (!confirm(`“${title}” 대화를 삭제할까요?\n메시지 ${item.message_count}개가 지워지고 되돌릴 수 없습니다.`)) return;
  const current = state.session_id;
  lock(true);
  try {
    const r = await api('/api/session/delete', {session_id: current, target: item.session_id, confirmed: true});
    await refresh(r.session_id !== current);
    notice(`“${title}” 대화를 삭제했습니다.`);
  } catch (e) { notice(e.message); } finally { lock(false); }
}
async function models() {
  try {
    const x = await api('/api/models');
    // Using x.selected_general instead of x.ready to just check if something is selected and installed
    let isReady = x.selected_general && x.installed.includes(x.selected_general);
    $('connection').classList.toggle('offline', !isReady);
    const engine = backendLabel(x.backend || liveBackend(state));
    $('connection-text').textContent = x.mock ? '데모 모드입니다. start_web.bat으로 실제 모델을 실행하세요.' : isReady ? `${engine} 사용 중 · ${x.selected_general}` : `${engine} 연결 확인 필요 · 모델 서버를 준비한 뒤 다시 확인하세요.`;
    return x.installed;
  } catch (e) { $('connection-text').textContent = e.message; return []; }
}
async function submit(text = $('input').value.trim()) {
  if (busy || !state) return;
  const selected = text.trim().startsWith('/') ? [] : pendingAttachments.slice();
  if (!text.trim() && selected.length) text = '첨부한 이미지의 내용을 설명해 주세요.';
  if (!text.trim()) return;
  const body = {message: text, session_id: state.session_id, attachments: selected.map(image => image.name)};
  if (new TextEncoder().encode(JSON.stringify(body)).length > 64000) { notice('요청이 너무 큽니다. 64KB 이하로 줄여 주세요.'); return; }
  const oldSession = state.session_id;
  lock(true); notice(''); $('input').value = ''; $('welcome').hidden = true;
  const displayed = text + (selected.length ? '\n' + selected.map(image => `![첨부 이미지 ${image.width}×${image.height}](alex-attachment:${image.name})`).join('\n') : '');
  message('user', displayed);
  clearAttachments(new Set(selected.map(image => image.name)));
  const answer = message('assistant', '모델이 생각하고 있습니다…');
  const imageCommand = text.match(/^\/(?:image-prompt|image)\s+([\s\S]+)/);
  const requestedImage = imageCommand ? imageCommand[1].trim() : text;
  if (imageCommand) {
    answer.update('이미지 생성 준비 중…');
    answer.content.replaceChildren(imageLoadingCard('요청을 준비하고 있습니다…', requestedImage));
  }
  let imageProgressActive = Boolean(imageCommand);
  let partial = '', finished = false;
  scrollBottom();
  try {
    const r = await fetch('/api/chat/stream', {method: 'POST', headers, body: JSON.stringify(body)});
    if (r.status === 403) { showTokenExpired(); throw new Error('연결이 만료되었습니다.'); }
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
        if (event.type === 'start') requestId = event.request_id;
        if (event.type === 'chunk') {
          const atBottom = $('chat-scroll').scrollHeight - $('chat-scroll').scrollTop - $('chat-scroll').clientHeight < 130;
          if (event.text.startsWith('[그림 설명을 영어로 옮기는 중') || event.text.startsWith('[그리는 중: ')) imageProgressActive = true;
          partial += event.text;
          answer.update(partial);
          const imageProgress = imageProgressActive ? imageProgressInfo(partial, requestedImage) : null;
          if (imageProgress) answer.content.replaceChildren(imageLoadingCard(imageProgress.phase, imageProgress.prompt));
          if (atBottom) scrollBottom();
        }
        if (event.type === 'error') throw new Error(event.error);
        if (event.type === 'done') {
          finished = true; answer.update(event.response, true);
          const m = event.metrics || {}, meta = [];
          if (event.elapsed !== undefined) meta.push(`${event.elapsed}초`);
          if (m.tokens_per_second) meta.push(`${m.tokens_per_second} tokens/s`);
          if (m.first_token_seconds !== null && m.first_token_seconds !== undefined) meta.push(`첫 응답 ${m.first_token_seconds}초`);
          if (m.done_reason === 'length') meta.push('최대 출력 길이에 도달했습니다');
          answer.el.append(node('div', 'message-meta', meta.join(' · ')));
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
          await refresh(event.session_id !== oldSession || text === '/new' || text.startsWith('/use '));
        }
      }
      if (done) break;
    }
    if (!finished) throw new Error('연결이 끊겼습니다. 새로고침해 저장된 응답을 확인하세요.');
  } catch (e) {
    answer.update((partial ? partial + '\n\n' : '') + '오류: ' + e.message);
    notice('전송한 요청: ' + text.slice(0, 120) + '\n' + e.message);
    // Keep the original request available for manual retry, never resend automatically.
    if (!$('input').value) $('input').value = text;
  } finally { requestId = null; lock(false); $('input').focus(); }
}
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
}
async function attachScreenshots(files) {
  if (busy || tokenExpired || appStopped || !state) return;
  const selected = Array.from(files);
  if (!selected.length) return;
  if (pendingAttachments.length + selected.length > 3) { notice('이미지는 한 번에 최대 3개까지 첨부할 수 있습니다.'); return; }
  if (selected.some(file => file.size > 10000000)) { notice('이미지는 파일당 10MB 이하로 보내 주세요.'); return; }
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
    notice('이미지를 첨부했습니다. 질문을 입력하고 전송하세요.');
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
$('new-chat').addEventListener('click', () => submit('/new'));
$('refresh-models').addEventListener('click', models);
$('stop').addEventListener('click', async () => {
  if (!requestId) return;
  $('stop').disabled = true; $('stop').textContent = '중단 요청 중…';
  try { await api('/api/cancel', {request_id: requestId}); } catch (e) { notice(e.message); }
});
document.querySelectorAll('[data-prompt]').forEach(b => b.addEventListener('click', () => submit(b.dataset.prompt)));
document.querySelectorAll('[data-command]').forEach(b => b.addEventListener('click', () => submit(b.dataset.command)));
$('task-form').addEventListener('submit', e => { e.preventDefault(); if (!busy) { const t = $('task-text').value.trim(); if (t) { submit('/task-add ' + t); $('task-text').value = ''; } } });
$('developer-form').addEventListener('submit', e => { e.preventDefault(); if (busy) return; const source = $('developer-file').value.trim(), test = $('developer-test').value.trim(), goal = $('developer-goal').value.trim(); if (source && test && goal && !source.includes('::') && !test.includes('::')) submit(`/dev-start ${source} :: ${test} :: ${goal}`); });
$('memory-form').addEventListener('submit', e => { e.preventDefault(); if (!busy) { const k = $('memory-key').value.trim(), v = $('memory-value').value.trim(); if (k && v && !k.includes('=')) { submit(`/remember ${k}=${v}`); $('memory-value').value = ''; } } });
$('file-form').addEventListener('submit', e => { e.preventDefault(); const p = $('file-path').value.trim(), q = $('file-question').value.trim(); if (!busy && p && q) submit(`${e.submitter?.value === 'edit' ? '/edit-file' : '/ask-file'} ${p} :: ${q}`); });
$('older').addEventListener('click', async () => {
  if (!oldest || busy) return;
  const button = $('older'); button.disabled = true;
  try {
    const page = await api(`/api/history?session_id=${encodeURIComponent(state.session_id)}&before=${oldest}`);
    const fragment = document.createDocumentFragment();
    for (const m of page.messages) message(m.role, m.content, fragment);
    $('messages').prepend(fragment); oldest = page.messages[0]?.id; button.hidden = !page.has_more;
  } catch (e) { notice(e.message); } finally { button.disabled = appStopped || busy; }
});
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
$('settings-open').addEventListener('click', async () => {
  if (!state || busy) return;
  $('settings-error').textContent = ''; $('settings-dialog').showModal();
  $('settings-save').disabled = true;
  try { await refresh(); } catch (e) { $('settings-error').textContent = e.message; return; }
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
  await populateSettingsModels();
});
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
(async () => { try { await refresh(true); await models(); if (state.busy) notice('다른 요청이 처리 중입니다. 완료 후 새로고침하세요.'); } catch (e) { notice('서버에 연결하지 못했습니다: ' + e.message); } })();

function setContextPanel(open) {
  document.querySelector('.context-panel').classList.toggle('open', open);
  $('context-toggle').setAttribute('aria-expanded', String(open));
  $('context-toggle').classList.toggle('active', open);
}
$('context-toggle').addEventListener('click', () => setContextPanel(!document.querySelector('.context-panel').classList.contains('open')));
$('context-close').addEventListener('click', () => setContextPanel(false));
document.addEventListener('keydown', e => { if (e.key === 'Escape' && document.querySelector('.context-panel.open') && !document.querySelector('dialog[open]')) setContextPanel(false); });
function filterSessions() {
  const query = $('session-search').value.trim().toLowerCase();
  for (const row of $('sessions').children) row.hidden = !!query && !row.textContent.toLowerCase().includes(query);
}
$('session-search').addEventListener('input', filterSessions);
// 설정 창 안의 명령(문서 저장·백업·모델 내리기)은 창을 닫고 대화에서 실행한다.
document.querySelectorAll('#settings-dialog [data-command]').forEach(b => b.addEventListener('click', () => $('settings-dialog').close()));
$('sessions-toggle').addEventListener('click', () => document.querySelector('.sidebar').classList.toggle('open'));
$('sessions').addEventListener('click', () => document.querySelector('.sidebar').classList.remove('open'));
$('workspace-change').addEventListener('click', () => { if (busy) return; $('workspace-path').value = state.project_root; $('workspace-error').textContent = ''; $('workspace-dialog').showModal(); });
$('workspace-close').addEventListener('click', () => $('workspace-dialog').close());
$('workspace-form').addEventListener('submit', async e => { e.preventDefault(); if (busy) return; try { await api('/api/workspace', {path: $('workspace-path').value, confirmed: true, session_id: state.session_id}); await refresh(); $('workspace-dialog').close(); notice('프로젝트 폴더를 변경했습니다. 기존의 미적용 수정안은 폐기됩니다.'); } catch (e) { $('workspace-error').textContent = e.message; } });
$('shutdown').addEventListener('click', async () => {
  if (busy) { notice('생성 완료 또는 중단 후 종료하세요.'); return; }
  if (appStopped) return;
  if (!confirm('ALEX를 종료할까요?\n대화와 작업 기록은 저장됩니다. ALEX 서버와 직접 시작한 이미지·영상 엔진을 종료합니다.\nOllama와 따로 실행한 이미지·영상 엔진은 별도 서비스로 계속 실행됩니다.')) return;
  lock(true);
  $('shutdown').disabled = true;
  $('shutdown').textContent = '종료 중…';
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
    $('shutdown').textContent = stopped ? '종료됨' : '종료 요청됨';
    notice((stopped ? 'ALEX 종료를 확인했습니다.' : '종료 요청을 보냈지만 서버 종료를 아직 확인하지 못했습니다.')
      + ' 대화와 작업 기록은 저장되어 있습니다.\n다시 사용하려면 START_ALEX.vbs를 실행하고 이 창을 새로고침하세요.\nOllama는 별도 서비스로 유지됩니다.' + warnings);
  } catch (e) {
    notice(e.message);
  } finally {
    if (!accepted) {
      lock(false);
      $('shutdown').disabled = false;
      $('shutdown').textContent = '⏻';
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
    const div = node('div', 'row', '');
    div.style.border = '1px solid #3c5058';
    div.style.padding = '10px';
    div.style.borderRadius = '5px';
    div.style.justifyContent = 'space-between';
    div.style.alignItems = 'center';
    
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
    if (href) { const link = extLink('출처', href); link.style.marginLeft = '10px'; name.append(link); }
    
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
            if (busy) return;
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
