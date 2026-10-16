import Foundation
import AppKit
// band <img>  -> 逐行平均亮度，找出非黑区块
let a=CommandLine.arguments
let cg=NSImage(contentsOfFile:a[1])!.cgImage(forProposedRect:nil,context:nil,hints:nil)!
let w=cg.width,h=cg.height
var buf=[UInt8](repeating:0,count:w*h*4)
let ctx=CGContext(data:&buf,width:w,height:h,bitsPerComponent:8,bytesPerRow:w*4,
  space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.draw(cg,in:CGRect(x:0,y:0,width:w,height:h))
func lum(_ x:Int,_ y:Int)->Int{let i=(y*w+x)*4
 return (Int(buf[i])*299+Int(buf[i+1])*587+Int(buf[i+2])*114)/1000}
var rows=[Int]()
for y in 0..<h { var s=0; for x in stride(from:0,to:w,by:4){s+=lum(x,y)}; rows.append(s/(w/4)) }
// 非黑行段
var bands=[(Int,Int)](); var st = -1
for y in 0..<h { let on = rows[y] > 12
  if on && st < 0 { st = y }
  if !on && st >= 0 { if y-st>=3 { bands.append((st,y-1)) }; st = -1 } }
if st >= 0 { bands.append((st,h-1)) }
print("image \(w)x\(h)")
for b in bands { print("  非黑带 y=\(b.0)..\(b.1)  h=\(b.1-b.0+1)") }
// 视频带的左右边界（取中间行）
if let vb = bands.max(by:{ ($0.1-$0.0) < ($1.1-$1.0) }) {
  let ym=(vb.0+vb.1)/2
  var x0=0,x1=w-1
  while x0<w && lum(x0,ym)<12 { x0+=1 }
  while x1>0 && lum(x1,ym)<12 { x1-=1 }
  print("  主视频带 y=\(vb.0)..\(vb.1)  x=\(x0)..\(x1)  尺寸 \(x1-x0+1)x\(vb.1-vb.0+1)")
}
