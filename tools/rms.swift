import Foundation
import AVFoundation

// usage: rms <file> <startSec> <endSec> [hopMs]
let a = CommandLine.arguments
let url = URL(fileURLWithPath: a[1])
let s = Double(a[2])!, e = Double(a[3])!
let hopMs: Double = a.count > 4 ? Double(a[4])! : 25.0

let asset = AVURLAsset(url: url)
guard let track = asset.tracks(withMediaType: .audio).first else { fatalError("no audio") }
let reader = try! AVAssetReader(asset: asset)
reader.timeRange = CMTimeRange(start: CMTime(seconds: s, preferredTimescale: 600),
                               end: CMTime(seconds: e, preferredTimescale: 600))
let settings: [String: Any] = [
    AVFormatIDKey: kAudioFormatLinearPCM,
    AVLinearPCMBitDepthKey: 32,
    AVLinearPCMIsFloatKey: true,
    AVLinearPCMIsNonInterleaved: false,
    AVNumberOfChannelsKey: 1,
    AVSampleRateKey: 16000.0,
]
let out = AVAssetReaderTrackOutput(track: track, outputSettings: settings)
reader.add(out)
reader.startReading()

let sr = 16000.0
let hop = Int(sr * hopMs / 1000.0)
var buf = [Float]()
var t = s
var results = [(Double, Double)]()

func flush(force: Bool) {
    while buf.count >= hop {
        let chunk = buf[0..<hop]
        var sum: Float = 0
        for v in chunk { sum += v * v }
        let rms = sqrt(sum / Float(hop))
        results.append((t, Double(rms)))
        t += hopMs / 1000.0
        buf.removeFirst(hop)
    }
}

while let sb = out.copyNextSampleBuffer() {
    guard let bb = CMSampleBufferGetDataBuffer(sb) else { continue }
    var len = 0; var ptr: UnsafeMutablePointer<Int8>?
    CMBlockBufferGetDataPointer(bb, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &len, dataPointerOut: &ptr)
    if let p = ptr {
        p.withMemoryRebound(to: Float.self, capacity: len / 4) { fp in
            buf.append(contentsOf: UnsafeBufferPointer(start: fp, count: len / 4))
        }
    }
    flush(force: false)
}
flush(force: true)

for (tt, r) in results {
    print(String(format: "%.3f %.6f", tt, r))
}
