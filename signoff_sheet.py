"""Build a single-file HTML page for signing off images one by one, and read back its results.

Usage:
  python signoff_sheet.py build ITEMS.json OUT.html [--lang en|zh]
  python signoff_sheet.py parse RESULTS.txt [--items ITEMS.json] [--out RESULTS.json]

`build` embeds every image in one self-contained page. For each image the reviewer sees what it
should show, the context, and why it needs a person, then clicks Pass or Fail (with a reason).
Decisions are kept in the browser's local storage, so the page can be closed and reopened. At the
end the reviewer copies the result as text, or downloads it as JSON.

`parse` turns the copied text back into JSON, so the decisions can be recorded by a script. With
`--items`, it also checks that every item in the sheet has a decision. The JSON is always UTF-8.

Only the Python standard library is needed. Pillow, if installed, is used to preview formats a
browser cannot show (for example TIFF); without it those items show "no preview".
"""
import argparse
import base64
import hashlib
import html
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BROWSER_TYPES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif',
                 '.webp': 'image/webp', '.svg': 'image/svg+xml', '.avif': 'image/avif'}

STRINGS = {
    'en': {
        'html_lang': 'en', 'title': 'Image sign-off', 'reviewed': 'Reviewed', 'copy': 'Copy result',
        'download': 'Download JSON', 'hide_done': 'Show only images not yet reviewed', 'pass': '✓ Pass',
        'fail': '✗ Fail', 'reason': 'Why it fails (for example: the plate is readable)',
        'expected': 'This image should show', 'context': 'Context', 'why': 'Why a person looks at it',
        'checklist': 'Check every image for:', 'intro_total': '{total} images.',
        'intro_how': 'Click <b>✓ Pass</b> when all points hold, or <b>✗ Fail</b> and write one line '
                     'about what is wrong. Click an image to enlarge it.',
        'intro_end': 'When you are done, click <b>Copy result</b> and send the text back, or download '
                     'the JSON. Your choices are kept if you close the page.',
        'result': 'Result', 'result_hint': '(Copy result copies the text below; select it by hand if '
                                           'copying is blocked)',
        'copied': 'Copied', 'copy_failed': 'Select and copy the result below by hand',
        'header': 'Sign-off result', 'PASS': 'PASS', 'FAIL': 'FAIL', 'NONE': 'NOT REVIEWED',
        'no_reason': '(no reason given)', 'no_preview': 'No preview: the browser cannot show this file',
        'converted': 'The browser cannot show this format; this is a converted preview',
    },
    'zh': {
        'html_lang': 'zh-CN', 'title': '看图签字', 'reviewed': '已看', 'copy': '复制结果',
        'download': '下载 JSON', 'hide_done': '只显示还没看的', 'pass': '✓ 通过', 'fail': '✗ 不通过',
        'reason': '不通过的原因（例如：车牌能读出来）', 'expected': '这张图应该拍的是', 'context': '背景',
        'why': '为什么要你看', 'checklist': '每张图都看这几点：', 'intro_total': '一共 {total} 张。',
        'intro_how': '都没问题就点 <b>✓ 通过</b>；有问题就点 <b>✗ 不通过</b>，写一句原因。点图片可以放大。',
        'intro_end': '看完点最上面的 <b>复制结果</b>，把文字发回来，或者下载 JSON。中途关掉页面也没关系，再打开会记得你点过的。',
        'result': '结果', 'result_hint': '（点"复制结果"会复制下面这段；复制不了就手动全选复制）',
        'copied': '已复制', 'copy_failed': '请手动复制最下面的结果', 'header': '看图结果',
        'PASS': '通过', 'FAIL': '不通过', 'NONE': '还没看', 'no_reason': '（没写原因）',
        'no_preview': '没有预览：浏览器打不开这个文件', 'converted': '浏览器打不开这种格式，下面是转换后的预览',
    },
}

LINE = re.compile(r'^#(?P<n>\d+)\s+(?P<id>\S+)\s+(?P<rest>.*)$')
DECISION_WORDS = {'PASS': 'pass', '通过': 'pass', 'FAIL': 'fail', '不通过': 'fail', 'NOT REVIEWED': None, '还没看': None}


def _image_source(path, raw, strings):
    """A data URI for the page and an optional note; formats a browser cannot show are converted if possible."""
    ext = os.path.splitext(path)[1].lower()
    if ext in BROWSER_TYPES:
        return f'data:{BROWSER_TYPES[ext]};base64,{base64.b64encode(raw).decode()}', None
    try:
        from PIL import Image, ImageFile  # optional dependency
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        out = io.BytesIO()
        Image.open(io.BytesIO(raw)).convert('RGB').save(out, 'JPEG', quality=90)
        return f'data:image/jpeg;base64,{base64.b64encode(out.getvalue()).decode()}', strings['converted']
    except Exception:  # noqa: BLE001 - no Pillow, or a file that cannot be opened at all
        return None, strings['no_preview']


def _list(value):
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def load_items(path):
    """Read and check the items file; image paths are relative to the file."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    items = data.get('items')
    if not isinstance(items, list) or not items:
        raise ValueError('items file needs a non-empty "items" list')
    base = os.path.dirname(os.path.abspath(path))
    seen = set()
    for index, item in enumerate(items, 1):
        if not item.get('id') or not item.get('image'):
            raise ValueError(f'item {index} needs "id" and "image"')
        if re.search(r'\s', str(item['id'])):
            raise ValueError(f'item id {item["id"]!r} must not contain spaces')
        if item['id'] in seen:
            raise ValueError(f'duplicate item id {item["id"]!r}')
        seen.add(item['id'])
        item['_path'] = os.path.join(base, item['image'])
        if not os.path.exists(item['_path']):
            raise ValueError(f'image not found for {item["id"]}: {item["_path"]}')
    return data


def build(items_path, out_path, lang='en'):
    strings = STRINGS[lang]
    data = load_items(items_path)
    items = data['items']
    title = data.get('title') or strings['title']
    checklist = _list(data.get('checklist'))
    # Decisions are stored under a key made from the ids and the image contents, so a sheet rebuilt
    # with new images under the same ids starts empty instead of showing the old decisions.
    fingerprint = hashlib.sha256()
    cards = []
    for number, item in enumerate(items, 1):
        raw = Path(item['_path']).read_bytes()
        fingerprint.update(f'{item["id"]}\n'.encode() + hashlib.sha256(raw).digest())
        src, note = _image_source(item['_path'], raw, strings)
        picture = (f'<img src="{src}" alt="{html.escape(item["id"])}" loading="lazy">' if src
                   else f'<div class="broken">{html.escape(strings["no_preview"])}</div>')
        tag = f'<span class="tag">{html.escape(str(item["tag"]))}</span>' if item.get('tag') else ''
        sections = []
        if item.get('expected'):
            sections.append(f'<h3>{strings["expected"]}</h3><p>{html.escape(str(item["expected"]))}</p>')
        if item.get('context'):
            lines = ''.join(f'<li>{html.escape(t)}</li>' for t in _list(item['context']))
            sections.append(f'<h3>{strings["context"]}</h3><ul>{lines}</ul>')
        if item.get('why'):
            lines = ''.join(f'<li>{html.escape(t)}</li>' for t in _list(item['why']))
            sections.append(f'<h3>{strings["why"]}</h3><ul>{lines}</ul>')
        label = item.get('label') or item['id']
        cards.append(f'''
<section class="card" id="n{number}" data-n="{number}" data-id="{html.escape(item['id'])}">
 <div class="head"><span class="num">#{number}</span>{tag}<span class="name">{html.escape(str(label))}</span></div>
 <div class="pic">{picture}</div>
 {f'<p class="note">{html.escape(note)}</p>' if note else ''}
 {''.join(sections)}
 <div class="choose">
  <button type="button" class="ok">{strings['pass']}</button>
  <button type="button" class="no">{strings['fail']}</button>
  <input type="text" class="reason" placeholder="{html.escape(strings['reason'])}" hidden>
 </div>
</section>''')
    checklist_html = (f'<p>{strings["checklist"]}</p><ol>' + ''.join(f'<li>{html.escape(c)}</li>' for c in checklist)
                      + '</ol>') if checklist else ''
    js_strings = {k: strings[k] for k in ('header', 'PASS', 'FAIL', 'NONE', 'no_reason', 'copied', 'copy_failed')}
    store = 'signoff-' + fingerprint.hexdigest()[:16]
    page = (PAGE.replace('{{LANG}}', strings['html_lang'])
            .replace('{{TITLE}}', html.escape(title))
            .replace('{{INTRO}}', f'<p><b>{strings["intro_total"].format(total=len(items))}</b></p>{checklist_html}'
                     f'<p>{strings["intro_how"]}</p><p>{strings["intro_end"]}</p>')
            .replace('{{BAR}}', f'<b>{strings["reviewed"]} <span id="done">0</span> / {len(items)}</b>'
                     f'<button type="button" id="copy">{strings["copy"]}</button>'
                     f'<button type="button" id="download">{strings["download"]}</button>'
                     f'<label><input type="checkbox" id="hide"> {strings["hide_done"]}</label><span id="copied"></span>')
            .replace('{{RESULT}}', f'<b>{strings["result"]}</b> {strings["result_hint"]}')
            .replace('{{CARDS}}', ''.join(cards))
            .replace('{{CONFIG}}', json.dumps({'store': store, 'title': title, 'generated': _now(),
                                               'strings': js_strings}, ensure_ascii=False)))
    Path(out_path).write_text(page, encoding='utf-8')
    return len(items)


def parse(text, items=None):
    """Decisions from the copied result text: [{n, id, decision, reason}]."""
    results = []
    for line in text.splitlines():
        match = LINE.match(line.strip())
        if not match:
            continue
        rest = match['rest'].strip()
        decision, reason, known = None, '', False
        for word, value in sorted(DECISION_WORDS.items(), key=lambda kv: -len(kv[0])):
            if rest == word or rest.startswith(word + ':') or rest.startswith(word + '：'):
                decision, known = value, True
                reason = rest[len(word):].lstrip(':：').strip()
                break
        if not known:
            raise ValueError(f'cannot read the decision in line: {line.strip()!r}')
        if reason in (STRINGS['en']['no_reason'], STRINGS['zh']['no_reason']):
            reason = ''
        if any(r['id'] == match['id'] for r in results):
            raise ValueError(f'more than one decision for {match["id"]!r}')
        results.append({'n': int(match['n']), 'id': match['id'], 'decision': decision, 'reason': reason})
    if items is not None:
        ids = [i['id'] for i in items]
        found = {r['id'] for r in results}
        missing = [i for i in ids if i not in found]
        unknown = [r['id'] for r in results if r['id'] not in ids]
        if missing or unknown:
            raise ValueError(f'result does not match the items: missing {missing}, unknown {unknown}')
    return results


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


PAGE = '''<!doctype html><html lang="{{LANG}}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{{TITLE}}</title>
<style>
:root { --bg:#f6f5f2; --card:#fff; --fg:#1d1d1b; --muted:#6b6a66; --line:#dddbd5; --ok:#1f7a45; --no:#b3261e; --tag:#3557a8; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#151514; --card:#1f1f1d; --fg:#ecebe7; --muted:#a3a19b; --line:#34332f; --ok:#5cc28a; --no:#f08b84; --tag:#8fa8ec; } }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--fg); font:16px/1.6 system-ui, "Microsoft YaHei", sans-serif; margin:0; }
.bar { position:sticky; top:0; z-index:5; background:var(--card); border-bottom:1px solid var(--line); padding:10px 16px; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
.bar b { font-size:18px; }
.bar button { font:inherit; padding:6px 14px; border-radius:8px; border:1px solid var(--line); background:var(--bg); color:var(--fg); cursor:pointer; }
.bar label, #copied { color:var(--muted); font-size:14px; }
main { max-width:860px; margin:0 auto; padding:16px; }
.intro, .card, .result { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 20px; margin-bottom:20px; }
.card { border-left:6px solid var(--line); }
.card.ok { border-left-color:var(--ok); } .card.no { border-left-color:var(--no); }
.head { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:10px; }
.num { font-size:22px; font-weight:700; }
.tag { font-size:13px; padding:2px 8px; border-radius:999px; color:#fff; background:var(--tag); }
.name { color:var(--muted); font-size:13px; word-break:break-all; }
.pic img { width:100%; height:auto; border-radius:8px; border:1px solid var(--line); cursor:zoom-in; display:block; }
.pic.big { position:fixed; inset:0; z-index:10; background:rgba(0,0,0,.92); display:flex; align-items:center; justify-content:center; padding:8px; }
.pic.big img { width:auto; max-width:100%; max-height:100%; cursor:zoom-out; border:0; }
.broken { padding:40px; text-align:center; border:2px dashed var(--line); border-radius:8px; color:var(--muted); }
.note { color:var(--muted); font-size:14px; }
h3 { font-size:15px; margin:14px 0 4px; color:var(--muted); }
p, li { margin:4px 0; } ul, ol { padding-left:22px; margin:4px 0; }
.choose { display:flex; gap:10px; flex-wrap:wrap; margin-top:16px; }
.choose button { font:inherit; font-size:17px; padding:10px 22px; border-radius:10px; border:2px solid var(--line); background:var(--bg); color:var(--fg); cursor:pointer; }
.card.ok .ok { background:var(--ok); border-color:var(--ok); color:#fff; }
.card.no .no { background:var(--no); border-color:var(--no); color:#fff; }
.reason { flex:1 1 260px; font:inherit; padding:10px; border-radius:10px; border:2px solid var(--no); background:var(--card); color:var(--fg); }
body.hide-done .card.ok:not(.keep), body.hide-done .card.no:not(.keep) { display:none; }
#out { width:100%; min-height:140px; font:14px/1.5 ui-monospace, monospace; margin-top:8px; background:var(--card); color:var(--fg); border:1px solid var(--line); border-radius:8px; padding:8px; }
</style></head><body>
<div class="bar">{{BAR}}</div>
<main>
<div class="intro">{{INTRO}}</div>
{{CARDS}}
<div class="result">{{RESULT}}<textarea id="out" readonly></textarea></div>
</main>
<script>
const CONFIG = {{CONFIG}};
const S = CONFIG.strings;
let state = {};
try { state = JSON.parse(localStorage.getItem(CONFIG.store) || '{}') || {}; } catch (e) { state = {}; }
function save() { try { localStorage.setItem(CONFIG.store, JSON.stringify(state)); } catch (e) {} }
// The card whose reason is being written stays in view when reviewed images are hidden.
let current = null;
const cards = [...document.querySelectorAll('.card')];
function decisions() {
  return cards.map((card) => {
    const s = state[card.dataset.n] || {};
    return { n: Number(card.dataset.n), id: card.dataset.id, decision: s.v === 'ok' ? 'pass' : s.v === 'no' ? 'fail' : null, reason: s.v === 'no' ? (s.r || '') : '' };
  });
}
function render() {
  let done = 0;
  for (const card of cards) {
    const s = state[card.dataset.n] || {};
    card.classList.toggle('ok', s.v === 'ok');
    card.classList.toggle('no', s.v === 'no');
    card.classList.toggle('keep', s.v === 'no' && (card.dataset.n === current || !s.r));
    const reason = card.querySelector('.reason');
    reason.hidden = s.v !== 'no';
    if (document.activeElement !== reason) reason.value = s.r || '';
    if (s.v) done++;
  }
  document.getElementById('done').textContent = done;
  const lines = decisions().map((d) => '#' + d.n + ' ' + d.id + ' ' + (d.decision === 'pass' ? S.PASS : d.decision === 'fail' ? S.FAIL + ': ' + (d.reason || S.no_reason) : S.NONE));
  document.getElementById('out').value = S.header + ' ' + done + '/' + cards.length + '\\n' + lines.join('\\n');
}
for (const card of cards) {
  const n = card.dataset.n;
  card.querySelector('.ok').onclick = () => { current = null; state[n] = { v: 'ok' }; save(); render(); };
  card.querySelector('.no').onclick = () => { current = n; state[n] = { v: 'no', r: (state[n] || {}).r || '' }; save(); render(); card.querySelector('.reason').focus(); };
  card.querySelector('.reason').oninput = (e) => { current = n; state[n] = { v: 'no', r: e.target.value }; save(); render(); };
  const pic = card.querySelector('.pic');
  pic.onclick = () => { if (pic.querySelector('img')) pic.classList.toggle('big'); };
}
document.getElementById('hide').onchange = (e) => { current = null; render(); document.body.classList.toggle('hide-done', e.target.checked); };
function flash(text) { const m = document.getElementById('copied'); m.textContent = text; setTimeout(() => { m.textContent = ''; }, 3000); }
document.getElementById('copy').onclick = async () => {
  render();
  const out = document.getElementById('out');
  try { await navigator.clipboard.writeText(out.value); flash(S.copied); }
  catch (e) { out.focus(); out.select(); try { document.execCommand('copy'); flash(S.copied); } catch (e2) { flash(S.copy_failed); } }
};
document.getElementById('download').onclick = () => {
  const body = JSON.stringify({ sheet: CONFIG.title, generated: CONFIG.generated, exported: new Date().toISOString(), items: decisions() }, null, 1);
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([body], { type: 'application/json' }));
  link.download = 'signoff-result.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
};
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') document.querySelectorAll('.pic.big').forEach((p) => p.classList.remove('big')); });
render();
</script></body></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build', help='build the sign-off page')
    b.add_argument('items')
    b.add_argument('out')
    b.add_argument('--lang', choices=sorted(STRINGS), default='en')
    p = sub.add_parser('parse', help='read a copied result back as JSON')
    p.add_argument('result')
    p.add_argument('--items')
    p.add_argument('--out', help='write the JSON to this file instead of standard output')
    args = parser.parse_args(argv)
    try:
        if args.command == 'build':
            count = build(args.items, args.out, args.lang)
            print(f'{count} images -> {args.out}')
        else:
            items = load_items(args.items)['items'] if args.items else None
            # utf-8-sig: editors such as Notepad may start the saved text with a byte order mark.
            text = Path(args.result).read_text(encoding='utf-8-sig')
            output = json.dumps(parse(text, items), ensure_ascii=False, indent=1) + '\n'
            if args.out:
                Path(args.out).write_text(output, encoding='utf-8')
            else:
                # JSON is UTF-8; on Windows the default is the code page, which may not hold the reasons.
                if hasattr(sys.stdout, 'reconfigure'):
                    sys.stdout.reconfigure(encoding='utf-8')
                sys.stdout.write(output)
    except ValueError as error:
        sys.exit(f'error: {error}')


if __name__ == '__main__':
    main()
