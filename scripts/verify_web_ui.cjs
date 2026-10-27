/* Optional Chromium check. Requires Node, playwright + Chromium, openssl and Python.
 * All data and model replies are synthetic; no GPU or external model is called.
 * Run: node scripts/verify_web_ui.cjs [python executable]
 */
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const https = require('https');
const http = require('http');
const {spawn, spawnSync} = require('child_process');
const {createInterface} = require('readline');
const assert = require('assert').strict;
const root = path.resolve(__dirname, '..');
const modulePath = require.resolve('playwright', {
  paths: [root, process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES || root]
});
const {chromium} = require(modulePath);

async function main() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'alex-browser-'));
  const record = path.join(temp, 'messages.json');
  let browser, proxy, child;
  try {
    const cert = spawnSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes',
      '-keyout', path.join(temp, 'key.pem'), '-out', path.join(temp, 'cert.pem'),
      '-days', '1', '-subj', '/CN=127.0.0.1',
      '-addext', 'subjectAltName=IP:127.0.0.1'], {encoding: 'utf8'});
    assert.equal(cert.status, 0, cert.stderr);
    child = spawn(process.argv[2] || 'python3', [path.join(root, 'tests/web_ui_fixture.py'),
      '--record', record], {cwd: root, stdio: ['ignore', 'pipe', 'inherit']});
    const ready = await new Promise((resolve, reject) => {
      const lines = createInterface({input: child.stdout});
      const timer = setTimeout(() => reject(new Error('fixture startup timeout')), 15000);
      lines.once('line', line => {clearTimeout(timer); lines.close(); resolve(JSON.parse(line));});
      child.once('error', reject);
      child.once('exit', code => reject(new Error('fixture exited: ' + code)));
    });
    proxy = https.createServer({key: fs.readFileSync(path.join(temp, 'key.pem')),
      cert: fs.readFileSync(path.join(temp, 'cert.pem'))}, (request, response) => {
      const upstream = http.request({hostname: '127.0.0.1', port: ready.port,
        path: request.url, method: request.method, headers: request.headers}, incoming => {
        response.writeHead(incoming.statusCode, incoming.headers);
        incoming.pipe(response);
      });
      upstream.on('error', () => {response.writeHead(503); response.end('fixture unavailable');});
      request.pipe(upstream);
    });
    await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve));
    const url = 'https://127.0.0.1:' + proxy.address().port;
    browser = await chromium.launch({headless: true,
      executablePath: process.env.ALEX_TEST_CHROMIUM || chromium.executablePath(),
      args: ['--ignore-certificate-errors']});
    const context = await browser.newContext({ignoreHTTPSErrors: true, viewport: {width: 1440, height: 1000}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.setDefaultTimeout(15000);
    const log = name => console.log('PASS: ' + name);
    const token = () => page.locator('meta[name="jarvis-token"]').getAttribute('content');
    const api = async (endpoint, body, ctx = context) => {
      const response = await ctx.request.fetch(url + endpoint, {method: body ? 'POST' : 'GET',
        headers: {'X-Jarvis-Token': await token(), 'Content-Type': 'application/json'},
        ...(body ? {data: body} : {})});
      return {status: response.status(), body: await response.json()};
    };
    await page.goto(url);
    await page.waitForFunction(() => document.getElementById('account-name').textContent !== '…');
    assert.equal(await page.locator('#login-screen').isVisible(), false);
    log('desktop load');
    await page.locator('#input').fill('안녕하세요 UI 검증');
    await page.locator('#send').click();
    await page.waitForFunction(() => document.getElementById('title').textContent === 'UI 검증 대화');
    log('chat and automatic title');
    page.once('dialog', dialog => dialog.accept('수동 검증 제목'));
    await page.locator('#title').click();
    await page.waitForFunction(() => document.getElementById('title').textContent === '수동 검증 제목');
    // 옆 목록 '대화 검색' 은 제목에 없는 메시지 본문도 찾는다(2026-10-26)
    await page.locator('#session-search').fill('안녕하세요');
    await page.locator('#session-hits .hit-item').filter({hasText: '수동 검증 제목'}).waitFor();
    await page.locator('#session-search').fill('');
    assert(await page.locator('#session-hits').isHidden());
    log('conversation search finds message text');
    await page.locator('#project-new').click();
    await page.locator('#project-form-name').fill('RTL 검증 프로젝트');
    await page.locator('#project-form-instructions').fill('검증 지침 ALPHA_INSTRUCTION');
    await page.locator('#project-form button.primary').click();
    await page.locator('#project-view').waitFor({state: 'visible'});
    await page.locator('#project-file').setInputFiles({name: 'fifo-notes.md', mimeType: 'text/markdown',
      buffer: Buffer.from('# FSM testbench\nFSM testbench uses ALPHA_PRIVATE_MARKER.\n')});
    await page.locator('#project-files').getByText('fifo-notes.md', {exact: true}).waitFor();
    await page.route('**/api/session/new', async route => {
      await new Promise(resolve => setTimeout(resolve, 300));
      await route.continue();
    });
    await page.locator('#input').fill('FSM testbench 설명해줘');
    await page.locator('#send').click();
    assert(await page.locator('#send').isDisabled(), 'project setup must prevent duplicate sends');
    await page.waitForFunction(() => document.getElementById('send').disabled === false);
    await page.unroute('**/api/session/new');
    let messages = JSON.parse(fs.readFileSync(record, 'utf8'));
    assert(messages.some(m => m.content.includes('ALPHA_INSTRUCTION')));
    assert(messages.some(m => m.content.includes('ALPHA_PRIVATE_MARKER')));
    const projectId = (await api('/api/state')).body.project.id;
    log('project create, upload, instructions and retrieval');
    // 새 요청이 실제로 모델에 갈 때까지 기다린다 — 전에는 앞 요청의 기록을 읽어 보내지도 않은 글을 검사할 수 있었다.
    const previousRecord = fs.readFileSync(record, 'utf8');
    // 새 대화가 만들어진 뒤 그 대화를 불러오는 동안 친 글도 새 대화에 남는다 — 전에는 새 대화의 빈 초안이 덮어
    // 가끔(이 구간에 칠 때만) 글이 사라졌다. 그 구간을 늦춰 매번 그때 친다.
    let opened;
    const opening = new Promise(resolve => { opened = resolve; });
    const isNewChat = url => url.pathname === '/api/session/new';
    let newSession = '';
    const isOpening = url => url.pathname === '/api/state' && newSession && url.searchParams.get('session_id') === newSession;
    await page.route(isNewChat, async route => {
      const response = await route.fetch();
      newSession = (await response.json()).session_id;
      await route.fulfill({response});
    });
    await page.route(isOpening, async route => {
      opened();
      await new Promise(resolve => setTimeout(resolve, 300));
      await route.continue();
    });
    await page.locator('#new-chat').click();
    await opening;
    await page.locator('#input').fill('FSM testbench 설명해줘');
    await page.waitForFunction(() => document.getElementById('send').disabled === false);
    await page.unroute(isNewChat);
    await page.unroute(isOpening);
    assert.equal(await page.locator('#input').inputValue(), 'FSM testbench 설명해줘', 'text typed while the new chat opens must stay');
    await page.locator('#send').click();
    await page.waitForFunction(() => document.getElementById('send').disabled === false);
    for (let i = 0; i < 100 && fs.readFileSync(record, 'utf8') === previousRecord; i++) await new Promise(r => setTimeout(r, 100));
    assert.notEqual(fs.readFileSync(record, 'utf8'), previousRecord, 'the new chat must reach the model');
    messages = JSON.parse(fs.readFileSync(record, 'utf8'));
    assert(!messages.some(m => m.content.includes('ALPHA_PRIVATE_MARKER')));
    assert(!messages.some(m => m.content.includes('ALPHA_INSTRUCTION')));
    log('project context does not leak outside');
    await page.locator('#settings-open').click();
    await page.locator('[data-theme-choice="dark"]').click();
    await page.locator('#settings-close').click();
    await page.reload();
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    log('theme persistence');
    await page.locator('#account-button').click();
    await page.locator('[data-dialog="memories-dialog"]').click();
    await page.locator('#memory-key').fill('테스트');
    await page.locator('#memory-value').fill('기억 유지');
    await page.locator('#memory-form button').click();
    await page.locator('#memories span').getByText('기억 유지', {exact: true}).waitFor();
    await page.locator('#memories-dialog [data-close]').click();
    await page.locator('#account-button').click();
    await page.locator('[data-dialog="tasks-dialog"]').click();
    await page.locator('#task-text').fill('합성 검증 할 일');
    await page.locator('#task-form button').click();
    await page.getByRole('checkbox', {name: '합성 검증 할 일'}).check();
    await page.locator('#tasks .task.done').waitFor();
    await page.locator('#tasks-dialog [data-close]').click();
    log('memory and task controls');
    await page.reload();
    assert.equal((await api('/api/state')).body.memories['테스트'], '기억 유지');
    const mobile = await browser.newContext({ignoreHTTPSErrors: true, viewport: {width: 390, height: 844},
      isMobile: true, hasTouch: true});
    const phone = await mobile.newPage();
    phone.on('pageerror', error => errors.push(error.message));
    await phone.goto(url);
    await phone.locator('#sessions-toggle').click();
    await phone.locator('#sidebar.open').waitFor();
    await phone.getByRole('button', {name: 'RTL 검증 프로젝트', exact: false}).click();
    await phone.locator('#project-view').waitFor({state: 'visible'});
    assert(await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await phone.locator('#input').fill('모바일 FSM 질문');
    await phone.locator('#send').click();
    await phone.waitForFunction(() => document.getElementById('send').disabled === false);
    log('phone drawer, project and composer');
    const cdp = await context.newCDPSession(page);
    const manifest = await cdp.send('Page.getAppManifest');
    assert.equal(manifest.errors.length, 0, JSON.stringify(manifest.errors));
    await page.evaluate(async () => {
      await Promise.race([navigator.serviceWorker.ready,
        new Promise((_, reject) => setTimeout(() => reject(new Error('service worker timeout')), 10000))]);
    });
    await page.reload();
    await page.waitForFunction(() => !!navigator.serviceWorker.controller);
    await context.setOffline(true);
    await page.goto(url + '/offline-check');
    await page.getByText('ALEX에 연결할 수 없습니다', {exact: true}).waitFor();
    await context.setOffline(false);
    await page.goto(url);
    log('PWA manifest, worker and offline navigation');
    const users = await api('/api/users', {action: 'create', name: '테스트 사용자',
      login: 'browser-member', password: 'browser-test-pass'});
    assert.equal(users.status, 200);
    const memberId = users.body.users.find(u => u.login === 'browser-member').id;
    const member = await browser.newContext({ignoreHTTPSErrors: true});
    const memberPage = await member.newPage();
    memberPage.on('pageerror', error => errors.push(error.message));
    await memberPage.goto(url);
    await api('/api/logout', {}, member);
    await memberPage.reload();
    await memberPage.locator('#login-id').fill('browser-member');
    await memberPage.locator('#login-password').fill('browser-test-pass');
    await memberPage.locator('#login-submit').click();
    await memberPage.waitForFunction(() => document.getElementById('login-screen').hidden &&
      document.body.classList.contains('member'));
    assert.equal(await memberPage.locator('#shutdown').isVisible(), false);
    assert.equal((await api('/api/me', undefined, member)).body.user.id, memberId);
    assert.equal((await api('/api/project?id=' + projectId, undefined, member)).status, 404);
    await api('/api/users', {action: 'disable', id: memberId});
    await memberPage.reload();
    await memberPage.locator('#login-screen').waitFor({state: 'visible'});
    log('real browser login, ownership and account disabling');
    assert.deepEqual(errors, []);
    log('zero JavaScript page errors');
    console.log('WEB UI VALIDATION: PASS');
  } finally {
    if (browser) await browser.close();
    if (proxy) await new Promise(resolve => proxy.close(resolve));
    if (child) {
      const stopped = new Promise(resolve => child.once('exit', resolve));
      child.kill('SIGTERM');
      await stopped;
    }
    fs.rmSync(temp, {recursive: true, force: true});
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
