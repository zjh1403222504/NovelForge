# NovelForge 小说生产工坊

基于 PyQt6 的离线小说创作软件：AI 接入（DeepSeek / 豆包 / OpenAI / 通义 / GLM）、
小说项目管理、全章节记忆系统、章节编辑器与全本导出。

跨平台（Windows / macOS）源码，`packaging/mac/` 内含 macOS 打包套件。

## 如何自动打包 macOS 版（GitHub Actions）

1. 在 github.com 新建一个**空**仓库（不要勾选自动生成 README/.gitignore）；
2. 把本目录里的文件上传到该仓库（网页版 "Add file → Upload files" 拖拽即可；
   注意要连 `.github/workflows/build-mac.yml` 一起传，且保持目录结构不变）；
3. 打开仓库 **Actions** 页面 → 左侧选中 **Build macOS app** → 点 **Run workflow**；
4. 等几分钟跑完，在本次运行结果底部 **Artifacts** 里下载 `NovelForge-mac`
   （内含 `NovelForge-1.0.0-mac.dmg`，双击即可安装到 Mac）。

> 若本地装有 git，也可用命令行：`git init` → 全部添加 → commit →
> `git remote add origin <你的仓库地址>` → `git push -u origin main`。

详细说明见 `packaging/mac/README_mac.md`。
