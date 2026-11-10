#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""程序字幕 vs 人工成片字幕：逐条并排对比。

用法：python3 human_compare.py --config <期次配置.json> --map 2=1914,3=1915,... [-o 输出.html]

人工那边只有成片，字幕先用 subocr 读出来放进 人工基准/<编号>.tsv。
做法：两边各自连成一串（去标点），逐字对齐；
    · 两边在同一位置断行 = 一致断点，以它为界分组，组内左右并排
    · 对不上的字标出来——可能是人工改了字，也可能是 OCR 读错（OCR 错多是单字形近）
断点重合 = 一致断点 / 程序断点；覆盖 = 一致断点 / 人工断点（与 rulecheck 口径相同）。
"""
import json, os, re, sys, html, difflib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rulecheck as R


def norm(t):
    return re.sub(r'[^一-鿿A-Za-z0-9%+]', '', t)


def esc(s):
    return html.escape(s, quote=True)


WATERMARK = ('版权所有银行螺丝钉侵权必究',)


def clean_human(segs, ref_text):
    """去掉 OCR 读进来的非字幕文字：水印、画面上的人名标签、乱码。
    474 期：水印「版权所有银行螺丝钉侵权必究」每隔几秒出现；「戴维斯」「巴菲特」是画面人名标签。
    ref_text = 程序字幕全文 + 本条录音识别全文 + 脚本原文，用来判断短片段是不是真说过的话。"""
    import jieba.posseg as pseg
    out, dropped = [], []
    cnt = {}
    for t in segs:
        cnt[t] = cnt.get(t, 0) + 1
    for t in segs:
        for w in WATERMARK:
            t = t.replace(w, '')
        if not t:
            continue
        # 反复单独出现的人名 = 画面标签（「然后呢」「这样的话」也会反复出现，但那是字幕）
        if len(t) <= 4 and cnt.get(t, 0) >= 3 and all(p.flag.startswith('nr') for p in pseg.cut(t)):
            dropped.append(t); continue
        if len(t) <= 2 and t not in ref_text:            # 程序字幕和录音里都没有的两字 = 乱码
            dropped.append(t); continue
        out.append(t)
    return out, dropped


def compare(prog_lines, human_lines):
    P = [norm(x) for x in prog_lines]
    H = [norm(x) for x in human_lines]
    P = [x for x in P if x]
    H = [x for x in H if x]
    ps, hs = ''.join(P), ''.join(H)
    bp, acc = [], 0
    for x in P:
        acc += len(x); bp.append(acc)
    bh, acc = [], 0
    for x in H:
        acc += len(x); bh.append(acc)
    sm = difflib.SequenceMatcher(a=ps, b=hs, autojunk=False)
    h2p, pm, hm = {}, [False]*len(ps), [False]*len(hs)
    for b in sm.get_matching_blocks():
        for k in range(b.size):
            h2p[b.b+k] = b.a+k
            pm[b.a+k] = hm[b.b+k] = True
    # 人工断点映射到程序坐标：断在第 k 个字之后 → 该字在程序串里的位置 + 1
    common = []            # (程序坐标, 人工坐标)
    sp = set(bp)
    for e in bh:
        if e - 1 in h2p and h2p[e-1] + 1 in sp:
            common.append((h2p[e-1] + 1, e))
    common = sorted(set(common))
    # 分组
    groups, lp, lh = [], 0, 0
    cuts = common + ([(len(ps), len(hs))] if not common or common[-1] != (len(ps), len(hs)) else [])
    pi = hi = 0
    for cp, ch in cuts:
        gp, gh = [], []
        while pi < len(P) and bp[pi] <= cp:
            gp.append((bp[pi]-len(P[pi]), P[pi])); pi += 1
        while hi < len(H) and bh[hi] <= ch:
            gh.append((bh[hi]-len(H[hi]), H[hi])); hi += 1
        if gp or gh:
            groups.append((gp, gh))
    diffs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != 'equal':
            diffs.append((tag, ps[i1:i2], hs[j1:j2], ps[max(0, i1-8):i1], ps[i2:i2+6]))
    stats = dict(np=len(bp), nh=len(bh), common=len([c for c in common if c[0] < len(ps)]),
                 chars=len(ps))
    return groups, pm, hm, diffs, stats


def kind_of(a, b):
    """定稿改动的类型（a=定稿前，b=定稿后）"""
    if set(a + b) <= set('他它们'):
        return '他/它'
    if b and not a and set(b) <= set('%、+'):
        return '补符号'
    if len(a) > 30:
        return '脚本重复段'
    if a and b:
        return '同音字'
    if a in ('这个', '那个', '那', '然后', '就是', '的话') or b in ('这个', '那个', '那', '然后'):
        return '口头语'
    return '删口吃'


def verdicts(pre_lines, post_lines, hs):
    """定稿前后不一样的每一处，看人工在同一位置写的是哪一版"""
    pre, post = norm(''.join(pre_lines)), norm(''.join(post_lines))
    sm = difflib.SequenceMatcher(a=post, b=hs, autojunk=False)
    p2h = {}
    for b in sm.get_matching_blocks():
        for k in range(b.size):
            p2h[b.a+k] = b.b+k
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=pre, b=post, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        a, b = pre[i1:i2], post[j1:j2]
        L = [p2h[k] for k in range(max(0, j1-6), j1) if k in p2h]
        Rr = [p2h[k] for k in range(j2, min(len(post), j2+6)) if k in p2h]
        h = hs[L[-1]+1:Rr[0]] if L and Rr else None
        v = 'same' if h == b else ('pre' if h == a else 'none')
        out.append(dict(pre=a, post=b, human=h, v=v, kind=kind_of(a, b),
                        left=post[max(0, j1-10):j1], right=post[j2:j2+8]))
    return out


def paint(start, text, mask, cls):
    out, cur = [], False
    for k, ch in enumerate(text):
        m = not mask[start+k]
        if m != cur:
            out.append('<mark class="%s">' % cls if m else '</mark>')
            cur = m
        out.append(esc(ch))
    if cur:
        out.append('</mark>')
    return ''.join(out)


CSS = r"""
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--mute:#76746f;--line:#e6e3dc;--same:#fbfaf7;
--diff:#fff7e6;--pm:#ffd9c7;--hm:#cfe8ff;--acc:#2f6f5e;--p:#b4532a;--h:#2563a8}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#171716;--card:#201f1e;--ink:#ecebe6;--mute:#9d9a93;
--line:#34322f;--same:#1c1b1a;--diff:#2a2415;--pm:#6b3522;--hm:#1f3d5c;--acc:#7cc4ae;--p:#f0a07a;--h:#8cbcf0}}
:root[data-theme="dark"]{--bg:#171716;--card:#201f1e;--ink:#ecebe6;--mute:#9d9a93;--line:#34322f;--same:#1c1b1a;
--diff:#2a2415;--pm:#6b3522;--hm:#1f3d5c;--acc:#7cc4ae;--p:#f0a07a;--h:#8cbcf0}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"PingFang SC","Source Han Sans CN",sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 80px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mute);font-size:13px;margin:0 0 16px}
table.sum{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-bottom:12px;font-size:13px}
.sum th,.sum td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left}
.sum td.n{text-align:right;font-variant-numeric:tabular-nums}.sum a{color:var(--ink)}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--mute);margin:10px 0 20px}
.legend i{display:inline-block;width:14px;height:14px;border-radius:3px;vertical-align:-2px;margin-right:5px}
.tools{display:flex;gap:8px;margin-bottom:16px;font-size:13px}
.tools button{font:inherit;padding:5px 12px;border:1px solid var(--line);border-radius:999px;background:var(--card);color:var(--ink);cursor:pointer}
.tools button.on{border-color:var(--acc);color:var(--acc)}
.clip{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:20px;overflow:hidden}
.clip header{padding:12px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--card);z-index:1}
.clip h2{font-size:16px;margin:0}.clip .meta{font-size:12px;color:var(--mute);margin:2px 0 0}
.hd,.g{display:grid;grid-template-columns:1fr 1fr}
.hd div{padding:6px 16px;font-size:12px;font-weight:600}.hd div:first-child{color:var(--p)}.hd div:last-child{color:var(--h)}
.g{border-top:1px solid var(--line)}.g>div{padding:5px 16px}.g>div+div{border-left:1px solid var(--line)}
.g.same{background:var(--same);color:var(--mute)}.g.diff{background:var(--diff)}
.g p{margin:0;font-size:16px;letter-spacing:.02em}
mark.p{background:var(--pm);color:inherit;border-radius:2px}mark.h{background:var(--hm);color:inherit;border-radius:2px}
body.only .g.same{display:none}
.vd{padding-bottom:12px}.sum.in{border:0;border-radius:0;margin:0}.vd details{padding:8px 16px 0}
.vd summary{cursor:pointer;font-size:13px;color:var(--acc)}.det td{font-size:13px}
del{background:var(--pm);text-decoration:line-through}ins{background:var(--hm);text-decoration:none}
b.ok{color:var(--acc)}b.no{color:var(--p)}b.mid{color:var(--mute)}
@media (max-width:560px){.g p{font-size:14px}.g>div,.hd div{padding:4px 8px}}
"""


def main():
    a = sys.argv
    cfg = json.load(open(a[a.index('--config')+1], encoding='utf-8'))
    pairs = [x.split('=') for x in a[a.index('--map')+1].split(',')]
    ep = cfg.get('episode', '')
    out = a[a.index('-o')+1] if '-o' in a else os.path.join(cfg['root'], '%s期-定稿字幕vs人工字幕.html' % ep)
    secs, rows, tot = [], [], dict(np=0, nh=0, common=0, diff_groups=0, groups=0)
    all_diffs = {}
    all_v = []
    for row, hn in pairs:
        v = cfg['rows'][row]
        d = os.path.join(cfg['root'], '%02d-%s' % (int(row), re.sub(r'[/:]', '_', v['title'])))
        prog = [l['text'] for l in json.load(open(os.path.join(d, '字幕.json')))['lines']]
        asr = ''.join(w['s'] for w in json.load(open(os.path.join(d, 'asr.json')))['words'])
        c = json.load(open(os.path.join(d, '脚本单元格.json')))['data']['ranges'][0]['cells'][0][0]
        raw = c.get('value') or ''.join(x['text'] for x in (c.get('rich_text') or []))
        human, dropped = clean_human(R.load_human(hn), norm(''.join(prog)) + asr + norm(raw))
        if dropped:
            print('  %s 滤掉非字幕 %d 条：%s' % (hn, len(dropped), ' '.join(sorted(set(dropped)))))
        groups, pm, hm, diffs, st = compare(prog, human)
        pre_p = os.path.join(d, '字幕.未定稿.json')
        if os.path.exists(pre_p):
            pre = [l['text'] for l in json.load(open(pre_p))['lines']]
            for x in verdicts(pre, prog, ''.join(norm(t) for t in human)):
                x['row'] = row
                all_v.append(x)
        all_diffs[row] = diffs
        html_g, ndg = [], 0
        for gp, gh in groups:
            same = len(gp) == len(gh) == 1 and gp[0][1] == gh[0][1]
            if not same:
                ndg += 1
            L = ''.join('<p>%s</p>' % paint(s, t, pm, 'p') for s, t in gp)
            Rr = ''.join('<p>%s</p>' % paint(s, t, hm, 'h') for s, t in gh)
            html_g.append('<div class="g %s"><div>%s</div><div>%s</div></div>' % ('same' if same else 'diff', L, Rr))
        prec = st['common'] / st['np'] * 100 if st['np'] else 0
        cov = st['common'] / st['nh'] * 100 if st['nh'] else 0
        for k in ('np', 'nh', 'common'):
            tot[k] += st[k]
        tot['diff_groups'] += ndg; tot['groups'] += len(groups)
        rows.append('<tr><td><a href="#r%s">%02d %s</a></td><td>%s</td><td class="n">%d / %d</td>'
                    '<td class="n">%.1f%%</td><td class="n">%.1f%%</td><td class="n">%d</td></tr>'
                    % (row, int(row), esc(v['title']), hn, st['np'], st['nh'], prec, cov, ndg))
        secs.append('<section class="clip" id="r%s"><header><h2>%02d %s</h2>'
                    '<p class="meta">人工 %s · 定稿 %d 行 / 人工 %d 行 · 断点重合 %.1f%% · 覆盖 %.1f%% · 不一致 %d 组</p></header>'
                    '<div class="hd"><div>定稿字幕（将用于渲染）</div><div>人工字幕（成片 OCR）</div></div>%s</section>'
                    % (row, int(row), esc(v['title']), hn, st['np'], st['nh'], prec, cov, ndg, ''.join(html_g)))
    P = tot['common'] / tot['np'] * 100 if tot['np'] else 0
    C = tot['common'] / tot['nh'] * 100 if tot['nh'] else 0
    vhtml = ''
    if all_v:
        n = {k: sum(1 for x in all_v if x['v'] == k) for k in ('same', 'pre', 'none')}
        kinds = []
        for k in dict.fromkeys(x['kind'] for x in all_v):
            xs = [x for x in all_v if x['kind'] == k]
            kinds.append('<tr><td>%s</td><td class="n">%d</td><td class="n">%d</td><td class="n">%d</td><td class="n">%d</td></tr>'
                         % (k, len(xs), sum(1 for x in xs if x['v'] == 'same'),
                            sum(1 for x in xs if x['v'] == 'pre'), sum(1 for x in xs if x['v'] == 'none')))
        lab = {'same': '<b class="ok">人工同定稿</b>', 'pre': '<b class="no">人工同定稿前</b>', 'none': '<b class="mid">都不同</b>'}
        items = ''.join(
            '<tr class="%s"><td>%02d</td><td>%s</td><td>…%s<del>%s</del><ins>%s</ins>%s…</td><td>%s</td><td>%s</td></tr>'
            % (x['v'], int(x['row']), x['kind'], esc(x['left']), esc(x['pre']) or '', esc(x['post']) or '',
               esc(x['right']), esc(x['human']) if x['human'] is not None else '?', lab[x['v']])
            for x in all_v)
        vhtml = ('<section class="clip vd"><header><h2>文本定稿效果</h2><p class="meta">定稿改动 %d 处：'
                 '人工同定稿 %d · 人工同定稿前 %d · 都不同 %d</p></header>'
                 '<table class="sum in"><tr><th>类型</th><th>处数</th><th>人工同定稿</th><th>同定稿前</th><th>都不同</th></tr>%s</table>'
                 '<details><summary>逐处明细</summary><table class="sum in det"><tr><th>条</th><th>类型</th>'
                 '<th>改动（删/增）</th><th>人工写的</th><th>结论</th></tr>%s</table></details></section>'
                 % (len(all_v), n['same'], n['pre'], n['none'], ''.join(kinds), items))
    page = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s期定稿vs人工字幕</title><style>%s</style></head><body><main>'
            '<h1>%s期 · 定稿字幕 vs 人工字幕</h1>'
            '<p class="sub">%d 条 · 断点重合 %.1f%% · 覆盖 %.1f%% · 不一致 %d 组 / 共 %d 组</p>'
            '<table class="sum"><tr><th>条目</th><th>人工</th><th>行数 定稿/人工</th><th>重合</th><th>覆盖</th><th>不一致组</th></tr>%s</table>'
            '<div class="legend"><span><i style="background:var(--pm)"></i>定稿有、人工没有的字</span>'
            '<span><i style="background:var(--hm)"></i>人工有、定稿没有的字（含 OCR 误读）</span>'
            '<span>灰行 = 两边断得一样</span></div>'
            '%s<div class="tools"><button id="t">只看不一致</button></div>%s</main>'
            '<script>const b=document.getElementById("t");b.onclick=()=>{document.body.classList.toggle("only");b.classList.toggle("on")};</script>'
            '</body></html>' % (ep, CSS, ep, len(pairs), P, C, tot['diff_groups'], tot['groups'], ''.join(rows), vhtml, ''.join(secs)))
    open(out, 'w', encoding='utf-8').write(page)
    json.dump({r: [list(x) for x in d] for r, d in all_diffs.items()},
              open(out.replace('.html', '.文字差异.json'), 'w'), ensure_ascii=False, indent=0)
    print('对比 -> %s　重合 %.1f%%　覆盖 %.1f%%' % (out, P, C))


if __name__ == '__main__':
    main()
