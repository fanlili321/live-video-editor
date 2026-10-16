#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""漏词检测：拿本机 ASR 当「逐字证人」，反查脚本被顺滑掉的口语词。

**为什么需要**：字幕文字来自脚本，而脚本是照飞书妙记的转写稿整理的。
妙记会做**口语顺滑**——把它认为是口水词的成分删掉。这是它作为会议纪要工具的
优点，但对字幕是缺陷：观众听见「这个基金经理擅长成长风」，字幕上却只有
「基金经理擅长成长风」，少了两个字。467 期 08 就漏了 4 个「这个」，
一直到人看成片才发现。

**为什么不用换 ASR 引擎**：本机 Apple Speech 虽然用词错得离谱
（「基金经理」听成「金经理」、「估值」听成「固执」），但它**逐字保留**，
一个口语词都不删。两者短板正好互补：

    妙记          用词准 ✅   逐字保留 ❌
    本机 ASR      用词错 ❌   逐字保留 ✅

而本机 ASR 每条切片本来就要跑（字幕对齐要用），**这份数据是白来的**。

**判据**：按词频比对，不按位置比对。ASR 的错字太多，逐位置对齐会淹没在噪声里；
但「这个/然后」这类常用词 Apple Speech 基本不会听错，计数是可靠的。

⚠️ **只报 [疑]，绝不自动补**。差值大不代表都该补：
「然后」「那个」很多时候确实是口水词，删掉更好；
「这个」在「这个基金经理」里却是指示词，删了就少了字。这个判断只能人来做。
"""
import sys, json, os, re, sys

# 常被口语顺滑掉的词。选它们的标准有两条：
#   1. 口播里高频
#   2. Apple Speech 基本不会听错（不是财经术语，没有同音混淆）
WATCH = ['这个', '那个', '然后', '其实', '的话', '一个', '咱们', '大家', '就是']
THRESHOLD = 2          # ASR 比脚本多这么多次才报，低于此视为 ASR 噪声

# ---- 分类规则：目标是把「需要人判断的次数」压到最少 ----
#
# 59 处疑似漏词逐条问人是不现实的。但它们不是 59 个独立判断——
# 同一个词在同一种语法位置上，答案是一样的。所以先按**语法功能**分类，再聚合。
#
# A 类 实义成分：删了会少字、观众听得出来 → 建议补，同类合成一个决策
# B 类 话语标记：口播里的连接词，删了更干净 → 默认忽略，只报个数
# C 类 拿不准：报出来逐条判
#
# 判据用 jieba 词性看**后接成分**：
#   这个 + 名词  →「这个基金经理」指示词，实义        → A
#   这个 + 动词/形容词 →「这个可能会更合适」填充      → B
DISCOURSE = {'然后', '其实', '的话', '就是'}     # 一律 B：纯连接/话语标记
PRONOUN   = {'咱们', '大家'}                    # 一律 A：人称代词，删了改语气
NOUNISH   = ('n', 'nr', 'ns', 'nt', 'nz', 'vn', 'an', 'r', 'm')   # 算「名词性」的词性

PUNCT_RE = re.compile(r'[，。！？、；：,.!?;:\s]')


def norm(t):
    return PUNCT_RE.sub('', t)


def asr_text(path):
    return norm(''.join(w['s'] for w in json.load(open(path))['words']))


def script_text(cell_path, fixes=None):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import make_srt as M
    if fixes and os.path.exists(fixes):
        M.MANUAL_FIXES = M.load_fixes(fixes)
    c = json.load(open(cell_path))['data']['ranges'][0]['cells'][0][0]
    raw = c.get('value') or ''.join(x['text'] for x in (c.get('rich_text') or []))
    return norm(M.clean_script(raw))


# bad_insert_point 已上移到 make_srt.py，与 restore_dropped 共用同一份实现。
# 书名号那条规则当初就是因为存在第二份副本，修了一处另一处照样拦人。
bad_insert_point = M.bad_insert_point


def classify(word, after):
    """看后接成分定类别。after 是脚本里该位置之后的一小段文字。"""
    if word in DISCOURSE:
        return 'B', '话语标记'
    if word in PRONOUN:
        return 'A', '人称代词'
    if not after:
        return 'C', '位于句末，无从判断'
    try:
        import jieba.posseg as pseg
        first = next(iter(pseg.cut(after)), None)
    except Exception:
        return 'C', '无法分词'
    if first is None:
        return 'C', '无从判断'
    if first.flag.startswith(NOUNISH):
        return 'A', '后接「%s」(%s)，是指示/量词成分' % (first.word, first.flag)
    if first.flag[0] in 'vad':
        return 'B', '后接「%s」(%s)，是填充' % (first.word, first.flag)
    return 'C', '后接「%s」(%s)' % (first.word, first.flag)


def locate_in_script(asr, script, word):
    """把 ASR 里每处出现映射回脚本位置。返回 [(脚本插入点, 脚本已有?)]"""
    import difflib
    out = []
    for m in re.finditer(re.escape(word), asr):
        probe = asr[max(0, m.start()-10):m.start()]
        if len(probe) < 4:
            continue
        sm = difflib.SequenceMatcher(a=script, b=probe, autojunk=False)
        b = sm.find_longest_match(0, len(script), 0, len(probe))
        if b.size < 3:
            continue
        pos = b.a + b.size
        out.append((pos, script[pos:pos+len(word)] == word))
    return out


def check(asr, script):
    """返回 [(词, ASR次数, 脚本次数, 差值)]，只含超阈值的"""
    out = []
    for w in WATCH:
        a, s = asr.count(w), script.count(w)
        if a - s >= THRESHOLD:
            out.append((w, a, s, a - s))
    return sorted(out, key=lambda x: -x[3])


def analyse(asr, script):
    """把疑似漏词分成 A/B/C 三类并聚合。返回 (A组, C条目, B计数)"""
    groups, todo, ignored = {}, [], 0
    for w, _, _, _ in check(asr, script):
        for pos, already in locate_in_script(asr, script, w):
            if already:
                continue                        # 脚本本来就有，不是漏词
            bad, reason = bad_insert_point(script, pos, w)
            if bad:
                ignored += 1
                continue
            after = script[pos:pos+8]
            cls, why = classify(w, after)
            ctx = '…%s⟨%s⟩%s…' % (script[max(0, pos-12):pos], w, script[pos:pos+12])
            if cls == 'A':
                key = (w, why.split('，')[-1])
                groups.setdefault(key, []).append(ctx)
            elif cls == 'C':
                todo.append((w, why, ctx))
            else:
                ignored += 1
    return groups, todo, ignored

def main():
    a = sys.argv
    if '--clip' in a:                       # 单条：dropcheck.py --clip <目录>
        dirs = [a[a.index('--clip')+1]]
    else:                                   # 整期：dropcheck.py --config <配置>
        cfg = json.load(open(a[a.index('--config')+1]))
        import glob
        # ⚠️ 曾经写的是 '0*-*'，只匹配 0 开头的目录——470 期编号跳到 16，
        #    10/11/13/14/15/16 共 6 条（46%）被**静默跳过**，输出里没有任何提示。
        #    467 期恰好全是 0x 开头，所以一直没暴露。
        dirs = sorted(glob.glob(os.path.join(cfg['root'], '[0-9][0-9]-*')))
        want = len(cfg.get('rows') or {})
        if want and len(dirs) != want:
            sys.exit('❌ dropcheck 覆盖不全：配置里 %d 条，只找到 %d 条目录\n   %s'
                     % (want, len(dirs), '\n   '.join(os.path.basename(d) for d in dirs)))
    total_groups = total_todo = total_ignored = 0
    for d in dirs:
        asr_p = os.path.join(d, 'asr.json')
        cell_p = os.path.join(d, '脚本单元格.json')
        if not (os.path.exists(asr_p) and os.path.exists(cell_p)):
            continue
        asr = asr_text(asr_p)
        sc = script_text(cell_p, os.path.join(d, '文本修正.txt'))
        groups, todo, ignored = analyse(asr, sc)
        name = os.path.basename(d)
        need = sum(len(v) for v in groups.values()) + len(todo)
        if not need:
            print('%-16s ✅ 无需判断（忽略话语标记 %d 处）' % (name[:16], ignored))
            continue
        total_groups += len(groups); total_todo += len(todo); total_ignored += ignored
        print('%-16s 建议补 %d 处/%d 类　待定 %d　已忽略话语标记 %d'
              % (name[:16], sum(len(v) for v in groups.values()), len(groups), len(todo), ignored))
        for (w, why), ctxs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            print('    [A] 「%s」×%d　%s' % (w, len(ctxs), why))
            for c in ctxs[:2]:
                print('          %s' % c)
            if len(ctxs) > 2:
                print('          …另 %d 处同类' % (len(ctxs)-2))
        for w, why, c in todo:
            print('    [C] 「%s」%s' % (w, why))
            print('          %s' % c)
    print()
    print('=' * 72)
    print('需要你判断：%d 类「建议补」+ %d 条「待定」　（已自动忽略话语标记与映射误差 %d 处）'
          % (total_groups, total_todo, total_ignored))
    print('同一类里的所有位置答案相同，**一次决定即可**；'
          '「待定」才需要逐条看。补法：写进该条的 文本修正.txt。')
    return 0        # 不阻断：漏词是判断题，拦下来会挡住正常工作


if __name__ == '__main__':
    sys.exit(main())
