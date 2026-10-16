#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""断行预览：渲染**之前**把字幕排成可通读的文本，并标出可疑断点。

用法：python3 preview.py <字幕.json> [-o 断行预览.txt]

为什么要有这一步（470 期的教训）：
断行问题一直是「人看了成片指出来 → 我加一条罚分 → 重渲」这个循环，
一轮要 40 分钟渲染。而字幕在渲染前就已经定稿了（字幕.json），
缺的不是文件，是**没人在渲染前看它**。
把它排成纯文本，复核成本从「等一轮渲染」降到「扫一眼」，
且发现问题时还没浪费渲染。

标记：
  ⚠️  断点劈开了一个词/整体片段（qc_subs.crossing_word，不依赖枚举词表）
  ▸   行宽接近上限，容易挤
"""
import json, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qc_subs as Q


def render(subs_path):
    lines = json.load(open(subs_path))['lines']
    out, bad = [], 0
    for i, l in enumerate(lines):
        t = l['text']
        w = Q.width(t)
        nxt = lines[i+1] if i+1 < len(lines) else None
        flag = ''
        if nxt and not nxt.get('clause_start'):
            c = Q.crossing_word(t, nxt['text'])
            if c:
                flag = '  ⚠️ 与下一行劈开了「%s」' % c
                bad += 1
        if not flag and w > 10.8:
            flag = '  ▸ %.1fem 接近上限' % w
        out.append('%4d  %7.2f  %-24s%s' % (i+1, l['start'], t, flag))
    return out, bad, len(lines)


def main():
    a = sys.argv
    subs = a[1]
    dst = a[a.index('-o')+1] if '-o' in a else os.path.join(
        os.path.dirname(subs), '断行预览.txt')
    body, bad, n = render(subs)
    head = ['# 断行预览　%s' % os.path.basename(os.path.dirname(subs)),
            '# 共 %d 行，可疑断点 %d 处' % (n, bad),
            '# 行号    时刻     字幕', '']
    open(dst, 'w', encoding='utf-8').write('\n'.join(head + body) + '\n')
    print('断行预览 -> %s　（%d 行，可疑 %d 处）' % (dst, n, bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
