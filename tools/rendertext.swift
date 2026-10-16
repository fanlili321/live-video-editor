import Foundation
import AppKit
import CoreText

// rendertext <text> <psFontName> <emPx> <outerStrokePx> <out.png>
// Draws white OUTER stroke (2-pass) + black fill, centered on a 1920x1080 mid-gray canvas.
let a = CommandLine.arguments
let text = a[1], fontName = a[2]
let em = CGFloat(Double(a[3])!), outer = CGFloat(Double(a[4])!)
let out = a[5]
let W = 1920, H = 1080

guard let font = NSFont(name: fontName, size: em) else {
    FileHandle.standardError.write("font not found: \(fontName)\n".data(using: .utf8)!); exit(1)
}

let ctx = CGContext(data: nil, width: W, height: H, bitsPerComponent: 8, bytesPerRow: 0,
                    space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.setFillColor(CGColor(red: 0.5, green: 0.5, blue: 0.5, alpha: 1))
ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))

let nsctx = NSGraphicsContext(cgContext: ctx, flipped: false)
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = nsctx

let base: [NSAttributedString.Key: Any] = [.font: font]
let measured = NSAttributedString(string: text, attributes: base).size()
let x = (CGFloat(W) - measured.width) / 2
let y = (CGFloat(H) - measured.height) / 2

// pass 1: white stroke only (positive strokeWidth = stroke, no fill). Line width straddles
// the outline, so to get `outer` px outside we need lineWidth = 2*outer.
let strokePct = (2 * outer / em) * 100.0
var s1 = base
s1[.strokeColor] = NSColor.white
s1[.strokeWidth] = strokePct
s1[.foregroundColor] = NSColor.clear
NSAttributedString(string: text, attributes: s1).draw(at: CGPoint(x: x, y: y))

// pass 2: black fill on top
var s2 = base
s2[.foregroundColor] = NSColor.black
NSAttributedString(string: text, attributes: s2).draw(at: CGPoint(x: x, y: y))

NSGraphicsContext.restoreGraphicsState()
let img = ctx.makeImage()!
try! NSBitmapImageRep(cgImage: img).representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
print(String(format: "%@ em=%.1f advance=%.1f", fontName, em, measured.width))
