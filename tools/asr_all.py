#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按静音把片子切成 <=MAXCHUNK 秒的块，逐块跑本地 ASR，汇总成绝对时间的词表。"""
import subprocess, os, sys, time, json

S = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get('ASR_OUT', '/tmp/asr_out.txt')
MAXCHUNK = 22.0
MINCHUNK = 8.0
OVERLAP = 5.0        # 分块重叠，抵消识别器吞头
HOLE_MIN = 0.8       # 有语音但无识别结果超过这个秒数就算空洞


def rms_gaps(video, dur):
    """返回 [(gapStart, gapEnd)]，静音间隙 >=0.25s"""
    txt = os.path.join(S, '_rms_all.txt')
    with open(txt, 'w') as f:
        subprocess.run([os.path.join(S, 'rms'), video, '0', str(dur), '25'], stdout=f, check=True)
    rows = []
    for l in open(txt):
        p = l.split()
        if len(p) == 2:
            rows.append((float(p[0]), float(p[1])))
    vals = sorted(r[1] for r in rows)
    thr = max(vals[len(vals)//10]*2.0, vals[len(vals)//2]*0.15)
    gaps, start = [], None
    for t, v in rows:
        if v < thr:
            if start is None:
                start = t
        else:
            if start is not None:
                if t-start >= 0.25:
                    gaps.append((start, t))
                start = None
    if start is not None:
        gaps.append((start, rows[-1][0]))
    return gaps


def make_chunks(dur, gaps):
    """在静音中点切块"""
    mids = [ (a+b)/2 for a, b in gaps ]
    chunks, cur = [], 0.0
    while cur < dur - 0.5:
        hi = cur + MAXCHUNK
        if hi >= dur:
            chunks.append((cur, dur)); break
        # 取 [cur+MINCHUNK, hi] 区间里最靠后的静音中点
        cands = [m for m in mids if cur + MINCHUNK <= m <= hi]
        nxt = cands[-1] if cands else hi
        chunks.append((cur, nxt))
        cur = nxt
    return chunks


def run_asr(video, a, b, idx):
    tmp = os.path.join(S, '_chunk%d.mov' % idx)
    subprocess.run([os.path.join(S, 'vtool'), 'trim', video, str(a), str(b), tmp],
                   check=True, stdout=subprocess.DEVNULL)
    if os.path.exists(OUT):
        os.remove(OUT)
    subprocess.run(['open', '-n', os.path.join(S, 'ASRTest.app'), '--args', tmp], check=True)
    for _ in range(180):
        time.sleep(1)
        if os.path.exists(OUT):
            lines = open(OUT).read().split('\n')
            if any(l.startswith('text:') for l in lines) or any(l.startswith('error') for l in lines):
                break
    words = []
    if os.path.exists(OUT):
        for l in open(OUT):
            p = l.rstrip('\n').split('\t')
            if len(p) == 3:
                try:
                    words.append((float(p[0])+a, float(p[1]), p[2]))
                except ValueError:
                    pass
    os.remove(tmp)
    return words


def speech_regions(dur, gaps):
    """静音间隙的补集 = 有语音的区间"""
    regs, pos = [], 0.0
    for a, b in gaps:
        if a > pos + 0.05:
            regs.append((pos, a))
        pos = max(pos, b)
    if pos < dur - 0.05:
        regs.append((pos, dur))
    return regs


def find_holes(words, regs):
    """找出有语音却没有识别结果的区间"""
    ts = sorted(w[0] for w in words)
    holes = []
    for a, b in regs:
        inside = [t for t in ts if a - 0.2 <= t <= b + 0.2]
        if not inside:
            if b - a >= HOLE_MIN:
                holes.append((a, b))
            continue
        # 区间内部的大间隔
        pts = [a] + inside + [b]
        for i in range(len(pts)-1):
            if pts[i+1] - pts[i] >= max(HOLE_MIN, 1.5):
                holes.append((pts[i], pts[i+1]))
    # 合并相邻空洞
    merged = []
    for h in sorted(holes):
        if merged and h[0] - merged[-1][1] < 0.5:
            merged[-1] = (merged[-1][0], max(merged[-1][1], h[1]))
        else:
            merged.append(list(h) if False else (h[0], h[1]))
    return [(a, b) for a, b in merged if b - a >= HOLE_MIN]


def main():
    video = sys.argv[1]
    out_json = sys.argv[2]
    info = subprocess.run([os.path.join(S, 'vtool'), 'info', video],
                          capture_output=True, text=True).stdout
    dur = float([l for l in info.split('\n') if 'duration' in l][0].split(':')[1])
    gaps = rms_gaps(video, dur)
    chunks = make_chunks(dur, gaps)
    print('duration=%.2f  gaps=%d  chunks=%d' % (dur, len(gaps), len(chunks)))
    allw = []
    for i, (a, b) in enumerate(chunks):
        # 往前多截 OVERLAP 秒，识别后只保留 >= a 的词；吞头就吞在重叠区里
        lo = max(0.0, a - OVERLAP)
        w = run_asr(video, lo, b, i)
        w = [x for x in w if x[0] >= a - 0.05]
        print('  chunk %d/%d  %.1f-%.1f (读%.1f)  -> %d words  %s' %
              (i+1, len(chunks), a, b, lo, len(w), ''.join(x[2] for x in w)[:36]))
        allw.extend(w)
        sys.stdout.flush()

    # 补洞：有语音却没识别结果的区间，单独再识别一次
    regs = speech_regions(dur, gaps)
    for rnd in range(2):
        holes = find_holes(allw, regs)
        if not holes:
            break
        print('  第%d轮补洞：%d 处 %s' % (rnd+1, len(holes),
              ' '.join('%.1f-%.1f' % h for h in holes)))
        for k, (ha, hb) in enumerate(holes):
            lo, hi = max(0.0, ha - 1.0), min(dur, hb + 1.0)
            w = run_asr(video, lo, hi, 900 + rnd*50 + k)
            w = [x for x in w if ha - 0.3 <= x[0] <= hb + 0.3]
            print('     %.1f-%.1f -> %d words  %s' % (ha, hb, len(w), ''.join(x[2] for x in w)[:36]))
            allw.extend(w)
            sys.stdout.flush()
        allw.sort(key=lambda x: x[0])
    left = find_holes(allw, regs)
    if left:
        print('  [警告] 仍有空洞: %s' % ' '.join('%.1f-%.1f' % h for h in left))
    allw.sort(key=lambda x: x[0])
    json.dump({'duration': dur,
               'gaps': gaps,
               'words': [{'t': t, 'd': d, 's': s} for t, d, s in allw]},
              open(out_json, 'w'), ensure_ascii=False, indent=1)
    print('total words=%d -> %s' % (len(allw), out_json))


if __name__ == '__main__':
    main()
