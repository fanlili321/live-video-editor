#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整期批量剪辑驱动。

用法：
  python3 batch.py --config <期次配置.json> --rows 2,3,7 [--stage all|locate|asr|render]

设计原则（吃过亏才定的）：
  1. 每条独立跑，一条失败不影响其他条，最后统一报告
  2. 分阶段可续跑，中间产物落盘；重跑某阶段不用从头再来
  3. 只打印紧凑摘要，详细日志写文件
"""
import json, os, re, sys, subprocess, time, difflib

TOOLS = os.path.dirname(os.path.abspath(__file__))


def sh(cmd, **kw):
    return subprocess.run(cmd, cwd=TOOLS, capture_output=True, text=True, **kw)


def log(job, msg):
    print('  [%s] %s' % (job, msg), flush=True)


PAD_HEAD, PAD_TAIL = 6.0, 6.0   # 粗定位余量；精度交给 make_srt --trim-ends


def norm(s):
    return re.sub(r'[\s　]', '', s)


def flat_transcript(cfg):
    """把文字稿拍平成一个长字符串 + 每个字符对应的时间。

    段落内按字数线性插值到下一段起点——够粗，但粗定位只需要 ±几秒。
    """
    paras = json.load(open(cfg['transcript']))['paragraphs']
    buf, times = [], []
    for i, p in enumerate(paras):
        s = norm(p['text'])
        if not s:
            continue
        t0 = p['t']
        t1 = paras[i+1]['t'] if i+1 < len(paras) else cfg['duration']
        step = (t1 - t0) / len(s)
        buf.append(s)
        times.extend(t0 + step*k for k in range(len(s)))
    return ''.join(buf), times


def locate(cfg, script_text):
    """粗定位：在文字稿全文里模糊匹配脚本，多留 6 秒余量。

    不用精确子串——脚本是人工润色过的（去口水、改措辞），首末句常对不上原话，
    行 2 和行 7 就是这么栽的。difflib 的匹配块能容忍这些改动。

    精定位不在这里做：切片跑完 ASR 后，make_srt 的 DP 对齐本身就给出了
    首字/末字的精确时间，让它顺手把多余的头尾当作剪切区间即可（--trim-ends）。
    一次 ASR 干两件事，比再开两个窗口跑 ASRTest 又快又准。
    """
    full, times = flat_transcript(cfg)
    body = norm(script_text)
    sm = difflib.SequenceMatcher(a=full, b=body, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size >= 6]
    if not blocks:
        return None, '粗定位失败(全文无匹配块)'
    cover = sum(b.size for b in blocks) / len(body)
    if cover < 0.55:
        return None, '粗定位失败(仅覆盖脚本 %.0f%%)' % (cover*100)
    a0 = blocks[0].a
    a1 = blocks[-1].a + blocks[-1].size - 1
    ht, tend = times[a0], times[min(a1, len(times)-1)]
    if tend <= ht:
        return None, '定位倒挂(首 %.1f > 末 %.1f)' % (ht, tend)
    return (round(max(0, ht-PAD_HEAD), 2),
            round(min(cfg['duration'], tend+PAD_TAIL), 2)), None



def sheet_sel(cfg):
    """飞书表格既可以按 sheet_id 也可以按名字定位。

    ⚠️ 两者不能混用：把名字（如 Sheet1）传给 --sheet-id，CLI 会返回空输出，
    调用方解析 JSON 时直接崩，而且错误信息跟真实原因毫无关系。
    这里按形态判断：纯字母数字且长度 <= 12 的当名字，其余当 id。
    """
    sid = str(cfg.get('sheet_id') or '')
    looks_like_name = bool(re.fullmatch(r'[A-Za-z\u4e00-\u9fff][\w\u4e00-\u9fff]{0,11}', sid))
    return ['--sheet-name', sid] if looks_like_name else ['--sheet-id', sid]

# ---------- 标题断行 ----------
PPT_TAIL_SNAP = 5.0  # PPT 距片尾不足这么多秒就补到片尾（复核意见⑫）
PPT_MIN_SEG = 3.0    # 拆段时短于这么多秒的碎片不单独配页（避免幻灯片一闪而过）
MAX_LINE = 7        # 每行不超过 7 字（06-规格/视觉规格标定-横版.md 四）
TITLE_TAIL_BAD = set('和与及或的了是在从对把被向为就也都还很更再一')


def title_lines(cfg, row, nlines):
    """取标题断行。配置里写了「断行」就用人工指定的，否则自动算。

    算法能覆盖绝大多数标题，但偶尔会出「闪电劈 / 下来时」这种读着别扭的。
    这时不该去改算法（会牵动其他标题），直接在配置里指定这一条即可。
    """
    r = cfg['rows'][str(row)]
    if r.get('断行'):
        return list(r['断行'])
    return split_title(r['title'], nlines)


def split_title(title, nlines):
    """把标题断成 nlines 行：只在词边界断，尽量各行等长，每行不超过 7 字。

    横版封面 2 行、竖版封面与视频内标题 3 行。
    早先按字数对半砍，砍出过「适合哪 / 些指数」——必须走分词。
    标点优先级最高（逗号处一定断），剩下的位置按 DP 选代价最小的组合。
    """
    import jieba
    segs = [s for s in re.split(r'[，,、。！？]', title) if s]

    def best_split(s):
        """在词边界上把 s 切两半，取最均衡的那个切点"""
        toks = list(jieba.cut(s))
        if len(toks) < 2:
            return None
        pos, acc = [], 0
        for t in toks[:-1]:
            acc += len(t)
            # 罚的是「整个词就是个连词/虚词」，不是「末字碰巧是虚词」——
            # 「增量资金和 / 存量资金」该罚，「为何总是 / 跷跷板」不该罚
            pos.append((acc, len(t) == 1 and t in TITLE_TAIL_BAD))
        # 距中点最近；同等条件取靠后的切点，让前行长于后行，读起来更顺
        p = min(pos, key=lambda x: (abs(x[0] - len(s)/2) + (1.5 if x[1] else 0), -x[0]))[0]
        return s[:p], s[p:]

    while len(segs) < nlines:
        # 拆最宽的那段；拆不动就换次宽的
        for i in sorted(range(len(segs)), key=lambda k: -len(segs[k])):
            two = best_split(segs[i])
            if two:
                segs[i:i+1] = list(two)
                break
        else:
            break
    while len(segs) > nlines:
        i = min(range(len(segs)-1), key=lambda k: len(segs[k]) + len(segs[k+1]))
        segs[i:i+2] = [segs[i] + segs[i+1]]
    return segs


# ---------- 已切分音频 ----------
# 「音频切分」流水线的切点来自**脚本正文与音频逐字对齐**，比这边按「时间范围」列
# 粗定位准得多。471 期实测：用它之后对齐相似度 0.790 → 0.911，掐头从 34.66s 变成 0。
#
# ⚠️ 两个必须注意的地方：
#   1. 成品按 01、02、03 顺序编号，**不是脚本表行号**（剪掉的行不占号）。
#   2. 文件名用的是钉大选定的那个标题，**未必是标题1**——471 期行 7 我的配置写的是
#      「现在是谁在买 REITs」，音频文件叫「保险机构为何愿意买 REITs」（标题2）。
#   所以只能按**标题模糊匹配**，绝不能按编号对应。三个标题都要拿来比。
def check_av_sync(video, audio, probes=(300, 1500, 3000), win=40.0, pad=3.0):
    """切点是算在音频上的，切的却是原片——先验证两者时间轴一致，否则整期都会错位。

    **比时长不够**：472 期原始音频 5992.85s、手机版 5972.99s，差 20 秒；
    而 474 期音频比视频长 0.053s，光看时长分不清「同源」还是「差一点」。
    要比**声音波形**：取几段做互相关，偏移必须恒定且接近 0。
    474 期实测四点偏移全为 0.000s，相关 1.000，次优仅 0.19~0.39，峰值很尖锐。

    ⚠️ rms 输出是「时刻 振幅」两列，**只能取第二列**。把时刻也当波形会算出
    完美相关（时刻单调递增），看起来一切正常——我第一次就是这么被骗过去的。
    """
    import statistics as st

    def series(path, a, b):
        r = sh([os.path.join(TOOLS, 'rms'), path, str(a), str(b), '25'])
        out = []
        for ln in r.stdout.splitlines():
            parts = ln.split()
            if len(parts) >= 2:
                try:
                    out.append(float(parts[1]))
                except ValueError:
                    pass
        return out

    offs = []
    for t in probes:
        v = series(video, t, t + win)
        a = series(audio, t - pad, t + win + pad)
        if len(v) < 100 or len(a) < len(v):
            continue
        mv = st.mean(v)
        dv = sum((x - mv) ** 2 for x in v) ** 0.5
        best = (-2.0, 0)
        for k in range(len(a) - len(v) + 1):
            w = a[k:k + len(v)]
            mw = st.mean(w)
            den = dv * sum((y - mw) ** 2 for y in w) ** 0.5
            if den <= 0:
                continue
            r = sum((x - mv) * (y - mw) for x, y in zip(v, w)) / den
            if r > best[0]:
                best = (r, k)
        if best[0] > 0.8:
            offs.append(best[1] * 0.025 - pad)
    if len(offs) < 2:
        return None, '音画对齐测不出来（有效采样点不足）'
    spread = max(offs) - min(offs)
    mean = st.mean(offs)
    if spread > 0.10:
        return None, '音画偏移不稳定（极差 %.3fs），切点不能直接用在原片上' % spread
    return mean, None


def cut_point(cfg, row):
    """从「音频切分」的切点数据里取这一行的起止（原片时间轴）。

    **按脚本表行号取，不靠标题猜。** 切点数据里每条都带 `row` 字段，
    而标题会被钉大改写（471 期行 7 配置写「现在是谁在买 REITs」，
    成品文件叫「保险机构为何愿意买 REITs」），按标题匹配是在没看到这份数据时的将就做法。

    ⚠️ 前提：切点算在「原始音频」上，而这里要切的是原片。两者时间轴必须一致。
    474 期实测：四个时间点波形互相关，偏移恒为 0.000s，相关系数 1.000（次优仅 0.19~0.39）。
    换一期务必重测一次——比时长不够，**要比声音波形**。
    """
    p = cfg.get('切点数据')
    if not p or not os.path.exists(p):
        return None
    try:
        cuts = json.load(open(p, encoding='utf-8'))
    except Exception as e:
        print('  [警告] 切点数据读不出来：%r' % e)
        return None
    for c in cuts:
        if int(c.get('row', -1)) == int(row):
            # ⚠️ 要取 `cut`，不是 `span`。切点数据里三组数的含义：
            #    span      = 粗窗口，首尾各带约 20 秒余量（474 行2：104.61–391.30）
            #    start/end = 首字/末字的对齐时刻，不带余量（124.60–369.87）
            #    cut       = 精确切点，首字前 0.30s、末字后 0.45s（124.30–370.32）
            #    先写成取 span，等于又退回了粗定位——474 期文本定稿前核对数据时才发现。
            sp = c.get('cut') or ([c['start'] - 0.30, c['end'] + 0.45]
                                  if c.get('start') is not None else c.get('span'))
            if sp and sp[0] is not None and sp[1] is not None:
                d = float(cfg.get('切点偏移') or 0.0)
                return [float(sp[0]) + d, float(sp[1]) + d]
    print('  [提示] 切点数据里没有第 %s 行，退回粗定位' % row)
    return None


def clip_duration(path):
    r = sh([os.path.join(TOOLS, 'vtool'), 'info', path])
    m = re.search(r'duration_sec: ([\d.]+)', r.stdout)
    return float(m.group(1)) if m else 0.0


# ---------- PPT 配图 ----------
def ppt_slides(cfg, span, subs):
    """标红区间 → 该说这段话时投屏上放的是第几页。

    subs['ppt_src'] 是切片内时间，加上 span[0] 换成原片时间，
    再去 PPT时间轴.json（同样是原片时间）里查页码。
    一个标红区间可能跨翻页，取覆盖时长最长的那一页。
    """
    tlf = cfg.get('ppt_timeline')
    if not tlf or not os.path.exists(tlf):
        return []
    segs = json.load(open(tlf))['segments']
    out = []
    src_ranges = subs.get('ppt_src') or []
    out_ranges = subs.get('ppt_ranges') or []
    for (oa, ob), (ra, rb) in zip(src_ranges, out_ranges):
        sa, sb = oa + span[0], ob + span[0]
        # ⚠️ 一个标红区间跨翻页时，**必须按翻页点拆成多段**，各配各的页。
        #    原先是「取覆盖时长最长的那一页」整段用到底：470 期 02 号整段用第 2 页，
        #    而第 3 页从原片 225.05s 起就该换了——成片 175.0–261.5s（86.5 秒）
        #    画面停在一张没有任何数字的表上，口播却在逐个念沪深300/中证500 的平均市值。
        #    04 号同理错 63 秒。当时 batch 自己算出 cover=0.63 却没有任何告警。
        parts = []
        for sg in segs:
            ov0, ov1 = max(sa, sg['start']), min(sb, sg['end'])
            if ov1 - ov0 > PPT_MIN_SEG:
                parts.append((ov0, ov1, sg))
        parts.sort()
        if not parts:
            out.append({'out_range': [round(ra, 2), round(rb, 2)],
                        'src': [round(sa, 2), round(sb, 2)],
                        'slide': None, 'image': None, 'cover': 0.0})
            continue
        # 区间内 src→out 是线性的（make_srt 已按剪切把 ppt_src/ppt_ranges 一起切开过）
        span_src = max(0.01, sb - sa)
        for k, (p0, p1, sg) in enumerate(parts):
            # 段间不留缝：把边界对齐到相邻段的中点，首尾贴住原区间
            a_src = sa if k == 0 else (parts[k-1][1] + p0) / 2.0
            b_src = sb if k == len(parts) - 1 else (p1 + parts[k+1][0]) / 2.0
            f0 = (a_src - sa) / span_src
            f1 = (b_src - sa) / span_src
            out.append({'out_range': [round(ra + (rb - ra) * f0, 2),
                                      round(ra + (rb - ra) * f1, 2)],
                        'src': [round(a_src, 2), round(b_src, 2)],
                        'slide': sg['slide'], 'image': sg['image'],
                        'cover': round((p1 - p0) / max(0.01, b_src - a_src), 2),
                        'split': len(parts) > 1})
    # 复核意见⑫：PPT 一直讲到片尾时，最后不要再切回人像露一下脸。
    # 只差几秒就到片尾的，直接把 PPT 拉到底。
    dur = subs.get('out_duration')
    if out and dur and 0 < dur - out[-1]['out_range'][1] <= PPT_TAIL_SNAP:
        out[-1]['out_range'][1] = round(dur, 2)
        out[-1]['tail_extended'] = True
    return out


# ---------- 主流程 ----------
def run_row(cfg, row, stage):
    root = cfg['root']
    title = cfg['rows'][str(row)]['title']
    safe = re.sub(r'[/:]', '_', title)
    outdir = os.path.join(root, '%02d-%s' % (row, safe))
    os.makedirs(outdir, exist_ok=True)
    rec = {'row': row, 'title': title, 'dir': outdir}

    # 脚本表行号：切片编号未必等于表行号（脚本会被整理/迁移到别的 sheet）
    sheet_row = int((cfg.get('行号映射') or {}).get('映射', {}).get(str(row), row))
    cell = os.path.join(outdir, '脚本单元格.json')
    if not os.path.exists(cell):
        r = subprocess.run(['lark-cli', 'sheets', '+cells-get', '--url', cfg['sheet_url'],
                            *sheet_sel(cfg),
                            '--range', 'B%d:B%d' % (sheet_row, sheet_row),
                            '--include', 'value,style'], capture_output=True, text=True)
        open(cell, 'w').write(r.stdout)
    c = json.load(open(cell))['data']['ranges'][0]['cells'][0][0]
    text = c.get('value') or ''.join(x['text'] for x in (c.get('rich_text') or []))
    rec['chars'] = len(text)

    clip = os.path.join(outdir, '切片.mov')
    # ① 优先用「音频切分」算好的切点。**切的是原片，不是拿音频当片源**——
    #    音频没有画面，人脸检测会静默地检不到东西（crop.json 变成 {}），
    #    渲染又找不到 切片.mov。471 期就是这么埋下的，只因当时没有 1080P 原片才没暴露。
    if not os.path.exists(clip):
        span = cut_point(cfg, row)
        if span:
            rec['span'] = span
            json.dump({'span': span, '来源': '音频切分切点'},
                      open(os.path.join(outdir, 'span.json'), 'w'), ensure_ascii=False)
            sh([os.path.join(TOOLS, 'vtool'), 'trim', cfg['video'],
                str(span[0]), str(span[1]), clip])
            log(row, '按切点切原片 %.2f-%.2f (%.1fs)' % (span[0], span[1], span[1]-span[0]))
    if not os.path.exists(clip):
        span, err = locate(cfg, text)
        if err:
            rec['error'] = err
            return rec
        rec['span'] = span
        json.dump({'span': span}, open(os.path.join(outdir, 'span.json'), 'w'))
        sh([os.path.join(TOOLS, 'vtool'), 'trim', cfg['video'], str(span[0]), str(span[1]), clip])
        log(row, '切片 %.2f-%.2f (%.1fs)' % (span[0], span[1], span[1]-span[0]))
    else:
        rec['span'] = json.load(open(os.path.join(outdir, 'span.json')))['span']

    crop = os.path.join(outdir, 'crop.json')
    if not os.path.exists(crop):
        r = sh([os.path.join(TOOLS, 'facescan'), clip, '2'])
        m = re.search(r'JSON (\{.*\})', r.stdout)
        open(crop, 'w').write(m.group(1) if m else '{}')
    rec['crop'] = json.load(open(crop))

    asr = os.path.join(outdir, 'asr.json')
    if not os.path.exists(asr):
        t0 = time.time()
        r = sh(['python3', os.path.join(TOOLS, 'asr_all.py'), clip, asr])
        open(os.path.join(outdir, 'asr.log'), 'w').write(r.stdout + r.stderr)
        log(row, 'ASR 完成 %.0f 秒' % (time.time()-t0))
    if not os.path.exists(asr):
        rec['error'] = 'ASR 未产出'
        return rec

    subs = os.path.join(outdir, '字幕.json')
    ov = os.path.join(outdir, '保留覆盖.txt')
    args = ['python3', os.path.join(TOOLS, 'make_srt.py'), asr, cell, subs]
    args.append(ov if os.path.exists(ov) else os.devnull)
    args.append('--trim-ends')
    args.append('--trim-gaps')
    fx = os.path.join(outdir, '文本修正.txt')
    if os.path.exists(fx):
        args += ['--fixes', fx]
    bf = os.path.join(outdir, '断行修正.txt')
    if os.path.exists(bf):
        args += ['--break-fixes', bf]
    r = sh(args)
    open(os.path.join(outdir, 'srt.log'), 'w').write(r.stdout + r.stderr)
    m = re.search(r'相似度[=:\s]*([0-9.]+)', r.stdout)
    if m:
        rec['sim'] = float(m.group(1))
    if not os.path.exists(subs):
        rec['error'] = 'make_srt 失败，见 srt.log'
        return rec
    d = json.load(open(subs))
    rec['lines'] = len(d['lines'])
    rec['cuts'] = len(d.get('cuts') or [])
    rec['out_duration'] = d.get('out_duration')
    rec['ppt_ranges'] = d.get('ppt_ranges') or []
    # 成片里的 PPT 区间要换算回原片时间，才能去 PPT时间轴.json 查页码
    rec['ppt_slides'] = ppt_slides(cfg, rec['span'], d)

    # 漏词检测：拿本机 ASR 当逐字证人，反查妙记顺滑掉、脚本因此也缺的口语词。
    # 只报不拦——「然后/那个」多是口水词删了更好，「这个」常是指示词删了就少字，
    # 这个判断只能人做。详见 dropcheck.py 开头。
    dc = sh(['python3', os.path.join(TOOLS, 'dropcheck.py'), '--clip', outdir])
    open(os.path.join(outdir, '漏词检测.log'), 'w').write(dc.stdout + dc.stderr)
    m = re.search(r'合计疑似漏词 (\d+) 处', dc.stdout)
    rec['漏词'] = int(m.group(1)) if m else 0

    # 渲染前把字幕排成可通读的文本。断行问题过去只能等成片出来才发现，
    # 一轮 40 分钟；字幕其实在这一步就定稿了，缺的只是「有人在渲染前看一眼」。
    sh(['python3', os.path.join(TOOLS, 'preview.py'), subs])

    q = sh(['python3', os.path.join(TOOLS, 'qc_subs.py'), subs])
    m = re.search(r'\[错\] (\d+) 处', q.stdout)
    rec['qc_err'] = int(m.group(1)) if m else -1
    open(os.path.join(outdir, 'qc.log'), 'w').write(q.stdout)

    # ⚠️ 曾经写的是 `if stage == 'render'`，于是**默认的 --stage all 不渲染**：
    #    前置步骤全跑、汇总表每行 ✅、退出码 0，就是不出视频。
    #    470 期第二轮差点把 09-01 的旧片当新片传上去，是核对文件时间戳才发现的。
    #    「all」必须真的是 all。
    if stage in ('render', 'all'):
        if rec['qc_err']:
            rec['error'] = '字幕有 %d 处错误，先修字幕再渲染' % rec['qc_err']
            return rec
        for kind in ('h', 'v'):
            res, err = render_one(cfg, rec, kind)
            if err:
                rec['error'] = err
                return rec
            rec.setdefault('渲染', {})['横版' if kind == 'h' else '竖版'] = round(res[1], 1)
            log(row, '%s 渲染完成 %.0fs' % ('横版' if kind == 'h' else '竖版', res[1]))
    return rec


# ---------- 渲染 ----------
FONT = 'SourceHanSansCN-Medium'
COVER_FONT = 'SourceHanSansCN-Heavy'
ASSETS = os.path.join(TOOLS, '..', '04-素材')
# 规格来源：06-规格/视觉规格标定-横版.md / -竖版.md
SPEC = {
    # cover_maxink：每一行标题 ink 宽的上限（px）。横版人物在右侧，两行所处高度不同，
    # 人物左边界也不同，所以上限逐行给。实测见 06-规格/封面版式规范.md
    'h': dict(canvas='1920,1080', em=133.5, stroke=10.0, bottom=75, band=None,
              tpl='横版封面模板-1920x1080.png', cover_em=171, cover_pitch=251, cover_lines=2,
              cover_maxink=[1350, 1150]),
    # 竖版封面标题不能垂直居中——模板里人物顶在 y=795，居中会把字压在脸上。
    # cover_inkbottom = 795 - 40（安全间距），实测见 06-规格/封面版式规范.md
    'v': dict(canvas='1080,1920', em=75, stroke=5.6, bottom=569, band='0,656,1080,608',
              tpl='竖版封面模板-1080x1920.png', cover_em=165, cover_pitch=206, cover_lines=3,
              cover_inkbottom=755, cover_maxink=[980, 980, 980],
              title_em=165, title_pitch=206, title_inkbottom=643),
}



def ink_ratio(line):
    """一行文字的 ink 宽 / 字号。

    实测（思源黑体 Heavy）：每汉字约 0.995 em，数字与拉丁字母约 0.55 em。
    这里取 1.0 略偏保守——估低了就会算出偏大的字号，直接压到人物身上。
    """
    r = 0.0
    for c in line:
        r += 0.55 if (c.isascii() and c.isalnum()) else 1.0
    return r


def fit_cover_em(lines, sp):
    """标题太长时按比例缩小字号，保证每行都落在安全宽度内。

    封面上人物是固定的，标题却长短不一。不缩字号的话，长标题会直接压到人物身上——
    467 期横版就压了 43px。缩字号是唯一不改版式又能保证不重叠的办法。
    """
    caps = sp.get('cover_maxink') or []
    em = float(sp['cover_em'])
    scale = 1.0
    for i, ln in enumerate(lines):
        cap = caps[i] if i < len(caps) else (caps[-1] if caps else None)
        if not cap:
            continue
        need = ink_ratio(ln) * em
        if need > cap:
            scale = min(scale, cap / need)
    return em * scale, float(sp['cover_pitch']) * scale


def render_one(cfg, rec, kind):
    sp = SPEC[kind]
    outdir = rec['dir']
    name = '横版' if kind == 'h' else '竖版'
    cov = os.path.join(outdir, '封面-%s.png' % name)

    # 封面有两种来源：
    #   模板排版（默认）——用 04-素材 的模板 + 程序排标题，见 06-规格/封面版式规范.md
    #   外部提供       ——设计侧直接给成品图，本管线只负责取用，不再排版
    # 470 期起走外部：图片放 <root>/封面/<编号>-横版.png，尺寸已统一到 1920×1080 / 1080×1920
    if cfg.get('封面来源') == '外部':
        src = os.path.join(cfg.get('封面目录') or os.path.join(cfg['root'], '封面'),
                           '%02d-%s.png' % (rec['row'], name))
        if not os.path.exists(src):
            return None, '外部封面缺失：%s' % src
        import shutil
        shutil.copyfile(src, cov)
        rec.setdefault('封面', {})[name] = '外部'
    else:
        lines = title_lines(cfg, rec['row'], sp['cover_lines'])
        rec.setdefault('封面断行', {})[name] = lines
        em, pitch = fit_cover_em(lines, sp)
        if em < sp['cover_em']:
            rec.setdefault('封面缩字号', {})[name] = round(em, 1)
        cmd = [os.path.join(TOOLS, 'cover'), 'make', os.path.join(ASSETS, sp['tpl']),
               '%.1f' % em, COVER_FONT, '250,244,198', cov,
               '--pitch', '%.1f' % pitch]
        if sp.get('cover_inkbottom'):
            cmd += ['--inkbottom', str(sp['cover_inkbottom'])]
        r = sh(cmd + lines)
        if not os.path.exists(cov):
            return None, '封面生成失败: ' + (r.stderr or r.stdout)[:120]

    c = rec['crop']
    subs = os.path.join(outdir, '字幕.json')
    d = json.load(open(subs))
    out = os.path.join(outdir, '%s.mp4' % name)
    cmd = [os.path.join(TOOLS, 'render'), '--src', os.path.join(outdir, '切片.mov'),
           '--out', out, '--canvas', sp['canvas'],
           '--crop', '%.1f,%.1f,%.1f,%.1f' % (c['cropX'], c['cropY'], c['cropW'], c['cropH']),
           '--subs', subs, '--cover', cov,
           '--font', FONT, '--em', str(sp['em']), '--stroke', str(sp['stroke']),
           '--bottom', str(sp['bottom']), '--fps', '25', '--bitrate', '10000000']
    if d.get('keeps'):
        cmd += ['--keep', ','.join('%.3f-%.3f' % (a, b) for a, b in d['keeps'])]
    if sp['band']:
        cmd += ['--band', sp['band']]
        tl = title_lines(cfg, rec['row'], 3)
        # 标题色和字体不覆盖：渲染器默认就是 466 定稿的米黄 250,244,198 + Bold。
        # 之前传了纯白，渲出来跟成片样本不是一个颜色。
        cmd += ['--title', '|'.join(tl), '--title-em', str(sp['title_em']),
                '--title-pitch', str(sp['title_pitch']),
                '--title-inkbottom', str(sp['title_inkbottom'])]
    ppt = [s for s in rec.get('ppt_slides') or [] if s.get('image')]
    if ppt:
        cmd += ['--ppt', ','.join('%.3f-%.3f:%s' % (s['out_range'][0], s['out_range'][1], s['image'])
                                  for s in ppt)]
    t0 = time.time()
    r = sh(cmd)
    open(os.path.join(outdir, 'render-%s.log' % name), 'w').write(' '.join(cmd) + '\n\n' + r.stdout + r.stderr)
    if not os.path.exists(out):
        return None, '渲染失败: ' + (r.stderr or r.stdout)[-200:]
    return (out, time.time()-t0), None


def main():
    # 规则自检：47 条清洗/断行规则互相牵制，改了一条可能弄坏另一条。
    # 2026-08-18 就发生过——修「每7到10年」的护栏，把「然后18年」一起挡掉了。
    # 规则没过测试就不要产视频，否则错误会一路烧进成片再由人工复核发现。
    if '--skip-tests' not in sys.argv:
        r = sh(['python3', os.path.join(TOOLS, 'test_subs.py')])
        if r.returncode != 0:
            print(r.stdout + r.stderr)
            print('❌ 字幕规则回归测试未通过，已中止。修好规则或加 --skip-tests 强行跑。')
            sys.exit(1)
        print('✅ 字幕规则回归测试通过')

    cfg = json.load(open(sys.argv[sys.argv.index('--config')+1]))
    rows = [int(x) for x in sys.argv[sys.argv.index('--rows')+1].split(',')]

    # 脚本预检：文本层面的问题在读到脚本那一刻就已确定，不必等 ASR 跑完 20 分钟。
    # 有 [错] 就别开工——否则错误会一路烧进成片，再由人工复核发现。
    # 用切点之前，先确认「算切点的音频」和「要切的原片」时间轴一致。
    # 这道检查整期只跑一次，约十几秒；不通过就不用切点，退回粗定位。
    if cfg.get('切点数据') and cfg.get('切点音频') and cfg.get('video'):
        off, err = check_av_sync(cfg['video'], cfg['切点音频'])
        if err:
            print('⚠️ %s —— 本期不使用切点，退回粗定位' % err)
            cfg.pop('切点数据', None)
        else:
            print('音画对齐自检：偏移 %+.3fs ✅' % off)
            if abs(off) > 0.20:
                print('   偏移超过 0.2s，切点将整体平移这个量')
                cfg['切点偏移'] = off

    if '--skip-precheck' not in sys.argv:
        pc = sh(['python3', os.path.join(TOOLS, 'precheck.py'),
                 '--config', sys.argv[sys.argv.index('--config')+1],
                 '--rows', ','.join(str(r) for r in rows)])
        print(pc.stdout.strip())
        if pc.returncode != 0:
            # 区分「预检跑完并判定不合格」和「预检自己崩了」——
            # 470 期第一次跑批就是后者（表名当成了 sheet_id），却被报成前者，
            # 让人以为卡口在正常工作。退出码相同，必须靠输出特征区分。
            ran = '需要确认的改动形态' in pc.stdout or '[错]' in pc.stdout
            if ran:
                print('❌ 脚本预检发现必须修的问题，已中止。见预检报告；确认无误可加 --skip-precheck。')
            else:
                print('❌ 脚本预检**自身出错**（不是内容问题），已中止：')
                print((pc.stderr or '').strip()[-500:] or '（无 stderr）')
            sys.exit(1)
    stage = sys.argv[sys.argv.index('--stage')+1] if '--stage' in sys.argv else 'all'
    out = []
    for row in rows:
        print('\n=== 行 %d  %s ===' % (row, cfg['rows'][str(row)]['title']), flush=True)
        try:
            out.append(run_row(cfg, row, stage))
        except Exception as e:
            out.append({'row': row, 'error': repr(e)})
            print('  异常：%r' % e, flush=True)
    json.dump(out, open(os.path.join(cfg['root'], '批量报告.json'), 'w'),
              ensure_ascii=False, indent=1)
    print('\n' + '='*70)
    print('%-4s %-22s %7s %6s %6s %5s %5s %5s %s %s'
          % ('行', '标题', '片长', '字数', '字幕', '剪切', 'PPT', '漏词', '质检', '渲染'))
    for r in out:
        if r.get('error'):
            print('%-4s %-22s  ✗ %s' % (r['row'], r.get('title', '')[:22], r['error']))
            continue
        dur = r['span'][1]-r['span'][0]
        dw = r.get('漏词', 0)
        # 渲染状态按**文件是否比字幕新**判定，不看流程有没有走到——
        # 「跑过了」和「出片了」是两回事，470 期就是栽在这个差别上。
        vids = [os.path.join(r['dir'], k + '.mp4') for k in ('横版', '竖版')]
        subs_p = os.path.join(r['dir'], '字幕.json')
        if all(os.path.exists(v) for v in vids) and os.path.exists(subs_p):
            fresh = min(os.path.getmtime(v) for v in vids) >= os.path.getmtime(subs_p) - 1
            rend = '✅' if fresh else '⚠️旧片'
        else:
            rend = '✗无'
        print('%-4s %-22s %6.1fs %6s %6s %5s %5s %5s %s %s' % (
            r['row'], r['title'][:22], dur, r.get('chars', '?'), r.get('lines', '?'),
            r.get('cuts', 0), len(r.get('ppt_ranges', [])),
            ('⚠️%d' % dw) if dw else '✅',
            '✅' if r.get('qc_err') == 0 else '⚠️%d' % r.get('qc_err', -1), rend))


if __name__ == '__main__':
    main()
