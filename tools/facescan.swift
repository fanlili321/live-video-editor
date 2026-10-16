import Foundation
import AVFoundation
import Vision
import AppKit

// facescan <video> <intervalSec>  -> prints per-sample face rect + median crop suggestion
let a = CommandLine.arguments
let url = URL(fileURLWithPath: a[1])
let interval = Double(a[2])!
let asset = AVURLAsset(url: url)
let dur = CMTimeGetSeconds(asset.duration)
guard let vt = asset.tracks(withMediaType: .video).first else { fatalError("no video") }
let srcW = abs(vt.naturalSize.width), srcH = abs(vt.naturalSize.height)

let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
gen.requestedTimeToleranceBefore = CMTime(seconds: 0.2, preferredTimescale: 600)
gen.requestedTimeToleranceAfter = CMTime(seconds: 0.2, preferredTimescale: 600)
gen.maximumSize = CGSize(width: 960, height: 960)

var hs: [Double] = [], cxs: [Double] = [], cys: [Double] = []
var t = 0.5
while t < dur {
    if let cg = try? gen.copyCGImage(at: CMTime(seconds: t, preferredTimescale: 600), actualTime: nil) {
        let req = VNDetectFaceRectanglesRequest()
        try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
        if let obs = (req.results as? [VNFaceObservation])?.max(by: { $0.boundingBox.height < $1.boundingBox.height }) {
            let b = obs.boundingBox
            hs.append(Double(b.height)); cxs.append(Double(b.midX)); cys.append(Double(1 - b.midY))
        }
    }
    t += interval
}
func med(_ v: [Double]) -> Double { let s = v.sorted(); return s.isEmpty ? 0 : s[s.count/2] }
let fh = med(hs), fcx = med(cxs), fcy = med(cys)
print("samples=\(hs.count)  source=\(Int(srcW))x\(Int(srcH))  duration=\(String(format:"%.2f",dur))")
print(String(format: "median face: h=%.4f  cx=%.4f  cy=%.4f", fh, fcx, fcy))

// targets
let tFaceH = 0.460, tCx = 0.49, tCy = 0.56
let cropH = (fh * srcH) / tFaceH
let cropW = cropH * 16.0/9.0
var cropX = fcx * srcW - tCx * cropW
var cropY = fcy * srcH - tCy * cropH
// clamp
cropX = max(0, min(cropX, srcW - cropW))
cropY = max(0, min(cropY, srcH - cropH))
print(String(format: "crop: x=%.1f y=%.1f w=%.1f h=%.1f  (%.1f%% of source, upscale %.3fx)",
             cropX, cropY, cropW, cropH, cropH/srcH*100, 1080.0/cropH))
if cropW > srcW || cropH > srcH { print("WARNING: crop larger than source; will be clamped") }
print(String(format: "JSON {\"cropX\":%.1f,\"cropY\":%.1f,\"cropW\":%.1f,\"cropH\":%.1f}", cropX, cropY, cropW, cropH))
