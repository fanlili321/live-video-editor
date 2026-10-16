#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕质检：把两份人工复核文档里的意见变成可执行检查。

用法：python3 qc_subs.py 字幕.json [--em 133.5] [--canvas-w 1920]

来源：
  《成长和价值，为何总是跷跷板-视频问题》 JKzPdrG4uoisrZxfroEchkNNnPc
  《视频问题》                        SRPLwSfcWiwjZhkPcw7c1Vd2nso
以及人工剪辑流程文档「六、修正字幕」17 条。

分两级：
  [错] 必须修，规则明确
  [疑] 需要人工看一眼，规则含判断
"""
import json, re, sys

LEAD_BAD = set('的了呢吗吧着过地得')
TAIL_BAD = set('的地得')
NAME_PREFIX = ('中证', '沪深', '上证', '深证', '北证', '科创', '创业板',
               '恒生', '标普', '纳斯达克', '中债', '国证')
UNIT_AFTER_NUM = ('年代', '年', '月', '日', '点', '倍', '亿', '万', '元',
                  '个', '只', '家', '星', '块', '成')
BOOKS = ['红利指数基金投资指南', '个人养老金投资指南', '指数基金投资指南',
         '主动基金投资指南', '股市长线法宝', '定投十年财务自由',
         '投资的未来']   # 470 期 16 号提到，词典里原先没有
FILLER = '啊哎诶呃呀'
BANNED = ['公众号', '天天基金', '支付宝', '微信']
# 规则②：他/它 区分。规范里一直写着有这项检查，代码里却没有——
# 02 期「他们跟整个星级的相关性」就是这么漏到成片里的。
# 「其他」是词不能算；「他」后面跟这些虚词时，多半在指代前文的物。
PRONOUN_THING = re.compile(r'(?<!其)他(?:们)?(?=[跟的是也会有在从对与])')
# 规范 一·14 补充（470 期定）：**机构、国家、央行、公司一律用「它」**。
# 470 期 16 号一条片子里，指美联储/美国用了 6 次「他」，指美债用了 2 次「它」，
# 同一条片子内不一致。规范原文只写了「指人用他、指公司或指数用它」，没覆盖国家和央行。
INSTITUTION = ('美联储', '央行', '财政部', '证监会', '交易所', '美国', '中国',
               '政府', '公司', '机构', '基金', '协会', '银行', '国家')
# 烧进画面的字幕里不该出现这些字符。470 期 16 号 B 列带着 Markdown 转义符
# `我负担得起6\~7个点`，反斜杠一路烧进了成片，五道卡口一个都没查字符集。
BAD_CHARS = '\\`|<>{}*_[]'


PUNCT = '，。！？、；：,.!?;:“”"\'（）()《》'


def starts_word(t):
    """行首那个字是不是某个实词的头一个字。

    「过」在 LEAD_BAD 里，但「过去十多年…」完全正常——不查词就会误报。
    分词后首词长度 >1 即视为实词开头。
    """
    try:
        import jieba
    except ImportError:
        return False
    toks = list(jieba.cut(t))
    return bool(toks) and len(toks[0]) > 1


def width(t):
    """必须与 make_srt.py 的 char_width 完全一致，否则一边放行一边误报。
    数值为 CoreText 对 SourceHanSansCN-Medium 实测的 advance。"""
    w = 0.0
    for c in t:
        if c.isdigit():
            w += 0.57
        elif c.isascii() and c.isalpha():
            w += 0.62
        elif c in '%％':
            w += 0.94
        else:
            w += 1.0
    return w


def unmarked_books(t):
    """找出没加书名号的书名。**precheck 也调这个，别再写第二份实现。**

    ⚠️ 长书名优先，且要跳过「已被更长书名包住」的子串——
    《红利指数基金投资指南》里含有「指数基金投资指南」，
    按短名匹配会误判成「这个书名没加书名号」。
    470 期栽过一次：qc_subs 修好了，precheck 里那份副本没修，于是照样拦住开工。
    """
    out = []
    for b in sorted(BOOKS, key=len, reverse=True):
        j = t.find(b)
        if j < 0:
            continue
        if j > 0 and t[j-1] == '《':
            continue
        if any(x != b and b in x and x in t for x in BOOKS):
            continue
        out.append(b)
    return out


def crossing_word(a, b, win=14):
    """断点是否劈开了一个词。**与 make_srt 共用同一套分词词典**，
    所以这里判「劈开了」，那边就一定不会那么断——两者不会打架。

    不枚举任何词表：把两行拼起来重新分词，看有没有 token 跨过断点。
    470 期报过的断行缺陷有 9/11 属于这一类（科创板、小微盘、每个月、书名、4%-5%），
    过去是报一个补一条罚分，现在一条检查全覆盖，包括还没出现过的形态。
    """
    try:
        import jieba, make_srt as M
    except ImportError:
        return None
    if not getattr(crossing_word, '_ready', False):
        for w in M.domain_lexicon():
            jieba.add_word(w, freq=100000)
        crossing_word._ready = True
    L, R = a[-win:], b[:win]
    # 整体不可拆的片段（书名、4%-5% 这类）不是分词器认识的「词」，按同一份正则单独查。
    # ATOMIC_RE 与 make_srt 共用，将来加形态只改那一处。
    for rx in M.ATOMIC_RE:
        for mm in rx.finditer(L + R):
            if mm.start() < len(L) < mm.end():
                return mm.group()
    pos = 0
    # HMM=False：只认词典里的词。开着 HMM 时 jieba 会把跨小句的相邻字
    # 硬粘成「高有」「亿加」「万有」这种非词，7 处报告全是这么来的。
    for t in jieba.cut(L + R, HMM=False):
        st, en = pos, pos + len(t)
        pos = en
        if st < len(L) < en and len(t) > 1:
            return t
    return None


def check(lines, max_em=11.5):
    errs, warns = [], []
    for i, l in enumerate(lines, 1):
        t = l['text']
        d = l['end'] - l['start']

        # —— 明确错误 ——
        # 含完整书名的行豁免宽度上限：470 期定「书名需完整在一行，可以超出上限」。
        # 《红利指数基金投资指南》连书名号 12.0 em，本来就放不进 11.5。
        has_book = '《' in t and '》' in t and t.index('《') < t.rindex('》')
        if width(t) > max_em and not has_book:
            errs.append((i, '超宽 %.1fem（会挤成两排）' % width(t), t))
        # 小句开头出现虚词是正常的：「…他的资产量呢，得相对的高一些」——
        # 逗号之后另起一行，并回去反而得到「资产量呢得」。
        # 只有**句中被拆出**的光杆虚词才是错。clause_start 由 make_srt 给出。
        if t and t[0] in LEAD_BAD and not starts_word(t):
            if l.get('clause_start'):
                warns.append((i, '小句开头是虚词「%s」（通常可接受）' % t[0], t))
            else:
                errs.append((i, '行首虚词「%s」应并到上一行' % t[0], t))
        # 专名被拆开的真实形态是「中证 / 500」——**下一行以数字开头**才算拆。
        # 「创业板」「科创板」本身是完整板块名，行尾出现它完全正常；
        # 470 期按 endswith 一刀切，7 处全是误报，还把整批开工拦住了。
        if t.endswith(NAME_PREFIX) and i < len(lines) and lines[i]['text'][:1].isdigit():
            errs.append((i, '专名被拆开（下一行以「%s」开头）' % lines[i]['text'][:3], t))
        bad = [c for c in t if c in BAD_CHARS]
        if bad:
            errs.append((i, '出现不该烧进画面的字符 %s' % ''.join(sorted(set(bad))), t))
        if re.search(r'\d,\d', t):
            errs.append((i, '数字里有逗号', t))
        if re.search(r'(19|20)\d{3,}', t):
            errs.append((i, '年份异常（可能是补全规则重复应用）', t))
        # 5 位以上数字通常是年份补全出的畸形数字（20102010 这类）。
        # 但 12000块 / 15000元 是正常金额——后面跟着单位就不算异常。
        # 9.2 高净值那期一条片子里误报了 7 次「12000块」。
        for mm in re.finditer(r'\d{5,}', t):
            if t[mm.end():mm.end()+1] in '块元万亿人名股份倍点':
                continue
            errs.append((i, '异常长数字', t))
            break
        if any(c in t for c in FILLER):
            errs.append((i, '残留语气词', t))
        for b in BANNED:
            if b in t:
                errs.append((i, '出现禁用词「%s」' % b, t))
        for b in unmarked_books(t):
            errs.append((i, '书名缺书名号：%s' % b, t))
        if t and t[-1].isdigit() and i < len(lines):
            nxt = lines[i]['text']
            # 下一行是新小句（脚本里这里有标点）就不是拆开的单位：
            # 474 期「账户里存100，家长会再拿出100」的「家」被当成了「100家」的量词
            if nxt.startswith(UNIT_AFTER_NUM) and not lines[i].get('clause_start'):
                errs.append((i, '数字与单位被拆到两行（下一行以「%s」开头）' % nxt[:2], t))

        # —— 需人工判断 ——
        # 两行 start 完全相同：渲染器取第一条匹配行，画面上不叠字，所以肉眼看不出来，
        # 但这是时间轴数据坏了。470 期 02 行144/145 同为 261.53（恰好等于 PPT 段结束时刻）、
        # 05 行1/2 同为 0.98。
        if i > 1 and abs(l['start'] - lines[i-2]['start']) < 0.001:
            errs.append((i, '与上一行起始时间相同（%.2f）' % l['start'], t))

        if t and t[-1] in TAIL_BAD:
            warns.append((i, '行尾是「%s」' % t[-1], t))
        if d < 0.5:
            warns.append((i, '显示仅 %.2fs，偏短' % d, t))
        if PRONOUN_THING.search(t):
            warns.append((i, '「他」是否应为「它」（指代物/指数/公司）', t))
        # 先行词常在前几行（字幕一行才十来个字），只看同行会漏。
        # 470 期 16 号 6 处「他」指美联储/美国，只有 1 处与机构名同行。
        # 同一小段里「他」「它」混用——不必知道指谁，前后不一致本身就是问题。
        # 9.2 高净值那期：行34「它底层到底投了啥」，行35「咱们得先搞清楚他拿了」，
        # 隔一行就换了字，指的却是同一个产品。这种形态比「猜指代对象」好查得多。
        if '他' in t:
            near = ''.join(x['text'] for x in lines[max(0, i-3):min(len(lines), i+3)]
                           if x is not l)
            if '它' in near:
                warns.append((i, '前后 3 行内「它」「他」混用，指代可能不一致', t))
        if '他' in t:
            ctx = ''.join(x['text'] for x in lines[max(0, i-4):i])
            hit = [k for k in INSTITUTION if k in ctx]
            if hit:
                warns.append((i, '前文提到「%s」却用了「他」，机构一律用「它」' % hit[0], t))
        if re.search(r'[0-9]+到[0-9]+', t):
            warns.append((i, '概数区间建议用中文（复核意见：五到六十）', t))
        if i < len(lines):
            gap = lines[i]['start'] - l['end']
            if gap > 2.0:
                warns.append((i, '与下一行间隔 %.1fs（>2s）' % gap, t))
            # 下一行是小句开头（前面就是标点）时，断点落在标点处，
            # 不可能劈开词——跳过，否则会把「回报会更高。/ 一星级…」报成劈开「高一」。
            w = None if lines[i].get('clause_start') else crossing_word(t, lines[i]['text'])
            if w:
                errs.append((i, '断行劈开了「%s」（下一行：%s）'
                             % (w, lines[i]['text'][:8]), t))
    return errs, warns


def main():
    lines = json.load(open(sys.argv[1]))['lines']
    max_em = 11.5
    if '--em' in sys.argv:
        pass
    errs, warns = check(lines, max_em)
    print('字幕 %d 行' % len(lines))
    print('\n[错] %d 处' % len(errs))
    for i, k, t in errs:
        print('   行%-3d %-34s %s' % (i, k, t))
    print('\n[疑] %d 处' % len(warns))
    for i, k, t in warns:
        print('   行%-3d %-34s %s' % (i, k, t))
    if errs:
        sys.exit(1)


if __name__ == '__main__':
    main()
