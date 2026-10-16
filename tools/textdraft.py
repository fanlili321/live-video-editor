#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文本定稿：渲染之前，先把每条片子的**最终字幕文字**定下来。

用法：
    python3 textdraft.py --config <期次配置.json>            # 整期出定稿文本
    python3 textdraft.py --config <期次配置.json> --rows 2,3  # 只出某几条

每条产出 `定稿文本.md`：纯文字按句成段，没有时间码、没有断行，一条一分钟能读完。
需要人判断的地方直接标在正文里，末尾再列一张清单。

**为什么要有这一步**（2026-09）：
历史上 31 条人工修正**全部是文字问题**——他/它 15 条、口吃 7 条、语序 3 条、缺百分号 3 条——
而它们都是看成片时才发现，改一个字就得重渲一轮。
工具早就会报「这里的『他』可能该用『它』」，但报在一堆输出里没人看，470 期和 9.2 期都栽在这儿。
单独一步、单独一份文件，读完改完再往下走。

**改动边界**（2026-09-16 与用户定）：
    可以改：同音换字（他/它、的/得）、补符号（%、顿号）、删口吃和赘词
            ——这三类都不改变观众听到的内容
    不能改：改动实际说出的内容（调语序、补全没说完的词、加主播没说的字）
            ——只标出来，由人逐条决定
改动一律写进 `文本修正.txt`，不直接改字幕。
"""
import json, os, re, sys, difflib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_srt as M

# 需要人读才能判断的几类，按历史修正的频次排
PRONOUN = re.compile(r'(?<!其)他们?')
BARE_NUM = re.compile(r'(?<![\d%.])(\d+(?:\.\d+)?)(?![\d%.年月日号个只家倍亿万元块点岁期天周层])')
INSTITUTION = ('公司', '机构', '基金', '银行', '保险', '券商', '交易所', '美联储', '央行',
               '政府', '国家', '美国', '指数', '企业', '行业', '板块', '市场')


def final_text(d, asr_name):
    """跑一遍清洗 + 补词 + 人工修正，返回 (定稿文本, 程序改动清单, 补词清单)"""
    fx = os.path.join(d, '文本修正.txt')
    M.NO_RESTORE.clear()
    M.MANUAL_FIXES = M.load_fixes(fx) if os.path.exists(fx) else []
    cell = json.load(open(os.path.join(d, '脚本单元格.json')))
    c = cell['data']['ranges'][0]['cells'][0][0]
    raw = c.get('value') or ''.join(x['text'] for x in (c.get('rich_text') or []))

    trace = []
    M.clean_script(raw, trace=trace)          # 只为拿到「程序改了什么」
    script, mask, ppt = M.load_rich(os.path.join(d, '脚本单元格.json'))

    restored = []
    asr_p = os.path.join(d, asr_name)
    if os.path.exists(asr_p):
        asr = json.load(open(asr_p))
        before = script
        script, mask, ppt = M.restore_dropped(script, mask, ppt,
                                              M.expand_words(asr['words']), verbose=False)
        sm = difflib.SequenceMatcher(a=before, b=script, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == 'insert':
                restored.append((before[max(0, i1-8):i1], script[j1:j2], before[i1:i1+8]))
    text = ''.join(ch for ch, m in zip(script, mask) if not m)
    struck = sum(1 for m in mask if m)
    return raw, text, trace, restored, struck


def mark(text):
    """在正文里标出要人看的地方，返回 (带标记的正文, 清单)"""
    items = []
    spans = []
    for m in PRONOUN.finditer(text):
        ctx = text[max(0, m.start()-24):m.start()]
        ctx = re.sub(r'企业家|国家队|基金经理|保险代理人', '', ctx)
        hint = next((k for k in INSTITUTION if k in ctx), '')
        if not hint:
            continue          # 前文没有机构/物，默认指人，不打扰
        why = '前文有「%s」，若指它应改「它」' % hint
        spans.append((m.start(), m.end(), '他', why))
    for m in BARE_NUM.finditer(text):
        after = text[m.end():m.end()+6]
        before = text[max(0, m.start()-8):m.start()]
        # 年份、编号、倍数这类不管；只看像比例、收益、利率的
        if not re.search(r'收益|利率|回报|涨|跌|回撤|股息|分红|占|比例|配|仓|点', before + after):
            continue
        spans.append((m.start(), m.end(), '数', '裸数字，是不是缺「%」'))
    spans.sort()
    out, pos = [], 0
    for i, (a, b, kind, why) in enumerate(spans, 1):
        out.append(text[pos:a])
        out.append('**%s**⁽%d⁾' % (text[a:b], i))
        pos = b
        items.append((i, kind, text[max(0, a-14):a] + '【' + text[a:b] + '】' + text[b:b+14], why))
    out.append(text[pos:])
    return ''.join(out), items


def paragraphs(t):
    """按句号问号叹号分段，每 2~3 句一段，读起来不累"""
    sents = [s for s in re.split(r'(?<=[。？！])', t) if s.strip()]
    paras, cur = [], ''
    for s in sents:
        cur += s
        if len(cur) >= 80:
            paras.append(cur); cur = ''
    if cur:
        paras.append(cur)
    return paras


def render(row, title, raw, text, trace, restored, struck, items_text):
    body, items = items_text
    lines = ['# %02d  %s' % (int(row), title), '',
             '> 脚本 %d 字 → 定稿 %d 字　·　删除线已去 %d 字　·　补回话语标记 %d 处　·　待看 %d 处'
             % (len(raw), len(text), struck, len(restored), len(items)), '']
    lines += ['## 正文', '']
    lines += [p + '\n' for p in paragraphs(body)]
    if items:
        lines += ['## 待看', '', '| # | 类型 | 原文 | 为什么要看 |', '|-|-|-|-|']
        for i, kind, ctx, why in items:
            lines.append('| %d | %s | %s | %s |' % (i, kind, ctx.replace('|', '｜'), why))
        lines.append('')
    if restored:
        lines += ['## 程序补回的词（转写时被顺滑删掉的）', '',
                  '核对：补的位置对不对。补进词组中间的要删。', '']
        for a, w, b in restored:
            lines.append('- …%s**【%s】**%s…' % (a, w, b))
        lines.append('')
    trace = [t for t in trace if (t['before'] + t['after']).strip()
             and t['rule'] not in ('数字空格', '去编辑批注')]
    if trace:
        lines += ['## 程序做过的清洗', '']
        for t in trace[:40]:
            lines.append('- %s（%s）：%s → `%s`' % (t['rule'], t['kind'], t['context'], t['after'] or '∅'))
        if len(trace) > 40:
            lines.append('- …另 %d 处' % (len(trace) - 40))
        lines.append('')
    return '\n'.join(lines)


def main():
    a = sys.argv
    cfg = json.load(open(a[a.index('--config')+1], encoding='utf-8'))
    want = None
    if '--rows' in a:
        want = {x.strip() for x in a[a.index('--rows')+1].split(',')}
    asr_name = 'asr-m4a.json' if '--m4a' in a else 'asr.json'
    tot = 0
    for row, v in sorted(cfg['rows'].items(), key=lambda x: int(x[0])):
        if want and row not in want:
            continue
        d = os.path.join(cfg['root'], '%02d-%s' % (int(row), v['title']))
        if not os.path.isdir(d):
            continue
        raw, text, trace, restored, struck = final_text(d, asr_name)
        md = render(row, v['title'], raw, text, trace, restored, struck, mark(text))
        open(os.path.join(d, '定稿文本.md'), 'w', encoding='utf-8').write(md)
        n = md.count('⁽')
        tot += n
        print('  行%-3s %-26s %5d 字  补词 %2d  待看 %2d' % (row, v['title'][:24], len(text), len(restored), n))
    print('合计待看 %d 处' % tot)


if __name__ == '__main__':
    main()
