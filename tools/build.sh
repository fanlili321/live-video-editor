#!/bin/bash
# 编译 Swift 工具。换机器后必须先跑这个，二进制没有打进包里。
# 全部重建约 1 分钟。本机不需要 ffmpeg、不需要 Homebrew。
set -e
cd "$(dirname "$0")"

for f in vtool rms measure facescan rendertext cover render band \
         scenescan slidematch covercheck colorbox patchfill subocr; do
  swiftc -O "$f.swift" -o "$f"
  echo "  ✓ $f"
done
swiftc -O measure_crop.swift -o crop        # 唯一一个源码名与产物名不同的
echo "  ✓ crop"

# 语音识别必须打成 .app：直接跑可执行文件会被系统的隐私保护直接杀掉。
mkdir -p ASRTest.app/Contents/MacOS
swiftc -O asr.swift -o ASRTest.app/Contents/MacOS/ASRTest
cp ASRTest-Info.plist ASRTest.app/Contents/Info.plist
codesign --force --deep -s - ASRTest.app
echo "  ✓ ASRTest.app"

echo
echo "完成。自检："
ls vtool rms measure crop facescan rendertext cover render band \
   scenescan slidematch covercheck colorbox patchfill subocr >/dev/null \
  && echo "  15 个可执行文件齐了"
python3 -c "import jieba" 2>/dev/null && echo "  jieba 已装" || echo "  ⚠️ 缺 jieba：pip3 install jieba"
ls ~/Library/Fonts/SourceHanSansCN-Medium.otf >/dev/null 2>&1 \
  && echo "  字体已装" || echo "  ⚠️ 字体没装：cp ../fonts/*.otf ~/Library/Fonts/"
