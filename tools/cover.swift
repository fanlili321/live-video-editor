import Foundation
import AppKit

// cover fill <in> <x> <y> <w> <h> <r> <g> <b> <out>
// cover make <template> <line1> [line2] <em> <out>
let a = CommandLine.arguments

func loadCG(_ p: String) -> CGImage {
    guard let img = NSImage(contentsOfFile: p),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        FileHandle.standardError.write("cannot load \(p)\n".data(using: .utf8)!); exit(1)
    }
    return cg
}
func save(_ cg: CGImage, _ p: String) {
    try! NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:])!
        .write(to: URL(fileURLWithPath: p))
    print("wrote \(p) \(cg.width)x\(cg.height)")
}

switch a[1] {
case "fill":
    let cg = loadCG(a[2])
    let W = cg.width, H = cg.height
    let ctx = CGContext(data: nil, width: W, height: H, bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.draw(cg, in: CGRect(x: 0, y: 0, width: W, height: H))
    let x = Int(a[3])!, yTop = Int(a[4])!, w = Int(a[5])!, h = Int(a[6])!
    let dev = CGColorSpaceCreateDeviceRGB()
    ctx.setFillColor(CGColor(colorSpace: dev, components: [CGFloat(Double(a[7])!)/255,
                                                          CGFloat(Double(a[8])!)/255,
                                                          CGFloat(Double(a[9])!)/255, 1])!)
    ctx.fill(CGRect(x: x, y: H - yTop - h, width: w, height: h))   // convert top-origin to CG
    save(ctx.makeImage()!, a[10])

case "make":
    // cover make <template> <em> <fontPS> <r,g,b> <out> <line1> [line2] ...
    let tpl = loadCG(a[2])
    let W = tpl.width, H = tpl.height
    let em = CGFloat(Double(a[3])!)
    let fontName = a[4]
    let rgb = a[5].split(separator: ",").map { CGFloat(Double($0)!)/255 }
    let out = a[6]
    let lines = Array(a[7...]).filter { !$0.hasPrefix("--") && Double($0) == nil }
    guard let font = NSFont(name: fontName, size: em) else {
        FileHandle.standardError.write("font not found \(fontName)\n".data(using: .utf8)!); exit(1)
    }
    let ctx = CGContext(data: nil, width: W, height: H, bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.draw(tpl, in: CGRect(x: 0, y: 0, width: W, height: H))
    let ns = NSGraphicsContext(cgContext: ctx, flipped: false)
    NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = ns
    let attrs: [NSAttributedString.Key: Any] = [
        .font: font,
        .foregroundColor: NSColor(cgColor: CGColor(colorSpace: CGColorSpaceCreateDeviceRGB(),
                                                   components: [rgb[0], rgb[1], rgb[2], 1])!)!,
    ]
    // 排版：pitch 为行 ink 顶到顶的像素距离；anchor 为最后一行 ink 底的 y（top-origin），"center" 则整块垂直居中
    let pitchArg = a.firstIndex(of: "--pitch").map { CGFloat(Double(a[$0+1])!) }
    let anchorArg = a.firstIndex(of: "--inkbottom").map { CGFloat(Double(a[$0+1])!) }
    let pitch = pitchArg ?? em * 1.47
    var inkRects: [CGRect] = []
    var ctLines: [CTLine] = []
    for line in lines where !line.hasPrefix("--") {
        let attr = NSAttributedString(string: line, attributes: attrs)
        let ct = CTLineCreateWithAttributedString(attr)
        ctLines.append(ct)
        inkRects.append(CTLineGetBoundsWithOptions(ct, .useGlyphPathBounds))
    }
    let n = CGFloat(ctLines.count)
    let blockInkH = (n - 1) * pitch + inkRects.last!.height
    let firstInkTop: CGFloat
    if let ab = anchorArg {
        firstInkTop = ab - blockInkH
    } else {
        firstInkTop = (CGFloat(H) - blockInkH) / 2
    }
    for (i, ct) in ctLines.enumerated() {
        let ink = inkRects[i]
        let inkTop = firstInkTop + CGFloat(i) * pitch
        let baselineTopOrigin = inkTop + ink.maxY
        let yCG = CGFloat(H) - baselineTopOrigin
        let xCG = (CGFloat(W) - ink.width) / 2 - ink.origin.x
        ctx.textPosition = CGPoint(x: xCG, y: yCG)
        CTLineDraw(ct, ctx)
    }
    NSGraphicsContext.restoreGraphicsState()
    save(ctx.makeImage()!, out)

default:
    FileHandle.standardError.write("unknown\n".data(using: .utf8)!); exit(1)
}
