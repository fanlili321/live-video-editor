import Foundation
import AVFoundation
import AppKit

// slidematch <录屏视频> <幻灯片目录> <起始秒> <结束秒> <步长秒> [偏移到原片的秒数]
// 逐点判断当前显示的是哪一页幻灯片。左下角人像小窗会被屏蔽后再比对。
// 输出：原片时间 \t 录屏时间 \t 命中页 \t 相似度 \t 次优页 \t 次优相似度

let a = CommandLine.arguments
let videoPath = a[1], slidesDir = a[2]
let t0 = Double(a[3])!, t1 = Double(a[4])!, step = Double(a[5])!
let offsetToSrc = a.count > 6 ? Double(a[6]) ?? 0 : 0

let W = 64, H = 36
// 人像小窗（相对比例）：左下角
let pipW = 0.20, pipH = 0.20
let cs = CGColorSpaceCreateDeviceRGB()

func feature(_ cg: CGImage) -> [Double] {
    var buf = [UInt8](repeating: 0, count: W*H*4)
    let c = CGContext(data: &buf, width: W, height: H, bitsPerComponent: 8, bytesPerRow: W*4,
                      space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    c.draw(cg, in: CGRect(x: 0, y: 0, width: W, height: H))
    var v = [Double]()
    for y in 0..<H {
        for x in 0..<W {
            // CG 原点在左下：y 小 = 画面下方
            if Double(x) < Double(W)*pipW && Double(y) < Double(H)*pipH { continue }
            let i = (y*W + x)*4
            v.append(Double(Int(buf[i])*299 + Int(buf[i+1])*587 + Int(buf[i+2])*114)/1000.0)
        }
    }
    return v
}

func ncc(_ x: [Double], _ y: [Double]) -> Double {
    let n = min(x.count, y.count)
    var mx = 0.0, my = 0.0
    for i in 0..<n { mx += x[i]; my += y[i] }
    mx /= Double(n); my /= Double(n)
    var num = 0.0, dx = 0.0, dy = 0.0
    for i in 0..<n {
        let p = x[i]-mx, q = y[i]-my
        num += p*q; dx += p*p; dy += q*q
    }
    return (dx*dy) > 0 ? num/(dx*dy).squareRoot() : -1
}

// 载入幻灯片
var slides: [(String, [Double])] = []
for f in (try! FileManager.default.contentsOfDirectory(atPath: slidesDir)).sorted() {
    guard f.lowercased().hasSuffix(".jpeg") || f.lowercased().hasSuffix(".jpg")
       || f.lowercased().hasSuffix(".png") else { continue }
    guard let img = NSImage(contentsOfFile: slidesDir + "/" + f),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { continue }
    slides.append((String(f.split(separator: ".")[0]), feature(cg)))
}
FileHandle.standardError.write("载入 \(slides.count) 页幻灯片\n".data(using: .utf8)!)

let asset = AVURLAsset(url: URL(fileURLWithPath: videoPath))
let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
gen.requestedTimeToleranceBefore = CMTime(seconds: 0.2, preferredTimescale: 600)
gen.requestedTimeToleranceAfter = CMTime(seconds: 0.2, preferredTimescale: 600)
gen.maximumSize = CGSize(width: 320, height: 320)

var t = t0
while t <= t1 {
    guard let cg = try? gen.copyCGImage(at: CMTime(seconds: t, preferredTimescale: 600),
                                        actualTime: nil) else { t += step; continue }
    let f = feature(cg)
    var scored = slides.map { ($0.0, ncc(f, $0.1)) }
    scored.sort { $0.1 > $1.1 }
    print(String(format: "%.2f\t%.2f\t%@\t%.4f\t%@\t%.4f",
                 t + offsetToSrc, t, scored[0].0, scored[0].1, scored[1].0, scored[1].1))
    t += step
}
