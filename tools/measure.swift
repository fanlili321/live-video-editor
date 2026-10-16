import Foundation
import AVFoundation
import AppKit
import Vision

let args = CommandLine.arguments
func die(_ m: String) -> Never { FileHandle.standardError.write((m+"\n").data(using:.utf8)!); exit(1) }
guard args.count >= 3 else { die("usage: measure <cmd> <path> [args]\n  fullframe <video> <sec> <out.png>\n  face <image.png>\n  text <image.png> [topFrac bottomFrac]") }
let cmd = args[1]

func loadCG(_ p: String) -> CGImage {
    guard let img = NSImage(contentsOfFile: p),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { die("cannot load \(p)") }
    return cg
}

switch cmd {
case "fullframe":
    let asset = AVURLAsset(url: URL(fileURLWithPath: args[2]))
    let gen = AVAssetImageGenerator(asset: asset)
    gen.appliesPreferredTrackTransform = true
    gen.requestedTimeToleranceBefore = .zero
    gen.requestedTimeToleranceAfter = .zero
    let cg = try! gen.copyCGImage(at: CMTime(seconds: Double(args[3])!, preferredTimescale: 600), actualTime: nil)
    let rep = NSBitmapImageRep(cgImage: cg)
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: args[4]))
    print("wrote \(args[4]) \(cg.width)x\(cg.height)")

case "face":
    let cg = loadCG(args[2])
    let req = VNDetectFaceRectanglesRequest()
    try! VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    print("image: \(cg.width)x\(cg.height)")
    for (i, obs) in (req.results ?? []).enumerated() {
        let b = obs.boundingBox // normalized, origin bottom-left
        let px = b.origin.x * CGFloat(cg.width)
        let pw = b.width * CGFloat(cg.width)
        let ph = b.height * CGFloat(cg.height)
        let pyTop = (1 - b.origin.y - b.height) * CGFloat(cg.height)
        print(String(format: "face[%d] px x=%.0f y=%.0f w=%.0f h=%.0f | faceH/frameH=%.4f centerX/frameW=%.4f centerY/frameH=%.4f",
                     i, px, pyTop, pw, ph,
                     b.height, b.midX, 1 - b.midY))
    }

case "text":
    // find near-white glyph pixels that sit next to dark outline, in a vertical band
    let cg = loadCG(args[2])
    let w = cg.width, h = cg.height
    let topF = args.count > 3 ? Double(args[3])! : 0.60
    let botF = args.count > 4 ? Double(args[4])! : 1.00
    let cs = CGColorSpaceCreateDeviceRGB()
    var buf = [UInt8](repeating: 0, count: w*h*4)
    let ctx = CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w*4,
                        space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
    func lum(_ x: Int, _ y: Int) -> Int {
        let i = (y*w + x)*4
        return (Int(buf[i])*299 + Int(buf[i+1])*587 + Int(buf[i+2])*114)/1000
    }
    let y0 = Int(Double(h)*topF), y1 = min(h, Int(Double(h)*botF))
    var rowCount = [Int](repeating: 0, count: h)
    var colCount = [Int](repeating: 0, count: w)
    for y in y0..<y1 {
        for x in 2..<(w-2) {
            if lum(x,y) > 235 {
                // needs a dark pixel within 4px horizontally (outline)
                var dark = false
                for d in 1...4 where !dark {
                    if lum(max(0,x-d),y) < 90 || lum(min(w-1,x+d),y) < 90 { dark = true }
                }
                if dark { rowCount[y] += 1; colCount[x] += 1 }
            }
        }
    }
    let peak = rowCount.max() ?? 0
    let thr = max(6, peak/8)
    var rows = (y0..<y1).filter { rowCount[$0] >= thr }
    guard let rTop = rows.first, let rBot = rows.last else { print("no text found (peak=\(peak))"); exit(0) }
    let cthr = max(3, (colCount.max() ?? 0)/12)
    let cols = (0..<w).filter { colCount[$0] >= cthr }
    let cLeft = cols.first ?? 0, cRight = cols.last ?? w-1
    print("image: \(w)x\(h)  peak=\(peak)")
    print(String(format: "text bbox: x=%d..%d (w=%d)  y=%d..%d (h=%d)", cLeft, cRight, cRight-cLeft+1, rTop, rBot, rBot-rTop+1))
    print(String(format: "  glyphH/frameH = %.4f   (%.2f%% of height)", Double(rBot-rTop+1)/Double(h), Double(rBot-rTop+1)/Double(h)*100))
    print(String(format: "  bottom gap    = %d px  = %.4f of height", h-1-rBot, Double(h-1-rBot)/Double(h)))
    print(String(format: "  centerX       = %.1f px  (frame center %.1f, offset %+.1f)", Double(cLeft+cRight)/2, Double(w)/2, Double(cLeft+cRight)/2 - Double(w)/2))
    print(String(format: "  textW/frameW  = %.4f", Double(cRight-cLeft+1)/Double(w)))
    // row profile to reveal multiple lines
    var bands: [(Int,Int)] = []
    var s = -1
    for y in y0..<y1 {
        let on = rowCount[y] >= thr
        if on && s < 0 { s = y }
        if !on && s >= 0 { if y-s >= 4 { bands.append((s, y-1)) }; s = -1 }
    }
    if s >= 0 { bands.append((s, y1-1)) }
    print("line bands: \(bands.map { "\($0.0)-\($0.1)(h=\($0.1-$0.0+1))" }.joined(separator: ", "))")

case "bright":
    // bright <image> <lumThreshold> <topFrac> <botFrac>  -> bbox of bright pixels + line bands
    let cg = loadCG(args[2])
    let w = cg.width, h = cg.height
    let lt = Int(args[3])!
    let topF = Double(args[4])!, botF = Double(args[5])!
    var buf = [UInt8](repeating: 0, count: w*h*4)
    let ctx = CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w*4,
                        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
    func lum(_ x: Int, _ y: Int) -> Int {
        let i = (y*w + x)*4
        return (Int(buf[i])*299 + Int(buf[i+1])*587 + Int(buf[i+2])*114)/1000
    }
    let y0 = Int(Double(h)*topF), y1 = min(h, Int(Double(h)*botF))
    let xa = args.count > 6 ? Int(args[6])! : 0
    let xb = args.count > 7 ? Int(args[7])! : w-1
    var rowCount = [Int](repeating: 0, count: h), colCount = [Int](repeating: 0, count: w)
    for y in y0..<y1 { for x in xa...xb where lum(x,y) > lt { rowCount[y] += 1; colCount[x] += 1 } }
    let peak = rowCount.max() ?? 0
    let thr = max(4, peak/10)
    let rows = (y0..<y1).filter { rowCount[$0] >= thr }
    guard let rTop = rows.first, let rBot = rows.last else { print("none (peak=\(peak))"); exit(0) }
    let cthr = max(3, (colCount.max() ?? 0)/12)
    let cols = (0..<w).filter { colCount[$0] >= cthr }
    print("image: \(w)x\(h) peak=\(peak)")
    print(String(format: "bbox x=%d..%d (w=%d) y=%d..%d (h=%d)", cols.first!, cols.last!, cols.last!-cols.first!+1, rTop, rBot, rBot-rTop+1))
    print(String(format: "  left margin=%.4f  right margin=%.4f  top=%.4f  bottom gap=%.4f",
                 Double(cols.first!)/Double(w), Double(w-1-cols.last!)/Double(w),
                 Double(rTop)/Double(h), Double(h-1-rBot)/Double(h)))
    print(String(format: "  centerX offset = %+.1f px", Double(cols.first!+cols.last!)/2 - Double(w)/2))
    var bands: [(Int,Int)] = []; var s = -1
    for y in y0..<y1 {
        let on = rowCount[y] >= thr
        if on && s < 0 { s = y }
        if !on && s >= 0 { if y-s >= 4 { bands.append((s,y-1)) }; s = -1 }
    }
    if s >= 0 { bands.append((s,y1-1)) }
    for (i,b) in bands.enumerated() { print("  line[\(i)] y=\(b.0)..\(b.1) h=\(b.1-b.0+1)  (h/frameH=\(String(format:"%.4f", Double(b.1-b.0+1)/Double(h))))") }
    if bands.count >= 2 {
        print(String(format: "  line pitch = %d px (baseline-to-baseline)", bands[1].1 - bands[0].1))
    }

case "dark":
    // bright <image> <lumThreshold> <topFrac> <botFrac>  -> bbox of bright pixels + line bands
    let cg = loadCG(args[2])
    let w = cg.width, h = cg.height
    let lt = Int(args[3])!
    let topF = Double(args[4])!, botF = Double(args[5])!
    var buf = [UInt8](repeating: 0, count: w*h*4)
    let ctx = CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w*4,
                        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
    func lum(_ x: Int, _ y: Int) -> Int {
        let i = (y*w + x)*4
        return (Int(buf[i])*299 + Int(buf[i+1])*587 + Int(buf[i+2])*114)/1000
    }
    let y0 = Int(Double(h)*topF), y1 = min(h, Int(Double(h)*botF))
    let xa = args.count > 6 ? Int(args[6])! : 0
    let xb = args.count > 7 ? Int(args[7])! : w-1
    var rowCount = [Int](repeating: 0, count: h), colCount = [Int](repeating: 0, count: w)
    for y in y0..<y1 { for x in xa...xb where lum(x,y) < lt { rowCount[y] += 1; colCount[x] += 1 } }
    let peak = rowCount.max() ?? 0
    let thr = max(4, peak/10)
    let rows = (y0..<y1).filter { rowCount[$0] >= thr }
    guard let rTop = rows.first, let rBot = rows.last else { print("none (peak=\(peak))"); exit(0) }
    let cthr = max(3, (colCount.max() ?? 0)/12)
    let cols = (0..<w).filter { colCount[$0] >= cthr }
    print("image: \(w)x\(h) peak=\(peak)")
    print(String(format: "bbox x=%d..%d (w=%d) y=%d..%d (h=%d)", cols.first!, cols.last!, cols.last!-cols.first!+1, rTop, rBot, rBot-rTop+1))
    print(String(format: "  left margin=%.4f  right margin=%.4f  top=%.4f  bottom gap=%.4f",
                 Double(cols.first!)/Double(w), Double(w-1-cols.last!)/Double(w),
                 Double(rTop)/Double(h), Double(h-1-rBot)/Double(h)))
    print(String(format: "  centerX offset = %+.1f px", Double(cols.first!+cols.last!)/2 - Double(w)/2))
    var bands: [(Int,Int)] = []; var s = -1
    for y in y0..<y1 {
        let on = rowCount[y] >= thr
        if on && s < 0 { s = y }
        if !on && s >= 0 { if y-s >= 4 { bands.append((s,y-1)) }; s = -1 }
    }
    if s >= 0 { bands.append((s,y1-1)) }
    for (i,b) in bands.enumerated() { print("  line[\(i)] y=\(b.0)..\(b.1) h=\(b.1-b.0+1)  (h/frameH=\(String(format:"%.4f", Double(b.1-b.0+1)/Double(h))))") }
    if bands.count >= 2 {
        print(String(format: "  line pitch = %d px (baseline-to-baseline)", bands[1].1 - bands[0].1))
    }

case "scanrow":
    // scanrow <image> <y> <x0> <x1>
    let cg = loadCG(args[2])
    let w = cg.width, h = cg.height
    var buf = [UInt8](repeating: 0, count: w*h*4)
    let ctx = CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w*4,
                        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
    let y = Int(args[3])!, x0 = Int(args[4])!, x1 = Int(args[5])!
    for x in x0...x1 {
        let i = (y*w + x)*4
        print("x=\(x) rgb=(\(buf[i]),\(buf[i+1]),\(buf[i+2]))")
    }

case "inkarea":
    // inkarea <img> <lumThr> <x0> <y0> <x1> <y1>
    let cg = loadCG(args[2])
    let w = cg.width, h = cg.height
    let lt = Int(args[3])!
    let x0 = Int(args[4])!, y0 = Int(args[5])!, x1 = Int(args[6])!, y1 = Int(args[7])!
    var buf = [UInt8](repeating: 0, count: w*h*4)
    let ctx = CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w*4,
                        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
    var n = 0
    for y in y0...y1 { for x in x0...x1 {
        let i = (y*w + x)*4
        let l = (Int(buf[i])*299 + Int(buf[i+1])*587 + Int(buf[i+2])*114)/1000
        if l < lt { n += 1 }
    }}
    let area = (x1-x0+1)*(y1-y0+1)
    print(String(format: "ink=%d  area=%d  ratio=%.4f", n, area, Double(n)/Double(area)))

default: die("unknown cmd")
}
