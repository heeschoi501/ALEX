// 실시간 통역(ADR-111) — 마이크 소리를 말 단위로 끊어 ALEX 로 보내고, 받아 적은 원문과 한국어 자막을 보인다.
// 끊기: 20ms 마다 소리 크기를 재고, 주변 소음보다 3배 크면 말 시작, 0.7초 조용하면 끝(12초 넘으면 자른다).
'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="jarvis-token"]').content;
const RATE = 16000, FRAME = 320, PREROLL = 15, END_SILENCE = 35, MAX_FRAMES = 600, MIN_VOICED = 15;
let sessionId = '', ctx = null, stream = null, node = null, wakeLock = null, running = false;
let size = parseFloat(localStorage.getItem('alex-live-size') || '2.2');
const entries = [];
let sequence = 0;

function setSize(value) {
  size = Math.min(5, Math.max(1.2, value));
  document.documentElement.style.setProperty('--size', size + 'rem');
  try { localStorage.setItem('alex-live-size', String(size)); } catch { /* 이 브라우저에 못 남기면 매번 기본 크기 */ }
}
function status(text, listening) { $('status').textContent = text; $('status').classList.toggle('listening', !!listening); }

// ── 화면 그리기: 마지막으로 들은 말이 크게, 그 앞 네 개는 작게 위에
function render() {
  const heard = entries.filter(e => e.text);
  const current = heard[heard.length - 1];
  $('lines').replaceChildren(...heard.slice(-5, -1).map(e => {
    const p = document.createElement('p');
    p.className = e.korean ? 'ko' : '';
    p.textContent = e.korean || e.text;
    return p;
  }));
  if (!current) return;
  const source = $('source');
  source.replaceChildren();
  if (current.translate) {
    const tag = document.createElement('span'); tag.className = 'tag'; tag.textContent = current.label;
    source.append(tag, document.createTextNode(current.text));
  }
  const korean = $('korean');
  korean.classList.toggle('pending', current.translate && !current.korean);
  korean.textContent = current.translate ? (current.korean || '…') : current.text;
}

// ── 보내기: 말 한 토막 → 줄마다 JSON(heard → translation → done)
async function send(samples, endedAt) {
  const entry = {seq: ++sequence, endedAt};
  entries.push(entry); if (entries.length > 40) entries.shift();
  let response;
  try {
    response = await fetch('/api/live/utterance', {method: 'POST', body: wav(samples), headers: {
      'Content-Type': 'audio/wav', 'X-Jarvis-Token': token, 'X-Live-Session': sessionId,
      'X-Live-Record': $('record').checked ? '1' : '0'}});
  } catch { status('ALEX 에 연결하지 못했습니다', running); return; }
  if (!response.ok) {
    let message = `오류 ${response.status}`;
    try { message = (await response.json()).error || message; } catch { /* 본문 없음 */ }
    if (response.status === 401) message = 'ALEX 화면에서 먼저 로그인해 주세요.';
    status(message, running); return;
  }
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const {value, done} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    let cut;
    while ((cut = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, cut); buffer = buffer.slice(cut + 1);
      if (!line.trim()) continue;
      const event = JSON.parse(line);
      if (event.type === 'heard') {
        Object.assign(entry, {text: event.text, label: event.label, translate: ['en', 'ja', 'zh', 'yue'].includes(event.lang)});
        entry.heardIn = (performance.now() - endedAt) / 1000;
        render();
      } else if (event.type === 'translation') {
        entry.korean = event.text;
        const total = (performance.now() - endedAt) / 1000;
        $('timing').textContent = `말 끝→자막 ${total.toFixed(1)}초 (받아쓰기 ${event.stt.toFixed(2)} · 기다림 ${event.wait.toFixed(1)} · 번역 ${event.translate.toFixed(1)})`;
        render();
      } else if (event.type === 'done' && entry.text && !entry.translate) {
        $('timing').textContent = `말 끝→글자 ${((performance.now() - endedAt) / 1000).toFixed(1)}초`;
      } else if (event.type === 'queue') {
        status(`번역 차례를 기다리는 중(앞에 ${event.position})`, running);
      } else if (event.type === 'error') {
        status(event.error, running);
      }
    }
  }
  if (running) status('듣는 중', true);
}

function wav(samples) {
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (offset, s) => { for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i)); };
  text(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); text(8, 'WAVE'); text(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, RATE, true);
  view.setUint32(28, RATE * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, samples[i])) * 0x7fff, true);
  return new Blob([view], {type: 'audio/wav'});
}

// ── 끊기(소리 크기 기준)
const cutter = {pending: new Float32Array(0), ratio: 1, phase: 0, floor: 0.01, frames: [], recent: [], voiced: 0, silence: 0, speaking: false};
function resample(input) {
  if (cutter.ratio === 1) return input;
  const out = [];
  let position = cutter.phase;
  while (position < input.length - 1) {
    const i = Math.floor(position), f = position - i;
    out.push(input[i] * (1 - f) + input[i + 1] * f);
    position += cutter.ratio;
  }
  cutter.phase = position - input.length;
  return Float32Array.from(out);
}
function feed(chunk) {
  const samples = resample(chunk);
  const merged = new Float32Array(cutter.pending.length + samples.length);
  merged.set(cutter.pending); merged.set(samples, cutter.pending.length);
  let offset = 0;
  for (; offset + FRAME <= merged.length; offset += FRAME) frame(merged.slice(offset, offset + FRAME));
  cutter.pending = merged.slice(offset);
}
function frame(samples) {
  let sum = 0;
  for (const s of samples) sum += s * s;
  const rms = Math.sqrt(sum / samples.length), threshold = Math.max(cutter.floor * 3, 0.008);
  if (!cutter.speaking) {
    if (rms < cutter.floor * 2) cutter.floor = Math.max(0.002, cutter.floor * 0.95 + rms * 0.05);
    cutter.recent.push(samples); if (cutter.recent.length > PREROLL) cutter.recent.shift();
    cutter.voiced = rms > threshold ? cutter.voiced + 1 : 0;
    if (cutter.voiced >= 3) { cutter.speaking = true; cutter.frames = cutter.recent.slice(); cutter.recent = []; cutter.silence = 0; status('듣는 중 — 말하는 중', true); }
    return;
  }
  cutter.frames.push(samples);
  if (rms > threshold * 0.7) { cutter.voiced++; cutter.silence = 0; } else cutter.silence++;
  if (cutter.silence >= END_SILENCE || cutter.frames.length >= MAX_FRAMES) {
    const ended = cutter.silence >= END_SILENCE;
    const frames = ended ? cutter.frames.slice(0, cutter.frames.length - END_SILENCE + 10) : cutter.frames;
    const voiced = cutter.voiced;
    cutter.frames = []; cutter.voiced = 0; cutter.silence = 0; cutter.speaking = !ended;
    if (voiced >= MIN_VOICED) {
      const joined = new Float32Array(frames.length * FRAME);
      frames.forEach((f, i) => joined.set(f, i * FRAME));
      status('받아 적는 중', true);
      send(joined, performance.now());
    } else if (running) status('듣는 중', true);
  }
}

// ── 시작·멈춤
async function keepAwake() {
  try { wakeLock = await navigator.wakeLock?.request('screen'); wakeLock?.addEventListener('release', () => { wakeLock = null; }); }
  catch { /* 화면 꺼짐 막기는 지원할 때만 */ }
}
async function start() {
  $('toggle').disabled = true;
  try {
    const r = await fetch('/api/live/start', {method: 'POST', body: '{}', headers: {'Content-Type': 'application/json', 'X-Jarvis-Token': token}});
    const body = await r.json().catch(() => ({}));
    if (r.status === 401) throw new Error('ALEX 화면에서 먼저 로그인해 주세요.');
    if (r.status === 403) throw new Error('연결이 만료되었습니다. 새로고침해 주세요.');
    if (!r.ok) throw new Error(body.error || `오류 ${r.status}`);
    sessionId = body.session_id;
    stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
    try { ctx = new AudioContext({sampleRate: RATE}); } catch { ctx = new AudioContext(); }
    cutter.ratio = ctx.sampleRate / RATE; cutter.phase = 0; cutter.pending = new Float32Array(0);
    await ctx.audioWorklet.addModule('/live-worklet.js');
    node = new AudioWorkletNode(ctx, 'alex-capture');
    node.port.onmessage = e => { if (running) feed(e.data); };
    ctx.createMediaStreamSource(stream).connect(node);
    running = true;
    await keepAwake();
    $('toggle').textContent = '멈춤'; $('toggle').classList.add('on');
    $('korean').textContent = '말씀하세요.'; $('source').textContent = '';
    status('듣는 중', true);
  } catch (e) {
    status(e.name === 'NotAllowedError' ? '마이크 사용을 허락해 주세요(주소창의 자물쇠 → 마이크).' : (e.message || String(e)), false);
    stop();
  } finally { $('toggle').disabled = false; }
}
function stop() {
  running = false;
  try { node?.disconnect(); } catch { /* 이미 끊김 */ }
  stream?.getTracks().forEach(t => t.stop());
  ctx?.close().catch(() => {});
  wakeLock?.release?.().catch(() => {});
  node = stream = ctx = wakeLock = null;
  Object.assign(cutter, {frames: [], recent: [], voiced: 0, silence: 0, speaking: false});
  $('toggle').textContent = '시작'; $('toggle').classList.remove('on');
  if ($('status').textContent.startsWith('듣는 중')) status('멈춤', false);
}
$('toggle').addEventListener('click', () => running ? stop() : start());
$('smaller').addEventListener('click', () => setSize(size - 0.2));
$('bigger').addEventListener('click', () => setSize(size + 0.2));
document.addEventListener('visibilitychange', async () => {
  if (running && document.visibilityState === 'visible' && !wakeLock) await keepAwake();
});
setSize(size);
if (!window.isSecureContext) status('마이크는 https 또는 이 PC(127.0.0.1)에서만 쓸 수 있습니다.', false);
