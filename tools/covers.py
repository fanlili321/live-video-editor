#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""取封面：把设计侧（Codex 那条线）出好、传到飞书的成品封面拉到本地，按行号归位。

用法：
    python3 covers.py --config <期次配置.json>                    # 自动找本期封面文件夹
    python3 covers.py --config <期次配置.json> --folder <token>   # 指定文件夹（总目录或当期子目录都行）

封面**不由本管线排版**：设计侧按条出成品图（标题已经印在图上），传到飞书
「02-新版视频封面」（`SnrQfpGkjlj323dcN7ccH1lSnbg`）下的「<期> 期」子文件夹，命名是 `<标题> 16-9.png`（横版）、`<标题> 9-16.png`（竖版）。
本脚本按标题把它们对到脚本表行号上，下载成 `<root>/封面/<行号>-横版.png`、`-竖版.png`，
尺寸不对就放大到 1920×1080 / 1080×1920，最后把 `封面来源=外部` 写回期次配置。
渲染时 `batch.py` 直接取用，不再排标题。

⚠️ 按**标题**匹配，不是按顺序——飞书文件夹里的顺序和脚本表行号没有关系。
标题以钉大反馈定的那个为准，与期次配置里的 title 一致。
"""
import json, os, re, subprocess, sys, difflib

SIZE = {'横版': (1920, 1080), '竖版': (1080, 1920)}
SUFFIX = {'横版': '16-9', '竖版': '9-16'}


def sh(cmd, cwd=None):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


def norm(t):
    return re.sub(r'[^一-鿿A-Za-z0-9]', '', t)


COVER_ROOT = 'SnrQfpGkjlj323dcN7ccH1lSnbg'      # 飞书「02-新版视频封面」，按期分子文件夹


def listdir(folder):
    r = sh(['lark-cli', 'drive', 'files', 'list',
            '--params', json.dumps({'folder_token': folder, 'page_size': 200})])
    return json.loads(r.stdout[r.stdout.index('{'):])['data']['files']


def episode_folder(root, ep):
    """在「02-新版视频封面」下找本期的子文件夹。子文件夹名有「474 期」「475期」两种写法"""
    subs = [f for f in listdir(root) if f['type'] == 'folder']
    for f in subs:
        if re.sub(r'\s', '', f['name']).startswith(str(ep)):
            return f
    raise SystemExit('封面总目录里没有 %s 期的文件夹，现有：%s'
                     % (ep, '、'.join(f['name'] for f in subs)))


def pick(files, title, kind):
    """在文件列表里找这一条的封面。先精确匹配，再退到最相似的一个"""
    want = norm(title) + SUFFIX[kind].replace('-', '')
    best, score = None, 0.0
    for f in files:
        n = norm(f['name'].replace('.png', ''))
        if not n.endswith(SUFFIX[kind].replace('-', '')):
            continue
        if n == want:
            return f, 1.0
        s = difflib.SequenceMatcher(a=want, b=n).ratio()
        if s > score:
            best, score = f, s
    return (best, score) if score >= 0.75 else (None, score)


def size_of(p):
    r = sh(['sips', '-g', 'pixelWidth', '-g', 'pixelHeight', p])
    m = dict(re.findall(r'(pixelWidth|pixelHeight): (\d+)', r.stdout))
    return int(m.get('pixelWidth', 0)), int(m.get('pixelHeight', 0))


def main():
    a = sys.argv
    cfgp = a[a.index('--config')+1]
    cfg = json.load(open(cfgp, encoding='utf-8'))
    folder = a[a.index('--folder')+1] if '--folder' in a else cfg.get('封面飞书目录')
    if not folder:
        f = episode_folder(COVER_ROOT, cfg['episode'])
        folder = f['token']
        print('本期封面文件夹：%s' % f['name'])
    dst = cfg.get('封面目录') or os.path.join(cfg['root'], '封面')
    os.makedirs(dst, exist_ok=True)
    def covers_in(fs):
        return [f for f in fs if f['type'] == 'file' and re.search(r'(16-9|9-16)', f['name'])]
    files = covers_in(listdir(folder))
    if not files:      # 给的是总目录（里面还放着交接文档之类），自动进本期子文件夹
        f = episode_folder(folder, cfg['episode'])
        folder = f['token']
        files = covers_in(listdir(folder))
        print('本期封面文件夹：%s' % f['name'])
    print('飞书封面目录 %d 个文件' % len(files))

    bad = 0
    for row, v in sorted(cfg['rows'].items(), key=lambda x: int(x[0])):
        for kind in ('横版', '竖版'):
            f, score = pick(files, v['title'], kind)
            out = os.path.join(dst, '%02d-%s.png' % (int(row), kind))
            if not f:
                print('  行%-3s %-4s ❌ 没找到（最接近 %.2f）　%s' % (row, kind, score, v['title']))
                bad += 1
                continue
            # ⚠️ lark-cli 只接受**相对路径**，绝对路径会被拒；所以 cd 到目标目录再下
            r = sh(['lark-cli', 'drive', '+download', '--file-token', f['token'],
                    '--output', './' + os.path.basename(out), '--overwrite'], cwd=dst)
            if not os.path.exists(out):
                print('  行%-3s %-4s ❌ 下载失败 %s' % (row, kind, (r.stderr or r.stdout)[:80]))
                bad += 1
                continue
            w, h = size_of(out)
            W, H = SIZE[kind]
            note = '%dx%d' % (w, h)
            if (w, h) != (W, H):
                # 出图现在是 1672×941 / 941×1672，比例一致，只是小一号；按目标尺寸重采样
                if abs(w / h - W / H) > 0.01:
                    print('  行%-3s %-4s ⚠️ 比例不对 %dx%d，需设计侧重出' % (row, kind, w, h))
                    bad += 1
                    continue
                sh(['sips', '--resampleHeightWidth', str(H), str(W), out])
                note += ' → %dx%d' % size_of(out)
            print('  行%-3s %-4s ✅ %-28s %s%s'
                  % (row, kind, f['name'][:26], note, '' if score == 1.0 else '  (模糊匹配 %.2f)' % score))

    cfg['封面来源'] = '外部'
    cfg['封面目录'] = dst
    cfg['封面飞书目录'] = folder
    json.dump(cfg, open(cfgp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('封面 -> %s　（期次配置已写入 封面来源=外部）' % dst)
    if bad:
        print('⚠️ %d 张没拿到，渲染会中断。缺的让设计侧补，或核对标题是否一致。' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
