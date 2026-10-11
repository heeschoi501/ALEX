// ALEX 앱(PWA) 서비스 워커(2026-10-11) — 홈 화면 설치와 연결이 끊겼을 때 안내만 한다.
// 페이지(토큰이 들어 있다)와 API 응답은 절대 저장하지 않는다. 화면 이동 요청만 보고, 네트워크가 안 되면 안내를 보여 준다.
const OFFLINE = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
  + '<title>ALEX</title></head><body><h2>ALEX에 연결할 수 없습니다</h2>'
  + '<p>PC가 켜져 있는지, 휴대폰의 Tailscale 연결이 켜져 있는지 확인한 뒤 다시 열어 주세요.</p></body></html>';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request).catch(() => new Response(OFFLINE, {headers: {'Content-Type': 'text/html; charset=utf-8'}})));
});
