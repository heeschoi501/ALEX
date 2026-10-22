'use strict';
// 코딩 세션 화면(ADR-112) — OpenCode 화면처럼: 세션을 만들고, 요청을 보내고, 진행(모델 글·도구)을 실시간으로 받고,
// 허락 요청에 답하고, 질문에 답하고, 중단하고, 바뀐 내용을 보고 되돌린다. 서버 API 는 /api/code/*(관리자만).
const token = document.querySelector('meta[name="jarvis-token"]').content;
const headers = {'Content-Type': 'application/json', 'X-Jarvis-Token': token};
const $ = id => document.getElementById(id);
try { document.documentElement.dataset.theme = localStorage.getItem('alex-theme') || 'light'; } catch { /* 저장소 막힘 */ }

const STATUS = {idle: '대기', running: '일하는 중', question: '답을 기다림', error: '오류'};
const TIER = {read: '읽기만', ask: '확인 후 실행', auto: '자동 실행'};
const PERMISSION = {edit: '파일 바꾸기', bash: '명령 실행', webfetch: '웹 읽기', websearch: '웹 검색', task: '하위 에이전트',
  skill: '스킬', question: '질문'};
let current = null;      // 보는 세션 id
let follow = null;       // 사건 흐름(AbortController)
let bubble = null;       // 모델 글이 이어 붙는 칸
let status = 'idle';
let commands = new Map();   // '/이름' 명령(OpenCode command — /init·/review·작업 폴더의 사용자 명령)

async function api(path, body) {
  const r = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers,
    body: body === undefined ? undefined : JSON.stringify(body)});
  let data = {};
  try { data = await r.json(); } catch { /* 빈 응답 */ }
  if (r.status === 403 && !data.admin_only) throw new Error('연결이 만료되었습니다. 대화 화면을 새로고침한 뒤 다시 여세요.');
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}
function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}
function scrollDown(force = false) {
  const s = $('scroll');
  if (force || s.scrollHeight - s.scrollTop - s.clientHeight < 240) s.scrollTop = s.scrollHeight;
}
function note(text, cls = '') { endBubble(); $('log').append(node('div', `note ${cls}`.trim(), text)); scrollDown(); }
function markdown(el, text) {
  el.replaceChildren();
  try { window.AlexMarkdown.render(el, text); } catch { el.textContent = text; }
}
function paintDiff(pre, text) {
  pre.replaceChildren();
  for (const line of String(text || '').split('\n')) {
    const cls = line.startsWith('@@') ? 'hunk' : line.startsWith('+') && !line.startsWith('+++') ? 'add'
      : line.startsWith('-') && !line.startsWith('---') ? 'del' : '';
    pre.append(node('span', cls, line || ' '));
  }
}

// ── 대화 칸
function userBubble(text) {
  endBubble();
  $('log').append(node('div', 'msg user', text));
  scrollDown(true);
}
function addDelta(text) {
  if (!bubble) {
    bubble = {el: node('div', 'msg alex'), text: ''};
    $('log').append(bubble.el);
  }
  bubble.text += text;
  bubble.el.textContent = bubble.text;   // 받는 동안은 글 그대로, 끝나면 마크다운으로
  scrollDown();
}
function endBubble() {
  if (bubble) { markdown(bubble.el, bubble.text); bubble = null; }
}
function alexText(text) {
  if (!text.trim()) return;
  endBubble();
  const el = node('div', 'msg alex');
  markdown(el, text);
  $('log').append(el);
}
function toolLine(text) {
  endBubble();
  $('log').append(node('div', 'tool', text.replace(/\n+$/, '')));
  scrollDown();
}
function permissionCard(request) {
  endBubble();
  const card = node('div', 'card permission');
  card.dataset.id = request.id;
  card.append(node('strong', '', `허락이 필요합니다 — ${PERMISSION[request.permission] || request.permission}`),
    node('div', 'target', request.pattern));
  const detail = request.detail || {};
  if (detail.diff) { const pre = node('pre', 'diff'); paintDiff(pre, detail.diff); card.append(pre); }
  else if (detail.description) card.append(node('small', 'muted', detail.description));
  const row = node('div', 'row');
  for (const [label, response, cls] of [['한 번 허락', 'once', 'primary'], ['항상 허락', 'always', 'secondary'],
    ['거절', 'reject', 'secondary danger']]) {
    const button = node('button', cls, label);
    button.type = 'button';
    button.title = response === 'always' ? `이 세션에서 '${request.pattern}' 는 다시 묻지 않습니다` : '';
    button.addEventListener('click', async () => {
      row.querySelectorAll('button').forEach(b => { b.disabled = true; });
      try { await api(`/api/code/session/${current}/permissions/${request.id}`, {response}); }
      catch (err) { note(err.message, 'error'); row.querySelectorAll('button').forEach(b => { b.disabled = false; }); }
    });
    row.append(button);
  }
  card.append(row);
  $('log').append(card);
  scrollDown(true);
}
function permissionDone(id, response) {
  const card = [...$('log').querySelectorAll('.permission')].find(c => c.dataset.id === id);
  if (!card) return;
  card.classList.add('answered');
  card.querySelector('.row')?.replaceWith(node('small', 'muted',
    {once: '한 번 허락했습니다.', always: '이 세션에서는 계속 허락합니다.', reject: '거절했습니다.'}[response] || response));
}
function questionCard(questions) {
  endBubble();
  if (!questions?.length) return;
  const card = node('div', 'card question');
  for (const q of questions) {
    card.append(node('strong', '', q.question || q.header || '질문'));
    const row = node('div', 'row');
    for (const option of q.options || []) {
      const button = node('button', 'secondary', option.label);
      button.type = 'button';
      button.title = option.description || '';
      button.addEventListener('click', () => send(option.label));
      row.append(button);
    }
    if (row.childElementCount) card.append(row);
  }
  card.append(node('small', 'muted', '고르거나 아래 입력창에 답을 쓰세요.'));
  $('log').append(card);
  scrollDown(true);
}
function finished(result) {
  endBubble();
  if (!result) return;
  if (['error', 'unverified', 'timeout'].includes(result.stopped)) note(`확인 필요: ${result.error || result.text || '알 수 없는 오류'}`, 'error');
  else if (result.stopped === 'max_steps') note('단계 상한에 닿아 여기까지 정리했습니다.');
  else if (!['done', 'question'].includes(result.stopped) && result.text) note(result.text);
  const tools = Object.entries(result.tools || {}).map(([k, v]) => `${k} ${v}`).join(' · ');
  for (const report of result.verification || []) {
    note(report.status === 'verified' ? `독립 검사 ${report.attempt}회차: ${(report.checks || []).length}개 파일 통과`
      : `독립 검사 ${report.attempt}회차: ${report.reason || '미통과·미검증'}`, report.status === 'verified' ? 'meta' : 'error');
  }
  note(`${result.steps}단계 · ${Math.round(result.seconds)}초${tools ? ` — ${tools}` : ''}`, 'meta');
}
function paintTodos(todos) {
  const box = $('todos');
  box.replaceChildren();
  for (const t of todos || []) {
    const mark = {completed: '✓', in_progress: '▸', cancelled: '×'}[t.status] || '○';
    box.append(node('span', t.status === 'completed' || t.status === 'cancelled' ? 'done' : t.status === 'in_progress' ? 'doing' : '',
      `${mark} ${t.content}`));
  }
  box.hidden = !box.childElementCount;
}

// ── 지난 대화 다시 그리기(세션을 열 때)
function target(args) {
  return String(args.filePath || args.pattern || args.command || args.url || args.query || args.name || args.description || '').slice(0, 120);
}
function argsOf(call) {
  const raw = call?.function?.arguments ?? call?.arguments ?? {};
  if (typeof raw !== 'string') return raw || {};
  try { return JSON.parse(raw); } catch { return {}; }
}
function paintHistory(messages) {
  for (const m of messages) {
    if (m.role === 'user') {
      let text = m.content;
      if (text.startsWith('<system-reminder>')) {
        const plan = text.indexOf('Execute this plan:\n');
        if (plan < 0) continue;
        text = text.slice(plan + 'Execute this plan:\n'.length);
      }
      userBubble(text.split('\n\n<system-reminder>')[0]);
    } else if (m.role === 'assistant') {
      alexText(m.content || '');
      for (const call of m.tool_calls || []) {
        const name = call?.function?.name || call?.name || '';
        toolLine(`[${name} ${target(argsOf(call))}]`);
      }
    } else if (m.role === 'tool') {
      if (m.tool_name === 'question') { note(m.content.replace('User has answered your questions: ', '답: ')); continue; }
      const box = node('details', 'result');
      box.append(node('summary', '', '결과'), node('pre', '', m.content.slice(0, 4000)));
      $('log').append(box);
    }
  }
}

// ── 사건 흐름(SSE 를 fetch 로 — 토큰 머리글이 필요해서)
function handle(e) {
  switch (e.type) {
    case 'message.user': userBubble(e.text); setStatus('running'); break;
    case 'message.delta': addDelta(e.text); break;
    case 'progress': toolLine(e.text); break;
    case 'permission.asked': permissionCard(e.request); break;
    case 'permission.replied': permissionDone(e.id, e.response); break;
    case 'question.asked': finished(e.result); questionCard(e.questions); paintTodos(e.todos); setStatus('question'); loadList(); break;
    case 'session.idle': finished(e.result); paintTodos(e.todos); setStatus(e.result?.stopped === 'error' ? 'error' : 'idle'); loadList(); break;
    case 'session.aborted': note('중단을 요청했습니다.'); break;
    case 'session.reverted': note(e.files?.length ? `되돌렸습니다: ${e.files.join(', ')}` : '되돌릴 바뀐 내용이 없습니다.'); break;
    default: break;
  }
}
async function followEvents(sid, since) {
  follow?.abort();
  const control = new AbortController();
  follow = control;
  while (!control.signal.aborted) {
    try {
      const r = await fetch(`/api/code/event?session=${encodeURIComponent(sid)}&since=${since}`, {headers, signal: control.signal});
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        note(data.error || `진행 상황을 받지 못했습니다(HTTP ${r.status}).`, 'error');
        return;
      }
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const {value, done} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream: true});
        let cut;
        while ((cut = buffer.indexOf('\n\n')) >= 0) {
          const block = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          for (const line of block.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            const event = JSON.parse(line.slice(6));
            since = event.n + 1;
            if (sid === current) handle(event);
          }
        }
      }
    } catch {
      if (control.signal.aborted) return;
    }
    await new Promise(resolve => setTimeout(resolve, 2000));  // 끊기면 받은 다음 번호부터 다시
  }
}

// ── 세션
function setStatus(value) {
  status = value;
  const badge = $('status');
  badge.hidden = !current;
  badge.textContent = STATUS[value] || value;
  badge.className = `badge ${value}`;
  const running = value === 'running';
  $('abort').disabled = !current || !running;
  $('diff-open').disabled = !current;
  $('input').disabled = $('send').disabled = $('mode').disabled = !current || running;
  $('hint').textContent = value === 'question' ? '질문에 답해 주세요' : running ? '일하는 중 — 중단할 수 있습니다' : '';
}
async function loadList() {
  let sessions = [];
  try { ({sessions} = await api('/api/code/session')); } catch (err) { note(err.message, 'error'); return; }
  const list = $('list');
  list.replaceChildren();
  for (const s of sessions) {
    const item = node('div', `item${s.id === current ? ' on' : ''}`);
    item.title = s.directory;
    item.append(node('span', `dot ${s.status}`), node('span', 'name', s.title || '새 세션'));
    const del = node('button', 'del', '×');
    del.type = 'button';
    del.setAttribute('aria-label', '세션 지우기');
    del.addEventListener('click', async e => {
      e.stopPropagation();
      if (!confirm(`'${s.title || '새 세션'}' 세션을 지울까요? 파일은 그대로 두고 대화 기록만 지웁니다.`)) return;
      try {
        await api(`/api/code/session/${s.id}/delete`, {});
        if (s.id === current) clearView();
        loadList();
      } catch (err) { note(err.message, 'error'); }
    });
    item.append(del);
    item.addEventListener('click', () => open(s.id));
    list.append(item);
  }
  if (!sessions.length) list.append(node('small', 'muted', '아직 세션이 없습니다.'));
}
function clearView() {
  follow?.abort();
  current = null;
  bubble = null;
  $('log').replaceChildren();
  $('title').textContent = '세션을 고르거나 새로 만드세요';
  $('dir').textContent = '';
  paintTodos([]);
  setStatus('idle');
}
async function open(sid) {
  follow?.abort();
  current = sid;
  bubble = null;
  $('log').replaceChildren();
  document.body.classList.remove('side-open');
  $('backdrop').hidden = true;
  try {
    const [{session}, {messages}, listed] = await Promise.all([api(`/api/code/session/${sid}`),
      api(`/api/code/session/${sid}/message`), api(`/api/code/session/${sid}/command`).catch(() => ({commands: []}))]);
    if (current !== sid) return;
    commands = new Map(listed.commands.map(c => [c.name, c]));
    $('input').placeholder = `무엇을 할까요? (Enter 보내기 · Shift+Enter 줄바꿈 · 명령 ${[...commands.keys()].map(n => '/' + n).join(' ')})`;
    $('title').textContent = session.title || '새 세션';
    $('dir').textContent = `${session.directory} · ${TIER[session.tier] || session.tier}`
      + (session.model ? ` · OpenCode 무료 ${session.model.slice(9)}(코드가 opencode.ai 로 감)` : '');
    $('mode').value = session.mode === 'plan' ? 'plan' : 'build';
    paintHistory(messages);
    endBubble();
    for (const request of session.permissions || []) permissionCard(request);
    if (session.status === 'question') questionCard(session.questions);
    paintTodos(session.todos);
    setStatus(session.status);
    scrollDown(true);
    try { localStorage.setItem('alex-code-session', sid); } catch { /* 저장소 막힘 */ }
    followEvents(sid, session.next_event);
    loadList();
  } catch (err) {
    note(err.message, 'error');
  }
}
async function send(text = $('input').value.trim()) {
  if (!current || !text) return;
  $('send').disabled = true;
  try {
    const slash = text.match(/^\/([\w./-]+)(?:\s+([\s\S]*))?$/);
    if (slash && commands.has(slash[1])) {
      await api(`/api/code/session/${current}/command`, {command: slash[1], arguments: slash[2] || '', mode: $('mode').value});
    } else {
      await api(`/api/code/session/${current}/prompt_async`, {text, mode: $('mode').value});
    }
    $('input').value = '';
    setStatus('running');
  } catch (err) {
    note(err.message, 'error');
    setStatus(status);
  }
}

$('new-model').addEventListener('change', () => {
  $('model-note').textContent = $('new-model').value
    ? 'OpenCode 를 통해 opencode.ai 에서 돕니다 — 코드가 그쪽으로 전송됩니다. 바뀐 내용은 이 PC 권한대로 들입니다.' : '';
});
$('new-form').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    const {session} = await api('/api/code/session', {directory: $('new-dir').value.trim(), tier: $('new-tier').value,
      model: $('new-model').value});
    await open(session.id);
    $('input').focus();
  } catch (err) { note(err.message, 'error'); }
});
$('composer').addEventListener('submit', e => { e.preventDefault(); send(); });
$('input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
});
$('abort').addEventListener('click', async () => {
  try { await api(`/api/code/session/${current}/abort`, {}); } catch (err) { note(err.message, 'error'); }
});
$('diff-open').addEventListener('click', async () => {
  try {
    const {diff} = await api(`/api/code/session/${current}/diff`);
    paintDiff($('diff'), diff || '이 세션이 바꾼 내용이 없습니다.');
    $('revert').disabled = !diff || status === 'running';
    $('diff-dialog').showModal();
  } catch (err) { note(err.message, 'error'); }
});
$('diff-close').addEventListener('click', () => $('diff-dialog').close());
$('revert').addEventListener('click', async () => {
  if (!confirm('이 세션이 바꾼 파일을 모두 처음 상태로 되돌릴까요?')) return;
  try {
    await api(`/api/code/session/${current}/revert`, {step: 0});
    $('diff-dialog').close();
  } catch (err) { note(err.message, 'error'); }
});
$('menu').addEventListener('click', () => { document.body.classList.add('side-open'); $('backdrop').hidden = false; });
$('backdrop').addEventListener('click', () => { document.body.classList.remove('side-open'); $('backdrop').hidden = true; });

(async () => {
  setStatus('idle');
  try {
    const {root, directory, models} = await api('/api/code/path');
    for (const m of models || []) $('new-model').append(new Option(`OpenCode 무료 · ${m.name}`, m.id));
    $('new-dir').placeholder = directory || root;
    $('root-note').textContent = root ? `비우면 지금 프로젝트. 상대 경로는 ${root} 기준.` : '승인된 파일 루트가 없어 세션을 열 수 없습니다.';
  } catch (err) { note(err.message, 'error'); }
  await loadList();
  let last = null;
  try { last = localStorage.getItem('alex-code-session'); } catch { /* 저장소 막힘 */ }
  if (last && [...$('list').querySelectorAll('.item')].length) {
    try { await api(`/api/code/session/${last}`); open(last); } catch { /* 지워진 세션 */ }
  }
})();
