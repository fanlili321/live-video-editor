import Foundation
import AVFoundation
import AppKit
import CoreText

// render --src X --out Y --crop x,y,w,h --subs subs.json [--cover cover.png]
//        [--fps 25] [--em 133.5] [--stroke 10] [--bottom 75] [--font SourceHanSansCN-Medium]
//        [--bitrate 10000000] [--coverdur 0.12]

func argv(_ k: String) -> String? {
    let a = CommandLine.arguments
    guard let i = a.firstIndex(of: k), i+1 < a.count else { return nil }
    return a[i+1]
}
func need(_ k: String) -> String {
    guard let v = argv(k) else { fatalError("missing \(k)") }
    return v
}

let srcPath = need("--src"), outPath = need("--out"), subsPath = need("--subs")
let coverPath = argv("--cover")
let fps = Double(argv("--fps") ?? "25")!
let em = CGFloat(Double(argv("--em") ?? "133.5")!)
let strokeOuter = CGFloat(Double(argv("--stroke") ?? "10")!)
let bottomGap = CGFloat(Double(argv("--bottom") ?? "75")!)
let fontName = argv("--font") ?? "SourceHanSansCN-Medium"
let bitrate = Int(argv("--bitrate") ?? "10000000")!
let coverDur = Double(argv("--coverdur") ?? "0.12")!
let noteText = argv("--note")
let noteEm = CGFloat(Double(argv("--note-em") ?? "46")!)
let noteMargin = CGFloat(Double(argv("--note-margin") ?? "42")!)
let cparts = need("--crop").split(separator: ",").map { CGFloat(Double($0)!) }
let crop = CGRect(x: cparts[0], y: cparts[1], width: cparts[2], height: cparts[3])

// 保留区间（源片秒）。未给则整段保留。
var keeps: [(Double, Double)] = []
if let ks = argv("--keep") {
    for seg in ks.split(separator: ",") {
        let p = seg.split(separator: "-").map { Double($0)! }
        keeps.append((p[0], p[1]))
    }
}
func srcToOut(_ t: Double) -> Double? {
    if keeps.isEmpty { return t }
    var acc = 0.0
    for (a, b) in keeps {
        if t < a { return nil }
        if t < b { return acc + (t - a) }
        acc += b - a
    }
    return nil
}

let canvasP = (argv("--canvas") ?? "1920,1080").split(separator: ",").map { Int($0)! }
let OUTW = canvasP[0], OUTH = canvasP[1]
// 视频带（输出坐标，top-origin）。默认铺满画布。
let bandP = (argv("--band") ?? "0,0,\(OUTW),\(OUTH)").split(separator: ",").map { CGFloat(Double($0)!) }
let bandRectTop = CGRect(x: bandP[0], y: bandP[1], width: bandP[2], height: bandP[3])
// 视频内标题（竖版常驻）
let titleText = argv("--title")
let titleEm = CGFloat(Double(argv("--title-em") ?? "139")!)
let titlePitch = CGFloat(Double(argv("--title-pitch") ?? "166")!)
let titleInkBottom = CGFloat(Double(argv("--title-inkbottom") ?? "600")!)
let titleFontName = argv("--title-font") ?? "SourceHanSansCN-Bold"
let titleRGB = (argv("--title-rgb") ?? "250,244,198").split(separator: ",").map { CGFloat(Double($0)!)/255 }
// 字幕样式：默认黑字白描边；给了 --shadow-alpha 就改成「填充色 + 半透明阴影」
let subFill = (argv("--sub-fill") ?? "0,0,0").split(separator: ",").map { CGFloat(Double($0)!)/255 }
let shadowAlpha = CGFloat(Double(argv("--shadow-alpha") ?? "0")!)
let shadowBlur = CGFloat(Double(argv("--shadow-blur") ?? "12")!)
let shadowDX = CGFloat(Double(argv("--shadow-dx") ?? "0")!)
let shadowDY = CGFloat(Double(argv("--shadow-dy") ?? "6")!)   // 视觉上向下的像素数
// PPT 区间："起-止:图片路径,起-止:图片路径"（成片时间）
struct PptSeg { let start: Double; let end: Double; let img: CGImage }
var pptSegs: [PptSeg] = []
let previewAt = argv("--preview-at").flatMap { Double($0) }
let previewOut = argv("--preview-out")
let dev = CGColorSpaceCreateDeviceRGB()

// ---------- 1. 字幕预渲染 ----------
struct Sub { let img: CGImage; let rect: CGRect; let start: Double; let end: Double; let text: String }

guard let font = NSFont(name: fontName, size: em) else { fatalError("font \(fontName) not found") }
let subsData = try! Data(contentsOf: URL(fileURLWithPath: subsPath))
let subsJSON = try! JSONSerialization.jsonObject(with: subsData) as! [String: Any]
let rawLines = subsJSON["lines"] as! [[String: Any]]

var subs: [Sub] = []
var maxWidth: CGFloat = 0
for l in rawLines {
    let text = l["text"] as! String
    let start = l["start"] as! Double, end = l["end"] as! Double
    let subColor = NSColor(cgColor: CGColor(colorSpace: dev,
                     components: [subFill[0], subFill[1], subFill[2], 1])!)!
    // ⚠️ CoreText 用属性串里的颜色，不认 ctx.setStrokeColor/setFillColor。
    //    描边和填充必须用两个各自带颜色的属性串，否则描边会被画成填充色。
    let ct = CTLineCreateWithAttributedString(
        NSAttributedString(string: text, attributes: [.font: font, .foregroundColor: subColor]))
    let ctStroke = CTLineCreateWithAttributedString(
        NSAttributedString(string: text, attributes: [.font: font, .foregroundColor: NSColor.white]))
    let ink = CTLineGetBoundsWithOptions(ct, .useGlyphPathBounds)
    let pad = shadowAlpha > 0 ? (shadowBlur + max(abs(shadowDX), abs(shadowDY)) + 6) : strokeOuter
    let iw = ceil(ink.width + 2*pad), ih = ceil(ink.height + 2*pad)
    maxWidth = max(maxWidth, ceil(ink.width))
    let c = CGContext(data: nil, width: Int(iw), height: Int(ih), bitsPerComponent: 8,
                      bytesPerRow: 0, space: dev,
                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    c.setAllowsAntialiasing(true)
    c.setLineJoin(.round); c.setLineCap(.round)
    let bx = pad - ink.origin.x, by = pad - ink.origin.y
    if shadowAlpha > 0 {
        // 半透明阴影 + 实色填充
        c.saveGState()
        c.setShadow(offset: CGSize(width: shadowDX, height: -shadowDY), blur: shadowBlur,
                    color: CGColor(colorSpace: dev, components: [0, 0, 0, shadowAlpha])!)
        c.setTextDrawingMode(.fill)
        c.setFillColor(CGColor(colorSpace: dev, components: [subFill[0], subFill[1], subFill[2], 1])!)
        c.textPosition = CGPoint(x: bx, y: by)
        CTLineDraw(ct, c)
        c.restoreGState()
    } else {
        // pass 1: 白色外描边（用白色属性串，见上面注释）
        c.saveGState()
        c.setTextDrawingMode(.stroke)
        c.setLineWidth(strokeOuter * 2)
        c.setStrokeColor(CGColor(colorSpace: dev, components: [1, 1, 1, 1])!)
        c.textPosition = CGPoint(x: bx, y: by)
        CTLineDraw(ctStroke, c)
        c.restoreGState()
        // pass 2: 填充
        c.saveGState()
        c.setTextDrawingMode(.fill)
        c.setFillColor(CGColor(colorSpace: dev, components: [subFill[0], subFill[1], subFill[2], 1])!)
        c.textPosition = CGPoint(x: bx, y: by)
        CTLineDraw(ct, c)
        c.restoreGState()
    }
    let img = c.makeImage()!
    // ink 底边固定在距画面底 (bottomGap + strokeOuter) 处，换样式不改变文字位置
    let inkBottomFromFrameBottom = bottomGap + strokeOuter
    subs.append(Sub(img: img, rect: CGRect(x: (CGFloat(OUTW) - iw)/2,
                                           y: inkBottomFromFrameBottom - pad,
                                           width: iw, height: ih),
                    start: start, end: end, text: text))
}
FileHandle.standardError.write("字幕预渲染 \(subs.count) 行，最宽 \(Int(maxWidth))px (\(String(format:"%.1f", maxWidth/CGFloat(OUTW)*100))% 画面宽)\n".data(using: .utf8)!)

// 载入 PPT 区间
if let spec = argv("--ppt") {
    for item in spec.split(separator: ",") {
        let parts = item.split(separator: ":", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { continue }
        let rg = parts[0].split(separator: "-").map { Double($0)! }
        guard let ni = NSImage(contentsOfFile: parts[1]),
              let cg = ni.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            FileHandle.standardError.write("PPT 图片读不到: \(parts[1])\n".data(using: .utf8)!)
            continue
        }
        pptSegs.append(PptSeg(start: rg[0], end: rg[1], img: cg))
    }
    FileHandle.standardError.write("PPT 区间 \(pptSegs.count) 段\n".data(using: .utf8)!)
}

// 视频内标题（常驻，竖版用；ink 底边锚定在 titleInkBottom）
var titleImgs: [(CGImage, CGRect)] = []
if let tt = titleText, let tf = NSFont(name: titleFontName, size: titleEm) {
    let lines = tt.split(separator: "|").map(String.init)
    var cts: [CTLine] = [], inks: [CGRect] = []
    let tcolor = NSColor(cgColor: CGColor(colorSpace: dev,
                       components: [titleRGB[0], titleRGB[1], titleRGB[2], 1])!)!
    for l in lines {
        let attr = NSAttributedString(string: l, attributes: [.font: tf, .foregroundColor: tcolor])
        let ct = CTLineCreateWithAttributedString(attr)
        cts.append(ct); inks.append(CTLineGetBoundsWithOptions(ct, .useGlyphPathBounds))
    }
    let n = lines.count
    for i in 0..<n {
        let ink = inks[i]
        // 自底向上堆叠：最后一行 ink 底 = titleInkBottom
        let inkBottomTop = titleInkBottom - CGFloat(n-1-i) * titlePitch
        let inkTopTop = inkBottomTop - ink.height
        let iw = ceil(ink.width) + 4, ih = ceil(ink.height) + 4
        let c = CGContext(data: nil, width: Int(iw), height: Int(ih), bitsPerComponent: 8,
                          bytesPerRow: 0, space: dev,
                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        c.setFillColor(CGColor(colorSpace: dev, components: [titleRGB[0], titleRGB[1], titleRGB[2], 1])!)
        c.setTextDrawingMode(.fill)
        c.textPosition = CGPoint(x: 2 - ink.origin.x, y: 2 - ink.origin.y)
        CTLineDraw(cts[i], c)
        let img = c.makeImage()!
        let rect = CGRect(x: (CGFloat(OUTW) - iw)/2,
                          y: CGFloat(OUTH) - inkTopTop - ih + 2,
                          width: iw, height: ih)
        titleImgs.append((img, rect))
    }
    FileHandle.standardError.write("视频内标题 \(lines.count) 行，em=\(titleEm)\n".data(using: .utf8)!)
}

// 角标（常驻，右上角）
var noteImg: CGImage? = nil
var noteRect = CGRect.zero
if let nt = noteText, let nf = NSFont(name: fontName, size: noteEm) {
    let ns = CGFloat(4.0)
    let ct = CTLineCreateWithAttributedString(NSAttributedString(string: nt, attributes: [.font: nf]))
    let ink = CTLineGetBoundsWithOptions(ct, .useGlyphPathBounds)
    let iw = ceil(ink.width + 2*ns), ih = ceil(ink.height + 2*ns)
    let c = CGContext(data: nil, width: Int(iw), height: Int(ih), bitsPerComponent: 8,
                      bytesPerRow: 0, space: dev,
                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    c.setLineJoin(.round); c.setLineCap(.round)
    let bx = ns - ink.origin.x, by = ns - ink.origin.y
    c.saveGState(); c.setTextDrawingMode(.stroke); c.setLineWidth(ns*2)
    c.setStrokeColor(CGColor(colorSpace: dev, components: [1,1,1,1])!)
    c.textPosition = CGPoint(x: bx, y: by); CTLineDraw(ct, c); c.restoreGState()
    c.saveGState(); c.setTextDrawingMode(.fill)
    c.setFillColor(CGColor(colorSpace: dev, components: [0,0,0,1])!)
    c.textPosition = CGPoint(x: bx, y: by); CTLineDraw(ct, c); c.restoreGState()
    noteImg = c.makeImage()
    noteRect = CGRect(x: CGFloat(OUTW) - iw - noteMargin,
                      y: CGFloat(OUTH) - ih - noteMargin, width: iw, height: ih)
    FileHandle.standardError.write("角标: \(nt)  \(Int(iw))x\(Int(ih))px\n".data(using: .utf8)!)
}

// ---------- 2. 素材 ----------
let srcAsset = AVURLAsset(url: URL(fileURLWithPath: srcPath))
guard let vTrack = srcAsset.tracks(withMediaType: .video).first else { fatalError("no video track") }
let srcDur = CMTimeGetSeconds(srcAsset.duration)

// 音频：前面插入 coverDur 的静音
let comp = AVMutableComposition()
var audioReader: AVAssetReader? = nil
var audioOut: AVAssetReaderTrackOutput? = nil
if let aTrack = srcAsset.tracks(withMediaType: .audio).first {
    let ct = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)!
    let cd = CMTime(seconds: coverDur, preferredTimescale: 600)
    if coverDur > 0 { ct.insertEmptyTimeRange(CMTimeRange(start: .zero, duration: cd)) }
    if keeps.isEmpty {
        try! ct.insertTimeRange(CMTimeRange(start: .zero, duration: srcAsset.duration), of: aTrack, at: cd)
    } else {
        var at = cd
        for (a, b) in keeps {
            let r = CMTimeRange(start: CMTime(seconds: a, preferredTimescale: 600),
                                duration: CMTime(seconds: b - a, preferredTimescale: 600))
            try! ct.insertTimeRange(r, of: aTrack, at: at)
            at = CMTimeAdd(at, r.duration)
        }
    }
    let r = try! AVAssetReader(asset: comp)
    let o = AVAssetReaderTrackOutput(track: comp.tracks(withMediaType: .audio).first!,
                                     outputSettings: [AVFormatIDKey: kAudioFormatLinearPCM])
    r.add(o); audioReader = r; audioOut = o
}

let vReader = try! AVAssetReader(asset: srcAsset)
let vOut = AVAssetReaderTrackOutput(track: vTrack, outputSettings: [
    kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA)])
vOut.alwaysCopiesSampleData = false
vReader.add(vOut)

// ---------- 2.5 单帧预览 ----------
if let pAt = previewAt, let pOut = previewOut {
    func outToSrc(_ t: Double) -> Double {
        if keeps.isEmpty { return t }
        var acc = 0.0
        for (a, b) in keeps {
            if t <= acc + (b - a) { return a + (t - acc) }
            acc += b - a
        }
        return keeps.last!.1
    }
    let srcT = outToSrc(pAt)
    let gen = AVAssetImageGenerator(asset: srcAsset)
    gen.appliesPreferredTrackTransform = true
    gen.requestedTimeToleranceBefore = .zero
    gen.requestedTimeToleranceAfter = .zero
    let srcImg = try! gen.copyCGImage(at: CMTime(seconds: srcT, preferredTimescale: 600), actualTime: nil)
    let sw = srcImg.width, sh = srcImg.height
    let c = CGContext(data: nil, width: OUTW, height: OUTH, bitsPerComponent: 8, bytesPerRow: 0,
                      space: dev, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    c.interpolationQuality = .high
    c.setFillColor(CGColor(colorSpace: dev, components: [0, 0, 0, 1])!)
    c.fill(CGRect(x: 0, y: 0, width: OUTW, height: OUTH))
    let cgCropY = CGFloat(sh) - crop.origin.y - crop.height
    let scale = bandRectTop.width / crop.width
    let bandCG = CGRect(x: bandRectTop.origin.x,
                        y: CGFloat(OUTH) - bandRectTop.origin.y - bandRectTop.height,
                        width: bandRectTop.width, height: bandRectTop.height)
    c.saveGState(); c.clip(to: bandCG)
    if let seg = pptSegs.first(where: { pAt >= $0.start && pAt < $0.end }) {
        c.draw(seg.img, in: bandCG)
    } else {
        c.translateBy(x: bandCG.minX - crop.origin.x * scale, y: bandCG.minY - cgCropY * scale)
        c.draw(srcImg, in: CGRect(x: 0, y: 0, width: CGFloat(sw)*scale, height: CGFloat(sh)*scale))
    }
    c.restoreGState()
    for (img, r) in titleImgs { c.draw(img, in: r) }
    for sb in subs where pAt >= sb.start && pAt < sb.end { c.draw(sb.img, in: sb.rect); break }
    let outImg = c.makeImage()!
    try! NSBitmapImageRep(cgImage: outImg).representation(using: .png, properties: [:])!
        .write(to: URL(fileURLWithPath: pOut))
    let shown = subs.first(where: { pAt >= $0.start && pAt < $0.end })?.text ?? "(无字幕)"
    print("预览帧 \(pOut)  成片\(String(format: "%.2f", pAt))s / 源\(String(format: "%.2f", srcT))s  字幕: \(shown)")
    exit(0)
}

// ---------- 3. 输出 ----------
try? FileManager.default.removeItem(atPath: outPath)
let writer = try! AVAssetWriter(outputURL: URL(fileURLWithPath: outPath), fileType: .mp4)
writer.shouldOptimizeForNetworkUse = true

let vIn = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264,
    AVVideoWidthKey: OUTW, AVVideoHeightKey: OUTH,
    AVVideoCompressionPropertiesKey: [
        AVVideoAverageBitRateKey: bitrate,
        AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
        AVVideoMaxKeyFrameIntervalKey: Int(fps*2),
        AVVideoAllowFrameReorderingKey: true,
    ],
])
vIn.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: vIn, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA),
    kCVPixelBufferWidthKey as String: OUTW,
    kCVPixelBufferHeightKey as String: OUTH,
])
writer.add(vIn)

var aIn: AVAssetWriterInput? = nil
if audioReader != nil {
    let i = AVAssetWriterInput(mediaType: .audio, outputSettings: [
        AVFormatIDKey: kAudioFormatMPEG4AAC,
        AVNumberOfChannelsKey: 2,
        AVSampleRateKey: 44100,
        AVEncoderBitRateKey: 192000,
    ])
    i.expectsMediaDataInRealTime = false
    writer.add(i); aIn = i
}

writer.startWriting()
writer.startSession(atSourceTime: .zero)
vReader.startReading()
audioReader?.startReading()

// ---------- 4. 绘制 ----------
var coverImg: CGImage? = nil
if let cp = coverPath, let ni = NSImage(contentsOfFile: cp) {
    coverImg = ni.cgImage(forProposedRect: nil, context: nil, hints: nil)
}

func newBuffer() -> CVPixelBuffer {
    var pb: CVPixelBuffer?
    CVPixelBufferPoolCreatePixelBuffer(nil, adaptor.pixelBufferPool!, &pb)
    return pb!
}
func context(_ pb: CVPixelBuffer) -> CGContext {
    CVPixelBufferLockBaseAddress(pb, [])
    return CGContext(data: CVPixelBufferGetBaseAddress(pb), width: OUTW, height: OUTH,
                     bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(pb),
                     space: dev,
                     bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue |
                                 CGBitmapInfo.byteOrder32Little.rawValue)!
}

let frameDur = 1.0/fps
let coverFrames = Int((coverDur * fps).rounded())
var outFrame = 0

let group = DispatchGroup()

// --- 视频 ---
group.enter()
let vq = DispatchQueue(label: "v")
vIn.requestMediaDataWhenReady(on: vq) {
    while vIn.isReadyForMoreMediaData {
        // 封面帧
        if outFrame < coverFrames {
            let pb = newBuffer()
            let c = context(pb)
            c.setFillColor(CGColor(colorSpace: dev, components: [0,0,0,1])!)
            c.fill(CGRect(x: 0, y: 0, width: OUTW, height: OUTH))
            if let ci = coverImg {
                c.draw(ci, in: CGRect(x: 0, y: 0, width: OUTW, height: OUTH))
            }
            CVPixelBufferUnlockBaseAddress(pb, [])
            adaptor.append(pb, withPresentationTime: CMTime(seconds: Double(outFrame)*frameDur,
                                                            preferredTimescale: 90000))
            outFrame += 1
            continue
        }
        guard let sb = vOut.copyNextSampleBuffer(), let src = CMSampleBufferGetImageBuffer(sb) else {
            vIn.markAsFinished(); group.leave(); return
        }
        let pts = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sb))
        guard let outT = srcToOut(pts) else { CMSampleBufferInvalidate(sb); continue }
        CVPixelBufferLockBaseAddress(src, .readOnly)
        let sw = CVPixelBufferGetWidth(src), sh = CVPixelBufferGetHeight(src)
        let sctx = CGContext(data: CVPixelBufferGetBaseAddress(src), width: sw, height: sh,
                             bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(src),
                             space: dev,
                             bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue |
                                         CGBitmapInfo.byteOrder32Little.rawValue)!
        let srcImg = sctx.makeImage()!
        CVPixelBufferUnlockBaseAddress(src, .readOnly)

        let pb = newBuffer()
        let c = context(pb)
        c.interpolationQuality = .high
        c.setFillColor(CGColor(colorSpace: dev, components: [0, 0, 0, 1])!)
        c.fill(CGRect(x: 0, y: 0, width: OUTW, height: OUTH))
        // 裁剪并绘入视频带（crop 用源画面 top-origin；band 用输出 top-origin）
        let cgCropY = CGFloat(sh) - crop.origin.y - crop.height
        let scale = bandRectTop.width / crop.width
        let bandCG = CGRect(x: bandRectTop.origin.x,
                            y: CGFloat(OUTH) - bandRectTop.origin.y - bandRectTop.height,
                            width: bandRectTop.width, height: bandRectTop.height)
        c.saveGState()
        c.clip(to: bandCG)
        if let seg = pptSegs.first(where: { outT >= $0.start && outT < $0.end }) {
            // PPT 全屏替换：幻灯片是 16:9，直接铺满画面带
            c.draw(seg.img, in: bandCG)
        } else {
            c.translateBy(x: bandCG.minX - crop.origin.x * scale, y: bandCG.minY - cgCropY * scale)
            c.draw(srcImg, in: CGRect(x: 0, y: 0, width: CGFloat(sw)*scale, height: CGFloat(sh)*scale))
        }
        c.restoreGState()
        // 视频内标题
        for (img, r) in titleImgs { c.draw(img, in: r) }
        // 字幕
        for s in subs where outT >= s.start && outT < s.end {
            c.draw(s.img, in: s.rect)
            break
        }
        if let ni = noteImg { c.draw(ni, in: noteRect) }
        CVPixelBufferUnlockBaseAddress(pb, [])
        adaptor.append(pb, withPresentationTime: CMTime(seconds: coverDur + outT, preferredTimescale: 90000))
        outFrame += 1
        if outFrame % 250 == 0 {
            FileHandle.standardError.write("  \(outFrame) 帧 / 源 \(String(format: "%.1f", pts))s -> 成片 \(String(format: "%.1f", outT))s\n".data(using: .utf8)!)
        }
        CMSampleBufferInvalidate(sb)
    }
}

// --- 音频 ---
if let ai = aIn, let ao = audioOut {
    group.enter()
    let aq = DispatchQueue(label: "a")
    ai.requestMediaDataWhenReady(on: aq) {
        while ai.isReadyForMoreMediaData {
            guard let sb = ao.copyNextSampleBuffer() else {
                ai.markAsFinished(); group.leave(); return
            }
            ai.append(sb)
        }
    }
}

group.wait()
let sem = DispatchSemaphore(value: 0)
writer.finishWriting { sem.signal() }
sem.wait()
if writer.status != .completed {
    FileHandle.standardError.write("FAILED: \(String(describing: writer.error))\n".data(using: .utf8)!)
    exit(1)
}
print("wrote \(outPath)  frames=\(outFrame)  duration=\(String(format: "%.2f", coverDur + srcDur))s")
