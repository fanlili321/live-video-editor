#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脚本预检：在切片和语音识别之前，先把整期字幕文本过一遍。

用法：
  python3 precheck.py --config <期次配置.json> [--rows 2,3,7] [--out 预检报告.md]

**为什么要有这一步**：字幕文本的清洗完全不依赖音频——脚本从表格读出来的那一刻，
文本层面的问题就已经全部确定了。但原来质检跑在语音识别之后，
意味着要等 20 分钟、烧完 ASR 才知道字幕有没有问题。

预检把这个确认点提前到**开工前 10 秒**，并且一次看完整期十几条，
而不是剪完一条看一条（对应项目文档第七节「人工确认点聚合」）。

报告里逐条列出**每一处改动及其归因规则**。三轮人工复核里最危险的一类错误
（`中证全指从2000多点` 被改成 `中证2000多点`）不违反任何质检项、读起来还通顺，
只有把改动摊开给人看才可能发现。
"""
import json, os, re, sys, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_srt as M
import qc_subs as Q
import changesig as C

APPROVED = os.path.join(os.path.dirname(os.path.abspath(__file__)), '已确认改动.json')

TOOLS = os.path.dirname(os.path.abspath(__file__))

# 改动里最值得人工盯的两类
RISKY_RULES = {'指数名补全', '删单字残句', '删语气词', '固定用字纠错'}



def sheet_sel(cfg):
    """飞书表格既可以按 sheet_id 也可以按名字定位。

    ⚠️ 两者不能混用：把名字（如 Sheet1）传给 --sheet-id，CLI 会返回空输出，
    调用方解析 JSON 时直接崩，而且错误信息跟真实原因毫无关系。
    这里按形态判断：纯字母数字且长度 <= 12 的当名字，其余当 id。
    """
    sid = str(cfg.get('sheet_id') or '')
    looks_like_name = bool(re.fullmatch(r'[A-Za-z\u4e00-\u9fff][\w\u4e00-\u9fff]{0,11}', sid))
    return ['--sheet-name', sid] if looks_like_name else ['--sheet-id', sid]

def load_approved():
    return json.load(open(APPROVED)) if os.path.exists(APPROVED) else {}


def save_approved(d):
    json.dump(d, open(APPROVED, 'w'), ensure_ascii=False, indent=1, sort_keys=True)


def fetch_cell(cfg, row, cache_dir):
    """读脚本单元格，优先用已缓存的（预检不该为了看一眼就重新拉表）"""
    for d in (cache_dir,):
        if not d:
            continue
        for sub in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if re.match(r'^%02d-' % row, sub):
                f = os.path.join(d, sub, '脚本单元格.json')
                if os.path.exists(f):
                    return json.load(open(f)), f
    sr = int((cfg.get('行号映射') or {}).get('映射', {}).get(str(row), row))
    r = subprocess.run(['lark-cli', 'sheets', '+cells-get', '--url', cfg['sheet_url'],
                        *sheet_sel(cfg), '--range', 'B%d:B%d' % (sr, sr),
                        '--include', 'value,style'], capture_output=True, text=True)
    # 读表失败要说清楚是「读不到」，不能让 json 解析异常冒上去——
    # 那样上层只看到非零退出码，会当成「预检发现问题」，与真实原因南辕北辙。
    txt = r.stdout[r.stdout.find('{'):] if '{' in r.stdout else ''
    if not txt:
        raise SystemExit('读脚本表失败（行 %d）：%s' % (sr, (r.stderr or r.stdout).strip()[:200]))
    return json.loads(txt), '（实时读取）'


def text_of(cell_json):
    c = cell_json['data']['ranges'][0]['cells'][0][0]
    return c.get('value') or ''.join(x['text'] for x in (c.get('rich_text') or []))


def check_text(clean):
    """只做**不依赖时间轴**的文本级检查（行级检查等 ASR 之后由 qc_subs 负责）"""
    errs, warns = [], []
    for b in Q.BANNED:
        if b in clean:
            errs.append('出现禁用词「%s」' % b)
    for c in Q.FILLER:
        if c in clean:
            errs.append('残留语气词「%s」' % c)
    if re.search(r'\d,\d', clean):
        errs.append('数字里有逗号')
    if re.search(r'(19|20)\d{3,}', clean):
        errs.append('年份异常（补全规则可能重复应用）')
    if re.search(r'\d{5,}', clean):
        errs.append('异常长数字')
    for b in Q.unmarked_books(clean):      # 复用 qc_subs 的实现，不写第二份
        errs.append('书名未加《》：%s' % b)
    # 两位数二元枚举：可能是年份没补全，也可能是月/号，交人看
    for m in re.finditer(r'(?<!\d)\d{2}\s*[、,，]\s*\d{2}(?!\d)(?!\s*年)', clean):
        warns.append('两位数枚举「%s」是否为年份？' % m.group(0))
    # 裸两位数（排除百分比、年代、时长）
    for m in re.finditer(r'(?<![\d.])(\d{2})(?![\d%％])', clean):
        nxt = clean[m.end():m.end()+2]
        if nxt.startswith(('年代', '年', '月', '日', '倍', '亿', '万', '点', '多', '来', '几')):
            continue
        warns.append('裸两位数「%s」（上下文：…%s…）'
                     % (m.group(1), clean[max(0, m.start()-8):m.end()+8]))
    return errs, warns


def main():
    a = sys.argv
    cfg = json.load(open(a[a.index('--config')+1]))
    rows = ([int(x) for x in a[a.index('--rows')+1].split(',')]
            if '--rows' in a else sorted(int(k) for k in cfg['rows']))
    out = a[a.index('--out')+1] if '--out' in a else os.path.join(cfg['root'], '预检报告.md')

    approved = load_approved()
    L = ['# 字幕预检报告', '',
         '> 期次：%s　　检查 %d 条' % (cfg.get('episode', '?'), len(rows)),
         '> 本报告只覆盖**文本层面**；行宽、行首虚词等断行问题在语音识别后由 `qc_subs.py` 复查。',
         '']
    total_err = 0
    summary = []
    new_sigs = {}          # 本期出现的、白名单里没有的改动形态
    for row in rows:
        title = cfg['rows'][str(row)]['title']
        cell, src = fetch_cell(cfg, row, cfg['root'])
        raw = text_of(cell)
        trace = []
        clean = M.clean_script(raw, trace=trace)
        for x in trace:
            sig = C.signature(x)
            if sig not in approved:
                new_sigs.setdefault(sig, {'n': 0, 'rule': x['rule'], 'row': row,
                                          'sample': C.describe(x)})
                new_sigs[sig]['n'] += 1
        errs, warns = check_text(clean)
        total_err += len(errs)
        summary.append((row, title, len(raw), len(clean), len(trace), len(errs), len(warns)))

        L.append('## 行 %d　%s' % (row, title))
        L.append('')
        L.append('原文 %d 字 → 清洗后 %d 字，共 %d 处改动' % (len(raw), len(clean), len(trace)))
        L.append('')
        if errs:
            L.append('**[错] %d 处**（必须修，否则不应开工）' % len(errs))
            L += ['- ' + e for e in errs] + ['']
        if warns:
            L.append('**[疑] %d 处**（请人工确认）' % len(warns))
            L += ['- ' + w for w in warns] + ['']

        risky = [d for d in trace if d['rule'] in RISKY_RULES]
        if risky:
            L.append('### ⚠️ 需重点核对的改动（改语义或删内容）')
            L.append('')
            L.append('| 规则 | 操作 | 改前 | 改后 | 上下文 |')
            L.append('|---|---|---|---|---|')
            for d in risky:
                L.append('| %s | %s | `%s` | `%s` | %s |'
                         % (d['rule'], d['kind'], d['before'] or '—',
                            d['after'] or '—', d['context']))
            L.append('')
        other = [d for d in trace if d['rule'] not in RISKY_RULES]
        if other:
            L.append('<details><summary>其余 %d 处改动（格式类，一般无需细看）</summary>' % len(other))
            L.append('')
            L.append('| 规则 | 改前 | 改后 |')
            L.append('|---|---|---|')
            for d in other:
                L.append('| %s | `%s` | `%s` |' % (d['rule'], d['before'] or '—', d['after'] or '—'))
            L.append('')
            L.append('</details>')
            L.append('')

    head = ['## 汇总', '',
            '| 行 | 标题 | 原文 | 清洗后 | 改动 | [错] | [疑] |', '|---|---|---|---|---|---|---|']
    for row, title, n0, n1, nt, ne, nw in summary:
        head.append('| %d | %s | %d | %d | %d | %s | %d |'
                    % (row, title, n0, n1, nt, '**%d**' % ne if ne else '0', nw))
    head.append('')
    L = L[:5] + head + L[5:]

    # ---- 新形态汇总：这是人**唯一**需要看的部分 ----
    head2 = ['## 需要确认的改动形态', '']
    if not new_sigs:
        head2 += ['✅ 本期所有改动形态都在已确认清单里，无需人工判断。', '']
    else:
        head2 += ['本期出现 **%d 种**此前没确认过的改动形态。'
                  '已确认过的形态不再重复列出（清单共 %d 种）。' % (len(new_sigs), len(approved)),
                  '确认无误后执行 `python3 precheck.py --config <配置> --approve` 收进清单。', '',
                  '| 次数 | 规则 | 首次出现 | 改动 |', '|---|---|---|---|']
        for sig, v in sorted(new_sigs.items(), key=lambda kv: -kv[1]['n']):
            head2.append('| %d | %s | 行%d | %s |' % (v['n'], v['rule'], v['row'], v['sample']))
        head2.append('')
    L = L[:5] + head2 + L[5:]

    if '--approve' in a:
        for sig, v in new_sigs.items():
            approved[sig] = {'rule': v['rule'], '示例': v['sample'],
                             '确认于': cfg.get('episode', '?')}
        save_approved(approved)
        print('已把 %d 种新形态收进确认清单（共 %d 种）' % (len(new_sigs), len(approved)))

    open(out, 'w').write('\n'.join(L))
    print('预检报告 → %s' % out)
    print('%-4s %-22s %6s %6s %6s %5s %5s' % ('行', '标题', '原文', '清洗后', '改动', '[错]', '[疑]'))
    for row, title, n0, n1, nt, ne, nw in summary:
        print('%-4d %-22s %6d %6d %6d %5s %5d'
              % (row, title[:22], n0, n1, nt, ('❌%d' % ne) if ne else '✅0', nw))
    print()
    if new_sigs:
        print('⚠️  %d 种改动形态此前未确认，见报告「需要确认的改动形态」一节' % len(new_sigs))
    else:
        print('✅ 所有改动形态均已确认过，无需人工判断')
    return 1 if total_err else 0


if __name__ == '__main__':
    sys.exit(main())
