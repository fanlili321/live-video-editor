import Foundation
import AppKit
// patchfill <in> <srcX> <srcY> <w> <h> <dstX> <dstY> <repeatX> <repeatY> <out>
// 把一块干净纹理平铺到目标区域，用于评估"抹字"可行性
let a = CommandLine.arguments
let cg = NSImage(contentsOfFile:a[1])!.cgImage(forProposedRect:nil,context:nil,hints:nil)!
let W=cg.width, H=cg.height
let ctx=CGContext(data:nil,width:W,height:H,bitsPerComponent:8,bytesPerRow:0,
  space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.draw(cg,in:CGRect(x:0,y:0,width:W,height:H))
let sx=Int(a[2])!, syTop=Int(a[3])!, w=Int(a[4])!, h=Int(a[5])!
let dx=Int(a[6])!, dyTop=Int(a[7])!, rx=Int(a[8])!, ry=Int(a[9])!
let patch=cg.cropping(to: CGRect(x:sx, y:H-syTop-h, width:w, height:h))!
for i in 0..<rx { for j in 0..<ry {
  ctx.draw(patch, in: CGRect(x: dx+i*w, y: H-dyTop-h-j*h, width: w, height: h))
}}
let out=ctx.makeImage()!
try! NSBitmapImageRep(cgImage:out).representation(using:.png,properties:[:])!.write(to:URL(fileURLWithPath:a[10]))
print("wrote \(a[10])")
