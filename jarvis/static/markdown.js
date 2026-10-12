'use strict';

// A small DOM renderer for chat Markdown. Raw HTML is always displayed as text.
// It never evaluates model output or loads remote images/scripts.
window.AlexMarkdown = (() => {
  const make = (tag, text) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const inlinePattern = /\\([\\`*_[\]{}()#+.!|>~-])|(`+)([^\n]+?)\2|(\*\*|__)(?=\S)([^\n]+?)\4|(\*|_)(?=\S)([^\n]+?)\6|~~(?=\S)([^\n]+?)~~|\[([^\]\n]{1,200})\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[A-Za-z0-9\-._~:/?#[\]@!$&'()*+,;=%]+)/g;
  const sourceLine = /^\[(\d+)\] (.+) — (https?:\/\/\S+)$/;
  const listLine = /^(\s*)([-+*]|\d+[.)])\s+(.+)$/;

  function codeBlock(text, language = '') {
    const block = make('div'), toolbar = make('div'), pre = make('pre'), content = make('code', text);
    block.className = 'code-block'; toolbar.className = 'code-toolbar';
    const label = make('span', /^[\w+-]{1,30}$/.test(language) ? language : '코드');
    if (/^[\w+-]{1,30}$/.test(language)) content.dataset.language = language;
    const copy = make('button', '코드 전체 복사');
    copy.type = 'button'; copy.className = 'code-copy';
    copy.setAttribute('aria-label', '코드 전체 복사'); copy.setAttribute('aria-live', 'polite');
    let reset;
    copy.addEventListener('click', async () => {
      clearTimeout(reset); copy.disabled = true;
      let copied = false;
      try { await navigator.clipboard.writeText(content.textContent); copied = true; } catch {
        // Clipboard API may be unavailable on HTTP or denied by browser settings.
        const active = document.activeElement, area = make('textarea');
        area.value = content.textContent; area.className = 'code-copy-buffer';
        area.setAttribute('aria-hidden', 'true'); area.tabIndex = -1;
        block.append(area); area.select();
        try { copied = document.execCommand('copy'); } catch { /* select code below */ }
        area.remove(); active?.focus({preventScroll: true});
      }
      copy.disabled = false;
      copy.textContent = copied ? '복사됨' : '코드를 선택했습니다';
      copy.title = copied ? '' : 'Ctrl+C 또는 기기의 복사 기능을 사용해 주세요.';
      if (!copied) {
        const range = document.createRange(); range.selectNodeContents(content);
        const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      }
      reset = setTimeout(() => { copy.textContent = '코드 전체 복사'; copy.title = ''; }, 2000);
    });
    toolbar.append(label, copy); pre.append(content); block.append(toolbar, pre);
    return block;
  }

  function link(label, raw) {
    try {
      const url = new URL(raw);
      if (!['http:', 'https:'].includes(url.protocol)) return null;
      const element = make('a', label);
      element.className = 'ext-link';
      element.href = url.href;
      element.target = '_blank';
      element.rel = 'noopener noreferrer';
      return element;
    } catch { return null; }
  }

  function inline(element, text, depth = 0) {
    if (depth > 4) { element.append(document.createTextNode(text)); return; }
    let last = 0;
    for (const match of text.matchAll(inlinePattern)) {
      element.append(document.createTextNode(text.slice(last, match.index)));
      let child, end = match.index + match[0].length;
      if (match[1]) child = document.createTextNode(match[1]);
      else if (match[2]) child = make('code', match[3]);
      else if (match[4] || match[6] || match[8]) {
        child = make(match[4] ? 'strong' : match[6] ? 'em' : 's');
        inline(child, match[5] || match[7] || match[8], depth + 1);
      } else {
        const raw = match[10] || match[11];
        const href = match[9] ? raw : raw.replace(/[.,;:!?'"\)\]}]+$/, '');
        if (!match[9]) end -= raw.length - href.length;
        child = link(match[9] || href, href) || document.createTextNode(match[0]);
      }
      element.append(child);
      last = end;
    }
    element.append(document.createTextNode(text.slice(last)));
  }

  const cells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).map(value => value.trim().replace(/\\\|/g, '|'));
  const tableRule = line => {
    const values = cells(line);
    return values.length > 1 && values.every(value => /^:?-{3,}:?$/.test(value));
  };

  function render(element, text, media = () => null, depth = 0) {
    if (depth > 4) { const p = make('p', text); element.append(p); return; }
    const lines = text.replace(/\r\n?/g, '\n').split('\n');
    let paragraph = [];
    const flush = () => {
      if (!paragraph.length) return;
      const p = make('p');
      const value = paragraph.join('\n'), source = value.match(sourceLine);
      const sourceLink = source && link(source[2], source[3]);
      if (sourceLink) {
        p.append(document.createTextNode(`[${source[1]}] `), sourceLink);
        const host = make('span', ' · ' + new URL(source[3]).host);
        host.className = 'link-host'; p.append(host);
      } else inline(p, value);
      element.append(p); paragraph = [];
    };

    for (let i = 0; i < lines.length;) {
      const line = lines[i];
      if (!line.trim()) { flush(); i++; continue; }
      const fence = line.match(/^\s{0,3}(`{3,}|~{3,})([^\n]*)$/);
      if (fence) {
        flush(); i++;
        const code = [], closer = new RegExp('^\\s{0,3}' + fence[1][0] + '{' + fence[1].length + ',}\\s*$');
        while (i < lines.length && !closer.test(lines[i])) code.push(lines[i++]);
        if (i < lines.length) i++;
        const language = fence[2].trim().split(/\s+/)[0];
        element.append(codeBlock(code.join('\n'), language)); continue;
      }
      const picture = media(line);
      if (picture) { flush(); element.append(picture); i++; continue; }
      const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)(?:\s+#+)?$/);
      if (heading) {
        flush(); const h = make('h' + heading[1].length);
        inline(h, heading[2]); element.append(h); i++; continue;
      }
      if (/^\s{0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$/.test(line)) {
        flush(); element.append(make('hr')); i++; continue;
      }
      if (/^\s{0,3}>/.test(line)) {
        flush(); const quote = [], block = make('blockquote');
        while (i < lines.length && /^\s{0,3}>/.test(lines[i])) quote.push(lines[i++].replace(/^\s{0,3}>\s?/, ''));
        render(block, quote.join('\n'), media, depth + 1); element.append(block); continue;
      }
      if (i + 1 < lines.length && line.includes('|') && tableRule(lines[i + 1])) {
        flush(); const wrapper = make('div'), table = make('table'), head = make('thead'), body = make('tbody');
        wrapper.className = 'markdown-table';
        const headers = cells(line), rules = cells(lines[i + 1]);
        const row = (values, tag) => {
          const tr = make('tr');
          headers.forEach((_, at) => {
            const cell = make(tag); inline(cell, values[at] || '');
            const rule = rules[at] || '';
            if (rule.endsWith(':')) cell.className = rule.startsWith(':') ? 'align-center' : 'align-right';
            tr.append(cell);
          });
          return tr;
        };
        head.append(row(headers, 'th')); i += 2;
        while (i < lines.length && lines[i].trim() && lines[i].includes('|')) body.append(row(cells(lines[i++]), 'td'));
        table.append(head, body); wrapper.append(table); element.append(wrapper); continue;
      }
      if (listLine.test(line)) {
        flush(); const stack = [];
        while (i < lines.length) {
          const item = lines[i].match(listLine);
          if (!item) break;
          const indent = item[1].replace(/\t/g, '    ').length, ordered = /^\d/.test(item[2]);
          while (stack.length && indent < stack.at(-1).indent) stack.pop();
          if (stack.length && indent === stack.at(-1).indent && ordered !== stack.at(-1).ordered) stack.pop();
          if (!stack.length || indent > stack.at(-1).indent) {
            const list = make(ordered ? 'ol' : 'ul');
            if (ordered) list.start = Number.parseInt(item[2], 10);
            (stack.at(-1)?.last || element).append(list);
            stack.push({indent, ordered, list, last: null});
          }
          const li = make('li'); inline(li, item[3]);
          stack.at(-1).list.append(li); stack.at(-1).last = li; i++;
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !listLine.test(lines[i])) {
            li.append(make('br')); inline(li, lines[i++].trim());
          }
        }
        continue;
      }
      paragraph.push(line); i++;
    }
    flush();
  }
  return {render, codeBlock};
})();
