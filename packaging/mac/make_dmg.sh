#!/usr/bin/env bash
# ============================================================
# 把 dist/NovelForge.app 打包成 dmg（需要先运行 build_mac.sh）
# 用法：bash packaging/mac/make_dmg.sh
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/../.."
APP="$PWD/dist/NovelForge.app"
OUT="$PWD/dist/NovelForge-1.0.0-mac.dmg"

if [ ! -d "$APP" ]; then
  echo "未找到 $APP，请先运行: bash packaging/mac/build_mac.sh" >&2
  exit 1
fi

rm -f "$OUT"
hdiutil create -volname "NovelForge" -srcfolder "$APP" -ov -format UDZO "$OUT"
echo "DMG 已生成: $OUT"
