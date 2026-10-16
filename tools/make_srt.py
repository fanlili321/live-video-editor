#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把脚本原文（人工已校对）按 ASR 词级时间轴对齐，输出字幕 JSON。

思路：ASR 出的字是错的但时间是对的；脚本的字是对的但没有时间。
用字符级 DP 对齐两条序列，把时间搬到脚本文本上。
"""
import json, re, sys, os, csv, io, difflib

# ---------- 文本清洗：视频制作流程文档「六、修正字幕」17 条规则 ----------
INDEX_FULL = {
    '沪深300': '沪深300', '中证500': '中证500', '中证2000': '中证2000',
    '中证1000': '中证1000', '中证800': '中证800', '上证50': '上证50',
}

def _s_strip_notes(t):
    t = t.replace('\r', '')
    t = re.sub(r'（[^）]*插入\s*PPT[^）]*）', '', t)
    return re.sub(r'\([^)]*插入\s*PPT[^)]*\)', '', t)


def _s_year_basic(t):
    """⑤ 补全年份：23 年 -> 2023年（两位数 >=90 视为 19xx，否则 20xx）"""
    def yr(m):
        # group(1) 命中 = 这个两位数不是年份，是时长或年代，原样留着
        if m.group(1):
            return m.group(0)
        n = int(m.group(2))
        return ('19%02d年' % n) if n >= 90 else ('20%02d年' % n)
    # 「前/后」要排除掉「然后/之后/以后/之前/以前」——那些是连词，后面跟的是真年份。
    # 「然后18年呢」曾被当成「后 18 年」的时长表达，整个漏掉没补全。
    DUR = (r'(世纪\s*|\d+\s*[到至~－-]\s*'
           r'|(?:每|近|过去|未来|将来|连续|持续)\s*'
           r'|(?<![然之以])[前后]\s*)?')
    # 后缀护栏：时长标记也可能跟在数字**后面**——「10 年以后再遇到熊市」是"十年之后"，
    # 不是 2010 年。原来只看前面，这句就被补成了「2010年以后」。
    # 不含「之后/以来」：「15年之后」「12年以来」里的两位数确实是年份。
    # 474 期：「一个 10 年的维度」「10 年多」「5 年、 10 年，甚至 15 年」「5 年后、 10 年后」
    # 全被补成了 2010 年。补上：前面是「一个」、后面是「多/后/的维度」的都是时长。
    # 「10 年期国债」「要存 18 年」同理。
    DUR = DUR[:-2] + r'|一个\s*|存\s*)?'
    TAIL_DUR = r'(?!\s*(?:以后|以内|之内|左右|上下|以上|以下|多|期|后(?![来面])|的维度))'
    # 并列里只要有一个一位数（「5 年、10 年」），整串都是时长——年份不会写成一位数。
    keep = []
    def protect(m):
        if re.search(r'(?<!\d)\d\s*年', m.group(0)):
            keep.append(m.group(0))
            return '\x00%d\x00' % (len(keep) - 1)
        return m.group(0)
    # 逗号不算并列：「09 年到 14 年，4 年多」里的「14 年」是年份。只认顿号，或「，甚至/，或者」。
    t = re.sub(r'(?<!\d)\d{1,2}\s*年[后]?(?:(?:\s*、|\s*[，,]\s*(?:甚至|或者|或))\s*\d{1,2}\s*年[后]?)+',
               protect, t)
    t = re.sub(DUR + r'(?<!\d)(\d{2})\s*年' + TAIL_DUR, yr, t)
    return re.sub('\x00(\\d+)\x00', lambda m: keep[int(m.group(1))], t)


def _s_thousand_comma(t):
    """① 千分位逗号：只有「逗号 + 恰好 3 位数字」才是千分位。
    原来一律删，把「09、10，11、12」的枚举逗号也吃了，糊成「1011」。"""
    t = re.sub(r'(?<=\d)[,，](?=\d{3}(?!\d))', '', t)

    # ⑧⑨ 枚举里的逗号改顿号——但**必须先确认这真的是枚举**。
    # 判据：这一串数字里至少有一个顿号，才说明整串是并列关系。
    # 反例（都不是枚举，改了就错）：
    #   「到了6700，6700点上下」  口播重复
    #   「924到930，9月24号到…」  逗号是句读，分隔数字和它的解释
    def norm_run(m):
        return re.sub(r'\s*[,，]\s*', '、', m.group(0)) if '、' in m.group(0) else m.group(0)
    return re.sub(r'\d+(?:\s*[、,，]\s*\d+)+', norm_run, t)


def _s_num_space(t):
    t = re.sub(r'(?<=\d)\s+(?=\d)', '', t)
    return re.sub(r'\s*(\d)\s*', r'\1', t)


def _s_filler(t):
    """① 去语气词。

    「啊」单独处理：流程文档规则①原话是「搜索【啊】**全部**替换为无」。
    它在中文里几乎只作语气词，不构成实词（音译用的是「阿」不是「啊」），
    所以可以全局删。470 期实测全片 60 处，无一例外都是语气词。
    早先只删标点相邻的，漏掉了「存储啊、AI啊方向的」这种夹在句中的。

    其余几个仍限定在虚词位置——「哈」尤其危险（哈佛/哈萨克），只在标点前后删。
    """
    t = t.replace('啊', '')
    for w in '哎诶呃哈呀':
        t = re.sub(r'(?<=[^，。！？、；：\s])%s(?=[，。！？、；：]|$)' % w, '', t)
        t = re.sub(r'(?<=[，。！？、；：])%s(?=[，。！？、；：])' % w, '', t)
    # 不含「哈」：句首的「哈」可能是「哈佛」「哈萨克」的字头，删了就毁词
    for w in '哎诶呃呀':
        t = re.sub(r'(?<=[，。！？、；：])%s(?=[^，。！？、；：])' % w, '', t)
        t = re.sub(r'^%s(?=[^，。！？、；：])' % w, '', t)
    return t


def _s_dedup_punct(t):
    return re.sub(r'[，、]{2,}', '，', t)


def _s_decade(t):
    """⑩ 20几年 -> 二十几年"""
    return t.replace('20几年', '二十几年').replace('10几年', '十几年')


def _s_space_to_pause(t):
    """⑧ 专有名词并列用顿号，不用空格"""
    t = re.sub(r'(?<=[一-龥\d])\s+(?=[一-龥])', '、', t)
    return re.sub(r'\s+', '', t)



# 人工文本修正：脚本表本身写错、且**不适合做成通用规则**时用这个。
# 例：467 期行 5「6%，60的仓位」——口播漏了百分号，但「60→60%」不能变成规则，
#     否则所有裸数字都会被加上百分号。
# 放在流水线最后一步，不会再被其他规则改动；每条修正都进 trace，预检报告里看得见。
MANUAL_FIXES = []      # 由 --fixes <文件> 载入
_APPLIED = set()       # 已生效的修正。脚本改过之后旧修正会静默失效，必须吭声——
                       # 但 clean_script 是**按富文本 run 逐段调用**的，
                       # 一条修正只要在某一段命中就算生效，不能按段判定。


NO_RESTORE = []        # 补词黑名单 [(左文, 右文)]，由修正文件里的「不补」行载入


def load_fixes(path):
    """读修正文件。每行 `原文 => 修正`，# 开头是注释，修正为空表示删除。

    另一种行：`不补 左文|右文`——补词步骤不在「左文」和「右文」之间补话语标记。
    补词跑在人工修正**之后**，补错位置的词用 `=>` 删不掉，只能这样拦住（474 期）。
    """
    out = []
    NO_RESTORE.clear()
    for ln in open(path, encoding='utf-8'):
        ln = ln.rstrip('\n')
        if not ln.strip() or ln.lstrip().startswith('#'):
            continue
        if ln.startswith('不补 ') and '|' in ln:
            l, r = ln[3:].split('|', 1)
            NO_RESTORE.append((l.strip(), r.strip()))
            continue
        if '=>' not in ln:
            raise SystemExit('修正文件格式错误（缺 =>）：%s' % ln)
        a, b = ln.split('=>', 1)
        out.append((a.strip(), b.strip()))
    return out


def _s_manual(t):
    for a, b in MANUAL_FIXES:
        if a in t:
            _APPLIED.add(a)
            t = t.replace(a, b)
    return t


# 清洗流水线。**顺序本身是规则的一部分**，调换会出错：
#   年份补全必须在千分位逗号之前（否则枚举逗号已被处理）
#   expand_year_before 必须在 _s_year_basic 之后（那时「15 年」才变成「2015年」）
def _s_unescape(t):
    """去掉 Markdown 转义反斜杠。

    脚本是从飞书表格取的，转写环节偶尔把 Markdown 转义符带进单元格。
    470 期 16 号 B 列原文就是 `我负担得起6\\~7个点`，反斜杠一路烧进了画面。
    只删「反斜杠 + 标点」里的反斜杠，不动正常文字。
    """
    return re.sub(r'\\([~*_\[\]()#+\-.!`>|])', r'\1', t)


# 「N到M」在利率/收益语境下说的是百分比，主播省略了单位。
# 470 期 16 号：「大家买余额宝6到7」「国债都有四到5个点」「降到了2到3的水平」。
# ⚠️ 三重条件缺一不可，否则会误伤：
#   ① 两个数都 ≤ RATE_MAX——利率是小数，「2000到3000亿」不是
#   ② 后面不跟量词单位（亿/万/年/只/家/倍…）——「4到5年」「4到5只」是数量不是利率
#   ③ 前后 14 字内出现利率语义词——没有证据就不改
RATE_MAX = 30
RATE_CTX = ('个点', '收益', '利率', '回报', '通胀', '股息', '分红', '涨', '跌',
            '降到', '保持', '国债', '余额宝', '存款', '理财', '债券', '票息')
RATE_UNIT_AFTER = '亿万元年月日只家倍位名代'
CN_D = {'一': '1', '二': '2', '两': '2', '三': '3', '四': '4', '五': '5',
        '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}


def _s_rate_range(t):
    def rep(m):
        a, b, dian = m.group(1), m.group(2), m.group(3) or ''
        a, b = CN_D.get(a, a), CN_D.get(b, b)
        try:
            if float(a) > RATE_MAX or float(b) > RATE_MAX:
                return m.group(0)
        except ValueError:
            return m.group(0)
        nxt = t[m.end():m.end()+1]
        # ⚠️ nxt 可能是空串（匹配到句末），而 '' in '任何字符串' 恒为 True，
        #    会把句末的情形当成「后面跟了单位」而放过。必须先判非空。
        if not dian and nxt and nxt in RATE_UNIT_AFTER:
            return m.group(0)
        lo, hi = max(0, m.start()-14), m.end()+14
        if not any(k in t[lo:hi] for k in RATE_CTX):
            return m.group(0)
        return '%s%%-%s%%' % (a, b)
    return re.sub(r'([0-9]+(?:\.[0-9]+)?|[一二两三四五六七八九十])到'
                  r'([0-9]+(?:\.[0-9]+)?|[一二两三四五六七八九十])(个点)?', rep, t)


def _s_head_stutter(t):
    """「单字 + 句读 + 以该字开头的词」——说了半个词，停顿，重说完整的。

    470 期 16 号：`美债它的体。体量呢非常非常的大` → `美债它的体量呢非常非常的大`
    规则⑦「删单字残句」只处理「句读**后**的单字」，够不着单字**在句读前**的这一种。
    两者是同一个口吃的两个方向。
    """
    # 474 期：`红包。包括` `不带分红，红利也是` 被粘成「红包括」「分红利」。
    # 区别在句读前那个字：口吃时它是说了一半的残字（「的体」不成词），
    # 而「红包」「分红」本身是完整的词——前字+该字成词的，不动。
    def fix(m):
        prev = m.string[max(0, m.start()-1):m.start()]
        if prev and _is_word(prev + m.group(1)):
            return m.group(0)
        return m.group(1) + m.group(2)
    return re.sub(r'([\u4e00-\u9fff])[，。！？、；：]\1([\u4e00-\u9fff])', fix, t)


def _is_word(w):
    import jieba
    jieba.dt.check_initialized()
    return jieba.dt.FREQ.get(w, 0) > 0 or w in DOMAIN_WORDS


def _s_echo_word(t):
    """小句衔接处的叠字：前一小句末字 == 后一小句首字，且重复无意义。

    470 期：`又出来了创业板，创业板呢` `科创板上市，上市之后呢`
            `像这个消费，消费的话呢`   `中证红利指数，指数基金`
    这些在脚本里本来带着标点，是小句合并把标点吃掉后才读成叠词的——
    所以真正的修法在合并阶段（见 split_lines），这里只处理脚本自带的重复。
    """
    return t


CLEAN_STEPS = [
    ('删跨句读口吃',      _s_head_stutter),
    ('去转义符',          _s_unescape),
    ('去编辑批注',        _s_strip_notes),
    ('年份补全·基础',     _s_year_basic),
    ('年份补全·无年枚举', lambda t: expand_year_runs(t)),
    ('年份补全·跟随枚举', lambda t: expand_year_before(t)),
    ('千分位逗号',        _s_thousand_comma),
    ('数字空格',          _s_num_space),
    # ⚠️ 必须排在「数字空格」之后。脚本里写的是「一年 2 到3」带空格，
    #    这一步原先排在前面，正则匹配不到「2到3」，471 期整条片子都没补上 %。
    ('利率区间补%',      _s_rate_range),
    ('删语气词',          _s_filler),
    ('删单字残句',        lambda t: drop_stutter(t)),
    ('合并重复标点',      _s_dedup_punct),
    ('年代口语化',        _s_decade),
    ('并列空格转顿号',    _s_space_to_pause),
    ('固定用字纠错',      lambda t: fix_names(t)),
    ('指数名补全',        lambda t: expand_index_names(t)),
    ('书名号',            lambda t: add_book_marks(t)),
    ('人工文本修正',      _s_manual),
]


def clean_script(t, trace=None):
    """按 CLEAN_STEPS 逐步清洗。

    传 trace（一个 list）进来时，每一步的改动都会被记下来：
    (步骤名, 改前片段, 改后片段)。**每个字符的变化都能归因到某条规则**，
    这样人工预检时才看得见「谁把 2000 改成了中证2000」。
    """
    for name, fn in CLEAN_STEPS:
        before = t
        t = fn(t)
        if trace is not None and t != before:
            trace.extend(_diff(name, before, t))
    return t


def _diff(rule, before, after):
    """把一步改动拆成若干条可读的 (规则, 改前, 改后)"""
    out = []
    sm = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            continue
        ctx = 6
        out.append({
            'rule': rule,
            'kind': {'replace': '改', 'delete': '删', 'insert': '增'}[tag],
            'before': before[i1:i2],
            'after': after[j1:j2],
            'context': before[max(0, i1-ctx):i1] + '【' + before[i1:i2] + '】' + before[i2:i2+ctx],
        })
    return out



# 规则③⑬⑰：脚本里也会写错的固定用字，按词典强制纠正。
# 「约翰伯格」在 467 期三条里出现 6 次，脚本原文就是错的——
# 脚本是权威文本，但**专有名词的权威在词典，不在脚本**。
NAME_FIX = [
    ('约翰·伯格', '约翰·博格'), ('约翰伯格', '约翰·博格'),   # 规则③
    ('必须消费', '必需消费'),                                  # 规则⑬
    ('a股', 'A股'), ('Ａ股', 'A股'),                           # 规则⑰
]


def fix_names(t):
    for a, b in NAME_FIX:
        t = t.replace(a, b)
    return t


def expand_year_runs(t):
    """顿号/逗号枚举里的两位数年份补全：02、03、04、05 -> 2002年、2003年…

    规则⑤要求年份补全，但原实现要求后面跟着「年」字。
    口播里「像02、03、04、05」「09、10、11、12、13、14」整串一个「年」都没有，
    全部漏网。判据：**连续 3 个以上、递增的两位数**——这种形态基本只可能是年份。
    只有 2 个的（如「12、13」）歧义太大（可能是月/号），不动，交质检提示。
    """
    def rep(m):
        nums = re.findall(r'\d{2}', m.group(0))
        if len(nums) < 3:
            return m.group(0)
        v = [int(x) for x in nums]
        if any(b <= a for a, b in zip(v, v[1:])):    # 必须严格递增
            return m.group(0)
        return '、'.join('%d年' % (1900 + n if n >= 90 else 2000 + n) for n in v)
    return re.sub(r'(?<!\d)\d{2}(?:\s*[、,，]\s*\d{2}){2,}\s*年?(?!\d)', rep, t)


def expand_year_before(t):
    """枚举里排在年份前面的裸两位数，跟着补全：14、2015年 -> 2014年、2015年

    必须放在基础年份规则之后跑——那时「15 年」已经变成「2015年」，
    剩下的「14、」才认得出是同一串枚举里的年份。

    与 expand_year_runs 的分工：
      本函数  枚举里**有**元素带「年」（哪怕只有最后一个）→ 前面的裸数字全部补全
      前者    枚举里**一个「年」都没有** → 靠「≥3 个递增两位数」判定

    「在14、15年之后」和「比如说16、17年的」就是本函数的来由：
    两个元素的枚举够不上前者的 3 个门槛，后一个又已经被基础规则吃掉了「年」字。
    """
    pat = re.compile(r'(?<!\d)(\d{2})(\s*[、,，]\s*)(?=(?:19|20)\d{2}\s*年)')

    def rep(m):
        n = int(m.group(1))
        return '%d年%s' % (1900 + n if n >= 90 else 2000 + n, m.group(2))

    # 循环处理「12、13、2014年」这种链式枚举，每轮从右往左推进一个
    for _ in range(8):
        new = pat.sub(rep, t)
        if new == t:
            break
        t = new
    return t


# 单字小句几乎一定是口吃或重起头，删掉。留白名单是能独立成句的字。
STUTTER_KEEP = set('对是好嗯行不哦')


def drop_stutter(t):
    """删掉「…。持，下跌很猛烈」里的孤立单字小句。

    ⚠️ **顿号分隔的单字是并列枚举，不是口吃**，绝不能删。
    470 期把「想把大、中、小、微不同的指数」删成了「想把大、微不同的指数」——
    「中、小」被当成口吃残字删掉，意思整个坏了。
    判据：口吃残字后面跟的是逗号或句号（说了半个词停下来重说）；
    并列项后面跟的是顿号。
    """
    parts = re.split(r'([，。！？、；：])', t)
    out = []
    for i in range(0, len(parts), 2):
        seg = parts[i]
        punc = parts[i+1] if i+1 < len(parts) else ''
        core = seg.strip()
        prev_punc = parts[i-1] if i >= 1 else ''
        in_enum = punc == '、' or prev_punc == '、'
        if (len(core) == 1 and not in_enum and core not in STUTTER_KEEP
                and not core.isdigit() and not core.isascii()):
            continue        # 连同它后面的标点一起丢掉
        out.append(seg + punc)
    return ''.join(out)


# 规则⑦：只补这几个指数，像「消费50」「科创50」这种不补
INDEX_EXPAND = [('2000', '中证2000'), ('1000', '中证1000'),
                ('800', '中证800'), ('500', '中证500'),
                ('300', '沪深300'), ('50', '上证50')]
# 前面出现这些字符时说明已有名字或属于别的指数，不动
INDEX_SKIP_PREFIX = '沪深中证上证科创创业板消费医药军工红利深证北证恒生纳斯达克标普ABC'
# 后面跟这些说明是普通数字，不是指数
# 后面跟这些说明是普通数字，不是指数。
# 「到」是 470 期加的：「就在一个2000到3000亿之间」是市值区间，
# 被补成了「中证2000到3000亿」——和 467 期「2000多点」同一类错，只是量词换了。
# 数字后面跟这些字的，是量词/单位，不是指数代号，**不补指数名**。
# 470 期 08「可转债的话1000块钱就行了」被补成「中证1000块钱」——差的就是「块」。
# 与「中证2000多点」「中证2000到3000亿」是同一条规则的同一类失效，第三次了：
# 每次都是补了具体的字而不是补全这一类，所以务必把货币/计量单位一次补齐。
INDEX_SKIP_SUFFIX = '亿万元点倍家只个%％年月日名位倍多来几到至~块角分毛千百十'


BOOK_TITLES = ['红利指数基金投资指南', '个人养老金投资指南', '指数基金投资指南',
               '主动基金投资指南', '股市长线法宝', '定投十年财务自由',
               '投资的未来']   # 470 期 16 号提到


def add_book_marks(t):
    """书名加《》（复核意见：书要加书名号）。长书名优先，已有书名号的不重复加。"""
    for b in sorted(BOOK_TITLES, key=len, reverse=True):
        i = 0
        while True:
            j = t.find(b, i)
            if j < 0:
                break
            before = t[j-1] if j > 0 else ''
            after = t[j+len(b)] if j+len(b) < len(t) else ''
            if before == '《' or after == '》':
                i = j + len(b); continue
            t = t[:j] + '《' + b + '》' + t[j+len(b):]
            i = j + len(b) + 2
    return t


def expand_index_names(t):
    for num, full in INDEX_EXPAND:
        out, i = '', 0
        while True:
            j = t.find(num, i)
            if j < 0:
                out += t[i:]; break
            prev = t[j-1] if j > 0 else ''
            nxt = t[j+len(num)] if j+len(num) < len(t) else ''
            # ⚠️ 中文字符 .isalpha() 也是 True，这里只想挡拉丁字母（A500 / A50）
            prev_latin = prev.isascii() and prev.isalpha()
            bad = (prev in INDEX_SKIP_PREFIX) or prev.isdigit() or prev_latin \
                  or nxt.isdigit() or (nxt in INDEX_SKIP_SUFFIX)
            out += t[i:j] + (num if bad else full)
            i = j + len(num)
        t = out
    return t


PUNCT = '，。！？、；：,.!?;:“”"\'（）()《》'
BREAK_STRONG = '。！？\n'
BREAK_WEAK = '，、；：'


# 字宽模型。**必须与 qc_subs.width() 完全一致**，否则一边放行一边误报。
# 数值是 CoreText 对 SourceHanSansCN-Medium 实测的 advance（不是估的）：
#     汉字 1.000   、，。 1.000   数字 0.570   拉丁 0.622   % 0.939
# ⚠️ 中文标点原来按 0.5 算，是错的——全角标点的 advance 就是 1 em，
#    只是 ink 很小（、只有 0.303）看着窄。结果「咱们会遇到一次熊市、牛市」
#    实测 12.000 em，模型算 11.500 em 刚好卡在上限内，护栏形同虚设。
def char_width(c):
    if c.isdigit():
        return 0.57
    if c.isascii() and c.isalpha():
        return 0.62
    if c in '%％':
        return 0.94
    return 1.0


def norm_for_align(c):
    return c


def expand_words(words):
    """ASR 词 -> 逐字符 (char, t)"""
    out = []
    for w in words:
        s = w['s']
        chars = [c for c in s if c not in PUNCT and not c.isspace()]
        if not chars:
            continue
        step = w['d'] / len(chars)
        for i, c in enumerate(chars):
            out.append((c, w['t'] + i*step, w['t'] + (i+1)*step))
    return out


def align(script, asr_chars):
    """返回 script 每个字符的 (start, end)，未匹配的插值"""
    a = [c for c, _, _ in asr_chars]
    b = [c for c in script]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    times = [None]*len(b)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2-i1):
                times[j1+k] = (asr_chars[i1+k][1], asr_chars[i1+k][2])
    # 插值填补
    known = [i for i, v in enumerate(times) if v]
    known_idx = list(known)
    if not known:
        raise SystemExit('对齐失败：没有任何匹配')
    # 头尾外推
    for i in range(known[0]):
        times[i] = times[known[0]]
    for i in range(known[-1]+1, len(times)):
        times[i] = times[known[-1]]
    for x in range(len(known)-1):
        i, j = known[x], known[x+1]
        if j - i <= 1:
            continue
        t0, t1 = times[i][1], times[j][0]
        n = j - i
        for k in range(1, n):
            s = t0 + (t1-t0)*(k-1)/max(1, n-1)
            e = t0 + (t1-t0)*k/max(1, n-1)
            times[i+k] = (s, e)
    return times, sm.ratio(), set(known_idx)


DOMAIN_WORDS = [
    '宽基指数', '中证全指', '沪深300', '中证500', '中证800', '中证1000', '中证2000',
    '上证50', '科创50', '创业板50', '上世纪', '年代', '沪深300', '中证500', '中证800', '中证1000', '中证2000',
    '上证50', '科创50', '总市值', '市盈率', '波动幅度', '涨跌波动', '暴涨暴跌',
    '熊市周期', '牛市中后期', '高估止盈', '低估买入', '正常估值', '螺丝钉星级',
    '进入到', '一潭死水', '指数基金', '股票基金', '基金规模', '杠杆资金',
    '心理准备', '分批止盈', '定投', '回撤', '星级', '大盘',
    # 第5轮复核补充：这些被 jieba 切成两个 token，断在中间读着就散了
    '基金经理', '风格漂移', '资金来源', '增量资金', '存量资金', '主动基金',
    '偏股基金', '宽基', '抱团率', '爆仓', '调仓', '搬家', '低估值', '高估值',
    # 第6轮质检补充：被 jieba 切成两个单字 token，断在中间就散了
    '牛熊', '牛熊市', '高低', '涨跌', '存取', '进出',
]

LEAD_BAD = set('的了呢吗吧着过地得于里中上下内外间')   # 不可作行首（配合分词，实词开头不罚）
TAIL_BAD = set('的地得')                        # 不宜作行尾
# 单字功能词/程度副词，不宜作行尾。「最」是 470 期人工复核发现的：
# 「这个是A股的最 / 有代表性的宽基指数」——DEGREE_TAIL 里只有「最为」，够不着光杆的「最」。
FUNC_TAIL = set('就也都还很更又再把被在从对给和与向为最太挺极超')
DEMONSTRATIVE_TAIL = set('那这某每各另')                  # 指示代词，后面必跟名词
# 程度副词后面必跟形容词，断在这里会切出「…比较 / 强的相关性」
DEGREE_TAIL = ('比较', '非常', '特别', '十分', '相当', '更加', '最为', '极其')


# 「多」必须在列：「2000多点」曾被断成「…中证2000 / 多点最后呢涨到了」
UNIT_AFTER_NUM = ('多', '来', '几', '年代', '年份', '年', '月份', '月', '日', '季度',
                  '点', '倍', '亿', '万', '元', '个', '只', '家', '星', '%', '％', '块', '成')
# 口播里数字常写成汉字：「五六月份」「两三年」——这些也是数词，不能和后面的单位拆开
CN_NUM = set('零一二三四五六七八九十百千万亿两几')

# 框式结构的后半截，只能跟在前半截屁股后面，不能起一行
# （「市值规模相对 / 来讲排在龙头」——「相对来讲」被劈成了两句）
POST_LEAD = ('来讲', '来说', '而言', '来看', '以来', '以后', '以前', '之后', '之前',
             '左右', '上下', '之间', '之内', '之外', '为止', '当中', '一样', '似的')

# 复核意见（9）：专有名词不能拆开——这些前缀后面跟的是指数名，不可在此断行
NAME_PREFIX = ('中证', '沪深', '上证', '深证', '北证', '科创', '创业板',
               '恒生', '标普', '纳斯达克', '中债', '国证')


# 完整专名。NAME_PREFIX 管的是「前缀留在行尾」（中证 / 500），
# 这份管的是**一刀劈在专名中间**的所有形态——470 期出现的是劈在后半截：
#   「…介绍过创业板和科 / 创板相关的指数」「比科 / 创板还要大一些」（科创板）
#   「这两个是A股的小 / 微盘的代表指数」（小微盘）
# jieba 会把 科创板 切成 科|创板，靠前缀表根本够不着，必须按完整名判断。
PROPER_NOUNS = ('科创板', '创业板', '北交所', '新三板', '中小板', '小微盘',
                '大盘股', '中盘股', '小盘股', '微盘股', '沪深300', '中证500',
                '中证1000', '中证2000', '北证50', '上证50', '深证100',
                '恒生指数', '纳斯达克', '标普500', '宽基指数', '窄基指数')


def break_cost(prev_tok, next_tok, tokens=None, i=None):
    """在 prev_tok 和 next_tok 之间断行的代价，越大越不该断

    tokens/i 是可选上下文：有些形态光看断点两侧的词判断不了，
    比如顿号枚举断在中途（「大、中、小 / 微不同的指数」）。
    """
    c = 0
    # 一刀劈在完整专名中间
    left, right = prev_tok[-4:], next_tok[:4]
    joint = left + right
    for nm in PROPER_NOUNS:
        j = joint.find(nm)
        if j >= 0 and j < len(left) < j + len(nm):
            c += 200
            break
    # 书名号不可拆（470 期人工复核定：书名需完整在一行，可以超出宽度上限）。
    # 主力机制是 merge_books 把《…》合成一个 token，根本走不到断行；
    # 这里是兜底——万一合并没覆盖到（比如书名号跨了样式分段），也不许断在中间。
    if ('《' in prev_tok and '》' not in prev_tok) or ('》' in next_tok and '《' not in next_tok):
        c += 500
    # 「每个 / 月」「三个 / 点」——量词「个」和它的单位不能拆
    if prev_tok.endswith('个') and next_tok[:1] in '月年点亿万天周期':
        c += 200
    # 顿号枚举断在中途，行尾正好是枚举项本身：「想把大、中、小 / 微不同的指数」
    # 屏幕上读成「微不同的指数」，语义全变了。上一轮这组词是被**删**，这一轮是被**拆**。
    # jieba 把「、」切成独立 token：['大','、','中','、','小','、','微',...]，
    # 所以断点左边是「、」本身，枚举项在 tokens[i-2]。
    # 只罚**单字枚举**：「白酒、医药、/ 银行这些行业」断在这里完全正常，
    # 「大、中、小 / 微不同的指数」才是灾难——屏幕上读成「微不同的指数」。
    if (tokens and i and i >= 2 and prev_tok.endswith('、')
            and len(next_tok) == 1 and len(tokens[i-2]) == 1):
        # 罚 500 而不是 200：这不是「读着别扭」，是**语义被改**——
        # 屏幕上「微不同的指数」是一个真实存在但完全不同的意思。
        # 200 抵不过相邻断点的代价，实测仍会被拆开。
        c += 500
    # 数字和它后面的量词/单位不能拆（「上世纪90 / 年代」这种）
    if prev_tok and (prev_tok[-1].isdigit() or prev_tok[-1] in CN_NUM) \
            and next_tok.startswith(UNIT_AFTER_NUM):
        c += 200
    # 专有名词前缀不能留在行尾（「…和中证 / 红利低波…」这种）
    if prev_tok.endswith(NAME_PREFIX):
        c += 200
    # 「过去」「的确」这类实词开头不该罚——只有光杆虚词成词时才是坏行首。
    # ⚠️ 罚 1500 而不是 70：qc_subs 把「行首光杆虚词」判为 [错]（阻断渲染），
    #    优化器却只给 70 分，两边不一致——卡口说「必须修」，DP 说「值 70 分」。
    #    9.2 高净值那期出现 3 处：小句 17em 必须切两刀，DP 算出切在「极限|的」
    #    代价 302 最低，就把「的」甩到了行首。既然是硬缺陷就该近乎禁止，
    #    只有实在无路可走时才允许。
    if next_tok and next_tok[0] in LEAD_BAD and len(next_tok) == 1:
        c += 1500
    if prev_tok and prev_tok[-1] in TAIL_BAD:
        c += 40
    if prev_tok in FUNC_TAIL:
        c += 60
    # 行尾**最后一个字**是光杆功能词/程度副词也不行——上面那条只判整个 token 相等，
    # 「这个是A股的最」整体不等于「最」，就漏过去了（470 期人工复核发现）。
    elif prev_tok and prev_tok[-1] in FUNC_TAIL:
        c += 60
    # 百分比区间不许断开：「…的4%」/「-5%降到了…」。主力靠 ATOMIC_RE 原子化，这里兜底。
    if prev_tok.endswith('%') and next_tok.startswith('-'):
        c += 500
    if prev_tok in DEGREE_TAIL:
        c += 200
    # 行尾留光杆阿拉伯数字：「在2021年和今年的5 / 到6月份」——数字要跟着它的区间走
    if prev_tok.isdigit():
        c += 200
    # 行尾留单字指示代词：「这个表现最好的那 / 百分之几的股票」
    if prev_tok in DEMONSTRATIVE_TAIL:
        c += 60
    if next_tok in POST_LEAD:
        c += 200
    # 拉丁词与紧跟的中文本来就是一个词：AI产业链 / A50指数 / ETF基金。
    # jieba 会把它们切成两个 token，断在中间就散了。
    # 这里全在同一个小句内判断（标点已先切开），不会误伤「买了ETF，然后…」这种跨句情形。
    if prev_tok and prev_tok[-1].isascii() and prev_tok[-1].isalpha() \
            and next_tok and '\u4e00' <= next_tok[0] <= '\u9fff':
        c += 200
    if prev_tok in ('会', '能', '要', '是', '有', '说', '让', '使'):
        c += 50
    # 单字碎片挂在行尾/行首通常是把词拆散了
    if prev_tok and len(prev_tok) == 1:
        c += 40
    if next_tok and len(next_tok) == 1:
        c += 20
    return c


def is_book(t):
    """这个 token 是不是一个整体不可拆的片段（书名 / 百分比区间）"""
    if t.startswith('《') and t.endswith('》') and len(t) > 2:
        return True
    return bool(re.fullmatch(r'\d+(?:\.\d+)?%(?:-\d+(?:\.\d+)?%)?', t))


# 这些片段整体不可断开。按正则在原文上找出跨度，再把落在跨度内的 token 粘成一个。
# 比逐 token 判断可靠——jieba 怎么切都不影响结果。
ATOMIC_RE = [
    re.compile(r'《[^》]{1,20}》'),                       # 书名（470 期人工复核）
    re.compile(r'\d+(?:\.\d+)?%-\d+(?:\.\d+)?%'),      # 利率区间 4%-5%
    re.compile(r'\d+(?:\.\d+)?%'),                      # 单个百分比，别把 % 甩到下一行
]


def merge_books(toks):
    """把不可断开的片段粘成单个 token。

    470 期两次踩到：
      ①《红利指数基金投资指南》被切成三行，「《红利」孤零零留在行尾；
      ② 我自己加的利率区间规则产出「4%-5%」，结果断成「…的4%」/「-5%降到了…」。
        ——**新规则产出的新字符串形态，要同时检查它会不会被断行拆开。**
    """
    text = ''.join(toks)
    spans = []
    for rx in ATOMIC_RE:
        for m in rx.finditer(text):
            if not any(a <= m.start() and m.end() <= b for a, b in spans):
                spans.append((m.start(), m.end()))
    if not spans:
        return toks
    out, buf, pos = [], '', 0
    for t in toks:
        a, b = pos, pos + len(t)
        pos = b
        inside = any(x < b and a < y for x, y in spans)
        if inside:
            buf += t
            if not any(x < pos < y for x, y in spans):   # 已走出所有跨度
                out.append(buf); buf = ''
        else:
            if buf:
                out.append(buf); buf = ''
            out.append(t)
    if buf:
        out.append(buf)
    return out


# ---------- 断句率：人在什么词性边界上断句 ----------
# 470 期把人工成片的 1608 个断点对齐回脚本统计出来的。**不是猜的，是量的。**
#
# 为什么需要它：dp_split 原先只有「惩罚」没有「奖励」——能说「这里不许断」，
# 说不出「这里最该断」。实测 77% 的断点 break_cost 为 0，即毫无语义信号，
# 于是宽度成了唯一裁判（72% 的断点由宽度主导）。
# 加了奖励项后，与人工的断点重合率 +4pp（留出集验证）。
#
# 最强的信号是语气词：「呢/吧/啊」之后人有 78% 概率断行——那正是口播的自然停顿点；
# 而「的」之前几乎从不断（0~0.6%）。
#
# 断句率.json 可以再生：攒了新一期的人工成片，重跑统计脚本即可，样本越多越稳。
_AFF = None


def affinity(l_tag, r_tag):
    """该词性边界上，人的断句率（0~1）。样本不足时按左词性回退，再不足用基准值。"""
    global _AFF
    if _AFF is None:
        try:
            _AFF = json.load(open(os.path.join(os.path.dirname(
                os.path.abspath(__file__)), '断句率.json'), encoding='utf-8'))
        except Exception:
            _AFF = {'pair': {}, 'single': {}, '基准断句率': 0.11}
    p = _AFF['pair'].get('%s>%s' % (l_tag, r_tag))
    if p and p[0] >= 25:
        return p[1] / p[0]
    q = _AFF['single'].get(l_tag)
    if q and q[0] >= 40:
        return q[1] / q[0]
    return _AFF.get('基准断句率', 0.11)


W_AFFINITY = 200.0     # 语义奖励权重。0 = 退回旧行为；实测 200 时重合率最高
# 停顿奖励权重。**实测设为 0**——这条信号是错的，不是弱。
#
# 470 期实测：人工断句率 与 该处真实停顿时长 的相关系数 r = -0.07（几乎无关）。
#   语气词后：断句率 78.3%，平均停顿 0.045s  ← 停顿最短，却最该断
#   连词后：  断句率  3.9%，平均停顿 0.169s  ← 停顿最长，却最不该断
# 说话人在「但是……」后面停顿是在组织语言，那恰恰是不能断开的地方。
#
# 字幕断行是**阅读**装置，不是语音节奏的转写。人按句法断，不按呼吸断。
# 这条一度是硬编码的 120，且喂给它的还是插值出来的假停顿（见 real_pause 注释）——
# 两个错误互相掩盖，所以一直没暴露。real_pause 保留着，将来若用于时间轴仍有价值。
W_PAUSE = 0.0
PAUSE_CAP = 0.5        # 停顿计入上限，避免超长静音一家独大

# ---------- 词组不可断 ----------
# 词性断句率是**相关性**，不是规则，它有一处致命的粗糙：
#   n→n（信贷|部门）断句率 5.6%，uj→n（的|信贷）7.2% —— 两者几乎一样。
# 但语言上它们完全相反：前者是偏正词组内部，绝不能断；后者是可分界处。
# 统计把「不能断」和「可以断」平均成了同一个分数。
#
# 中文里这件事有清楚的形式标记：**「的」是可分界处，两个实词紧邻是黏合的**。
#   银行的 | 信贷部门     ← 可断（人工就是这么断的）
#   银行的信贷 | 部门     ← 把「信贷部门」劈开了
# 所以这条不能靠学，要直接编码：相邻两个实词之间断行，视同劈开词组。
#
# 9.2 高净值那期「信贷|部门」「个人|养老金账户」都是这么断错的。
CONTENT_TAGS = {'n', 'nr', 'ns', 'nt', 'nz', 'ng', 'vn', 'an', 'a', 'b', 'j', 'l', 'i', 's'}
W_COMPOUND = 400.0     # 劈开词组的罚分。必须显著高于「行尾是的」那 80 分——
                       # 行尾「的」只是读着别扭（规范里是 [疑]），劈开词组是硬伤。


def compound_break(l_tag, r_tag):
    """在这两个词之间断行，是不是把一个偏正词组劈开了"""
    return l_tag in CONTENT_TAGS and r_tag in CONTENT_TAGS


# 插词的判据要比断行**更严**：断行只是把一句话分两行，插词是往里加字，改的是内容。
# 所以这里的实词表更宽，把动词、方位词、量词都算进去：
#   体量/n + 下/f      「一个体量下」被插成「一个体量⟨这个⟩下」
#   普涨(v) + 行情/n   「一个普涨行情」被插成「一个普涨⟨然后⟩行情」
# 合法的插入点长什么样：都在「的」之后或标点之后——
#   各种各样的⟨这个⟩金融机构、每年的⟨这个⟩利息、是主板的股票。⟨然后⟩后来呢
# 所以卡住「实词接实词」不会误伤它们。
# ⚠️ 不含动词 v。「拿【这个】分红」「出现【这个】场内的指数基金」这类主播很可能真这么说，
#    卡掉就是误杀——而误杀违背「宁可全补」的既定取舍。
#    要卡的是**数量结构与名名复合**这种紧密单位：
#      100|来亿、四五十|亿、2014|年、一个|影响、代表性|指数、精选|层
INSERT_BLOCK = CONTENT_TAGS | {'f', 'q', 'm', 't', 'nrt'}


def bad_insert_pair(l_tag, r_tag):
    """在这两个词之间插字，是不是插进了词组内部"""
    return l_tag in INSERT_BLOCK and r_tag in INSERT_BLOCK


def dp_split(tokens, widths, max_em, target=9.5, gaps=None, tags=None):
    """在词边界上做最优断行：断点代价 + 行宽偏离惩罚"""
    n = len(tokens)
    INF = float('inf')
    best = [INF]*(n+1); back = [0]*(n+1)
    best[0] = 0
    for j in range(1, n+1):
        w = 0
        for i in range(j-1, -1, -1):
            w += widths[i]
            # j-i == 1 表示这一行只有一个 token：它本身就超宽也没法再切，允许超。
            # 书名整体成词后走的正是这条路。
            if w > max_em and j - i > 1:
                break
            if best[i] == INF:
                continue
            cost = best[i] + (w - target)**2
            if i > 0:
                cost += break_cost(tokens[i-1], tokens[i], tokens, i)
                # 语义奖励：断在人常断的边界上代价低，断在人从不断的边界上代价高。
                # 写成 (1 - 断句率) 而不是直接减断句率——后者会让「多断几刀」白赚，
                # DP 会倾向切得过碎。
                if tags:
                    cost += W_AFFINITY * (1.0 - affinity(tags[i-1], tags[i]))
                    if compound_break(tags[i-1], tags[i]):
                        cost += W_COMPOUND
                if gaps:
                    cost -= min(gaps[i], PAUSE_CAP) * W_PAUSE   # 在停顿处断行是最自然的
            if cost < best[j]:
                best[j] = cost; back[j] = i
    if best[n] == INF:
        return None
    cuts, j = [], n
    while j > 0:
        cuts.append((back[j], j)); j = back[j]
    return list(reversed(cuts))


# 小句合并的宽度上限。**这才是决定「一行多长」的东西，不是 dp_split 的 target。**
# 470 期实测：target 从 6.5 调到 8.0，平均行长纹丝不动（恒为 8.3 字）——
# 因为绝大多数行是小句合并拼出来的，根本没进 DP。
# 把上限从 max_em(11.5) 降到 8.0 后，对人工断点的覆盖率 +5pp，行长 8.3→7.8
# （人工是 6.9）。这是一个「断多勤」的旋钮，与「断在哪」的 W_AFFINITY 各管一头。
MERGE_MAX_EM = 8.0
SPLIT_EM = 11.5        # 小句宽过这个才拆；不到的整句一行
DP_TARGET = 9.5        # 拆句时的目标行宽

LEADING_BAD = set('的了呢吗吧着过地得也就都还很更又再')

def fix_leading(parts, max_em=11.5):
    """不允许一行以虚词开头：把它挪回上一行。

    ⚠️ 必须检查上一行会不会因此超宽。原来不检查，直接把「就」并回去，
    把 11 字的行撑成 12 字（12.0em > 11.5em）——**字幕挤成两排，违反规则②**。
    两害相权：行首虚词只是读着别扭，挤成两排是硬伤，所以宽度优先。
    """
    def w(t):
        return sum(char_width(c) for c in t)

    import jieba

    def lone_function_word(line):
        """行首那个字是不是**光杆虚词**，而不是某个词的头一个字。

        「很难」「过去」都是完整的词，把「很」「过」抠出来并回上一行
        就把词劈成了两半——第5轮复核里「过去」被拆开就是这么来的。
        """
        toks = list(jieba.cut(line))
        return bool(toks) and len(toks[0]) == 1

    for i in range(1, len(parts)):
        while (parts[i][0] and parts[i][0][0] in LEADING_BAD
               and len(parts[i][0]) > 1 and lone_function_word(parts[i][0])):
            c, ci = parts[i][0][0], parts[i][1][0]
            if w(parts[i-1][0] + c) > max_em:
                break               # 挪不动就算了，宁可行首别扭也不能挤成两排
            parts[i-1] = (parts[i-1][0] + c, parts[i-1][1] + [ci])
            parts[i] = (parts[i][0][1:], parts[i][1][1:])
    return [p for p in parts if p[0]]


def qc_timing(lines, gaps10, words, keeps, lead=0.08):
    """分类统计字幕起点精度：
       - 停顿后起句：应精确吸附到真实起音点（可验证）
       - 句中断行：起点取自 ASR 词时间（无停顿可比对，观感上也不敏感）"""
    def out2src(t):
        acc = 0.0
        for x, y in keeps:
            if t <= acc + (y - x):
                return x + (t - acc)
            acc += y - x
        return keeps[-1][1]
    wt = sorted(w['t'] for w in words)
    after_pause, mid, bad = 0, 0, []
    for ln in lines:
        s0 = out2src(ln[1])
        cand = [(abs(g1 - s0), g0, g1) for g0, g1 in gaps10 if abs(g1 - s0) <= 0.8]
        if cand:
            _, g0, g1 = min(cand)
            between = [t for t in wt if g1 - 0.20 < t < s0 - 0.05]
            if not between:
                after_pause += 1
                err = abs(s0 - (g1 - lead))
                if err > 0.15:
                    bad.append((err, ln[0], ln[1]))
                continue
        mid += 1
    print('[时轴质检] 停顿后起句 %d 行（吸附到真实起音，误差>0.15s: %d）｜句中断行 %d 行'
          % (after_pause, len(bad), mid))
    for e, t, st in sorted(bad, reverse=True)[:5]:
        print('    %.2fs  误差%.3f  %s' % (st, e, t))


def snap_onsets(lines, gaps10, wstarts, lead=0.08, win=0.45):
    """行首若落在停顿附近，就吸附到真实起音点，并留出 lead 秒提前量。
    行首处于连续语音中间的（前面没停顿）不动——那种情况 ASR 时间本来就更准。"""
    moved = 0
    for ln in lines:
        cand = [(abs(g1 - ln[1]), g0, g1) for g0, g1 in gaps10 if abs(g1 - ln[1]) <= win]
        if not cand:
            continue
        _, g0, g1 = min(cand)
        if any(g1 - 0.20 < w < ln[1] - 0.05 for w in wstarts):
            continue   # 停顿后已经有别的词 -> 本行是句中断行，不吸附
        new = max(g0 + 0.02, g1 - lead)
        if abs(new - ln[1]) > 0.005:
            moved += 1
        ln[1] = new
    print('[吸附] 起点吸附到真实起音点: %d / %d 行' % (moved, len(lines)))
    return lines


# ---------- 分词词典：唯一来源 ----------
# dp_split **本来就只在词边界断行**，所以「词被拆开」的根因永远是分词器的边界错了，
# 不是罚分不够。470 期报过的 11 处断行缺陷里有 9 处属于这一类，而
#   科创板 / 创业板 / 北交所 / 小微盘 / 中小板
# 一个都不在 DOMAIN_WORDS 里——「沪深300」「中证500」倒是各重复了两遍。
# 手工维护的词表就是这个下场：报一个补一个，还补重。
#
# 现在把散在四处的专名清单合成**一个来源**，全部灌进分词器：
#   DOMAIN_WORDS（人工整理的行话）+ PROPER_NOUNS（完整专名）
#   + BOOK_TITLES（书名）+ NAME_PREFIX × 常见数字（自动展开指数代号）
INDEX_NUMS = ('50', '100', '300', '500', '800', '1000', '2000', '3000')
BOARD_WORDS = ('科创板', '创业板', '北交所', '新三板', '中小板', '主板',
               # 产品/账户名。9.2 高净值那期「月薪宝」被拆成「月薪|宝」，
               # 又是词典缺词——新题材就会带来新专名，这里是最该先补的地方。
               '月薪宝', '个人养老金账户', '个人养老金', '养老金账户',
               '信贷部门', '百万医疗险', '重疾险',
               # 471 期（房地产）新增
               '普涨行情', '普涨牛市', '普涨', '结构性行情', '结构性牛市',
               '资产配置', '中产家庭', '非标准化', '落户口', '房产税',
               'REITs', '指数基金', '公募REITs',
               '定期寿险', '意外险', '教育金', '应纳税所得额', '汇算清缴',
               '速算扣除数', '税前扣除', '固收加',
               '小微盘', '大盘股', '中盘股', '小盘股', '微盘股',
               '宽基', '窄基', '每个月', '每个季度', '每一年')


def domain_lexicon():
    """要喂给分词器的全部专名。加词时**只改这里**。"""
    words = set(DOMAIN_WORDS) | set(PROPER_NOUNS) | set(BOARD_WORDS)
    words |= {'《%s》' % b for b in BOOK_TITLES} | set(BOOK_TITLES)
    for pre in NAME_PREFIX:                    # 中证500 / 北证50 / 科创50 …
        for n in INDEX_NUMS:
            words.add(pre + n)
    return {w for w in words if len(w) > 1}


_LEX_LOADED = False


def ensure_lexicon():
    """把领域词典灌进 jieba。幂等，重复调用无副作用。

    ⚠️ 必须在**任何**用到分词的步骤之前调用。471 期栽过：词典原先只在
    split_lines 里加载，而 restore_dropped 跑在它前面——补词时 jieba 还不认识
    「普涨行情」，于是把「然后」插进了「普涨|行情」中间。
    """
    global _LEX_LOADED
    if _LEX_LOADED:
        return
    import jieba
    for w in domain_lexicon():
        jieba.add_word(w, freq=100000)
    _LEX_LOADED = True


def crossing_word(a, b, win=14):
    """行尾 a 与行首 b 之间的断点，是否劈开了一个词？劈开了就返回那个词。

    把两行拼起来重新分词，看有没有 token 跨过断点。**不需要枚举任何词表**——
    词典配齐后，这一条检查就覆盖了「词被拆开」的全部形态，
    包括将来出现的、现在还不知道的那些。
    """
    import jieba
    L, R = a[-win:], b[:win]
    pos = 0
    # HMM=False：只认词典里的词。开着 HMM 时 jieba 会把跨小句的相邻字
    # 硬粘成「高有」「亿加」「万有」这种非词，7 处报告全是这么来的。
    for t in jieba.cut(L + R, HMM=False):
        st, en = pos, pos + len(t)
        pos = en
        if st < len(L) < en and len(t) > 1:
            return t
    return None


_SIL = None


def build_silences(words):
    """从 ASR 词边界提取真实静音区间，供断行判断使用。"""
    global _SIL
    out = []
    for k in range(len(words) - 1):
        e = words[k]['t'] + words[k]['d']
        st = words[k+1]['t']
        if st - e > 0.06:
            out.append((e, st, st - e))
    _SIL = sorted(out)


def real_pause(t, tol=0.35):
    """t 时刻附近有多长的**真实**停顿。

    ⚠️ 不要用 times[i][0] - times[i-1][1] 算停顿。times 里只有 76% 的字符是
    锚定的，其余靠线性插值填出来——**插值区间内相邻字的间隔恒为 0**。
    实测：字符级间隔里只有 2.1% 大于 0.01s，而 ASR 词级间隔有 9.6% 大于 0.15s。
    也就是说「在停顿处断行」这个奖励，过去大部分时候根本没被触发，
    而停顿恰恰是人断句最主要的依据。
    """
    if not _SIL:
        return 0.0
    import bisect
    i = bisect.bisect_left(_SIL, (t - tol, 0.0, 0.0))
    best = 0.0
    for k in range(max(0, i - 2), min(len(_SIL), i + 3)):
        a, b, dur = _SIL[k]
        if a - tol <= t <= b + tol:
            best = max(best, dur)
    return best


# ---------- 人工断行修正 ----------
# 程序先切、人读一遍改。改动记在 断行修正.txt 里，一行一条：
#     旧行1 / 旧行2 => 新行1 / 新行2
# 只动断点，**不许动字**——两边拼起来必须逐字相同，否则报错中止。
# 这条约束很重要：它保证复核只能调整节奏，不会悄悄改内容。
#
# 为什么要有这个文件，而不是每次让人直接改字幕：
#   · 程序的产出仍然是可复现的基准，改动是显式的、可 diff 的、可累积的
#   · 改动攒多了能看出规律——同一种毛病反复出现，就该回去修规则，而不是每期手改
BREAK_FIXES = []


def load_break_fixes(path):
    """读断行修正文件。每行 `旧行1 / 旧行2 => 新行1 / 新行2`，# 开头是注释。"""
    out = []
    for ln in open(path, encoding='utf-8'):
        ln = ln.rstrip('\n')
        if not ln.strip() or ln.lstrip().startswith('#'):
            continue
        if '=>' not in ln:
            raise SystemExit('断行修正格式错误（缺 =>）：%s' % ln)
        a, b = ln.split('=>', 1)
        # ⚠️ 用「空格斜杠空格」分隔，不能用裸的 /——字幕里有「房产1/4」这类内容，
        #    裸 / 会把它劈成两行。471 期写第一份修正文件时就撞上了。
        old = [x.strip() for x in a.split(' / ') if x.strip()]
        new = [x.strip() for x in b.split(' / ') if x.strip()]
        # 允许增删「、，」——行尾的顿号在断行时本来就会被丢掉（行尾不显示「、」），
        # 把两行并回一行时需要把它加回来。**除标点外一个字都不许改。**
        strip = lambda x: re.sub(r'[、，]', '', x)
        if strip(''.join(old)) != strip(''.join(new)):
            raise SystemExit('断行修正改动了文字（只允许改断点与顿号逗号）：\n  旧 %s\n  新 %s'
                             % (''.join(old), ''.join(new)))
        out.append((old, new))
    return out


def apply_break_fixes(merged):
    """把人工的断行修正应用到已合并的行上。下标随文字一起重新分配。"""
    if not BREAK_FIXES:
        return merged
    hit = 0
    for old, new in BREAK_FIXES:
        for i in range(len(merged) - len(old) + 1):
            if [merged[i+k][0] for k in range(len(old))] != old:
                continue
            idxs = []
            for k in range(len(old)):
                idxs += merged[i+k][1]
            tail = merged[i+len(old)-1]          # 沿用最后一段的断句/分隔属性
            # 下标按各行**字数占比**分配。新文字可能比原来多出标点（没有对应的
            # 源字符），所以不能按 len 直接切，否则最后一行会分不到下标。
            rep, tot = [], sum(len(x) for x in new)
            pos = 0
            for j, t in enumerate(new):
                take = len(idxs) if j == len(new)-1 else max(
                    1, round(len(idxs) * len(t) / max(1, tot)))
                seg = idxs[pos:pos+take] if j < len(new)-1 else idxs[pos:]
                if not seg:                       # 兜底，保证每行至少有一个下标
                    seg = [idxs[min(pos, len(idxs)-1)]]
                rep.append([t, seg,
                            tail[2] if j == len(new)-1 else False,
                            tail[3] if j == len(new)-1 else '',
                            merged[i][4] if j == 0 else False])
                pos += len(seg)
            merged[i:i+len(old)] = rep
            hit += 1
            break
    miss = len(BREAK_FIXES) - hit
    print('[断行修正] 应用 %d 条%s' % (hit, '，%d 条未命中（行可能已变）' % miss if miss else ''))
    return merged


def split_lines(script, times, max_em=11.5, gaps10=None):
    """按标点切句 -> 超宽的在 jieba 词边界上均分 -> 相邻短句合并"""
    import jieba
    import jieba.posseg as pseg
    build_silences(ASR_WORDS)
    ensure_lexicon()
    # 1) 按标点切成 clause，并记录该 clause 后面是不是强断句
    clauses, cur, curi = [], '', []
    strong_after, sep_after = [], []
    for i, c in enumerate(script):
        if c in BREAK_STRONG or c in BREAK_WEAK:
            if cur:
                clauses.append((cur, curi))
                strong_after.append(c in BREAK_STRONG)
                sep_after.append(c)
            cur, curi = '', []
        else:
            cur += c; curi.append(i)
    if cur:
        clauses.append((cur, curi)); strong_after.append(True); sep_after.append('')

    def width(t):
        return sum(char_width(x) for x in t)

    # 2) 超宽 clause 在词边界均分
    pieces = []   # (text, idxs, strongAfter, sepAfter)
    for (text, idxs), st, sp in zip(clauses, strong_after, sep_after):
        W = width(text)
        if W <= min(max_em, SPLIT_EM):
            pieces.append((text, idxs, st, sp, True)); continue
        toks = merge_books([t for t in jieba.cut(text) if t])
        widths = [width(t) for t in toks]
        # 单个 token 就超宽时退化为按字切——但**书名不许退化**：
        # 470 期人工复核要求「遇到书名需完整在一行里，可以超出上限」。
        # 《红利指数基金投资指南》连书名号 12.0 em，本来就放不进 11.5 的上限，
        # 退化按字切的结果是「咱们《红利 / 指数基金投资 / 指南》封面上的」，
        # 书名号孤零零留在行尾。
        if max(widths) > max_em and not any(is_book(t) for t in toks):
            toks = list(text); widths = [width(c) for c in toks]
        pos0 = [0]
        for t in toks:
            pos0.append(pos0[-1] + len(t))
        gaps = []
        for i in range(len(toks)):
            if i == 0:
                gaps.append(0.0)
            else:
                gi = idxs[pos0[i]]
                gaps.append(real_pause(times[gi][0]))
        # 词性逐 token 单独标注：pseg 对整句的切分与这里的 toks 未必一致，
        # 逐个标能保证下标严格对齐（代价是丢一点上下文，实测影响不大）。
        tags = [next(iter(pseg.cut(t)), None) for t in toks]
        tags = [x.flag if x else 'x' for x in tags]
        cuts = dp_split(toks, widths, min(max_em, SPLIT_EM), target=DP_TARGET, gaps=gaps, tags=tags) \
            or dp_split(toks, widths, max_em, target=DP_TARGET, gaps=gaps, tags=tags)
        parts = []
        pos = [0]
        for t in toks:
            pos.append(pos[-1] + len(t))
        for (i, j) in cuts:
            seg = ''.join(toks[i:j])
            parts.append((seg, idxs[pos[i]:pos[j]]))
        parts = fix_leading(parts, max_em)
        for j, (t, ii) in enumerate(parts):
            last = j == len(parts)-1
            pieces.append((t, ii, st if last else False, sp if last else '', j == 0))

    # 3) 相邻合并（不跨强断句），合并后不超宽
    merged = []
    for k, (t, ii, st, sp, cs) in enumerate(pieces):
        if merged and not merged[-1][2]:
            glue = '、' if merged[-1][3] == '、' else ''
            # 前一小句末字 == 后一小句首字时，裸接会读成叠词：
            # 「又出来了创业板」+「创业板呢」→「又出来了创业板创业板呢」。
            # 脚本原文是带逗号的，是合并把标点吃掉才产生的。把标点还回去。
            # 470 期人工复核在 02/04/10/14 四条上都指了出来。
            # 重复单元不一定只有一个字：「科创板上市」+「上市之后呢」是两个字，
            # 「像这个消费」+「消费的话呢」也是。按 4→1 字逐级试。
            if not glue and merged[-1][3]:
                for k in (4, 3, 2, 1):
                    if len(t) >= k and merged[-1][0].endswith(t[:k]):
                        glue = merged[-1][3]
                        break
            if (width(merged[-1][0] + glue + t) <= MERGE_MAX_EM
                    or ('《' in t and '》' in t and not merged[-1][0].strip())):
                # ⚠️ 前瞻一步：单字枚举不许断在中途。
                # 「大、中、小、微」贪心合并到「…大、中、小」正好 10em，再加「、微…」超宽，
                # 于是「微」被甩到下一行，屏幕上读成**「微不同的指数」**——语义全变了。
                # 上一轮这组词是被「删单字残句」删成「大、微」，这一轮是被拆，同一处栽两次。
                # 所以：本段是单字枚举项、后面还接着顿号时，要求下一段也放得下才合并。
                nxt = pieces[k+1] if k + 1 < len(pieces) else None
                strand = (sp == '、' and len(t) == 1 and nxt is not None
                          and width(merged[-1][0] + glue + t + '、' + nxt[0]) > max_em)
                if not strand:
                    merged[-1] = [merged[-1][0] + glue + t, merged[-1][1] + ii,
                                  st, sp, merged[-1][4]]
                    continue
        merged.append([t, ii, st, sp, cs])

    merged = apply_break_fixes(merged)

    # 第 5 个字段 clause_start：这一行是不是从一个新小句（标点之后）开始。
    # 质检要用它——小句开头出现虚词是正常的（「…资产量呢，得相对的高一些」），
    # 跟「句中被拆出个光杆虚词」是两回事，不能一样对待。
    lines = [[t, times[ii[0]][0], times[ii[-1]][1], cs] for t, ii, _, _, cs in merged if t]

    if gaps10:
        lines = snap_onsets(lines, gaps10, sorted(w["t"] for w in ASR_WORDS))
        lines.sort(key=lambda x: x[1])
        globals()['_QC'] = (lines, gaps10)

    # 4) 空隙不超过 2 秒
    for i in range(len(lines)-1):
        gap = lines[i+1][1] - lines[i][2]
        if 0 < gap <= 2.0:
            lines[i][2] = lines[i+1][1] - 0.02
    # 5) 最短显示时长
    for ln in lines:
        if ln[2] - ln[1] < 0.5:
            ln[2] = ln[1] + 0.5
    return lines


PPT_RED = {'#f54a45'}          # 飞书标准红；标红区间 = 播 PPT


def load_rich(path, unstrike=()):
    """读 cells-get 的富文本，返回 (清洗后文本, 删除线掩码, PPT掩码)

    ⚠️ 整格样式统一时飞书**不返回 rich_text**，只给 cell_styles——
       必须回退处理，否则整格标红/整格删除线的脚本会被漏掉。
    """
    d = json.load(open(path))
    cell = d['data']['ranges'][0]['cells'][0][0]
    runs = cell.get('rich_text')
    if not runs:
        st = cell.get('cell_styles') or {}
        runs = [{'text': cell.get('value') or '', 'style': st}]
        print('  [提示] 该格样式统一，无 rich_text，按 cell_styles 整体处理：%s'
              % {k: v for k, v in st.items() if k in ('font_color', 'font_line')})
    _APPLIED.clear()
    text, strike, ppt = '', [], []
    for r in runs:
        t = clean_script(r.get('text', ''))
        st = r.get('style', {}) or {}
        is_strike = st.get('font_line') == 'line-through'
        is_ppt = str(st.get('font_color', '')).lower() in PPT_RED
        text += t
        strike += [is_strike]*len(t)
        ppt += [is_ppt]*len(t)
    for frag in unstrike:
        f = clean_script(frag)
        i = text.find(f)
        if i < 0:
            print('  [警告] 保留片段未匹配到：%s' % frag)
            continue
        for k in range(i, i+len(f)):
            strike[k] = False
        print('  [覆盖] 恢复保留：%s' % f)
    missed = [a for a, _ in MANUAL_FIXES if a not in _APPLIED]
    if missed:
        print('  [警告] %d 条人工修正未命中（脚本可能已改，或跨越了样式分段）：%s'
              % (len(missed), '、'.join(missed)))
    return text, strike, ppt


# ---------- 补回被转写「口语顺滑」删掉的词 ----------
# 飞书妙记转写时会做口语顺滑，把话语标记直接删掉。470 期 02 号一条片子实测：
#   本地逐字 ASR：然后 16 次 / 这个 15 次 / 那 10 次
#   脚本（=妙记稿）：然后  0 次 / 这个  9 次 / 那  2 次
# 脚本是从妙记稿来的，于是字幕里没有这些词，而音频里听得见——音画不符。
# 人工复核一眼就发现了（三条片子上都指了出来），dropcheck 也每一处都找到了，
# 却按「话语标记」自动忽略，等于查到了又扔掉。
#
# 补回来不只是修观感，**更是修根因**：脚本缺字 → 对齐锚点少 → 掐头掐尾容易切错。
# 03 的「北证50」被切掉，正是因为它没能锚上。
RESTORE_WORDS = ['然后', '这个', '那个', '其实', '就是', '的话', '一个',
                 '咱们', '大家', '那么', '那']


def bad_insert_point(script, pos, word):
    """这个位置根本不可能是漏词点——多半是映射误差。

    两种情况：
      1. 插进去会把已有的词劈开：「中⟨这个⟩证全指」——「中证全指」是一个词
      2. 插进去语法不通：「市场上⟨这个⟩的钱」——「这个」后面不能直接跟「的/了/呢」
    ASR 错字多，映射到脚本的位置本来就有偏差，这两条能滤掉大部分假阳性。

    **dropcheck 和 restore_dropped 共用这一份**——书名号那条规则当初就是因为
    存在第二份副本，修了一处另一处照样拦人，别再犯。
    """
    if script[pos:pos+1] in '的了呢吗吧地得':
        return True, '后接助词，插入不成句'
    try:
        import jieba
    except ImportError:
        return False, ''
    acc, bounds = 0, {0}
    for t in jieba.cut(script[max(0, pos-20):pos+20]):
        acc += len(t)
        bounds.add(acc)
    if (pos - max(0, pos-20)) not in bounds:
        return True, '落在词内部，会把已有的词劈开'
    # 落在两个相邻实词之间也不行——那是一个偏正词组，插进去等于把它劈开。
    # 471 期实测两处：「一个体量下」被插成「一个体量⟨这个⟩下」，
    # 「一个普涨行情」被插成「一个普涨⟨然后⟩行情」。
    # 这和断行把词组切开是同一件事的两面，所以复用同一个判据（compound_break）。
    try:
        import jieba.posseg as pseg
        left = script[max(0, pos-8):pos]
        right = script[pos:pos+8]
        lt = list(pseg.cut(left))
        rt = list(pseg.cut(right))
        if lt and rt and bad_insert_pair(lt[-1].flag, rt[0].flag):
            return True, '落在词组内部（%s|%s），插进去会劈开词组' % (lt[-1].word, rt[0].word)
    except Exception:
        pass
    return False, ''


def restore_dropped(script, mask, pptmask, asr_chars, verbose=True):
    """把 ASR 听见、脚本里却没有的话语标记补回脚本。

    返回 (script, mask, pptmask)。掩码必须跟着一起插——它们是按字符下标存的，
    只改文本不改掩码，删除线和 PPT 标红区间会整体错位。

    **定位靠全局对齐，不要用局部搜索。** 被顺滑删掉的词，在「ASR → 脚本」的
    diff 里天然表现为 `delete` 块（ASR 有、脚本没有），落点由整体对齐决定，
    唯一且一致。第一版我用「取 10 字探针在全篇找最长匹配」，4 字即命中，
    脚本里重复短语一多就乱落点——**对齐相似度不升反降，当场证伪。**
    相似度是这一步的自检指标：补的是真词，它必升（数学上 T > M 恒成立）。
    """
    import difflib
    ensure_lexicon()
    asr = ''.join(c for c, _, _ in asr_chars)
    sm = difflib.SequenceMatcher(a=asr, b=script, autojunk=False)
    # 只认 delete 块太保守：ASR 错字多，漏词多半被卷进 replace 块里，
    # 02 号实测缺 16 个「然后」，纯 delete 只能捞到 4 个。
    # 所以改成：**用 equal 块建全局位置映射**（可靠），再逐处看脚本该位置有没有这个词。
    blocks = [b for b in sm.get_matching_blocks() if b.size >= 3]
    if not blocks:
        return script, mask, pptmask

    def map_pos(i):
        """ASR 下标 -> 脚本下标。块内精确，块间线性插值。"""
        prev = None
        for b in blocks:
            if b.a <= i < b.a + b.size:
                return b.b + (i - b.a)
            if b.a + b.size <= i:
                prev = b
                continue
            if prev is None:
                return max(0, b.b - (b.a - i))
            ga, gb = prev.a + prev.size, b.a
            ja, jb = prev.b + prev.size, b.b
            return ja if gb <= ga else int(ja + (jb - ja) * (i - ga) / (gb - ga))
        return min(len(script), prev.b + prev.size + (i - prev.a - prev.size))

    ins, seen = [], set()
    for w in RESTORE_WORDS:
        for m in re.finditer(re.escape(w), asr):
            p = max(0, min(len(script), map_pos(m.start())))
            # ⚠️ 脚本有标点、ASR 没有，映射落点会**系统性地卡在标点之前**：
            #    得到「是主板的股票⟨然后⟩。后来呢」，而实际是「…股票。然后后来呢」。
            #    话语标记领起下一个小句，所以要跨过标点再插。
            while p < len(script) and script[p] in '，。！？、；：':
                p += 1
            # 脚本这一带本来就有这个词，就不是被删的那一处
            if w in script[max(0, p-3):p+len(w)+3]:
                continue
            if p in seen or bad_insert_point(script, p, w)[0]:
                continue
            if any(script[:p].endswith(l) and script[p:].startswith(r) for l, r in NO_RESTORE):
                continue
            seen.add(p)
            ins.append((p, w))
    if not ins:
        return script, mask, pptmask
    for pos, w in sorted(ins, reverse=True):       # 从后往前插，下标才不会失效
        ref = pos - 1 if pos > 0 else 0            # 掩码沿用左邻字符
        script = script[:pos] + w + script[pos:]
        mv = mask[ref] if mask else False
        pv = pptmask[ref] if pptmask else False
        mask[pos:pos] = [mv]*len(w)
        pptmask[pos:pos] = [pv]*len(w)
    if verbose:
        cnt = {}
        for _, w in ins:
            cnt[w] = cnt.get(w, 0) + 1
        print('[补词] 转写顺滑删掉的话语标记，补回 %d 处：%s'
              % (len(ins), '　'.join('%s×%d' % kv for kv in
                                     sorted(cnt.items(), key=lambda x: -x[1]))))
    return script, mask, pptmask


def mask_ranges(mask, times):
    """掩码 -> 时间区间列表"""
    out, i = [], 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j+1 < len(mask) and mask[j+1]:
                j += 1
            out.append((times[i][0], times[j][1]))
            i = j+1
        else:
            i += 1
    return out


def snap_one(t, gaps, limit=1.2):
    """把单个时间点吸附到最近的静音间隙中心"""
    cand = [(abs((g0+g1)/2 - t), (g0+g1)/2) for g0, g1 in gaps if abs((g0+g1)/2 - t) <= limit]
    return min(cand)[1] if cand else t


def snap_cuts(cuts, gaps, limit=1.2):
    """把剪切区间的端点吸附到最近的静音间隙，避免切在字上"""
    out = []
    for a, b in cuts:
        best_a = a
        for g0, g1 in gaps:
            if abs((g0+g1)/2 - a) < abs(best_a - a) or (best_a == a and abs((g0+g1)/2 - a) <= limit):
                if abs((g0+g1)/2 - a) <= limit:
                    best_a = (g0+g1)/2
        best_b = b
        cand = [(abs((g0+g1)/2 - b), (g0+g1)/2) for g0, g1 in gaps if abs((g0+g1)/2 - b) <= limit]
        if cand:
            best_b = min(cand)[1]
        cand_a = [(abs((g0+g1)/2 - a), (g0+g1)/2) for g0, g1 in gaps if abs((g0+g1)/2 - a) <= limit]
        if cand_a:
            best_a = min(cand_a)[1]
        out.append((best_a, best_b))
    return out


FLASH_MIN = 0.30        # 短于这个时长的行肉眼读不出来
FLASH_RATE = 0.16       # 重分配窗口至少要有这么多秒/字（约 6 字/秒，口播正常语速）


def rebalance_flashes(lines):
    """一闪而过的行：和相邻行合成一个窗口，按字数重分时间。

    474 期 4 行只显示 0.03~0.11 秒：脚本里有、识别器没听出来的字没有时间锚，
    被挤在前后两个锚点之间（「超长期国债，超长期国债」第一遍被识别成「操场」；
    「最长熊市了」所在的 4 秒识别为空）。
    窗口首尾时刻不动，所以不影响整体同步，只是窗口内按字数摊匀。
    """
    L = [list(x) for x in lines]
    for i in range(len(L)):
        if L[i][2] - L[i][1] >= FLASH_MIN:
            continue
        a = b = i
        while True:
            span = L[b][2] - L[a][1]
            chars = sum(len(L[k][0]) for k in range(a, b+1))
            if span >= chars * FLASH_RATE or b - a >= 6:
                break
            # 往时长富余的那一侧扩
            left = L[a-1][2] - L[a-1][1] if a > 0 else -1
            right = L[b+1][2] - L[b+1][1] if b + 1 < len(L) else -1
            if left < 0 and right < 0:
                break
            if left >= right:
                a -= 1
            else:
                b += 1
        t0, t1 = L[a][1], L[b][2]
        chars = sum(len(L[k][0]) for k in range(a, b+1))
        cur = t0
        for k in range(a, b+1):
            d = (t1 - t0) * len(L[k][0]) / chars
            L[k][1], L[k][2] = cur, cur + d - (0.02 if k < b else 0)
            cur += d
        print('  [时间摊匀] 第 %d-%d 行（%s…）按字数重分 %.2f-%.2f 秒'
              % (a+1, b+1, L[a][0][:8], t0, t1))
    return [tuple(x) for x in L]


def main():
    asr = json.load(open(sys.argv[1]))
    globals()["ASR_WORDS"] = asr["words"]
    unstrike = []
    if len(sys.argv) > 4:
        unstrike = [l.strip() for l in open(sys.argv[4]) if l.strip()]
    if '--fixes' in sys.argv:
        f = sys.argv[sys.argv.index('--fixes')+1]
        if os.path.exists(f):
            globals()['MANUAL_FIXES'] = load_fixes(f)
            print('人工文本修正：载入 %d 条' % len(MANUAL_FIXES))
    if '--break-fixes' in sys.argv:
        f = sys.argv[sys.argv.index('--break-fixes')+1]
        if os.path.exists(f):
            globals()['BREAK_FIXES'] = load_break_fixes(f)
            print('人工断行修正：载入 %d 条' % len(BREAK_FIXES))
    script, mask, pptmask = load_rich(sys.argv[2], unstrike)
    out_json = sys.argv[3]

    asr_chars = expand_words(asr['words'])
    # 补回转写「口语顺滑」删掉的话语标记。默认开启——这是固定流程的一步，
    # 不是可选优化：不补，字幕就和音频对不上（人工复核在三条片子上都指了出来）。
    if '--no-restore' not in sys.argv:
        script, mask, pptmask = restore_dropped(script, mask, pptmask, asr_chars)
    times, ratio, anchored = align(script, asr_chars)

    # 1) 删除线区间 -> 源片剪切区间
    cuts = []
    i = 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j+1 < len(mask) and mask[j+1]:
                j += 1
            cuts.append((times[i][0], times[j][1]))
            i = j+1
        else:
            i += 1
    cuts = snap_cuts(cuts, asr['gaps'])
    dur = asr['duration']

    # 长静音剪除（复核意见：2分43-2分46 存在较长时间空白，可以剪掉）。
    # 只剪超过阈值的部分，留 LEAVE 秒作为自然停顿——全剪光会显得话赶话。
    MAXGAP, LEAVE, BRIDGE = 1.2, 0.45, 0.5
    if '--trim-gaps' in sys.argv:
        # 相邻停顿之间只夹着极短的声音（一次吸气、一个没说完的字）时，
        # 实际观感是一整段空白，要合起来算。05 那处 2.8 秒空白就是被一个
        # 孤立的「很」劈成 1.50s + 0.55s，两段都没够到阈值而漏掉。
        def has_word(a, b):
            """两段静音之间是不是真有话。

            ⚠️ 必须按**重叠**判，不能按「完全落在区间内」判——
            ASR 词时间戳有 ±0.2s 抖动（见项目文档 6.4），词经常跨在静音边界上。
            05 期「然后」t=43.03 d=0.62 跨过了间隙起点 43.12，按包含判就成了「无词」，
            于是两段静音被桥起来，把这个词整个剪掉了。
            """
            return any(w['t'] < b and w['t'] + w['d'] > a and w['d'] > 0.3
                       for w in asr['words'])

        def clip_to_silence(a, b, margin=0.08):
            """把候选剪切区间收缩到不碰任何 ASR 词。

            这是最后一道保险：不管间隙是怎么算出来的，只要区间里有词就往回缩。
            剪掉半个字比留一段空白难看得多。
            """
            lo, hi = a, b
            for w in asr['words']:
                ws, we = w['t'] - margin, w['t'] + w['d'] + margin
                if we <= lo or ws >= hi:
                    continue
                if ws - lo >= hi - we:      # 词偏右，砍掉右半
                    hi = ws
                else:                        # 词偏左，砍掉左半
                    lo = we
            return lo, hi

        gs = []
        for g0, g1 in sorted(asr['gaps']):
            # 桥接只为「一次吸气、一个没说完的字」设计。08 期把两段静音之间的
            # 「比如说」（0.63s 的完整短语）一并桥掉剪了——所以先查词表。
            if gs and g0 - gs[-1][1] < BRIDGE and not has_word(gs[-1][1], g0):
                gs[-1] = (gs[-1][0], g1)
            else:
                gs.append((g0, g1))
        for g0, g1 in gs:
            if g1 - g0 > MAXGAP:
                a, b = clip_to_silence(g0 + LEAVE, g1 - 0.1)
                if b - a > 0.3:
                    cuts.append((round(a, 3), round(b, 3)))
        cuts = sorted(c for c in cuts if c[1] - c[0] > 0.15)
        merged = []
        for a, b in cuts:      # 与删除线剪切区间可能重叠，合并
            if merged and a <= merged[-1][1] + 0.02:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        cuts = merged

    # 1b) --trim-ends：切片是粗定位多留了头尾的，用对齐结果把多余部分当作剪切区间。
    #     定位精度因此等同于字幕的时间精度，不必再单独跑窗口 ASR。
    #     必须放在 snap_cuts 之后：头切的 0.0 / 尾切的 dur 一旦被吸附到静音中心就切不干净了，
    #     只有内侧端点需要吸附。
    if '--trim-ends' in sys.argv:
        # ⚠️ 不能直接用 times[0]：脚本首字可能被**孤立的错误匹配**锚到片头
        # （切片头部多留的 6 秒里有上一句，里面碰巧有相同的字）。
        # 08 就是这么在片头多带出一整句话的。改用「连续 4 个字都对上」的第一处。
        def anchor_run(idxs, n=4):
            a = sorted(idxs)
            for k in range(len(a) - n + 1):
                if a[k+n-1] - a[k] == n - 1:
                    return a[k]
            return a[0] if a else 0

        first = anchor_run(anchored)
        last = len(script) - 1 - anchor_run({len(script)-1-i for i in anchored})
        head = snap_one(max(0.0, times[first][0] - 0.30), asr['gaps'])
        # ⚠️ 掐头下限。与掐尾是同一个病：首句被 ASR 听岔就凑不出 4 连锚点，
        #    anchor_run 退到后面某个对上的字，掐头就切进了第一句话。
        #    470 期 03「北证50」被听成「北京五零」→ 前 4 个字连音带字被切掉；
        #    07「有朋友问，」被听成「有盼头」→ 前 5 个字被切掉。人工复核都指了出来。
        #    上一轮我写「头部不做对称处理，那里的失效方向是多带一整句」——**这个理由不成立**，
        #    片头两个方向都会失效。anchor_run 防的是多带，这里防的是切掉。
        #    脚本说 first 之前那些字存在，就得按语速给它们留出时间。
        if first > 0 and last > first:
            rate = (times[last][1] - times[first][0]) / (last - first)   # 秒/字
            back = min(first * rate * 1.3, 4.0)
            head = snap_one(max(0.0, times[first][0] - 0.30 - back), asr['gaps'])
        tail = snap_one(min(dur, times[last][1] + 0.35), asr['gaps'], limit=0.6)
        # ⚠️ 掐尾下限。anchor_run 要求「连续 4 个字对上」，末句被 ASR 听岔时凑不出
        #    4 连——470 期 04「北证50是小微盘偏成长」被听成「北京五零是小薇盘篇成长」，
        #    last 退到末句之前，整句收尾连音带字被当片尾杂音剪掉（11 号同理丢 9 字）。
        #    所以再压一道下限：**任何锚定上的脚本字都不许落进掐尾区间**。
        #    头部不做对称处理——那里的失效方向是「多带一整句」，anchor_run 正是为此加的。
        if anchored:
            tail = max(tail, min(dur, times[max(anchored)][1] + 0.35))
        if head > 0.05:
            cuts.insert(0, (0.0, head))
        if tail < dur - 0.05:
            cuts.append((tail, dur))
        print('首尾定位: 掐头 %.2fs  掐尾 %.2fs  正片 %.2f-%.2f' % (head, dur-tail, head, tail))

    # ⚠️ 下面的 keeps 循环用 `pos = max(pos, b)` 推进，**要求 cuts 按起点升序**。
    #    掐尾是 append 到末尾的，若前面还有起点更晚的静音剪切，`a > pos` 判定为假，
    #    整个掐尾区间被静默吞掉——470 期 08 号掐尾 215.66，保留区间却到 226.45，
    #    片尾多出 7.19s 没有字幕的说话画面。02/06/07/08/10/15/16 七条都中招。
    cuts.sort()

    # 2) 保留区间
    keeps, pos = [], 0.0
    for a, b in cuts:
        if a > pos:
            keeps.append((pos, a))
        pos = max(pos, b)
    if pos < dur:
        keeps.append((pos, dur))

    def src2out(t):
        """源片时间 -> 成片时间"""
        acc = 0.0
        for a, b in keeps:
            if t < a:
                return acc
            if t <= b:
                return acc + (t - a)
            acc += b - a
        return acc

    # 3) 字幕：只保留未删除的字
    # 质检：连续无锚点区段（时间全靠插值 → 字幕可能漂移）
    runs, cur = [], None
    for i in range(len(script)):
        if i not in anchored:
            cur = i if cur is None else cur
        else:
            if cur is not None and i - cur >= 12:
                runs.append((cur, i-1))
            cur = None
    if cur is not None and len(script) - cur >= 12:
        runs.append((cur, len(script)-1))
    if runs:
        print('[质检] 无锚点长区段 %d 处（时间靠插值，可能漂移）:' % len(runs))
        for a, b in runs:
            print('   %.2f-%.2fs  %d字  %s' % (times[a][0], times[b][1], b-a+1, script[a:b+1][:30]))
    else:
        print('[质检] 无锚点长区段: 无')

    kept_idx = [i for i in range(len(script)) if not mask[i]]
    sub_script = ''.join(script[i] for i in kept_idx)
    sub_times = [times[i] for i in kept_idx]
    lines = split_lines(sub_script, sub_times, gaps10=asr.get('gaps10'))
    # 时间映射到成片
    lines = [[t, src2out(s), src2out(e), cs] for t, s, e, cs in lines]
    lines = [l for l in lines if l[2] - l[1] > 0.15]
    # 两行落到**同一个成片起始时刻**：470 期 02 行144/145 同为 261.53（恰好是 PPT 段边界）、
    # 05 行1/2 同为 0.98。渲染器取第一条匹配行，画面上不叠字，肉眼和抽帧都看不出来——
    # 但后一行等于永远不显示，是时间轴数据坏了。
    # ⚠️ 必须放在 src2out **之后**：源片上两行是分开的，是剪切换算把它们压到了一起，
    #    在 split_lines 里去重是修不掉的（我第一次就修错了地方）。
    for i in range(1, len(lines)):
        if lines[i][1] - lines[i-1][1] < 0.05:
            lines[i][1] = lines[i-1][1] + 0.05
        if lines[i-1][2] > lines[i][1]:
            lines[i-1][2] = lines[i][1] - 0.02
        if lines[i][2] < lines[i][1] + 0.10:
            lines[i][2] = lines[i][1] + 0.10

    if asr.get('gaps10'):
        qc_timing(lines, asr['gaps10'], asr['words'], keeps)

    out_dur = sum(b-a for a, b in keeps)
    print('脚本字数=%d（删除线 %d 字）  ASR字数=%d  对齐相似度=%.3f' %
          (len(script), sum(mask), len(asr_chars), ratio))
    strike_spans = mask_ranges(mask, times)

    def cut_kind(a, b):
        if a <= 0.01:
            return '掐头'
        if b >= dur - 0.05:
            return '掐尾'
        for x, y in strike_spans:
            if min(b, y) - max(a, x) > 0.05:
                return '删除线'
        return '静音'

    print('剪切区间（源片秒）:')
    for a, b in cuts:
        # ⚠️ 一定要打印区间里说了什么。08 期「比如说」被静音桥接吞掉，三道卡口
        #    一个都没拦住，就是因为剪切这一步只报区间不报内容，看不出剪掉了话。
        said = ''.join(w['s'] for w in asr['words']
                       if w['t'] >= a - 0.05 and w['t'] + w['d'] <= b + 0.05)
        kind = cut_kind(a, b)
        # 掐头掐尾和删除线区间里有语音是**正常的**（那本来就不属于这条片子 / 是要删的）；
        # 只有「静音」类剪切里出现语音才是问题。
        # 掐头/掐尾/删除线区间里有语音是正常的，但**有脚本字就不正常**——
        # 那说明剪掉的是这条片子该保留的内容。470 期 04/11 的末句就是这么没的，
        # 当时三道卡口全部放行，因为豁免是按「区间类型」给的，没看内容。
        # 只查掐头/掐尾：删除线区间**本来就是要删脚本内容的**，命中是正常的。
        # 另要求命中成串（>=4 字）——单个字多半是孤立的错误匹配，
        # 08 的掐头里就有一个被错锚到片头的「打」字。
        hit = [i for i in anchored
               if not mask[i] and times[i][0] >= a - 0.05 and times[i][1] <= b + 0.05]
        if kind not in ('掐头', '掐尾') or len(hit) < 4:
            hit = []
        if kind == '静音' and said.strip():
            tag = '  ❌ 静音剪切里有语音：%s' % said
        elif hit:
            tag = '  ❌ %s 区间内有 %d 个脚本字被剪掉：%s' % (
                kind, len(hit), ''.join(script[i] for i in sorted(hit))[:24])
        elif said.strip():
            tag = '  %s：%s' % (kind, said[:28])
        else:
            tag = '  %s（无语音）' % kind
        print('   %.2f - %.2f  (%.2fs)%s' % (a, b, b-a, tag))
    print('保留区间:', ' '.join('%.2f-%.2f' % k for k in keeps))
    print('成片时长 %.2f -> %.2f  字幕 %d 行' % (dur, out_dur, len(lines)))
    # ppt_src（源片时间）与 ppt_out（成片时间）必须一一对应，所以一起过滤
    pairs = [(s, (src2out(s[0]), src2out(s[1]))) for s in mask_ranges(pptmask, times)]
    pairs = [p for p in pairs if p[1][1] - p[1][0] > 0.3]
    ppt_src = [p[0] for p in pairs]
    ppt_out = [p[1] for p in pairs]
    if ppt_out:
        print('标红区间（需插入 PPT，成片时间）:')
        for a, b in ppt_out:
            print('   %.2f - %.2f  (%.2fs)' % (a, b, b-a))
    lines = rebalance_flashes(lines)
    json.dump({'lines': [{'text': t, 'start': s, 'end': e, 'clause_start': bool(cs)}
                         for t, s, e, cs in lines],
               'keeps': keeps, 'cuts': cuts, 'out_duration': out_dur,
               'ppt_ranges': ppt_out, 'ppt_src': ppt_src},
              open(out_json, 'w'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
