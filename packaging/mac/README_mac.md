# NovelForge macOS 打包说明

> 一句话：**PyInstaller 不支持跨平台交叉编译**——Windows 上无法直接生成 Mac 的
> `.app`。所以 Mac 版必须在 macOS 环境里构建一次。本目录就是一套"拿到 Mac 上就能跑"的
> 打包套件，Windows 版完全不受影响。

## 本套件内容

| 文件 | 作用 |
|---|---|
| `app.icns` | macOS 应用图标（由 app.png 生成，128/256/512/1024 等档齐全） |
| `app_icon_1024.png` | 1024 方形图标备份 |
| `NovelForge_mac.spec` | Mac 版 PyInstaller 配置（窗口程序、icns 图标、app.ico 随包） |
| `build_mac.sh` | 一键打包脚本（建 venv → 装依赖 → 打包 → ad-hoc 签名） |
| `make_dmg.sh` | 把 `.app` 压成 dmg |
| `build-mac.yml` | GitHub Actions 自动构建工作流（可选） |

## 已做的源码适配（对 Windows 零影响）

`NovelForge.py` 只加了一个 `_resource_path()` 帮助函数，用于在两个地方（主窗口、
QApplication）查找 `app.ico`：
- 依次探测 `sys._MEIPASS`（Mac 的 `Contents/Frameworks`）→ `Contents/Frameworks` →
  `Contents/Resources` → 程序目录，保证 Mac bundle 里窗口/任务栏图标正常。
- Windows 下所有候选路径不命中时回退到原 `APP_DIR\app.ico`，行为与原来完全一致；
  已装好的 Windows 安装包不受任何影响。
- 代码里其余 Windows 专属部分（深色标题栏 `DwmSetWindowAttribute`、任务栏分组
  `SetCurrentProcessExplicitAppUserModelID`）本来就带 `sys.platform != "win32"`
  或 `try/except` 保护，Mac 上自动跳过。

## 方式 A：在一台 Mac 上本地打包（推荐，最快）

需要任意一台 Mac（Intel 或 Apple Silicon 均可），已装 Python 3.10+。

```bash
# 1. 把整个项目文件夹拷到 Mac 上（含 NovelForge.py、app.ico、packaging/mac/）
# 2. 在项目根目录执行：
bash packaging/mac/build_mac.sh

# 3. 打包完成，产物在 dist/NovelForge.app，双击即可运行
# 4. 想要 dmg 再执行：
bash packaging/mac/make_dmg.sh
```

- 首次运行若被 Gatekeeper 拦截：右键图标 → 打开 → 确认；或
  `xattr -cr dist/NovelForge.app` 后重开。
- 打包脚本只在本项目目录里建 `build_mac_venv/`，不动系统环境。

## 方式 B：GitHub Actions 自动构建（没有 Mac 也能出包）

1. 把项目推到 GitHub 仓库；
2. 把 `packaging/mac/build-mac.yml` 放到仓库 `.github/workflows/build-mac.yml`；
3. 在仓库 Actions 页手动 Run workflow（或推送 `v*` 标签）；
4. 跑完在 Actions 页面下载 `NovelForge-mac` 产物（内含 dmg）。

建议在仓库根目录加一个 `.gitignore`，至少忽略：

```
dist/
build/
build_mac_venv/
novels/
logs/
参考文库/
__pycache__/
```

## 常见问题

- **为什么不能在 Windows 上直接给我 .app/.dmg？** PyInstaller 不提供交叉编译，
  必须在目标操作系统上构建，这是工具层面的硬限制。
- **Apple Silicon 与 Intel 通用包？** 默认产物只适配打包时那台 Mac 的架构。
  如需 universal2 通用包，在 `NovelForge_mac.spec` 里把 `target_arch` 改为
  `'universal2'`（需本机同时具备 x86_64 与 arm64 的依赖，较易出错，一般不必）。
- **深色标题栏在 Mac 上不生效？** 这是 Windows 专属的 DwmSetWindowAttribute 效果，
  Mac 上自动跳过，界面其余暗色样式不受影响，属正常降级。
- **数据目录**：与 Windows 版一致，novels / logs / 参考文库 都在 `.app` 同级
  的 `dist/NovelForge.app` 外层？——不，运行后它们创建在 **可执行文件同目录**
  （Mac 下是 `Contents/MacOS/`）。如需放到更合适的位置（如 `~/Documents`），
  需要在代码里调整 `APP_DIR` 的定位逻辑。
