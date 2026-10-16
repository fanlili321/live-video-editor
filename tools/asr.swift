import Foundation
import Speech
import AVFoundation

// usage: ASRApp <audio-or-video-path> <out.json>
let args = CommandLine.arguments
// 输出路径：优先用环境变量 ASR_OUT，否则固定 /tmp/asr_out.txt（不要再硬编码会话目录）
let logURL = URL(fileURLWithPath: ProcessInfo.processInfo.environment["ASR_OUT"] ?? "/tmp/asr_out.txt")
var logBuf = ""
func log(_ s: String) {
    logBuf += s + "\n"
    try? logBuf.write(to: logURL, atomically: true, encoding: .utf8)
}

var done = false


func extractAudio(_ src: URL, completion: @escaping (URL?) -> Void) {
    let asset = AVURLAsset(url: src)
    let out = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("asr_audio_\(UUID().uuidString).m4a")
    guard let ex = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetAppleM4A) else {
        completion(nil); return
    }
    ex.outputURL = out
    ex.outputFileType = .m4a
    ex.exportAsynchronously {
        completion(ex.status == .completed ? out : nil)
    }
}

func recognize(_ audio: URL) {
    guard let rec = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN")) else {
        log("no zh-CN recognizer"); done = true; return
    }
    log("available=\(rec.isAvailable) onDevice=\(rec.supportsOnDeviceRecognition)")
    let req = SFSpeechURLRecognitionRequest(url: audio)
    req.shouldReportPartialResults = false
    req.requiresOnDeviceRecognition = rec.supportsOnDeviceRecognition
    rec.recognitionTask(with: req) { result, err in
        if let e = err {
            log("error: \(e.localizedDescription)")
            done = true; return
        }
        guard let r = result, r.isFinal else { return }
        var out = "text: " + r.bestTranscription.formattedString + "\n"
        out += "segments: \(r.bestTranscription.segments.count)\n"
        for s in r.bestTranscription.segments {
            out += String(format: "%.3f\t%.3f\t%@\n", s.timestamp, s.duration, s.substring)
        }
        log(out)
        done = true
    }
}

log("args: \(args)")
SFSpeechRecognizer.requestAuthorization { st in
    log("auth: \(st.rawValue)")
    guard st == .authorized else { done = true; return }
    let src = URL(fileURLWithPath: args[1])
    extractAudio(src) { audio in
        guard let a = audio else { log("audio extract failed"); done = true; return }
        log("audio: \(a.path)")
        recognize(a)
    }
}

let deadline = Date().addingTimeInterval(600)
while !done && Date() < deadline {
    RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.2))
}
if !done { log("TIMEOUT") }
