#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定稿字幕 HTML：把整期每条的 字幕.json 排成一页，渲染前给人通读。

用法：python3 subs_html.py --config <期次配置.json> [-o 输出.html]

和 preview.py 的区别：preview 是单条纯文本；这里整期一页、带画面示意，
文本定稿改过的字高亮出来——读的人一眼能看到「这一处是人工改过的」。
"""
import json, os, re, sys, html

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import qc_subs as Q

MAX_EM = 11.5


def fix_targets(path):
    """文本修正.txt 里「=>」右边的改后文字；太短的（单字）不标，否则满篇都是"""
    out = []
    if not os.path.exists(path):
        return out
    for ln in open(path, encoding='utf-8'):
        ln = ln.strip()
        if not ln or ln.startswith('#') or '=>' not in ln:
            continue
        a, b = [x.strip() for x in ln.split('=>', 1)]
        if len(b) >= 2:
            out.append(b)
    return out


def mark_chars(lines, targets):
    """在各行拼接后的全文里找改后文字，返回每行的高亮掩码"""
    full = ''.join(l['text'] for l in lines)
    hit = [False] * len(full)
    for t in targets:
        # 修正里带标点，字幕行里标点已被去掉/换成空格——比对时两边都去掉
        core = re.sub(r'[，。？！、；：\s]', '', t)
        if len(core) < 2:
            continue
        for m in re.finditer(re.escape(core), full):
            for k in range(m.start(), m.end()):
                hit[k] = True
    out, p = [], 0
    for l in lines:
        out.append(hit[p:p+len(l['text'])])
        p += len(l['text'])
    return out


def fmt(t):
    return '%d:%04.1f' % (int(t // 60), t % 60)


def esc(s):
    return html.escape(s, quote=True)


def clip_section(row, title, d):
    subs = json.load(open(os.path.join(d, '字幕.json')))
    lines = subs['lines']
    ppt = subs.get('ppt_ranges') or []
    masks = mark_chars(lines, fix_targets(os.path.join(d, '文本修正.txt')))
    rows_html, n_bad, n_mark = [], 0, 0
    for i, l in enumerate(lines):
        t = l['text']
        w = Q.width(t)
        nxt = lines[i+1] if i+1 < len(lines) else None
        flag = ''
        if nxt and not nxt.get('clause_start'):
            c = Q.crossing_word(t, nxt['text'])
            if c:
                flag = '<span class="flag bad" title="和下一行劈开了一个词">劈开「%s」</span>' % esc(c)
                n_bad += 1
        if not flag and w > MAX_EM:
            flag = '<span class="flag bad">超宽 %.1f</span>' % w
            n_bad += 1
        in_ppt = any(a - 0.05 <= l['start'] < b for a, b in ppt)
        # 高亮：连续的改动字合成一个 <mark>
        buf, cur = [], None
        for ch, m in zip(t, masks[i]):
            if m != cur:
                if cur:
                    buf.append('</mark>')
                if m:
                    buf.append('<mark>')
                cur = m
            buf.append(esc(ch))
        if cur:
            buf.append('</mark>')
        if any(masks[i]):
            n_mark += 1
        cls = ['ln']
        if l.get('clause_start') and i:
            cls.append('cs')
        if in_ppt:
            cls.append('ppt')
        rows_html.append(
            '<li class="%s" data-t="%s"><span class="tm">%s</span>'
            '<span class="tx">%s</span><span class="wd"><i style="width:%.0f%%"></i></span>%s</li>'
            % (' '.join(cls), esc(t), fmt(l['start']), ''.join(buf), min(100, w / MAX_EM * 100), flag))
    dur = subs.get('out_duration') or (lines[-1]['end'] if lines else 0)
    head = ('<section class="clip" id="r%s"><header><span class="no">%02d</span>'
            '<h2>%s</h2><p class="meta">%s · %d 行%s%s%s</p></header>'
            % (row, int(row), esc(title), fmt(dur).rsplit('.', 1)[0], len(lines),
               ' · <span class="m">改动 %d 行</span>' % n_mark if n_mark else '',
               ' · <span class="p">PPT %d 段</span>' % len(ppt) if ppt else '',
               ' · <span class="b">可疑 %d</span>' % n_bad if n_bad else ''))
    return head + '<ol>' + ''.join(rows_html) + '</ol></section>', len(lines), n_bad, dur


CSS = r"""
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--mute:#76746f;--line:#e6e3dc;--mark:#ffe58a;
--ppt:#eef4ff;--pptb:#9db8ea;--bad:#c2410c;--acc:#2f6f5e;--frame:#2b2b2b}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#171716;--card:#201f1e;--ink:#ecebe6;
--mute:#9d9a93;--line:#34322f;--mark:#6b5a12;--ppt:#1d2a3d;--pptb:#44628f;--bad:#f59e6b;--acc:#7cc4ae;--frame:#0e0e0e}}
:root[data-theme="dark"]{--bg:#171716;--card:#201f1e;--ink:#ecebe6;--mute:#9d9a93;--line:#34322f;--mark:#6b5a12;
--ppt:#1d2a3d;--pptb:#44628f;--bad:#f59e6b;--acc:#7cc4ae;--frame:#0e0e0e}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"PingFang SC","Source Han Sans CN",sans-serif}
.wrap{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:24px;max-width:1180px;margin:0 auto;padding:24px 16px 80px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mute);margin:0 0 16px;font-size:13px}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--mute);margin-bottom:16px}
.legend span{display:inline-flex;align-items:center;gap:6px}.sw{width:14px;height:14px;border-radius:3px;display:inline-block}
nav.toc{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:20px}
nav.toc a{font-size:12px;padding:4px 9px;border:1px solid var(--line);border-radius:999px;color:var(--ink);text-decoration:none;background:var(--card)}
nav.toc a:hover{border-color:var(--acc)}
.clip{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:20px;overflow:hidden}
.clip header{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;padding:14px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--card);z-index:1}
.no{font:600 13px ui-monospace,Menlo,monospace;color:var(--acc)}h2{font-size:17px;margin:0}
.meta{margin:0;color:var(--mute);font-size:12px;width:100%}.meta .m{color:inherit;background:var(--mark);padding:0 4px;border-radius:3px}
.meta .p{color:inherit;background:var(--ppt);padding:0 4px;border-radius:3px}.meta .b{color:var(--bad);font-weight:600}
ol{list-style:none;margin:0;padding:6px 0}
.ln{display:grid;grid-template-columns:54px minmax(0,1fr) 46px auto;align-items:center;gap:10px;padding:3px 16px;cursor:pointer;border-left:3px solid transparent}
.ln:hover,.ln.on{background:color-mix(in srgb,var(--acc) 10%,transparent)}
.ln.on{border-left-color:var(--acc)}
.ln.cs{margin-top:7px}
.ln.ppt{background:var(--ppt);border-left-color:var(--pptb)}
.tm{font:12px ui-monospace,Menlo,monospace;color:var(--mute)}
.tx{font-size:17px;letter-spacing:.02em;overflow-wrap:anywhere}
mark{background:var(--mark);color:inherit;border-radius:2px;padding:0 1px}
.wd{height:4px;background:var(--line);border-radius:2px;overflow:hidden}.wd i{display:block;height:100%;background:var(--mute);opacity:.5}
.flag{font-size:11px;white-space:nowrap}.flag.bad{color:var(--bad);font-weight:600}
aside{position:sticky;top:16px;align-self:start}
aside h3{font-size:13px;margin:0 0 8px;color:var(--mute);font-weight:500}
.fr{position:relative;background:var(--frame);border-radius:6px;overflow:hidden;margin-bottom:14px;container-type:inline-size}
.fr.h{aspect-ratio:16/9}.fr.v{aspect-ratio:9/16;width:62%}
.fr .band{position:absolute;left:0;right:0;background:#3b3b3b}
.fr .cap{position:absolute;left:0;right:0;text-align:center;color:#fff;font-weight:500;white-space:nowrap;
font-family:"Source Han Sans CN","PingFang SC",sans-serif;
-webkit-text-stroke:.075em #000;paint-order:stroke fill}
.fr.h .cap{font-size:calc(100cqw*133.5/1920);bottom:calc(100cqw*75/1920)}
.fr.v .cap{font-size:calc(100cqw*75/1080);bottom:calc(100cqw*569/1080)}
.fr.v .band{top:calc(100cqw*656/1080);height:calc(100cqw*608/1080)}
.hint{font-size:12px;color:var(--mute)}
@media (max-width:860px){.wrap{grid-template-columns:1fr}aside{position:static;order:-1}.fr.v{width:45%}}
@media (max-width:480px){.ln{grid-template-columns:44px minmax(0,1fr);row-gap:0}.wd,.flag{grid-column:2}.tx{font-size:16px}}
"""

JS = r"""
const caps=[...document.querySelectorAll('.cap')];
function show(li){document.querySelectorAll('.ln.on').forEach(x=>x.classList.remove('on'));
 li.classList.add('on');caps.forEach(c=>c.textContent=li.dataset.t);}
document.querySelectorAll('.ln').forEach(li=>li.addEventListener('click',()=>show(li)));
const first=document.querySelector('.ln');if(first)show(first);
document.addEventListener('keydown',e=>{const on=document.querySelector('.ln.on');if(!on)return;
 const all=[...document.querySelectorAll('.ln')];const i=all.indexOf(on);
 const n=e.key==='ArrowDown'?all[i+1]:e.key==='ArrowUp'?all[i-1]:null;
 if(n){e.preventDefault();show(n);n.scrollIntoView({block:'nearest'});}});
"""


def main():
    a = sys.argv
    cfgp = a[a.index('--config')+1]
    cfg = json.load(open(cfgp, encoding='utf-8'))
    ep = cfg.get('episode', '')
    out = a[a.index('-o')+1] if '-o' in a else os.path.join(cfg['root'], '%s期-定稿字幕.html' % ep)
    secs, toc, tot_l, tot_b = [], [], 0, 0
    for row, v in sorted(cfg['rows'].items(), key=lambda x: int(x[0])):
        d = os.path.join(cfg['root'], '%02d-%s' % (int(row), re.sub(r'[/:]', '_', v['title'])))
        if not os.path.exists(os.path.join(d, '字幕.json')):
            continue
        s, n, b, dur = clip_section(row, v['title'], d)
        secs.append(s); tot_l += n; tot_b += b
        toc.append('<a href="#r%s">%02d %s</a>' % (row, int(row), esc(v['title'])))
    page = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s期定稿字幕</title><style>%s</style></head><body><div class="wrap"><main>'
            '<h1>%s期 · 定稿字幕</h1><p class="sub">%d 条 · %d 行字幕 · 可疑断点 %d 处 · 横竖版字幕行相同</p>'
            '<div class="legend"><span><i class="sw" style="background:var(--mark)"></i>文本定稿改过的字</span>'
            '<span><i class="sw" style="background:var(--ppt)"></i>PPT 插入段</span>'
            '<span>句首前留空隙</span><span>行尾灰条 = 行宽（满格 11.5 字宽）</span>'
            '<span>点一行看画面效果，↑↓ 切换</span></div>'
            '<nav class="toc">%s</nav>%s</main>'
            '<aside><h3>横版 1920×1080</h3><div class="fr h"><div class="cap"></div></div>'
            '<h3>竖版 1080×1920</h3><div class="fr v"><div class="band"></div><div class="cap"></div></div>'
            '<p class="hint">字号、位置按渲染规格换算；灰块为竖版视频画面区。</p></aside>'
            '</div><script>%s</script></body></html>'
            % (ep, CSS, ep, len(secs), tot_l, tot_b, ''.join(toc), ''.join(secs), JS))
    open(out, 'w', encoding='utf-8').write(page)
    print('定稿字幕 -> %s　（%d 条，%d 行，可疑 %d）' % (out, len(secs), tot_l, tot_b))


if __name__ == '__main__':
    main()
