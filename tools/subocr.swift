// 从烧录字幕的成片里把字幕文字读出来。
//
// 用途：拿人工制作的成片与管线产出做对比时，人工那边只有 mp4，没有字幕文件。
// 做法：按固定间隔取帧 -> 裁出字幕带 -> Vision OCR -> 合并连续相同的文字。
//
// 用法：subocr <视频> [起点 终点 步长]   默认 0 到片尾，步长 0.4s
// 输出：每行 `开始秒\t结束秒\t文字`
import Foundation
import AVFoundation
import Vision
import CoreImage
import AppKit

let a = CommandLine.arguments
guard a.count >= 2 else { FileHandle.standardError.write("用法: subocr <视频> [起 止 步长]\n".data(using:.utf8)!); exit(2) }
let url = URL(fileURLWithPath: a[1])
let asset = AVURLAsset(url: url)
let dur = CMTimeGetSeconds(asset.duration)
let t0 = a.count > 2 ? Double(a[2])! : 0.0
let t1 = a.count > 3 ? Double(a[3])! : dur
let step = a.count > 4 ? Double(a[4])! : 0.4

let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
// 允许零容差之外的一点偏差，否则逐帧解码极慢
gen.requestedTimeToleranceBefore = CMTime(seconds: 0.05, preferredTimescale: 600)
gen.requestedTimeToleranceAfter  = CMTime(seconds: 0.05, preferredTimescale: 600)

func ocrBand(_ cg: CGImage) -> String {
    let W = cg.width, H = cg.height
    // 字幕在下方：横版取下 26%，竖版取 58%~82%（竖版下方是留白/口播区）
    // 字幕位置是**已知规格**，不该靠「取画面里最大的文字」猜——
    // PPT 满屏时幻灯片标题比字幕还大，02 号因此噪声高达 48%。
    // 横版：bottom=75、em=133.5（06-规格/视觉规格标定-横版.md）
    // 竖版：bottom=569、em=75
    let isPortrait = H > W
    let bottom = isPortrait ? 569.0 * Double(H) / 1920.0 : 75.0 * Double(H) / 1080.0
    let em     = isPortrait ? 75.0 * Double(H) / 1920.0 : 133.5 * Double(H) / 1080.0
    let pad    = em * 0.45                       // 上下各留些余量，容纳描边和不同基线
    let y = max(0, Int(Double(H) - bottom - em - pad))
    let h = min(H - y, Int(em + pad * 2))
    guard let band = cg.cropping(to: CGRect(x: 0, y: y, width: W, height: h)) else { return "" }
    let req = VNRecognizeTextRequest()
    req.recognitionLanguages = ["zh-Hans", "en-US"]
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = false      // 字幕是既定文本，纠错只会改坏
    let handler = VNImageRequestHandler(cgImage: band, options: [:])
    try? handler.perform([req])
    guard let obs = req.results else { return "" }
    // 只要最大的那一行——字幕字号远大于画面里其它文字（PPT 正文、水印）
    var best = ""; var bestH = 0.0
    for o in obs {
        guard let c = o.topCandidates(1).first else { continue }
        let hh = Double(o.boundingBox.height)
        if hh > bestH { bestH = hh; best = c.string }
    }
    // 带子已按规格裁到只剩字幕行，不再需要「够大才算」的阈值——
    // 那个阈值在窄带里反而会把正常字幕滤掉（相对高度变大了）。
    return best     // 太小的判定为非字幕
}

var out: [(Double, Double, String)] = []
var t = t0
while t < t1 {
    if let cg = try? gen.copyCGImage(at: CMTime(seconds: t, preferredTimescale: 600), actualTime: nil) {
        let s = ocrBand(cg).trimmingCharacters(in: .whitespacesAndNewlines)
        if !s.isEmpty {
            if var last = out.last, last.2 == s {
                last.1 = t + step; out[out.count-1] = last
            } else {
                out.append((t, t + step, s))
            }
        }
    }
    t += step
}
for (s, e, txt) in out {
    print(String(format: "%.2f\t%.2f\t%@", s, e, txt))
}
