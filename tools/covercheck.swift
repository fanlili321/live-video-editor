import Foundation
import AppKit

// covercheck <成品封面> <模板> [安全距离px]
// 标题 = 成品 - 模板 的差分（模板只有底图和人物，成品多了标题），
// 比对模板里人物轮廓的逐行左边界 / 顶边，报告最小间距。
// 差分法比「按颜色找米黄字」可靠：人物的皮肤和高光也是米黄色系。

let a = CommandLine.arguments
guard a.count >= 3 else { print("用法: covercheck <成品> <模板> [安全距离]"); exit(2) }
let SAFE = a.count > 3 ? Int(a[3])! : 40

func load(_ p: String) -> (Int, Int, [UInt8]) {
    guard let im = NSImage(contentsOfFile: p),
          let cg = im.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        FileHandle.standardError.write("读不到 \(p)\n".data(using: .utf8)!); exit(1)
    }
    let w = cg.width, h = cg.height
    var b = [UInt8](repeating: 0, count: w*h*4)
    let ctx = CGContext(data: &b, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w*4,
                        space: CGColorSpaceCreateDeviceRGB(),
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
    return (w, h, b)
}

let (w, h, cov) = load(a[1])
let (tw, th, tpl) = load(a[2])
guard w == tw && h == th else { print("❌ 尺寸不一致 \(w)x\(h) vs \(tw)x\(th)"); exit(1) }

func rgb(_ b: [UInt8], _ x: Int, _ y: Int) -> (Int, Int, Int) {
    let i = (y*w + x)*4; return (Int(b[i]), Int(b[i+1]), Int(b[i+2]))
}
func isYellow(_ c: (Int, Int, Int)) -> Bool { c.0 > 200 && c.1 > 150 && c.2 < 110 }

// 1) 标题 ink = 差分
var inkRows = [Int](repeating: 0, count: h)
var inkLeft = [Int](repeating: -1, count: h), inkRight = [Int](repeating: -1, count: h)
for y in 0..<h {
    for x in 0..<w {
        let c = rgb(cov, x, y), t = rgb(tpl, x, y)
        if abs(c.0-t.0) + abs(c.1-t.1) + abs(c.2-t.2) > 90 {
            inkRows[y] += 1
            if inkLeft[y] < 0 { inkLeft[y] = x }
            inkRight[y] = x
        }
    }
}
// 2) 人物轮廓：逐行左边界 + 顶边
var personLeft = [Int](repeating: -1, count: h)
var personTop = -1
for y in 0..<h {
    for x in 0..<w where isYellow(rgb(tpl, x, y)) {
        personLeft[y] = x; if personTop < 0 { personTop = y }; break
    }
}
// 3) 标题行分段
var segs = [(Int, Int)](); var st = -1
for y in 0..<h {
    let on = inkRows[y] > 2
    if on && st < 0 { st = y }
    if !on && st >= 0 { if y - st >= 8 { segs.append((st, y-1)) }; st = -1 }
}
if st >= 0 { segs.append((st, h-1)) }

guard !segs.isEmpty else { print("❌ 没检测到标题（成品与模板无差异？）"); exit(1) }
print("画布 \(w)x\(h)   人物顶 y=\(personTop)   安全距离 \(SAFE)px")
var worst = Int.max, fail = false
for (i, s) in segs.enumerated() {
    let lo = s.0, hi = s.1
    var l = w, r = -1
    for y in lo...hi { if inkLeft[y] >= 0 { l = min(l, inkLeft[y]); r = max(r, inkRight[y]) } }
    // 该行 y 范围内人物的最左边界
    var pl = Int.max
    for y in lo...hi where personLeft[y] >= 0 { pl = min(pl, personLeft[y]) }
    let wid = r - l
    var note = ""
    if pl == Int.max {
        note = "该行无人物遮挡 ✅"
    } else {
        let gap = pl - r
        worst = min(worst, gap)
        note = gap >= SAFE ? "距人物 \(gap)px ✅" : (gap >= 0 ? "距人物仅 \(gap)px ⚠️" : "压住人物 \(-gap)px ❌")
        if gap < SAFE { fail = true }
    }
    let over = wid > w - 2*50
    print(String(format: "  第%d行  y=%d..%d  ink x=%d..%d  宽 %d (%.0f%%)  %@%@",
                 i+1, lo, hi, l, r, wid, Double(wid)/Double(w)*100, note,
                 over ? "  ⚠️行宽超过安全宽度" : ""))
}
// 标题整体底边 vs 人物顶：只在「标题各行都不与人物同高」时才有意义。
// 竖版是这种情形（人物在下方，标题必须整体在其之上）；
// 横版人物在右侧，标题跟它同高但错开左右，逐行 x 间距已经判过了，这里不能重复判。
if let last = segs.last, personTop >= 0, worst == Int.max {
    let clear = personTop - last.1
    print("标题末行 ink 底 y=\(last.1)   人物顶 y=\(personTop)   净空 \(clear)px " +
          (clear >= SAFE ? "✅" : (clear >= 0 ? "⚠️ 偏紧" : "❌ 压住人物")))
    if clear < SAFE { fail = true }
}
exit(fail ? 1 : 0)
