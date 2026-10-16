import Foundation
import AVFoundation
import CoreImage
import AppKit

let args = CommandLine.arguments

func die(_ m: String) -> Never { FileHandle.standardError.write((m + "\n").data(using: .utf8)!); exit(1) }

guard args.count >= 3 else { die("usage: vtool <info|frame|trim> ...") }
let cmd = args[1]
let asset = AVURLAsset(url: URL(fileURLWithPath: args[2]))

switch cmd {
case "info":
    let d = CMTimeGetSeconds(asset.duration)
    print("duration_sec: \(d)")
    for t in asset.tracks {
        print("track \(t.trackID) \(t.mediaType.rawValue) size=\(t.naturalSize) fps=\(t.nominalFrameRate) bitrate=\(t.estimatedDataRate)")
    }

case "frame":
    // vtool frame <in> <sec> <out.png>
    let sec = Double(args[3])!
    let out = args[4]
    let gen = AVAssetImageGenerator(asset: asset)
    gen.appliesPreferredTrackTransform = true
    gen.requestedTimeToleranceBefore = .zero
    gen.requestedTimeToleranceAfter = .zero
    gen.maximumSize = CGSize(width: 960, height: 960)
    let cg = try! gen.copyCGImage(at: CMTime(seconds: sec, preferredTimescale: 600), actualTime: nil)
    let rep = NSBitmapImageRep(cgImage: cg)
    let data = rep.representation(using: .png, properties: [:])!
    try! data.write(to: URL(fileURLWithPath: out))
    print("wrote \(out)")

case "trim":
    // vtool trim <in> <start> <end> <out.mov>
    let start = Double(args[3])!, end = Double(args[4])!
    let out = URL(fileURLWithPath: args[5])
    try? FileManager.default.removeItem(at: out)
    guard let ex = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetPassthrough) else { die("no export session") }
    ex.outputURL = out
    ex.outputFileType = .mov
    ex.shouldOptimizeForNetworkUse = true
    ex.timeRange = CMTimeRange(start: CMTime(seconds: start, preferredTimescale: 600),
                               end: CMTime(seconds: end, preferredTimescale: 600))
    let sem = DispatchSemaphore(value: 0)
    ex.exportAsynchronously { sem.signal() }
    while sem.wait(timeout: .now() + 2) == .timedOut {
        FileHandle.standardError.write(String(format: "  progress %.1f%%\n", ex.progress * 100).data(using: .utf8)!)
    }
    if ex.status != .completed { die("export failed: \(String(describing: ex.error))") }
    print("wrote \(out.path)")

default:
    die("unknown cmd \(cmd)")
}
