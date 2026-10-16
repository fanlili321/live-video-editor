import Foundation
import AVFoundation
import AppKit

// scenescan <video> <startSec> <endSec> <stepSec>
// 逐点输出：时间、平均亮度、与上一帧的平均绝对差。用于找场景切换 / PPT 换页。
let a = CommandLine.arguments
let asset = AVURLAsset(url: URL(fileURLWithPath: a[1]))
let t0 = Double(a[2])!, t1 = Double(a[3])!, step = Double(a[4])!
let topFrac = a.count > 5 ? Double(a[5])! : 1.0   // 只比对画面上部，避开字幕

let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
gen.requestedTimeToleranceBefore = .zero
gen.requestedTimeToleranceAfter = .zero
gen.maximumSize = CGSize(width: 160, height: 160)

let cs = CGColorSpaceCreateDeviceRGB()
func gray(_ cg: CGImage) -> [Int] {
    let w = 96, h = 54
    var buf = [UInt8](repeating: 0, count: w*h*4)
    let c = CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w*4,
                      space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    c.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
    let hh = max(1, Int(Double(h)*topFrac))
    var out = [Int](repeating: 0, count: w*hh)
    for i in 0..<(w*hh) {
        out[i] = (Int(buf[i*4])*299 + Int(buf[i*4+1])*587 + Int(buf[i*4+2])*114)/1000
    }
    return out
}

var prev: [Int]? = nil
var t = t0
while t <= t1 {
    guard let cg = try? gen.copyCGImage(at: CMTime(seconds: t, preferredTimescale: 600),
                                        actualTime: nil) else { t += step; continue }
    let g = gray(cg)
    let mean = g.reduce(0, +) / g.count
    var diff = 0
    if let p = prev {
        var s = 0
        for i in 0..<g.count { s += abs(g[i] - p[i]) }
        diff = s / g.count
    }
    print(String(format: "%.2f\t%d\t%d", t, mean, diff))
    prev = g
    t += step
}
