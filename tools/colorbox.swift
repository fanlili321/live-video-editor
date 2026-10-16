import Foundation
import AppKit
// colorbox <img> <r> <g> <b> <tol>   找出接近该颜色的像素范围
let a=CommandLine.arguments
let cg=NSImage(contentsOfFile:a[1])!.cgImage(forProposedRect:nil,context:nil,hints:nil)!
let W=cg.width,H=cg.height
var buf=[UInt8](repeating:0,count:W*H*4)
let c=CGContext(data:&buf,width:W,height:H,bitsPerComponent:8,bytesPerRow:W*4,
  space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue)!
c.draw(cg,in:CGRect(x:0,y:0,width:W,height:H))
let tr=Int(a[2])!,tg=Int(a[3])!,tb=Int(a[4])!,tol=Int(a[5])!
var minX=W,maxX=0,minY=H,maxY=0,n=0
var rowCount=[Int](repeating:0,count:H)
for y in 0..<H { for x in 0..<W {
  let i=(y*W+x)*4
  let r=Int(buf[i]),g=Int(buf[i+1]),b=Int(buf[i+2])
  if abs(r-tr)<=tol && abs(g-tg)<=tol && abs(b-tb)<=tol {
    n+=1; rowCount[H-1-y]+=1
    minX=min(minX,x); maxX=max(maxX,x)
    let yt=H-1-y; minY=min(minY,yt); maxY=max(maxY,yt)
  }
}}
print("匹配 \(n) 像素")
if n>0 {
  print("bbox(top-origin) x=\(minX)..\(maxX) (w=\(maxX-minX+1))  y=\(minY)..\(maxY) (h=\(maxY-minY+1))")
  let rows=(0..<H).filter{rowCount[$0] > (rowCount.max()!/6)}
  if let f=rows.first,let l=rows.last { print("主体行范围 y=\(f)..\(l) (h=\(l-f+1))") }
}
