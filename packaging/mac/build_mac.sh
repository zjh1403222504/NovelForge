#!/usr/bin/env bash
# ============================================================
# NovelForge macOS 一键打包脚本
# 用法（在 Mac 上执行）：
#   bash packaging/mac/build_mac.sh
# 产物：dist/NovelForge.app
# 说明：PyInstaller 不能跨平台交叉编译，本脚本必须在 macOS 上运行。
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
SPEC="$ROOT/packaging/mac/NovelForge_mac.spec"
PYTHON="${PYTHON:-python3}"
VENV="$ROOT/build_mac_venv"

echo "==> 项目根目录: $ROOT"
echo "==> 使用 Python: $PYTHON"

echo "==> 创建虚拟环境 $VENV"
if [ ! -d "$VENV" ]; then
  "$PYTHON" -m venv "$VENV"
fi
source "$VENV/bin/activate"

echo "==> 安装/更新依赖"
python -m pip install --upgrade pip >/dev/null
python -m pip install --quiet PyQt6 requests PyYAML pyinstaller

echo "==> PyInstaller 打包（可能需要几分钟）"
python -m PyInstaller --noconfirm --clean "$SPEC"

APP="$ROOT/dist/NovelForge.app"
if [ ! -d "$APP" ]; then
  echo "错误：未找到产物 $APP" >&2
  exit 1
fi

echo "==> ad-hoc 签名（Apple Silicon 本地运行必需）"
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "  签名跳过（可忽略）"

echo ""
echo "============================================"
echo " 打包完成: $APP"
echo " 双击即可运行；如被 Gatekeeper 拦截：右键图标 -> 打开"
echo " 制作 dmg：bash packaging/mac/make_dmg.sh"
echo "============================================"
