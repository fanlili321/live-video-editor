import Foundation
import AppKit
// crop <in> <x> <y> <w> <h> <scale> <out>
let a = CommandLine.arguments
let img = NSImage(contentsOfFile: a[1])!
let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil)!
let r = CGRect(x: Int(a[2])!, y: Int(a[3])!, width: Int(a[4])!, height: Int(a[5])!)
let sub = cg.cropping(to: r)!
let s = Int(a[6])!
let W = sub.width*s, H = sub.height*s
let ctx = CGContext(data: nil, width: W, height: H, bitsPerComponent: 8, bytesPerRow: 0,
                    space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.interpolationQuality = .none
ctx.draw(sub, in: CGRect(x: 0, y: 0, width: W, height: H))
let out = ctx.makeImage()!
try! NSBitmapImageRep(cgImage: out).representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: a[7]))
print("wrote \(a[7]) \(W)x\(H)")
