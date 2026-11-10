#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""断行规则回归：拿人工成片当基准，量一次改动是帮忙还是帮倒忙。

用法：
    python3 rulecheck.py              # 跑全部基准，输出总分
    python3 rulecheck.py --save 基线   # 存一份基线
    python3 rulecheck.py --vs 基线     # 与基线逐条比较

**为什么必须有这个**（471 期的教训）：
我从几个例子里凑出「量词短语不能被切开」这条规则，套用了 14 处，
自认为是在做人工复核——实测三条里两条**被改坏了**：
    02  81.5% → 79.3%      07  74.4% → 73.4%
再去看人工成片，人家恰恰就是那样断的：
    你买一个 ⏐ 产业园区的这个REITS
    标准的中产家庭 ⏐ 它的一个
凭直觉造规则、不验证就上线，是这个项目里最贵的一类错误。
基准数据当时就在手边，跑一次只要几十秒。

⚠️ 这个分数是「与某一位剪辑同事的断法有多接近」，**不等于「好」**。
人工自己也不一致（同样是「一个」，有时留行尾有时不留）。
所以：分数明显下降＝这条改动有害；分数微升微降＝在噪声里，别据此下结论。
"""
import json, os, sys, glob, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, '人工基准')
ROOT = os.path.abspath(os.path.join(HERE, '..', '02-过程'))
sys.path.insert(0, HERE)
import make_srt as M


def norm(t):
    import re
    return re.sub(r'[^一-鿿A-Za-z0-9%]', '', t)


def load_human(hn):
    """读 subocr 产出的人工字幕，合并连续同句"""
    import difflib
    segs = []
    for ln in open(os.path.join(BASE, hn + '.tsv'), encoding='utf-8'):
        p = ln.rstrip('\n').split('\t')
        if len(p) != 3:
            continue
        t = norm(p[2].replace('版权所有银行螺丝钉侵权必究', ''))   # 474 期起人工成片带水印
        if not t or sum(1 for c in t if '一' <= c <= '鿿') < 2 or len(t) > 16:
            continue
        if segs:
            a, b = segs[-1], t
            if a == b or (abs(len(a)-len(b)) <= 2
                          and difflib.SequenceMatcher(a=a, b=b).ratio() >= 0.8):
                if len(t) > len(segs[-1]):
                    segs[-1] = t
                continue
        segs.append(t)
    return segs


def positions(lines, script):
    """每行结束处在脚本中的字符下标"""
    import difflib
    sn = [i for i, c in enumerate(script) if norm(c)]
    plain = ''.join(script[i] for i in sn)
    joined = ''.join(norm(x) for x in lines)
    sm = difflib.SequenceMatcher(a=plain, b=joined, autojunk=False)
    m = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2-i1):
                m[j1+k] = i1+k
    out, acc = [], 0
    for ln in lines:
        acc += len(norm(ln))
        for cand in (acc-1, acc-2, acc):
            if cand in m:
                out.append(sn[m[cand]])
                break
    return out


def run():
    idx = json.load(open(os.path.join(BASE, '对照表.json'), encoding='utf-8'))
    rows = []
    for ep, m in idx.items():
        for num, hn in sorted(m.items()):
            ds = glob.glob(os.path.join(ROOT, ep, num + '-*'))
            if not ds or not os.path.exists(os.path.join(ds[0], 'asr.json')):
                continue
            d = ds[0]
            fx = os.path.join(d, '文本修正.txt')
            M.MANUAL_FIXES = M.load_fixes(fx) if os.path.exists(fx) else []
            script, mask, ppt = M.load_rich(os.path.join(d, '脚本单元格.json'))
            asr = json.load(open(os.path.join(d, 'asr.json')))
            ac = M.expand_words(asr['words'])
            script, mask, ppt = M.restore_dropped(script, mask, ppt, ac, verbose=False)
            times, _, _ = M.align(script, ac)
            M.ASR_WORDS = asr['words']
            cur = [l[0] for l in M.split_lines(script, times, 11.5, asr.get('gaps'))]
            man = load_human(hn)
            mp = set(positions(man, script))
            cp = positions(cur, script)
            hit = sum(1 for x in cp if any((x+k) in mp for k in (-1, 0, 1)))
            rows.append({'ep': ep, 'num': num, 'hn': hn,
                         'agree': 100*hit/max(1, len(cp)),
                         'cover': 100*hit/max(1, len(mp)),
                         'lines': len(cur),
                         'avg': st.mean([len(norm(x)) for x in cur])})
    return rows


def main():
    a = sys.argv
    rows = run()
    tot_a = st.mean([r['agree'] for r in rows])
    tot_c = st.mean([r['cover'] for r in rows])
    if '--save' in a:
        json.dump(rows, open(a[a.index('--save')+1] + '.json', 'w'), ensure_ascii=False)
        print('已存基线：%d 条，重合 %.1f%%，覆盖 %.1f%%' % (len(rows), tot_a, tot_c))
        return 0
    if '--vs' in a:
        old = {(r['ep'], r['num']): r for r in json.load(open(a[a.index('--vs')+1] + '.json', encoding='utf-8'))}
        print('%-16s %-4s %16s %16s' % ('期次', '条', '重合  旧→新', '覆盖  旧→新'))
        worse = better = 0
        for r in rows:
            o = old.get((r['ep'], r['num']))
            if not o:
                continue
            da, dc = r['agree']-o['agree'], r['cover']-o['cover']
            if da < -0.5: worse += 1
            elif da > 0.5: better += 1
            flag = '↓' if da < -0.5 else ('↑' if da > 0.5 else ' ')
            print('%-16s %-4s %6.1f→%6.1f %s %6.1f→%6.1f' % (
                r['ep'][:14], r['num'], o['agree'], r['agree'], flag, o['cover'], r['cover']))
        ob = st.mean([old[(r['ep'], r['num'])]['agree'] for r in rows if (r['ep'], r['num']) in old])
        print('\n合计重合 %.1f%% → %.1f%%（%+.1f）　变好 %d 条，变差 %d 条'
              % (ob, tot_a, tot_a-ob, better, worse))
        return 1 if worse > better else 0
    print('%-16s %-4s %8s %8s %6s %6s' % ('期次', '条', '断点重合', '覆盖人工', '行数', '均长'))
    for r in rows:
        print('%-16s %-4s %7.1f%% %7.1f%% %6d %6.1f' % (
            r['ep'][:14], r['num'], r['agree'], r['cover'], r['lines'], r['avg']))
    print('\n%d 条基准　平均重合 %.1f%%　平均覆盖 %.1f%%' % (len(rows), tot_a, tot_c))
    return 0


if __name__ == '__main__':
    sys.exit(main())
