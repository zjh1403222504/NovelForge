# -*- coding: utf-8 -*-
"""
NovelForge —— 小说生产软件（记忆所有章节）
=========================================
基于原 RabBox 精简重构：仅保留写小说必需能力
  · LLM 接入（DeepSeek / 豆包 / OpenAI / 通义 / GLM / 自定义）
  · 小说项目管理（meta + 章节 + 记忆库）
  · 全章节记忆系统：AI 创作时注入「世界观 + 人物 + 全部章节摘要 + 最近N章全文」
  · 章节编辑器 + 全本导出
已移除冗余：企业登录/服务端/同步、AI 操作电脑、文件索引、插件、文档导入转换、
笔记 RAG 检索、联网搜索、知识图谱等。
"""

import sys
import os
import json
import re
import time
import math
import hashlib
import shutil
from datetime import datetime
import logging
from logging.handlers import TimedRotatingFileHandler

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout,
    QTextEdit, QTextBrowser, QLineEdit, QLabel, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QSplitter, QDialog, QFormLayout,
    QSpinBox, QCheckBox, QMessageBox, QInputDialog, QFileDialog, QFrame,
    QScrollArea, QToolButton, QPlainTextEdit, QSizePolicy, QStackedWidget,
    QMenu, QToolBar, QTabWidget, QTreeWidget, QTreeWidgetItem, QAbstractItemView,
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsPathItem, QGraphicsTextItem)
from PyQt6.QtGui import (QAction, QIcon, QTextCursor, QFont, QShortcut, QKeySequence,
                         QPainter, QColor, QFontMetrics, QPen, QBrush, QPainterPath,
                         QLinearGradient)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QRectF, QPointF

try:
    import ctypes
    _HAS_CTYPES = True
except Exception:
    _HAS_CTYPES = False


# ===================== 全局常量 =====================
APP_NAME = "NovelForge"
APP_VERSION = "1.0"

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _resource_path(name):
    """返回打包后资源文件路径：优先 PyInstaller 解包目录(_MEIPASS)，再退回程序目录。

    macOS .app 里可执行文件在 Contents/MacOS，data 可能落在 Contents/Frameworks
    （即 _MEIPASS）或 Contents/Resources；Windows onedir 下 _MEIPASS 即程序根目录。
    依次探测，全部不中则回退到 APP_DIR，保证跨平台都能找到 app.ico。
    """
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(meipass)
    candidates.append(os.path.join(APP_DIR, "..", "Frameworks"))
    candidates.append(os.path.join(APP_DIR, "..", "Resources"))
    candidates.append(APP_DIR)
    for base in candidates:
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(APP_DIR, name)

NOVELS_DIR = os.path.join(APP_DIR, "novels")
CONFIG_PATH = os.path.join(APP_DIR, f".{APP_NAME}_config.yaml")
LOG_DIR = os.path.join(APP_DIR, "logs")

DEFAULT_CONFIG = {
    "provider": "DeepSeek",
    "ollama_model": "deepseek-chat",
    "api_key": "",
    "api_base": "https://api.deepseek.com/v1",
    "proxy": "",
    "timeout": 180,
    "frost_mode": True,          # 磨砂暗色界面
    "dark_mode": True,           # 深色标题栏（与暗色UI一致）
    "novel_recent_full": 3,      # AI创作时注入的最近完整章节数
    "novel_max_full_chars": 6000,# 每章全文注入上限（字符）
    "novel_auto_memory": True,   # 保存章节后自动更新该章记忆摘要
    "novel_backup_keep": 5,      # 每章自动备份保留份数
    "novel_daily_target": 0,     # 每日目标字数（0=不启用）
    "novel_min_words": 2000,     # 每章最少字数（不足时保存提醒、AI创作强制执行）
    "last_novel": "",            # 上次打开的小说目录
    "last_chapter": 0,           # 上次打开的章节号
}


def _ensure_dirs():
    for p in (NOVELS_DIR, LOG_DIR):
        os.makedirs(p, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(DEFAULT_CONFIG, f, sort_keys=False, allow_unicode=True)
        except Exception:
            pass


_ensure_dirs()


def _setup_logging():
    logger = logging.getLogger("NovelForge")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger
    try:
        fh = TimedRotatingFileHandler(
            os.path.join(LOG_DIR, "novelforge.log"),
            when="D", interval=1, backupCount=7, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
        logger.addHandler(fh)
    except Exception:
        pass
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(ch)
    return logger


LOG = _setup_logging()


def set_window_titlebar(win, light=True):
    if not _HAS_CTYPES or sys.platform != "win32":
        return
    try:
        hwnd = int(win.winId())
        val = ctypes.c_int(0 if light else 1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


# ===================== 配置 =====================
def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(DEFAULT_CONFIG, f, sort_keys=False, allow_unicode=True)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        cfg = {}
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, sort_keys=False, allow_unicode=True)


APP_CONFIG = load_config()


# ===================== LLM 接入 =====================
PROVIDER_PRESETS = {
    "DeepSeek": {
        "api_base": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "豆包（火山引擎）": {
        "api_base": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-1.5-pro-32k", "doubao-1.5-lite-32k", "doubao-pro-32k",
                   "doubao-pro-128k", "doubao-lite-32k"],
    },
    "OpenAI": {
        "api_base": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
    },
    "通义千问（阿里云）": {
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max", "qwen-long"],
    },
    "智谱 GLM": {
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-flash", "glm-4-air", "glm-4-long"],
    },
    "自定义（OpenAI 兼容）": {"api_base": "", "models": []},
}

MODEL_ALIASES = {
    "deepseek-r1": "deepseek-reasoner", "r1": "deepseek-reasoner",
    "deepseek-v3": "deepseek-chat", "v3": "deepseek-chat",
}


def _proxies():
    p = {}
    if APP_CONFIG.get("proxy", ""):
        p["http"] = APP_CONFIG["proxy"]
        p["https"] = APP_CONFIG["proxy"]
    return p


_LLM_SESSION = None


def _get_llm_session():
    global _LLM_SESSION
    if _LLM_SESSION is None:
        import requests
        _LLM_SESSION = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=1)
        _LLM_SESSION.mount("http://", adapter)
        _LLM_SESSION.mount("https://", adapter)
    return _LLM_SESSION


def call_llm_stream(messages, tools=None, timeout=None,
                    temperature=0.5, on_chunk=None) -> dict:
    """流式调用大语言模型，返回 {"content": str}。tools 仅作兼容保留。"""
    import requests
    model = APP_CONFIG["ollama_model"].strip()
    timeout = timeout or APP_CONFIG.get("timeout", 180)
    base = APP_CONFIG["api_base"].rstrip("/")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    cloud_model = MODEL_ALIASES.get(model.lower(), model)
    headers = {"Authorization": f"Bearer {APP_CONFIG['api_key']}", "Content-Type": "application/json"}
    payload = {"model": cloud_model, "messages": messages, "temperature": temperature, "stream": True}
    try:
        session = _get_llm_session()
        resp = session.post(url, headers=headers, json=payload, timeout=timeout, proxies=_proxies(), stream=True)
    except requests.exceptions.ConnectionError:
        raise Exception(f"无法连接 API（{url}），请检查网络或代理设置")
    except requests.exceptions.Timeout:
        raise Exception(f"API 请求超时（{timeout}秒）")
    if not resp.ok:
        try:
            detail = resp.json().get("error", {}).get("message", resp.text[:300])
        except Exception:
            detail = resp.text[:300]
        raise Exception(f"API 返回 {resp.status_code}: {detail}")

    full_content = ""
    try:
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("data: "):
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_content += content
                        if on_chunk:
                            try:
                                on_chunk(content)
                            except Exception:
                                pass
                except Exception:
                    continue
    except Exception:
        pass
    return {"content": full_content}


def test_llm_connection():
    import requests
    try:
        if not APP_CONFIG.get("api_key"):
            return False, "请先填写 API Key"
        base = APP_CONFIG["api_base"].rstrip("/")
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        model = MODEL_ALIASES.get(APP_CONFIG["ollama_model"].strip().lower(), APP_CONFIG["ollama_model"].strip())
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {APP_CONFIG['api_key']}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
            timeout=30, proxies=_proxies())
        if resp.ok:
            return True, f"连接成功（模型：{model}）"
        try:
            detail = resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            detail = resp.text[:200]
        return False, f"返回 {resp.status_code}: {detail}"
    except Exception as e:
        return False, str(e)


# ===================== Markdown 轻量渲染 =====================
def esc(text):
    return (str(text).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def _inline_md(text):
    s = esc(text)
    s = re.sub(r'`([^`]+)`', r"<code style='background:#2b2d33;padding:1px 4px;border-radius:3px;font-family:Consolas,monospace;'>\1</code>", s)
    s = re.sub(r'\*\*(.+?)\*\*', r"<b>\1</b>", s)
    s = re.sub(r'__(.+?)__', r"<b>\1</b>", s)
    s = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', r"<i>\1</i>", s)
    s = re.sub(r'(?<!_)_([^_\n]+?)_(?!_)', r"<i>\1</i>", s)
    s = re.sub(r'~~(.+?)~~', r"<s>\1</s>", s)
    s = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" style="color:#8ab4f8;">\1</a>', s)
    s = re.sub(r'\[\[([^\]]+)\]\]', r"<span style='color:#8ab4f8;background:#2b3a52;padding:0 3px;border-radius:3px;'>[[\1]]</span>", s)
    s = re.sub(r'(?<=\s)\*(?=\s|$)', '', s)
    s = re.sub(r'^\*(?=\s)', '', s, flags=re.MULTILINE)
    s = re.sub(r'\*{2,}', '', s)
    return s


def md_to_html(text):
    if not text:
        return ""
    lines = text.split("\n")
    out = []
    in_ul = in_ol = in_code = False
    code_buf, table_buf = [], []

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>"); in_ul = False
        if in_ol:
            out.append("</ol>"); in_ol = False

    def flush_table():
        nonlocal table_buf
        if not table_buf:
            return
        rows = []
        for r in table_buf:
            rows.append([c.strip() for c in r.strip().strip("|").split("|")])
        if len(rows) >= 2 and re.match(r'^[\s|:\-]+$', "|".join(rows[1])):
            t = ["<table style='border-collapse:collapse;margin:6px 0;font-size:12px;'>"]
            t.append("<tr>" + "".join(
                f"<th style='border:1px solid #3a3d45;padding:4px 8px;background:#2b2d33;'>{_inline_md(c)}</th>"
                for c in rows[0]) + "</tr>")
            for row in rows[2:]:
                t.append("<tr>" + "".join(
                    f"<td style='border:1px solid #3a3d45;padding:4px 8px;'>{_inline_md(c)}</td>" for c in row) + "</tr>")
            t.append("</table>")
            out.append("".join(t))
        else:
            for r in table_buf:
                out.append(f"<p style='margin:3px 0;'>{_inline_md(r)}</p>")
        table_buf = []

    for line in lines:
        if line.strip().startswith("```"):
            if in_code:
                out.append("<pre style='background:#1f2126;border:1px solid #33363e;border-radius:4px;"
                           "padding:8px;font-family:Consolas,monospace;font-size:12px;white-space:pre-wrap;'>"
                           + esc("\n".join(code_buf)) + "</pre>")
                code_buf, in_code = [], False
            else:
                close_lists(); flush_table(); in_code = True
            continue
        if in_code:
            code_buf.append(line); continue
        if line.strip().startswith("|") and line.strip().endswith("|"):
            close_lists(); table_buf.append(line); continue
        elif table_buf:
            flush_table()
        m = re.match(r'^(#{1,4})\s+(.+)$', line)
        if m:
            close_lists()
            lv = len(m.group(1))
            out.append(f"<h{lv} style='margin:8px 0 4px;'>{_inline_md(m.group(2))}</h{lv}>")
            continue
        m = re.match(r'^[\s]*(?:[-*+]|\d+[.)])\s+(.+)$', line)
        if m:
            is_ordered = bool(re.match(r'^[\s]*\d+[.)]', line))
            if is_ordered:
                if in_ul:
                    out.append("</ul>"); in_ul = False
                if not in_ol:
                    out.append("<ol style='margin:4px 0;padding-left:20px;'>"); in_ol = True
            else:
                if in_ol:
                    out.append("</ol>"); in_ol = False
                if not in_ul:
                    out.append("<ul style='margin:4px 0;padding-left:20px;'>"); in_ul = True
            out.append(f"<li>{_inline_md(m.group(1))}</li>")
            continue
        m = re.match(r'^>\s*(.*)$', line)
        if m:
            close_lists()
            out.append(f"<blockquote style='border-left:3px solid #8ab4f8;color:#9aa0ab;margin:6px 0;padding:2px 10px;'>{_inline_md(m.group(1))}</blockquote>")
            continue
        if re.match(r'^[\s]*[-*_]{3,}[\s]*$', line):
            close_lists()
            out.append("<hr style='border:none;border-top:1px solid #33363e;margin:8px 0;'>")
            continue
        if not line.strip():
            close_lists()
            out.append("<div style='height:6px;'></div>")
            continue
        close_lists()
        out.append(f"<p style='margin:3px 0;'>{_inline_md(line)}</p>")
    close_lists()
    flush_table()
    if in_code:
        out.append("<pre style='background:#1f2126;border:1px solid #33363e;border-radius:4px;"
                   "padding:8px;font-family:Consolas,monospace;font-size:12px;white-space:pre-wrap;'>"
                   + esc("\n".join(code_buf)) + "</pre>")
    return "\n".join(out)


# ===================== 小说数据模型 =====================
def sanitize_filename(name):
    return re.sub(r'[\\/:*?"<>|]', '_', str(name).strip()) or "未命名"


class NovelProject:
    """一本小说：meta.yaml + chapters/*.md + memory/(memory.md, summaries.yaml)"""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.name = os.path.basename(self.root)
        self.meta_path = os.path.join(self.root, "meta.yaml")
        self.chapters_dir = os.path.join(self.root, "chapters")
        self.memory_dir = os.path.join(self.root, "memory")
        self.memory_path = os.path.join(self.memory_dir, "memory.md")
        self.summaries_path = os.path.join(self.memory_dir, "summaries.yaml")
        self.plans_path = os.path.join(self.memory_dir, "chapter_plans.yaml")
        self.daily_path = os.path.join(self.memory_dir, "daily_words.json")
        self.backups_dir = os.path.join(self.memory_dir, "backups")
        self.outline_path = os.path.join(self.memory_dir, "outline.yaml")
        self.outline_md_path = os.path.join(self.memory_dir, "outline.md")
        self.foreshadow_path = os.path.join(self.memory_dir, "foreshadows.yaml")
        self.characters_path = os.path.join(self.memory_dir, "characters.yaml")
        self.meta = {}
        self.chapters = []
        self._summaries = {}
        self._plans = {}
        self._daily = {}
        self._outline = []
        self._foreshadows = None
        self._characters = None
        self._load()

    # ---------- 创建 / 加载 ----------
    @classmethod
    def create(cls, root, title, genre="", synopsis_one="", synopsis="",
               world="", characters="", plot_points=""):
        root = os.path.abspath(root)
        os.makedirs(os.path.join(root, "chapters"), exist_ok=True)
        os.makedirs(os.path.join(root, "memory"), exist_ok=True)
        meta = {
            "title": title, "genre": genre, "status": "连载中",
            "synopsis_one": synopsis_one, "synopsis": synopsis,
            "world": world, "characters": characters, "plot_points": plot_points,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        with open(os.path.join(root, "meta.yaml"), "w", encoding="utf-8") as f:
            yaml.dump(meta, f, sort_keys=False, allow_unicode=True)
        np_ = cls(root)
        np_.add_chapter("楔子", "# 楔子\n\n")
        np_.save_memory()
        return np_

    def _load(self):
        if os.path.exists(self.meta_path):
            try:
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    self.meta = yaml.safe_load(f) or {}
            except Exception as e:
                LOG.error(f"加载meta失败 {self.meta_path}: {e}")
                self.meta = {}
        else:
            self.meta = {"title": self.name, "genre": "", "status": "连载中"}
        self._load_summaries()
        self._load_plans()
        self._load_daily()
        self.refresh_chapters()

    # ---------- 摘要索引 ----------
    def _load_summaries(self):
        self._summaries = {}
        if os.path.exists(self.summaries_path):
            try:
                with open(self.summaries_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                self._summaries = {int(k): v for k, v in data.items()}
            except Exception:
                pass

    def save_summaries(self):
        os.makedirs(self.memory_dir, exist_ok=True)
        try:
            with open(self.summaries_path, "w", encoding="utf-8") as f:
                yaml.dump({k: v for k, v in sorted(self._summaries.items())},
                          f, sort_keys=False, allow_unicode=True)
        except Exception as e:
            LOG.error(f"保存摘要失败: {e}")

    def set_summary(self, num, text):
        self._summaries[int(num)] = (text or "").strip()
        self.save_summaries()

    def get_summary(self, num):
        return self._summaries.get(int(num), "")

    # ---------- 章节创作提示 ----------
    def _load_plans(self):
        self._plans = {}
        if os.path.exists(self.plans_path):
            try:
                with open(self.plans_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                self._plans = {int(k): v for k, v in data.items()}
            except Exception:
                pass

    def save_plans(self):
        os.makedirs(self.memory_dir, exist_ok=True)
        try:
            with open(self.plans_path, "w", encoding="utf-8") as f:
                yaml.dump({k: v for k, v in sorted(self._plans.items())},
                          f, sort_keys=False, allow_unicode=True)
        except Exception as e:
            LOG.error(f"保存章节提示失败: {e}")

    def set_plan(self, num, text):
        num = int(num)
        text = (text or "").strip()
        if text:
            self._plans[num] = text
        else:
            self._plans.pop(num, None)
        self.save_plans()

    def get_plan(self, num):
        return self._plans.get(int(num), "")

    # ---------- 字数统计（今日新增） ----------
    def _load_daily(self):
        self._daily = {}
        if os.path.exists(self.daily_path):
            try:
                with open(self.daily_path, "r", encoding="utf-8") as f:
                    self._daily = json.load(f) or {}
            except Exception:
                pass

    def _save_daily(self):
        try:
            with open(self.daily_path, "w", encoding="utf-8") as f:
                json.dump(self._daily, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def record_words(self, num, words):
        """记录某章当前字数，返回今日新增字数。"""
        date = datetime.now().strftime("%Y-%m-%d")
        today = self._daily.setdefault(date, {"total": 0, "chapters": {}})
        old = today["chapters"].get(str(num), 0)
        delta = max(0, words - old)
        today["total"] = today.get("total", 0) + delta
        today["chapters"][str(num)] = words
        self._save_daily()
        return today["total"]

    def today_words(self):
        date = datetime.now().strftime("%Y-%m-%d")
        return int(self._daily.get(date, {}).get("total", 0))

    # ---------- 自动备份 ----------
    def backup_chapter(self, num):
        """保存前将现有版本备份到 memory/backups，保留最近 N 份。"""
        path = self.chapter_path(num)
        if not os.path.exists(path):
            return
        os.makedirs(self.backups_dir, exist_ok=True)
        try:
            with open(path, "r", encoding="utf-8") as f:
                f.read()
        except Exception:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dst = os.path.join(self.backups_dir, f"第{num:02d}章_{ts}.md")
        try:
            shutil.copy2(path, dst)
        except Exception:
            return
        # 清理多余备份
        import glob as _g
        files = sorted(_g.glob(os.path.join(self.backups_dir, f"第{num:02d}章_*.md")))
        keep = int(APP_CONFIG.get("novel_backup_keep", 5))
        for f in files[:-keep]:
            try:
                os.remove(f)
            except Exception:
                pass

    def list_versions(self, num):
        """列出某章历史版本：[{path, time, words}]，按时间倒序。"""
        import glob as _g
        files = sorted(_g.glob(os.path.join(self.backups_dir, f"第{int(num):02d}章_*.md")))
        out = []
        for fp in files:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    content = f.read()
                words = len(re.sub(r'\s', '', content))
            except Exception:
                content, words = "", 0
            name = os.path.basename(fp)
            ts = ""
            m = re.search(r'第\d+章_(\d{8})_(\d{6})', name)
            if m:
                ts = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]} {m.group(2)[:2]}:{m.group(2)[2:4]}:{m.group(2)[4:]}"
            out.append({"path": fp, "time": ts, "words": words, "content": content})
        out.sort(key=lambda x: x["path"], reverse=True)
        return out

    def restore_backup(self, num, backup_path):
        """把历史版本写回章节文件，返回正文；失败返回 None。"""
        try:
            with open(backup_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            LOG.error(f"读取备份失败: {e}")
            return None
        try:
            with open(self.chapter_path(num), "w", encoding="utf-8") as f:
                f.write(content)
            self.refresh_chapters()
            return content
        except Exception as e:
            LOG.error(f"回滚失败: {e}")
            return None

    # ---------- 跨章全文检索 ----------
    def search_all(self, keyword):
        """全书检索关键词，返回 [{num, title, snippet}]。"""
        kw = (keyword or "").strip()
        if not kw:
            return []
        out = []
        for c in self.chapters:
            content = self.load_chapter_content(c["path"])
            if kw not in content:
                continue
            # 取第一次出现位置前后约 60 字作为上下文
            idx = content.find(kw)
            start = max(0, idx - 60)
            end = min(len(content), idx + len(kw) + 60)
            snippet = content[start:end].replace("\n", " ")
            if start > 0:
                snippet = "…" + snippet
            if end < len(content):
                snippet = snippet + "…"
            out.append({"num": c["num"], "title": c["title"], "snippet": snippet,
                        "count": content.count(kw)})
        return out

    # ---------- 章节 ----------
    def refresh_chapters(self):
        items = []
        if os.path.isdir(self.chapters_dir):
            for fn in os.listdir(self.chapters_dir):
                if not fn.endswith(".md"):
                    continue
                full = os.path.join(self.chapters_dir, fn)
                try:
                    num = int(re.match(r'^第\s*(\d+)\s*章', fn).group(1))
                except Exception:
                    try:
                        num = int(re.search(r'(\d+)', fn).group(1))
                    except Exception:
                        num = 99999
                title = self._extract_title(full)
                try:
                    words = len(re.sub(r'\s', '', open(full, encoding='utf-8').read()))
                except Exception:
                    words = 0
                items.append({"num": num, "file": fn, "path": full,
                              "title": title, "words": words,
                              "summary": self.get_summary(num)})
        items.sort(key=lambda x: x["num"])
        self.chapters = items
        return self.chapters

    @staticmethod
    def _extract_title(full):
        try:
            with open(full, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("# "):
                        return line[2:].strip()
                    if line:
                        break
        except Exception:
            pass
        return os.path.splitext(os.path.basename(full))[0]

    def next_chapter_num(self):
        if not self.chapters:
            return 1
        return max(c["num"] for c in self.chapters) + 1

    def chapter_path(self, num):
        return os.path.join(self.chapters_dir, f"第{num:02d}章.md")

    def add_chapter(self, title="新章节", content=""):
        num = self.next_chapter_num()
        path = self.chapter_path(num)
        body = f"# {title}\n\n{content}"
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        self.refresh_chapters()
        return num

    def create_chapter_at(self, num, title="新章节", content=""):
        """按指定章节号创建章节（供"按大纲补齐章节"使用）；该号已存在则跳过，不覆盖。"""
        num = int(num)
        if any(c["num"] == num for c in self.chapters):
            return num
        os.makedirs(self.chapters_dir, exist_ok=True)
        path = self.chapter_path(num)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n{content}")
        self.refresh_chapters()
        return num

    def load_chapter_content(self, file):
        path = file if os.path.isabs(file) else os.path.join(self.chapters_dir, file)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def load_chapter_by_num(self, num):
        for c in self.chapters:
            if c["num"] == num:
                return self.load_chapter_content(c["path"])
        return ""

    def save_chapter(self, num, title, content, record=True):
        path = self.chapter_path(num)
        title = (title or "未命名").strip()
        content = (content or "").strip()
        lines = content.split("\n")
        if lines and lines[0].strip().startswith("#"):
            content = "\n".join(lines[1:]).strip()
        body = f"# {title}\n\n{content}".rstrip() + "\n"
        # 内容有变化时先备份旧版
        try:
            with open(path, "r", encoding="utf-8") as f:
                old_body = f.read()
            if old_body != body:
                self.backup_chapter(num)
        except FileNotFoundError:
            pass
        except Exception:
            pass
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        self.refresh_chapters()
        # 记录今日新增字数
        if record:
            words = len(re.sub(r'\s', '', body))
            self.record_words(num, words)
        self.meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.save_meta()
        return body

    # ---------- 大纲 ----------
    def load_outline(self):
        """返回大纲条目列表：[{title, desc, chapters:[{num,title,outline,hook}]}]"""
        self._outline = []
        if os.path.exists(self.outline_path):
            try:
                with open(self.outline_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or []
                if isinstance(data, list):
                    self._outline = data
            except Exception as e:
                LOG.error(f"加载大纲失败 {self.outline_path}: {e}")
        return self._outline

    def save_outline(self, items):
        os.makedirs(self.memory_dir, exist_ok=True)
        self._outline = items or []
        with open(self.outline_path, "w", encoding="utf-8") as f:
            yaml.dump(self._outline, f, sort_keys=False, allow_unicode=True)
        # 同步生成人类可读 Markdown 大纲
        md = self.outline_to_md(self._outline)
        with open(self.outline_md_path, "w", encoding="utf-8") as f:
            f.write(md)
        return self._outline

    @staticmethod
    def outline_to_md(items):
        title = ""
        lines = ["# 小说大纲", ""]
        for i, vol in enumerate(items or [], 1):
            vt = (vol.get("title") or "").strip() or f"第{i}卷"
            lines.append(f"## {vt}")
            vd = (vol.get("desc") or "").strip()
            if vd:
                lines.append(f"{vd}")
                lines.append("")
            for c in vol.get("chapters", []) or []:
                ct = (c.get("title") or "").strip() or f"第{c.get('num', '?')}章"
                lines.append(f"### 第{c.get('num', '?')}章 {ct}")
                if (c.get("outline") or "").strip():
                    lines.append(f"- 梗概：{c['outline'].strip()}")
                if (c.get("hook") or "").strip():
                    lines.append(f"- 结尾钩子：{c['hook'].strip()}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def ensure_outline_sync(self):
        """确保大纲包含每一章（缺章自动补条目），返回大纲条目列表。"""
        self.load_outline()
        chapters = self.refresh_chapters()
        if not self._outline:
            self._outline = [{"title": "第一卷", "desc": "", "chapters": []}]
        # 建立 章号->条目 映射
        num_map = {}
        for vol in self._outline:
            for c in vol.get("chapters", []) or []:
                if isinstance(c.get("num"), int):
                    num_map[c["num"]] = c
        added = False
        for c in chapters:
            if c["num"] not in num_map:
                body = self.load_chapter_content(c["path"])
                # 取第一段正文前 60 字作梗概占位
                para = re.sub(r'^#.*', '', body or "").strip() or "（待 AI 生成大纲）"
                para = re.sub(r'\s+', '', para)[:60]
                self._outline[0].setdefault("chapters", []).append(
                    {"num": c["num"], "title": c["title"], "outline": para, "hook": ""})
                added = True
        if added:
            # 按章节号排序
            for vol in self._outline:
                vols = vol.get("chapters") or []
                vols.sort(key=lambda x: x.get("num", 0))
            self.save_outline(self._outline)
        return self._outline

    def update_outline_chapter(self, num, title=None, outline=None, hook=None):
        """更新某章的大纲条目（不新增）。"""
        self.load_outline()
        for vol in self._outline:
            for c in vol.get("chapters", []) or []:
                if c.get("num") == int(num):
                    if title is not None:
                        c["title"] = title
                    if outline is not None:
                        c["outline"] = outline
                    if hook is not None:
                        c["hook"] = hook
                    self.save_outline(self._outline)
                    return True
        return False

    def delete_outline_chapter(self, num):
        self.load_outline()
        changed = False
        for vol in self._outline:
            vols = vol.get("chapters") or []
            before = len(vols)
            vol["chapters"] = [c for c in vols if c.get("num") != int(num)]
            if len(vol["chapters"]) != before:
                changed = True
        if changed:
            self.save_outline(self._outline)
        return changed

    def delete_chapter(self, num):
        path = self.chapter_path(num)
        if os.path.exists(path):
            os.remove(path)
        self._summaries.pop(int(num), None)
        self.save_summaries()
        self._plans.pop(int(num), None)
        self.save_plans()
        self.refresh_chapters()
        return True

    def _swap_chapter_meta(self, a_num, b_num):
        """交换两章的摘要/创作提示/字数记录（用于章节重排后数据跟随内容）"""
        a_num, b_num = int(a_num), int(b_num)
        if a_num in self._summaries or b_num in self._summaries:
            sa, sb = self._summaries.get(a_num), self._summaries.get(b_num)
            if sa is not None:
                self._summaries[b_num] = sa
            else:
                self._summaries.pop(b_num, None)
            if sb is not None:
                self._summaries[a_num] = sb
            else:
                self._summaries.pop(a_num, None)
            self.save_summaries()
        if a_num in self._plans or b_num in self._plans:
            pa, pb = self._plans.get(a_num), self._plans.get(b_num)
            if pa is not None:
                self._plans[b_num] = pa
            else:
                self._plans.pop(b_num, None)
            if pb is not None:
                self._plans[a_num] = pb
            else:
                self._plans.pop(a_num, None)
            self.save_plans()
        # 字数记录跟随内容
        changed = False
        for date, day in self._daily.items():
            ca = day.get("chapters", {}).get(str(a_num))
            cb = day.get("chapters", {}).get(str(b_num))
            if ca is not None:
                day["chapters"][str(b_num)] = ca
            else:
                day["chapters"].pop(str(b_num), None)
            if cb is not None:
                day["chapters"][str(a_num)] = cb
            else:
                day["chapters"].pop(str(a_num), None)
            changed = True
        if changed:
            self._save_daily()

    def move_chapter(self, num, delta):
        self.refresh_chapters()
        idx = next((i for i, c in enumerate(self.chapters) if c["num"] == num), None)
        if idx is None:
            return False
        j = idx + delta
        if j < 0 or j >= len(self.chapters):
            return False
        a, b = self.chapters[idx], self.chapters[j]
        tmp = self.chapter_path(0)
        os.rename(a["path"], tmp)
        os.rename(b["path"], self.chapter_path(a["num"]))
        os.rename(tmp, self.chapter_path(b["num"]))
        self._swap_chapter_meta(a["num"], b["num"])
        self.refresh_chapters()
        return True

    def reorder_chapters_by_nums(self, desired_nums):
        """按目标顺序（旧章号列表）重排真实章节文件并重映射记忆；成功返回 {旧号:新号}，否则 None。"""
        self.refresh_chapters()
        cur = [c["num"] for c in self.chapters]
        desired = [int(n) for n in desired_nums if int(n) in cur]
        if len(desired) != len(cur) or set(desired) != set(cur):
            return None
        if desired == cur:
            return None
        mapping = {old: new for new, old in enumerate(desired, start=1)}
        raw = {c["num"]: self.load_chapter_content(c["path"]) for c in self.chapters}
        # 两阶段：先全部移到临时名，再写入新号，避免相互覆盖
        tmp_map = {}
        for old in raw:
            tmp = self.chapter_path(9000 + old)
            os.replace(self.chapter_path(old), tmp)
            tmp_map[old] = tmp
        for old, content in raw.items():
            with open(self.chapter_path(mapping[old]), "w", encoding="utf-8") as f:
                f.write(content)
        for tmp in tmp_map.values():
            try:
                os.remove(tmp)
            except Exception:
                pass
        self._remap_memory(mapping)
        self.refresh_chapters()
        return mapping

    def _remap_memory(self, mapping):
        """章节重排后，把摘要/创作提示/每日字数按 {旧号:新号} 重映射。"""
        def remap_int(d):
            nd = {}
            for k, v in d.items():
                try:
                    old = int(k)
                except Exception:
                    nd[k] = v
                    continue
                nd[mapping.get(old, old)] = v
            return nd
        if self._summaries:
            self._summaries = remap_int(self._summaries)
            self.save_summaries()
        if self._plans:
            self._plans = remap_int(self._plans)
            self.save_plans()
        changed = False
        for date, day in self._daily.items():
            ch = day.get("chapters") or {}
            if not ch:
                continue
            nch = {}
            for k, v in ch.items():
                try:
                    old = int(k)
                except Exception:
                    nch[k] = v
                    continue
                nch[str(mapping.get(old, old))] = v
            if set(nch) != set(ch):
                day["chapters"] = nch
                changed = True
        if changed:
            self._save_daily()

    # ---------- meta ----------
    def save_meta(self):
        try:
            with open(self.meta_path, "w", encoding="utf-8") as f:
                yaml.dump(self.meta, f, sort_keys=False, allow_unicode=True)
        except Exception as e:
            LOG.error(f"保存meta失败: {e}")

    def update_meta(self, **kw):
        self.meta.update(kw)
        self.save_meta()

    # ---------- 记忆库 ----------
    def build_memory_markdown(self):
        meta = self.meta
        L = []
        L.append(f"# 《{meta.get('title', self.name)}》 全章节记忆库")
        L.append("")
        L.append("> 由 NovelForge 自动维护。AI 创作时会完整读取本记忆库，请勿手改。")
        L.append("")
        L.append("## 一、作品概要")
        L.append(f"- 书名：《{meta.get('title', '')}》")
        L.append(f"- 类型：{meta.get('genre', '')}")
        L.append(f"- 状态：{meta.get('status', '连载中')}")
        L.append(f"- 一句话梗概：{meta.get('synopsis_one', '')}")
        L.append("")
        L.append("### 完整梗概")
        L.append(meta.get("synopsis", "") or "（未填写）")
        L.append("")
        L.append("## 二、世界观设定")
        L.append(meta.get("world", "") or "（未填写）")
        L.append("")
        L.append("## 三、人物设定")
        L.append(meta.get("characters", "") or "（未填写）")
        L.append("")
        L.append("## 四、伏笔 · 大纲 · 关键物品")
        L.append(meta.get("plot_points", "") or "（未填写）")
        L.append("")
        L.append("## 五、章节记忆（全章节）")
        L.append(f"已写 {len(self.chapters)} 章：")
        for c in self.chapters:
            L.append("")
            L.append(f"### 第{c['num']}章 · {c['title']}")
            L.append(c.get("summary") or "（暂无摘要，点击「更新记忆」生成）")
        return "\n".join(L)

    def save_memory(self):
        os.makedirs(self.memory_dir, exist_ok=True)
        text = self.build_memory_markdown()
        with open(self.memory_path, "w", encoding="utf-8") as f:
            f.write(text)
        return text

    def load_memory(self):
        try:
            with open(self.memory_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return self.build_memory_markdown()

    # ---------- 导出 ----------
    def export_all(self):
        parts = [f"# 《{self.meta.get('title', self.name)}》", ""]
        if self.meta.get("synopsis_one"):
            parts += [f"> {self.meta['synopsis_one']}", ""]
        for c in self.chapters:
            content = self.load_chapter_content(c["path"])
            if content:
                parts.append(content.rstrip())
                parts.append("")
        return "\n".join(parts)

    def export_epub(self, path):
        """导出 EPUB 3 电子书到 path。返回是否成功。"""
        import zipfile
        title = self.meta.get("title", self.name) or self.name
        author = self.meta.get("author", "未知作者")
        uuid = f"urn:uuid:novelforge-{self.name}"
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""")
                # 章节 XHTML
                chapters = []
                for i, c in enumerate(self.chapters, 1):
                    raw = self.load_chapter_content(c["path"])
                    raw = re.sub(r'(?m)^#\s*(.*)$', lambda m: f"<h2>{esc(m.group(1))}</h2>", raw)
                    paras = []
                    for line in raw.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("<h2>"):
                            paras.append(line)
                        else:
                            paras.append(f"<p>{esc(line)}</p>")
                    body = "\n".join(paras)
                    fname = f"chap{i:03d}.xhtml"
                    chapters.append((fname, c["title"], c["num"]))
                    z.writestr(f"OEBPS/{fname}", f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>{esc(c['title'])}</title></head>
<body>
<h1>{esc(c['title'])}</h1>
{body}
</body>
</html>""")
                # content.opf
                manifest = "\n".join(
                    f'    <item id="chap{i}" href="{fn}" media-type="application/xhtml+xml"/>'
                    for i, (fn, _, _) in enumerate(chapters, 1))
                spine = "\n".join(f'    <itemref idref="chap{i}"/>' for i in range(1, len(chapters) + 1))
                ncx_navpoints = "\n".join(
                    f'    <navPoint id="nav{i}" playOrder="{i}"><navLabel><text>{esc(t)}</text></navLabel>'
                    f'<content src="{fn}"/></navPoint>'
                    for i, (fn, t, _) in enumerate(chapters, 1))
                z.writestr("OEBPS/content.opf", f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{uuid}</dc:identifier>
    <dc:title>{esc(title)}</dc:title>
    <dc:creator>{esc(author)}</dc:creator>
    <dc:language>zh-CN</dc:language>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
{manifest}
  </manifest>
  <spine toc="ncx">
{spine}
  </spine>
</package>""")
                z.writestr("OEBPS/toc.ncx", f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{uuid}"/></head>
  <docTitle><text>{esc(title)}</text></docTitle>
  <navMap>
{ncx_navpoints}
  </navMap>
</ncx>""")
            return True
        except Exception as e:
            LOG.error(f"导出EPUB失败: {e}")
            return False

    def pack_backup(self, zip_path):
        """把整本小说（meta+chapters+memory，不含 backups 自身）打包成 zip。"""
        import zipfile
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                for folder, _subs, files in os.walk(self.root):
                    rel = os.path.relpath(folder, self.root)
                    if rel == "memory" or rel.startswith("memory" + os.sep):
                        if os.path.basename(folder) == "backups":
                            continue
                    for fn in files:
                        full = os.path.join(folder, fn)
                        arc = os.path.join(rel, fn) if rel != "." else fn
                        z.write(full, arc)
            return True
        except Exception as e:
            LOG.error(f"打包失败: {e}")
            return False

    # ---------- 人物卡片（结构化） ----------
    def load_characters(self):
        if self._characters is None:
            try:
                with open(self.characters_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or []
                self._characters = data if isinstance(data, list) else []
            except Exception:
                self._characters = []
        return self._characters

    def save_characters(self):
        try:
            with open(self.characters_path, "w", encoding="utf-8") as f:
                yaml.dump(self._characters or [], f, sort_keys=False, allow_unicode=True)
        except Exception as e:
            LOG.error(f"保存人物失败: {e}")

    def ensure_characters(self):
        """若尚无结构化人物卡，则从 meta.characters 文本自动导入。"""
        self.load_characters()
        if self._characters:
            return self._characters
        txt = (self.meta.get("characters") or "").strip()
        items = []
        if txt:
            for line in txt.splitlines():
                line = re.sub(r'^[-*·\s]+', '', line.strip())
                if not line:
                    continue
                m = re.match(r'^(.+?)[：:（(]\s*(.*?)[）)]\s*$', line)
                if m:
                    name, identity = m.group(1).strip(), m.group(2).strip()
                elif "：" in line:
                    name, _, identity = line.partition("：")
                    name, identity = name.strip(), identity.strip()
                else:
                    name, identity = line, ""
                items.append({"name": name, "role": "", "identity": identity,
                              "relations": "", "arc": "", "notes": ""})
        self._characters = items
        self.save_characters()
        return items

    def characters_to_md(self):
        """结构化人物卡 -> 文本（供 meta 展示与 AI 上下文注入）。"""
        self.ensure_characters()
        lines = []
        for c in self._characters:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            bits = []
            if c.get("role"):
                bits.append(f"定位：{c['role']}")
            if c.get("identity"):
                bits.append(f"身份：{c['identity']}")
            if c.get("relations"):
                bits.append(f"关系：{c['relations']}")
            if c.get("arc"):
                bits.append(f"成长弧：{c['arc']}")
            if c.get("notes"):
                bits.append(f"备注：{c['notes']}")
            lines.append(f"- {name}" + (f"（{'；'.join(bits)}）" if bits else ""))
        return "\n".join(lines) if lines else "（未填写）"

    # ---------- 多平台发布辅助 ----------
    def export_for_publish(self, dir_path):
        """生成投稿包目录：投稿信息.txt + 全文.txt。返回说明文本。"""
        os.makedirs(dir_path, exist_ok=True)
        title = self.meta.get("title", self.name) or self.name
        n = len(self.chapters)
        total = sum(c["words"] for c in self.chapters)
        info = []
        info.append(f"书名：《{title}》")
        info.append(f"类型：{self.meta.get('genre', '')}")
        info.append(f"状态：{self.meta.get('status', '连载中')}")
        info.append(f"字数：{total} 字　章节数：{n} 章")
        info.append(f"一句话简介：{self.meta.get('synopsis_one', '')}")
        info.append("完整简介：")
        info.append(self.meta.get("synopsis", "") or "（未填写）")
        info.append("")
        info.append("【作品标签】")
        tags = [t.strip() for t in re.split(r'[，,、\s]+', self.meta.get("genre", "") or "") if t.strip()]
        info.append("、".join(tags) if tags else "（可在小说信息中补充）")
        info.append("")
        info.append("【当前大纲】")
        info.append(self.outline_to_md(self.load_outline()))
        with open(os.path.join(dir_path, "投稿信息.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(info))
        full = []
        for c in self.chapters:
            content = self.load_chapter_content(c["path"]).rstrip()
            if content:
                full.append(content)
                full.append("")
        with open(os.path.join(dir_path, "全文.txt"), "w", encoding="utf-8") as f:
            f.write("\n\n".join(full))
        return f"{n} 章 / {total} 字"

    # ---------- 导入恢复 ----------
    def restore_from_zip(self, zip_path, target_dir):
        """从整本备份 zip 恢复小说到 target_dir；返回是否成功。"""
        import zipfile
        try:
            os.makedirs(target_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(target_dir)
            return True
        except Exception as e:
            LOG.error(f"导入失败: {e}")
            return False

    # ---------- 伏笔台账 ----------
    def load_foreshadows(self):
        if self._foreshadows is None:
            try:
                with open(self.foreshadow_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or []
                self._foreshadows = data if isinstance(data, list) else []
            except Exception:
                self._foreshadows = []
        return self._foreshadows

    def save_foreshadows(self):
        try:
            with open(self.foreshadow_path, "w", encoding="utf-8") as f:
                yaml.dump(self._foreshadows or [], f, sort_keys=False, allow_unicode=True)
        except Exception as e:
            LOG.error(f"保存伏笔失败: {e}")

    def extract_foreshadows(self):
        """从各章记忆摘要的『伏笔与悬念/结尾钩子』小节 + 大纲 hook 提取伏笔，重建台账。"""
        items = []
        seen = set()
        def push(ch, text):
            t = (text or "").strip()
            t = re.sub(r'^[\s\-*·◦]+', '', t).strip()
            if not t or t in ("无", "无。", "（无）", "无，", "暂无"):
                return
            if len(t) < 4:
                return
            key = re.sub(r'\s', '', t)[:30]
            if key in seen:
                return
            seen.add(key)
            items.append({"chapter": int(ch), "text": t, "status": "待回收"})
        for c in self.chapters:
            s = self.get_summary(c["num"]) or ""
            m = re.search(r'###\s*伏笔与悬念\s*\n(.*?)(?=\n###|\Z)', s, re.S)
            if m:
                for line in m.group(1).splitlines():
                    if line.strip():
                        push(c["num"], line)
            m2 = re.search(r'###\s*结尾钩子\s*\n(.*?)(?=\n###|\Z)', s, re.S)
            if m2:
                for line in m2.group(1).splitlines():
                    if line.strip():
                        push(c["num"], line)
        for vol in self.load_outline() or []:
            for c in vol.get("chapters", []) or []:
                if c.get("hook"):
                    push(c.get("num", 0), c["hook"])
        self._foreshadows = items
        self.save_foreshadows()
        return items

    def resolve_foreshadows(self):
        """启发式：若后续章节摘要包含某伏笔关键词并带回收词，标记为已回收。"""
        self.load_foreshadows()
        for item in self._foreshadows:
            if item.get("status") == "已回收":
                continue
            ch = int(item.get("chapter", 0) or 0)
            core = re.sub(r'[\s。，、；：？！“”"\'（）()]', '', item.get("text", ""))[:6]
            if len(core) < 3:
                continue
            for c in self.chapters:
                if int(c["num"]) <= ch:
                    continue
                s = self.get_summary(c["num"]) or ""
                if core in re.sub(r'\s', '', s) and any(
                        w in s for w in ("回收", "解决", "真相", "揭示", "揭开", "证实", "明白",
                                         "恍然大悟", "水落石出", "谜底", "答案")):
                    item["status"] = "已回收"
                    item["resolved_ch"] = int(c["num"])
                    break
        self.save_foreshadows()
        return self._foreshadows


# ===================== 章节摘要 =====================
SUMMARY_PROMPT = """你是资深小说编辑。请为下面的章节生成一份【记忆摘要】，供创作后续章节时保持连贯，格式严格如下（必须包含四个小节）：

### 摘要
（200字以内，概括本章完整剧情走向）
### 关键事件
（分点列出本章重要事件与情节转折）
### 出场人物
（分点列出出场人物及其状态/情感变化）
### 伏笔与悬念
（分点列出本章埋下的伏笔、悬念与结尾钩子；若没有则写"无"）

章节标题：{title}
章节正文：
{content}
"""


def heuristic_summary(text, limit=280):
    text = (text or "").strip()
    if not text:
        return "（空章）"
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines and lines[0].startswith("#"):
        lines = lines[1:]
    body = " ".join(lines)
    if not body:
        return "（空章）"
    return body[:limit] + ("……" if len(body) > limit else "")


def count_words(text):
    """统计正文字数（去除空白字符）。"""
    return len(re.sub(r'\s', '', text or ""))


# ===================== 套路引擎 与 去AI味 =====================
# 常见网文套路库：用于"套路判定"与"套用套路增强创作"
TROPES = {
    "golden_three": {
        "name": "黄金三章",
        "focus": "开篇三章",
        "block": (
            "· 黄金三章法：前三章内必须做到——第一章 300 字内立住主角人设与处境，"
            "1000 字内抛出核心矛盾/危机，章末给一个必须读下去的理由；"
            "第二章升级冲突并展示主角独特优势（金手指/性格闪光点）；"
            "第三章迎来第一个小高潮或身份反转，把最大悬念（书名级钩子）甩到读者脸上。\n"
            "· 每章结尾都留一个“未完成事件”，让读者忍不住点下一章。"
        ),
    },
    "face_slap": {
        "name": "扮猪吃虎·打脸流",
        "focus": "爽点制造",
        "block": (
            "· 打脸流节奏：先压后扬——铺垫反派/路人轻视主角的“因”，"
            "再让主角在关键时刻用压倒性实力/身份揭穿并反杀（打脸要干脆利落、有旁观者视角）。\n"
            "· 制造阶层落差与信息差：读者知道主角藏拙，对手不知道；每三到五章安排一次打脸小高潮。\n"
            "· 打脸后要立刻安排新台阶（更大的对手/更深的秘密），避免爽完就散。"
        ),
    },
    "system": {
        "name": "系统·金手指流",
        "focus": "金手指",
        "block": (
            "· 金手指要“有代价、有成长”：给主角的金手指设置限制（冷却、消耗、副作用或条件触发），"
            "避免无脑无敌；每次使用都推动情节而非自动解决一切。\n"
            "· 通过金手指的成长（解锁新能力/新等级）牵引剧情升级，并埋下金手指本身的来历之谜。"
        ),
    },
    "hook": {
        "name": "悬疑钩子流",
        "focus": "每章留钩",
        "block": (
            "· 每章结尾必须留一个具体可感的钩子：一个反常物件、一句没说完的话、一个危险的脚步声、"
            "一条信息差，而不是“这究竟是……还是……”式反问。\n"
            "· 采用“冰山下悬念”：明线推进的同时，暗线（伏笔）每隔几章露一小块，牵引长线追读。"
        ),
    },
    "payoff": {
        "name": "铺垫回收·草蛇灰线",
        "focus": "伏笔管理",
        "block": (
            "· 草蛇灰线：前文埋下的伏笔（物件、台词、细节）必须在后文兑现并产生影响，"
            "回收时制造“原来如此”的惊叹感。\n"
            "· 伏笔分三层：近期（本章内回收）、中期（3-10章回收）、长期（全书主线），"
            "写作时明确当前在埋哪一层、还哪一层。"
        ),
    },
    "ensemble": {
        "name": "群像多线叙事",
        "focus": "人物与多线",
        "block": (
            "· 群像写法：给配角独立的动机与秘密，多线并进、偶尔交汇；"
            "每章以一人视角为主，但让其他线的变化在背景里若隐若现。\n"
            "· 交汇点制造冲突升级：两条线的目标在同一事件上相撞，引爆情节。"
        ),
    },
    "twist": {
        "name": "反转流·意外展开",
        "focus": "意外与反转",
        "block": (
            "· 反转要有铺垫依据：每个反转必须在前面埋下可回看的细节（读者重读时能发现），"
            "不能为转而转。\n"
            "· 用“读者预期管理”制造反转：先引导读者相信 A，再用一个被忽视的细节翻转为 B，"
            "反转后要重新解释前文。"
        ),
    },
}

# 去AI味·文风铁律（写作类模式注入，最高优先级）
ANTI_AI_STYLE = (
    "【去AI味·文风铁律】（写作类模式最高优先级，宁可保留一点粗粝，也不要“AI腔”）\n"
    "1. 禁用以下 AI 高频词/腔调：此外、然而、不禁、仿佛、似乎、显得、微微、缓缓、轻轻、"
    "渐渐、些许、某种、瞬间、下一刻、这一刻、与此同时、值得一提的是、值得注意的是、"
    "总的来说、不仅如此、除此之外、换句话说（除非确有必要）。\n"
    "2. 不用“总结式/旁白式”解释人物内心：用动作、细节、对话和留白让读者自己体会，"
    "不写“他心想”“他意识到”“这让他感到”。\n"
    "3. 不用模板化转场与结尾：不写“就这样”“于是”“第二天清晨”流水账；"
    "结尾钩子落到具体动作/物件/一句未说完的话，不用“这究竟是……还是……？”式反问收束。\n"
    "4. 用具体取代抽象：写感官细节（气味、触感、光线、声响）和具体物件、数字、动作，"
    "不写“环境显得很荒凉”这类空泛句。\n"
    "5. 长短句交错，口语与书面结合；对话要有各角色自己的腔调；敢用粗粝的比喻，敢留白；"
    "禁止排比堆砌、四字成语连发、每段都“金句化”的悬浮感。\n"
    "6. 像一位有十年功底、风格鲜明的中文小说家那样写作，不要像 AI 助理。"
)

# 逻辑铁律（写作类模式注入：保证因果/时间线/动机/能力边界自洽）
LOGIC_RULES = (
    "【逻辑铁律】（写作类模式必须遵守，保障剧情逻辑严密）\n"
    "1. 因果闭环：每一处情节都要有“因为所以”——发生的事要有前因，做出的选择要有后果；"
    "禁止无源之水式的突然解决、天上掉馅饼、反派突然降智送人头。\n"
    "2. 时间线自洽：出场→赶路→交手→恢复等过程用时合理，章节内时间不跳变；"
    "前文明确的时间（白天/夜晚/天数/季节）不得在本章悄悄改口。\n"
    "3. 动机成立：人物每一步行动都要有符合其性格与利益的动机；"
    "禁止让角色“为了剧情需要”做明显违背其立场、能力与情商的事（工具人化）。\n"
    "4. 能力边界：角色当前已展现/已获得的能力、道具、资源必须与设定一致，"
    "不得凭空多出或突然失效；升级要给出原因与代价。\n"
    "5. 设定自洽：本章引入的任何新设定（地名/道具/规则/势力）都要与既有世界观相容，"
    "并与前文保持一致；对话、动作、表情要符合说话者身份与处境。\n"
    "6. 信息有据：人物知道的信息只能来自其视角能获得的渠道；"
    "不得让角色“恰巧知道”不该知道的内容。"
)

# 发散性要求（写作类模式注入：增强脑洞与非常规走向）
DIVERGE_RULES = (
    "【发散性要求】（提升吸引力与差异化，避免套路化流水账）\n"
    "1. 每章至少引入一个意外变量或非常规选择：让剧情往读者“意料之外、情理之中”的方向偏转；"
    "禁止顺理成章地“一步到位”解决所有问题。\n"
    "2. 主动制造冲突与代价：好结局要有代价，得到要有失去，胜利背后留隐患；"
    "别让主角事事如意。\n"
    "3. 允许低成本高脑洞的设定与细节（新道具/新规则/反直觉反应），但要符合本书世界观，"
    "并给出自洽解释。\n"
    "4. 多线并行：在推进主线时，让一个次要人物/旧伏笔/环境细节“意外”发挥作用，"
    "增加层次感。\n"
    "5. 避免“主角视角全知”的平铺叙述：适当用他人视角或留白制造悬念与张力。"
)

# AI 高频“缝合词”（用于本地扫描提示）
AI_CLICHES = ["此外", "然而", "不禁", "仿佛", "似乎", "显得", "微微", "缓缓", "轻轻",
              "渐渐", "些许", "某种", "瞬间", "下一刻", "这一刻", "与此同时",
              "值得一提的是", "值得注意的是", "总的来说", "不仅如此", "除此之外", "换句话说",
              "言归正传", "总而言之", "由此可见"]

# 可安全自动清除的纯冗余连接词（机械替换，不动文意）
AI_FILLERS = ["值得一提的是", "值得注意的是", "总的来说", "综上所述", "与此同时",
              "由此可见", "换句话说", "不仅如此", "除此之外", "言归正传", "总而言之"]


def auto_trope(novel):
    """根据当前进度启发式推荐套路；无匹配时返回 None。"""
    n = len(novel.chapters)
    if n <= 3:
        return "golden_three"
    ctx = "\n".join((c.get("summary") or "") for c in novel.chapters)
    for key, feats in (("system", ["系统", "金手指", "面板", "任务", "奖励"]),
                       ("face_slap", ["打脸", "废柴", "扮猪吃虎", "退婚", "嘲讽"]),
                       ("hook", ["谜", "秘密", "失踪", "异象", "怪物", "遗迹"])):
        if any(f in ctx for f in feats):
            return key
    if n > 12:
        return "payoff"
    return "hook"


# ===================== 人物关系图谱 =====================
REL_COLOR_RULES = [
    ("敌对", "#c0392b"), ("仇", "#c0392b"), ("恨", "#c0392b"), ("死敌", "#c0392b"),
    ("对手", "#c0392b"), ("宿敌", "#c0392b"),
    ("师徒", "#3a6ea5"), ("师", "#3a6ea5"), ("徒", "#3a6ea5"), ("弟子", "#3a6ea5"),
    ("老师", "#3a6ea5"), ("师傅", "#3a6ea5"), ("学生", "#3a6ea5"), ("弟子", "#3a6ea5"),
    ("恋人", "#d05f9b"), ("爱", "#d05f9b"), ("喜欢", "#d05f9b"), ("追求", "#d05f9b"),
    ("情侣", "#d05f9b"), ("暧昧", "#d05f9b"), ("伴侣", "#d05f9b"), ("女友", "#d05f9b"),
    ("女友", "#d05f9b"), ("丈夫", "#d05f9b"), ("妻子", "#d05f9b"), ("夫妻", "#d05f9b"),
    ("亲人", "#2e8b57"), ("家人", "#2e8b57"), ("父", "#2e8b57"), ("母", "#2e8b57"),
    ("兄", "#2e8b57"), ("弟", "#2e8b57"), ("姐", "#2e8b57"), ("妹", "#2e8b57"),
    ("父子", "#2e8b57"), ("母女", "#2e8b57"), ("兄弟", "#2e8b57"), ("家族", "#2e8b57"),
    ("朋友", "#16a2b8"), ("同伴", "#16a2b8"), ("伙伴", "#16a2b8"), ("队友", "#16a2b8"),
    ("盟友", "#16a2b8"), ("好友", "#16a2b8"), ("搭档", "#16a2b8"),
    ("主仆", "#e67e22"), ("主人", "#e67e22"), ("仆人", "#e67e22"), ("下属", "#e67e22"),
    ("上司", "#e67e22"), ("手下", "#e67e22"), ("主子", "#e67e22"), ("主子", "#e67e22"),
    ("臣", "#e67e22"), ("君主", "#e67e22"), ("雇主", "#e67e22"), ("雇佣", "#e67e22"),
]
CHAR_ROLE_COLORS = {"主角": "#3a5f9e", "女主": "#d05f9b", "反派": "#c0392b",
                    "导师": "#3a6ea5", "配角": "#2e8b57"}


def _rel_color(word):
    for kw, c in REL_COLOR_RULES:
        if kw in (word or ""):
            return QColor(c)
    return QColor("#7a8190")


def build_character_graph(novel):
    """从人物卡构建关系图谱：返回 (nodes, edges)。
    nodes=[(key, label, color)]；edges=[(a, b, 关系标签, color)]。"""
    novel.ensure_characters()
    chars = novel.load_characters()
    nodes, edges = [], []
    by_name = {}
    seen_pairs = set()
    for c in chars:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        role = (c.get("role") or "").strip()
        color = CHAR_ROLE_COLORS.get(role, QColor("#5c6470"))
        nodes.append((name, name, color))
        by_name[name] = c
    for c in chars:
        name = (c.get("name") or "").strip()
        rel = (c.get("relations") or "").strip()
        if not name or not rel or name not in by_name:
            continue
        for p in re.split(r'[;；,，、\n]', rel):
            p = p.strip()
            if not p:
                continue
            target = None
            rword = ""
            m = re.match(r'^([^：:]{1,6})[：:]\s*([^\s，,；;、]+)', p)
            if m:
                rword, target = m.group(1).strip(), m.group(2).strip()
            else:
                m2 = re.match(r'^(?:与|和|跟)([^\s，,；;、]+?)(?:为|是|乃)?([^，,；;、]{1,6})$', p)
                if m2:
                    target, rword = m2.group(1).strip(), m2.group(2).strip()
            if target and target != name and target in by_name and rword:
                key = tuple(sorted((name, target)))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                edges.append((name, target, rword[:6], _rel_color(rword)))
    return nodes, edges


def scan_ai_cliches(text):
    """扫描文本中的 AI 高频词，返回 [(词, 次数)] 按次数降序。"""
    if not text:
        return []
    counts = {}
    for w in AI_CLICHES:
        c = text.count(w)
        if c:
            counts[w] = c
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _judge_tropes(novel):
    """套路判定：本地启发式 + LLM 深度分析，返回 Markdown 报告。"""
    chapters = novel.chapters
    n = len(chapters)
    lines = ["# 套路判定报告", "", f"**当前进度**：共 {n} 章"]
    summaries = "".join((c.get("summary") or "") for c in chapters)
    full = "\n".join(novel.load_chapter_content(c["path"]) or "" for c in chapters)
    if n:
        hooks = sum(1 for c in chapters if re.search(r'伏笔|悬念|钩子|秘密|异象|失踪|谜', c.get("summary") or ""))
        if hooks < n * 0.6:
            lines.append(f"**钩子密度**：{hooks}/{n} 章结尾有明显钩子/悬念（偏低，建议每章结尾都留具体钩子）")
        else:
            lines.append(f"**钩子密度**：{hooks}/{n} 章结尾有明显钩子/悬念（良好，继续保持）")
        avg = count_words(full) // n
        if avg < 2000:
            lines.append(f"**平均每章字数**：约 {avg} 字（偏低，建议每章 2000 字以上以保证剧情容量）")
        else:
            lines.append(f"**平均每章字数**：约 {avg} 字（达标）")
    matched = []
    for key, feats in (("系统·金手指流", ["系统", "金手指", "面板", "任务", "奖励", "积分"]),
                       ("扮猪吃虎·打脸流", ["打脸", "废柴", "扮猪吃虎", "退婚", "嘲讽", "废物"]),
                       ("悬疑钩子流", ["谜", "秘密", "失踪", "异象", "怪物", "遗迹", "心跳", "罗盘"]),
                       ("废土/末世生存", ["辐射", "废土", "末日", "幸存者", "变异"])):
        if any(f in summaries for f in feats):
            matched.append(key)
    if n <= 3:
        lines.append("**开局阶段**：正处于“黄金三章”窗口期——检查人设是否 300 字内立住、"
                     "1000 字内是否抛出核心矛盾、每章是否留钩。")
    if matched:
        lines.append(f"**已识别套路特征**：{'、'.join(matched)}")
    else:
        lines.append("**已识别套路特征**：暂无明显标签化套路，属自由叙事——吸引力更依赖文笔与人物，"
                     "可考虑引入明确的套路节奏（爽点/钩子/打脸）。")
    cl = scan_ai_cliches(full)
    if cl:
        top = "、".join(f"{w}×{c}" for w, c in cl[:8])
        lines.append(f"**AI 高频词扫描**：命中 {sum(c for _, c in cl)} 处（{top}…）"
                     "——这是“一眼AI”的主要来源，建议创作时强调去AI味。")
    else:
        lines.append("**AI 高频词扫描**：未发现明显 AI 腔词汇。")
    llm = _llm_generate(
        "请基于以下全章节记忆与启发式结论，输出一份面向作者的『套路与吸引力诊断』：\n"
        "1) 当前故事更适合强化哪种/哪几种网文套路（给出具体做法）；\n"
        "2) 前文在爽点/钩子/伏笔/节奏上最强的 3 点与最弱的 3 点；\n"
        "3) 接下来 3-5 章最该安排的套路动作（要具体到情节）；\n"
        "4) 写作风格上的去AI味建议。\n"
        "用 Markdown 输出，务实、可直接执行，不要空话。\n\n"
        f"【启发式结论】\n{chr(10).join(lines)}\n\n"
        f"【全章节记忆】\n{build_creation_context(novel)}",
        system="你是一位深谙网文市场的资深主编，擅长套路设计与吸引力优化。")
    if llm:
        lines.append("")
        lines.append("## AI 主编深度诊断")
        lines.append(llm)
    return "\n".join(lines)


def build_facts_context(novel, max_chars=4000):
    """提炼『既定事实清单』用于一致性体检（人物/能力/伏笔/时间线，不含正文）。"""
    meta = novel.meta
    parts = []
    parts.append(f"书名《{meta.get('title', novel.name)}》 类型：{meta.get('genre', '')}")
    if meta.get('synopsis'):
        parts.append("【故事梗概】" + meta.get('synopsis'))
    if meta.get('characters') or os.path.exists(novel.characters_path):
        try:
            chars = novel.characters_to_md()
        except Exception:
            chars = meta.get('characters', '')
        parts.append("【人物设定（结构化人物卡）】" + chars)
    if meta.get('world'):
        parts.append("【世界观】" + meta.get('world'))
    if meta.get('plot_points'):
        parts.append("【伏笔/大纲/关键物品】" + meta.get('plot_points'))
    parts.append("【各章记忆（既定剧情）】")
    for c in novel.chapters:
        s = (c.get("summary") or "").strip()[:400]
        parts.append(f"· 第{c['num']}章《{c['title']}》：{s}")
    return "\n".join(parts)[:max_chars]


def check_consistency(novel, new_text, max_chars=6000):
    """生成后一致性体检：比对全书记忆与新正文，返回冲突清单文本；无冲突/无Key返回空串。"""
    if not APP_CONFIG.get("api_key"):
        return ""
    facts = build_facts_context(novel)
    body = re.sub(r'^#.*', '', new_text or "", count=1).strip()[:max_chars]
    if count_words(body) < 100:
        return ""
    prompt = (
        "你是严格的剧情一致性审稿编辑。下面给出【全书既定事实】与【待审新正文】。\n"
        "请找出新正文中与既定事实冲突、或自身前后矛盾的地方，例如：\n"
        "- 人物关系/身份/称呼/生死与已设定不符；\n"
        "- 已学会的能力、已获得的道具、已发生的事件被遗忘、倒退或重复发生；\n"
        "- 已埋伏笔未承接、上一章结尾钩子未回应；\n"
        "- 时间线、地点、人物状态、因果错乱。\n"
        "若没有冲突，只输出一行：无冲突\n"
        "若有冲突，逐条输出，每条一行，格式：\n"
        "【冲突N】一句话描述 | 新正文位置 | 应如何修正\n"
        "只报一致性冲突，不要评价文笔。\n\n"
        f"【全书既定事实】\n{facts}\n\n"
        f"【待审新正文】\n{body}"
    )
    res = _llm_generate(prompt, system="你是一位严谨的网文剧情一致性审稿编辑，只报冲突、不评文笔。", temperature=0.2)
    if not res or "无冲突" in res:
        return ""
    return res.strip()


def remove_fillers(text):
    """清除纯冗余连接词并规整标点；返回清洗后的文本。"""
    t = text or ""
    for w in AI_FILLERS:
        t = t.replace(w, "")
    t = re.sub(r'[。！？]，', lambda m: m.group(0)[0], t)      # 。，→。
    t = re.sub(r'，[。！？]', lambda m: m.group(0)[1], t)      # ，。→。
    t = re.sub(r'[，、]{2,}', '，', t)
    t = re.sub(r'[。]{2,}', '。', t)
    t = re.sub(r'^[，、。\s]+', '', t, flags=re.MULTILINE)
    t = re.sub(r'\s{2,}', ' ', t)
    return t.strip()


# AI 高频词的“去AI味”替换方案（一键机械替换，保留语义、去掉AI腔）
AI_REPLACEMENTS = {
    "此外": "另外",
    "然而": "不过",
    "不禁": "忍不住",
    "仿佛": "像",
    "似乎": "好像",
    "显得": "",
    "某种": "",
    "些许": "",
    "与此同时": "",
    "值得一提的是": "",
    "值得注意的是": "",
    "总的来说": "",
    "综上所述": "",
    "由此可见": "",
    "不仅如此": "",
    "除此之外": "",
    "言归正传": "",
    "总而言之": "",
    "换句话说": "也就是说",
}


def de_ai_text(text):
    """一键去AI味：替换 AI 高频词为自然表达、清除冗余连接词、规整标点。"""
    t = text or ""
    for w, r in AI_REPLACEMENTS.items():
        t = t.replace(w, r)
    # 带“地/的/着”后缀的叠词：整体处理避免残留
    t = re.sub(r'轻轻(?:地|的)?', '', t)
    t = re.sub(r'微微(?:地|的)?', '', t)
    t = re.sub(r'缓缓(?:地|的)?', '慢慢', t)
    t = re.sub(r'渐渐(?:地|的)?', '慢慢', t)
    t = re.sub(r'瞬间', '一瞬', t)
    t = re.sub(r'[。！？]，', lambda m: m.group(0)[0], t)
    t = re.sub(r'，[。！？]', lambda m: m.group(0)[1], t)
    t = re.sub(r'[，、]{2,}', '，', t)
    t = re.sub(r'[。]{2,}', '。', t)
    t = re.sub(r'\s{2,}', ' ', t)
    return t.strip()


# ===================== 参考相似文：本地参考文库（BM25 离线检索） =====================
REF_LIB_DIR = os.path.join(APP_DIR, "参考文库")
_REF_INDEX = None            # BM25Index 实例
_REF_INDEX_READY = False
_REF_INDEX_STATS = ""        # 索引状态文本（文档数/分块数/路径）


def _zh_tokens(text):
    """零依赖中文检索分词：字符 bigram（对中文短文本检索效果稳定）。"""
    t = re.sub(r'[\s。，、；：？！“”"\'（）()《》\n\r\t·…—\-—]+', '', text or '')
    if len(t) < 2:
        return []
    return [t[i:i + 2] for i in range(len(t) - 1)]


class BM25Index:
    """BM25 检索索引（纯 Python，无需向量库/模型，完全离线）。"""

    def __init__(self):
        self.chunks = []       # [(title, text)]
        self._tokens = []
        self._df = {}
        self._avgdl = 1.0
        self._k1, self._b = 1.5, 0.75

    def clear(self):
        self.chunks, self._tokens, self._df = [], [], {}
        self._avgdl = 1.0

    def add(self, title, text):
        text = (text or "").strip()
        if not text:
            return
        toks = _zh_tokens(text)
        self.chunks.append((title, text))
        self._tokens.append(toks)

    def build(self):
        N = len(self.chunks)
        n = 0
        for toks in self._tokens:
            for t in set(toks):
                self._df[t] = self._df.get(t, 0) + 1
            n += len(toks)
        self._avgdl = (n / N) if N else 1.0

    def search(self, query, top=3):
        qtoks = _zh_tokens(query)
        if not qtoks or not self._tokens:
            return []
        N = len(self._tokens)
        qset = list(set(qtoks))
        scored = []
        for i, toks in enumerate(self._tokens):
            if not toks:
                continue
            dl = len(toks)
            s = 0.0
            for t in qset:
                if t in toks:
                    tf = toks.count(t)
                    df = self._df.get(t, 0)
                    idf = math.log((N - df + 0.5) / (df + 0.5) + 1.0)
                    s += idf * (tf * (self._k1 + 1.0)) / (tf + self._k1 * (1 - self._b + self._b * dl / self._avgdl))
            scored.append((s, i))
        scored.sort(key=lambda x: -x[0])
        out = []
        for s, i in scored[:top]:
            if s > 0:
                out.append((self.chunks[i][0], self.chunks[i][1], round(s, 2)))
        return out


def build_ref_index(lib_dir=None):
    """扫描参考文库目录（*.txt/*.md，按约 400 字切块、重叠 100 字），重建全局 BM25 索引。"""
    global _REF_INDEX, _REF_INDEX_READY, _REF_INDEX_STATS
    lib_dir = lib_dir or APP_CONFIG.get("ref_lib_dir") or REF_LIB_DIR
    os.makedirs(lib_dir, exist_ok=True)
    idx = BM25Index()
    files = []
    for root, _dirs, fnames in os.walk(lib_dir):
        for fn in fnames:
            if fn.lower().endswith((".txt", ".md", ".markdown")):
                files.append(os.path.join(root, fn))
    files.sort()
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
        except Exception:
            continue
        # 去掉 markdown 标题符号，切成段落
        raw = re.sub(r'(?m)^#+\s*', '', raw)
        paras = [p.strip() for p in re.split(r'\n\s*\n', raw) if p.strip()]
        rel = os.path.relpath(fp, lib_dir)
        for p in paras:
            # 长段落切成 400 字块（重叠 100 字）
            step = 300
            for i in range(0, len(p), step):
                idx.add(rel, p[i:i + 400])
    idx.build()
    _REF_INDEX = idx
    _REF_INDEX_READY = True
    _REF_INDEX_STATS = f"{len(files)} 个文件 · {len(idx.chunks)} 个片段"
    return idx, files, len(idx.chunks)


def ref_snippets(query, top=3):
    """返回参考文风片段 [(标题, 文本, 分)]；未建索引返回 []。"""
    global _REF_INDEX, _REF_INDEX_READY
    if not _REF_INDEX_READY or _REF_INDEX is None:
        return []
    try:
        return _REF_INDEX.search((query or "")[:3000], top=top)
    except Exception:
        return []


# ===================== 参考相似文：联网搜索（博查 BoCha） =====================
_PENDING_WEB_REFS = []   # 联网参考结果暂存，供下次 AI 生成注入；由“联网参考”对话框管理


def web_search(query, key, top=5):
    """调用博查（BoCha）Web 搜索 API，返回 [{title, snippet, url}]。"""
    import requests
    if not key:
        raise Exception("未配置联网搜索 Key")
    url = "https://api.bochaai.com/v1/web-search"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"query": (query or "").strip(), "freshness": "noLimit", "summary": True, "count": int(top)}
    resp = requests.post(url, headers=headers, json=payload, timeout=25, proxies=_proxies())
    if not resp.ok:
        raise Exception(f"联网搜索失败（HTTP {resp.status_code}）：{resp.text[:200]}")
    data = resp.json()
    pages = (data.get("data") or {}).get("webPages") or {}
    out = []
    for p in (pages.get("value") or []):
        out.append({"title": p.get("name") or "", "url": p.get("url") or "",
                    "snippet": (p.get("summary") or p.get("snippet") or "")[:500]})
    return out


def parse_outline_yaml(text):
    """把 AI 返回的大纲文本解析为条目列表；解析失败返回 []。"""
    if not text:
        return []
    t = text.strip()
    # 去掉可能的 ```yaml / ``` 包裹
    t = re.sub(r'^```[a-zA-Z]*\s*', '', t)
    t = re.sub(r'\s*```$', '', t)
    # 尝试直接解析
    try:
        data = yaml.safe_load(t)
        if isinstance(data, list):
            return [v for v in data if isinstance(v, dict)]
    except Exception:
        pass
    # 尝试截取从首个 "- title" 或 "[" 开始的部分
    m = re.search(r'(- title:|\s*-\s*\n?\s*title:)', t)
    if m:
        try:
            data = yaml.safe_load(t[m.start():])
            if isinstance(data, list):
                return [v for v in data if isinstance(v, dict)]
        except Exception:
            pass
    return []


def parse_characters_yaml(text):
    """把 AI 返回的人物卡 YAML 解析为 dict 列表；失败返回 []。"""
    if not text:
        return []
    t = text.strip()
    t = re.sub(r'^```[a-zA-Z]*\s*', '', t)
    t = re.sub(r'\s*```$', '', t)
    try:
        data = yaml.safe_load(t)
        if isinstance(data, list):
            return [v for v in data if isinstance(v, dict)]
    except Exception:
        pass
    m = re.search(r'(- name:)', t)
    if m:
        try:
            data = yaml.safe_load(t[m.start():])
            if isinstance(data, list):
                return [v for v in data if isinstance(v, dict)]
        except Exception:
            pass
    return []


def _llm_generate(prompt, system="", temperature=0.5, timeout=180, max_chars=12000):
    """通用LLM生成（非流式UI，直接返回文本）；未配置API或失败返回 None。"""
    try:
        if not APP_CONFIG.get("api_key"):
            return None
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": (prompt or "")[:max_chars]})
        res = call_llm_stream(messages, tools=None, timeout=timeout, temperature=temperature)
        return (res.get("content") or "").strip()
    except Exception as e:
        LOG.error(f"LLM生成失败: {e}")
        return None


def gen_chapter_summary(title, content):
    """生成章节记忆摘要：优先 LLM，未配置 API 时用启发式兜底。"""
    text = re.sub(r'(?m)^\s*#.*$', '', content or "", count=1).strip()
    summ = _llm_generate(
        SUMMARY_PROMPT.format(title=title, content=text[:6000]),
        system="你是一位专业小说编辑，擅长提炼剧情记忆。")
    if not summ:
        summ = heuristic_summary(text)
    return summ


# ===================== 全章节记忆上下文 =====================
def build_creation_context(novel, recent_full=None, max_chars=None):
    """构建 AI 创作注入的『全章节记忆上下文』：世界观+人物+全部章节摘要+最近N章全文。"""
    recent_full = int(APP_CONFIG.get("novel_recent_full", 3) if recent_full is None else recent_full)
    max_chars = int(APP_CONFIG.get("novel_max_full_chars", 6000) if max_chars is None else max_chars)
    meta = novel.meta
    chapters = novel.chapters
    parts = []
    parts.append("【作品概要】")
    parts.append(f"书名：《{meta.get('title', novel.name)}》　类型：{meta.get('genre', '')}　状态：{meta.get('status', '连载中')}")
    parts.append(f"一句话梗概：{meta.get('synopsis_one', '')}")
    parts.append(f"完整梗概：{meta.get('synopsis', '') or '（未填写）'}")
    parts.append("")
    parts.append("【世界观设定】")
    parts.append(meta.get("world", "") or "（未填写）")
    parts.append("")
    parts.append("【人物设定】")
    parts.append(meta.get("characters", "") or "（未填写）")
    parts.append("")
    parts.append("【伏笔·大纲·关键物品】")
    parts.append(meta.get("plot_points", "") or "（未填写）")
    parts.append("")
    parts.append("【已写全部章节记忆】（这是全书记忆，创作时严禁与以下任何一条冲突）")
    if chapters:
        for c in chapters:
            summ = (c.get("summary") or "（暂无摘要）").strip()[:500]
            plan = novel.get_plan(c["num"])
            if plan:
                parts.append(f"· 第{c['num']}章《{c['title']}》：{summ}（本章创作提示：{plan.strip()[:200]}）")
            else:
                parts.append(f"· 第{c['num']}章《{c['title']}》：{summ}")
    else:
        parts.append("（尚未有章节）")
    # 章节创作提示汇总（若有）
    plans = [(c["num"], novel.get_plan(c["num"])) for c in chapters if novel.get_plan(c["num"])]
    if plans:
        parts.append("")
        parts.append("【章节创作提示汇总】（用户为各章设定的写作要点，续写时须满足最近一章的要点）")
        for num, plan in plans:
            parts.append(f"· 第{num}章：{plan.strip()[:300]}")
    # 待回收伏笔与钩子：注入台账，让 AI 续写时主动推进/回收
    fs_items = []
    try:
        for it in novel.load_foreshadows():
            if it.get("status") != "已回收":
                fs_items.append(it)
    except Exception:
        pass
    if fs_items:
        parts.append("")
        parts.append("【待回收伏笔/悬念台账】（以下伏笔尚未回收，续写新章时请优先承接、推进至少 1-2 条，"
                     "按情节需要在合适节点回收；每章结尾仍要留下清晰新钩子）")
        for it in fs_items[:15]:
            parts.append(f"· 埋设于第{it.get('chapter', '?')}章：{it.get('text', '')[:120]}")
    if chapters:
        parts.append("")
        parts.append("【最近章节全文】（保持文风与细节连贯）")
        for c in chapters[-recent_full:]:
            full = novel.load_chapter_content(c["path"])
            full = re.sub(r'(?m)^\s*#.*$', '', full, count=1).strip()
            if len(full) > max_chars:
                full = full[:max_chars] + "\n……（过长截断）"
            parts.append(f"===== 第{c['num']}章《{c['title']}》 =====")
            parts.append(full)
    return "\n\n".join(parts)


# ===================== 小说库管理 =====================
def list_novels(novels_dir=None):
    novels_dir = novels_dir or NOVELS_DIR
    result = []
    if not os.path.isdir(novels_dir):
        return result
    for name in sorted(os.listdir(novels_dir)):
        d = os.path.join(novels_dir, name)
        if os.path.isdir(d) and os.path.exists(os.path.join(d, "meta.yaml")):
            try:
                result.append(NovelProject(d))
            except Exception as e:
                LOG.error(f"加载小说失败 {d}: {e}")
    return result


def ensure_demo_novel():
    """首次运行时若无任何小说，创建一部示例小说（废土修仙·元素纪元），展示全章节记忆。"""
    if list_novels():
        return None
    root = os.path.join(NOVELS_DIR, "废土修仙-元素纪元")
    np_ = NovelProject.create(
        root, "废土修仙：元素纪元", genre="废土 · 科学修仙",
        synopsis_one="在核废土世界，少年凭一张元素周期表罗盘踏上以元素筑基的修仙之路。",
        synopsis=(
            "公元 2087 年，核战争将旧世界焚为焦土，大地被辐射覆盖，人类退守废土聚居地。"
            "自然灵气早已枯竭，取而代之的是“元素之力”——每个人体内都封印着对应化学元素的印记。"
            "少年沈青山在苦水湖捡到一块锈蚀的元素罗盘，罗盘指向湖底的“铀母”遗迹，"
            "由此揭开以元素周期表为根基的修炼体系：观想元素、激活印记、筑基炼气、凝聚道基……"
            "他必须在辐射兽潮、掠夺者与“元素教廷”的夹缝中成长，查明罗盘与自己身世的真相。"),
        world=(
            "- 时代背景：2087 年核战后废土，辐射尘笼罩，旧文明崩塌。\n"
            "- 修炼体系：以化学元素周期表构建“元素修仙”——吸收对应元素精粹，点燃“元素之火”，"
            "逐级修炼（观想→激活→筑基→炼气→结丹）。\n"
            "- 关键区域：苦水湖（辐射毒湖，湖底藏铀母遗迹）、铁锈平原、废都残城。\n"
            "- 危险生物：辐射兽（铁锈蜥蜴、辐斑狼）、污染体。\n"
            "- 势力：元素教廷（垄断元素修法）、拾荒者联盟、防辐射机甲佣兵团。"),
        characters=(
            "- 沈青山（主角）：苦水湖拾荒少年，坚韧寡言，身世成谜；觉醒“氢”元素之火，掌控第一主族之力。\n"
            "- 元素罗盘（关键道具）：锈蚀罗盘，可感应元素精粹，指向铀母遗迹。\n"
            "- 苏晚晴（女主）：拾荒者联盟的医师学徒，精通辐射药理，与沈青山相识于苦水湖。\n"
            "- 铁叔（导师）：退役防辐射机甲驾驶员，教沈青山在废土生存。"),
        plot_points=(
            "- 伏笔1：苦水湖底的“铀母”与沈青山身世相关。\n"
            "- 伏笔2：罗盘共有七格刻度，现只点亮一格。\n"
            "- 伏笔3：元素教廷在暗中收集“周期表碎片”。\n"
            "- 大纲：第1章 苦水湖的少年（觉醒）→ 第2章 元素之火（激活氢）→ 第3章 铁锈平原试炼（首战）→ "
            "后续：探湖底铀母、卷入教廷与拾荒者冲突、身世之谜。"),
    )
    # 写入前三章内容
    ch1 = ("苦水湖的水是锈红色的。\n\n沈青山蹲在龟裂的湖岸，把一块巴掌大的罗盘翻来覆去地看。"
           "罗盘上的指针早已锈死，可就在刚才，它在他手心轻轻转了一下，指向湖心的方向。\n\n"
           "湖水深处，有什么东西在呼吸。")
    ch2 = ("夜里，沈青山盯着罗盘上那圈褪色的符号，忽然看见第一格刻度亮起幽蓝的光。"
           "一股冰凉的气流从地底涌上，顺着手臂钻进他的经脉。\n\n"
           "他体内的印记苏醒了——那是周期表第一格的“氢”。元素之火在他指尖腾起，"
           "没有温度，却烧得空气都微微扭曲。")
    ch3 = ("铁锈平原上，一头铁锈蜥蜴拦住了回村的去路。它鳞甲如铁，尾尖拖出两道焦痕。\n\n"
           "沈青山握紧罗盘，指尖的氢火在风里忽明忽暗。铁叔说过，元素之力不怕硬，只怕巧。\n\n"
           "他一跃而起，火流贴着鳞甲缝隙钻入，蜥蜴发出金属般的哀鸣。")
    for num, (title, content) in enumerate([
        ("苦水湖的少年", ch1), ("元素之火", ch2), ("铁锈平原的试炼", ch3)], start=1):
        np_.save_chapter(num, title, content, record=False)
    # 生成章节记忆（无API时用启发式）
    for c in np_.refresh_chapters():
        content = np_.load_chapter_content(c["path"])
        np_.set_summary(c["num"], gen_chapter_summary(c["title"], content))
    np_.save_memory()
    np_.refresh_chapters()
    # 清理生成占位章时留下的备份
    if os.path.isdir(np_.backups_dir):
        try:
            shutil.rmtree(np_.backups_dir)
        except Exception:
            pass
    return np_


# ===================== AI 创作线程 =====================
MODE_LABELS = {
    "write_next": "续写下一章",
    "write_current": "创作/重写本章",
    "polish": "润色改写",
    "outline": "生成大纲",
    "qa": "剧情问答",
    "characters": "人物设定",
    "world": "世界观设定",
    "trope_judge": "套路判定",
}

MODE_DESCS = {
    "write_next": "你现在要创作【下一章】。必须承接上一章结尾的悬念/钩子，场景与情绪无缝衔接，"
                  "剧情自然推进；本章结尾要彰显/升级上一章埋下的钩子，并留下新的钩子。",
    "write_current": "你现在要【创作/重写当前这一章】。根据用户要求与全章节记忆，输出本章完整正文；"
                     "结尾彰显已埋伏笔或埋下新钩子。",
    "polish": "你现在要【润色改写】一段文字，保持原意与剧情，提升文学性与画面感；不得缩短正文。",
    "outline": "你现在要【规划剧情大纲】。基于全章节记忆，输出后续分章大纲（含关键事件、冲突、伏笔与钩子的安排与回收）。",
    "qa": "你现在要【回答关于本书剧情的问题】。严格依据全章节记忆作答，不得编造。",
    "characters": "你现在要【整理/补充人物设定】。基于已写剧情输出人物卡（Markdown），包含性格、动机、关系与成长弧。",
    "world": "你现在要【整理/补充世界观设定】。基于已写剧情输出世界观说明（Markdown）。",
    "trope_judge": "你现在要【判定本书的套路结构】。基于全章节记忆输出套路与吸引力诊断报告。",
    "diverge": "你现在要【发散剧情灵感】。基于全章节记忆与当前剧情，脑洞式地给出多个差异化走向，"
               "越反套路、越有记忆点越好，为创作提供可选方向。",
    "logic_check": "你现在要【做章节逻辑体检】。检查正文的因果、时间线、动机、能力边界与设定自洽，"
                   "只报逻辑问题与修复方向，不改写正文。",
    "logic_fix": "你现在要【按逻辑问题清单重写当前章节】。严格修正逻辑硬伤，保持原有文风与剧情走向，"
                 "结尾保留并彰显钩子。",
    "stuck_help": "你现在要【帮作者破局】。作者写到某个点卡住了，基于全章节记忆与当前卡点，"
                  "给出 3 个可立即落笔的破局方向（每个要具体到本场的动作/冲突/新变量）。",
}


class NovelAgentThread(QThread):
    chunk_signal = pyqtSignal(str)
    done_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)
    state_signal = pyqtSignal(str)
    consistency_signal = pyqtSignal(str)   # 一致性体检报告（发现冲突时发出）

    WRITE_MODES = ("write_next", "write_current", "polish", "fix_conflict", "logic_fix")

    def __init__(self, novel, mode, instruction="", current_text="", parent=None, trope=""):
        super().__init__(parent)
        self.novel = novel
        self.mode = mode
        self.instruction = (instruction or "").strip()
        self.current_text = current_text or ""
        self.trope = (trope or "").strip()

    def run(self):
        try:
            # 套路判定：不依赖 API，本地启发式 + LLM（有 Key 时）
            if self.mode == "trope_judge":
                self.state_signal.emit("正在分析套路结构与吸引力短板…")
                self.done_signal.emit(_judge_tropes(self.novel))
                return
            if not APP_CONFIG.get("api_key"):
                self.error_signal.emit("尚未配置 API Key，请在「设置」中填写后再使用 AI 创作。")
                return
            self.state_signal.emit("正在装载全章节记忆…")
            context = build_creation_context(self.novel)
            min_words = int(APP_CONFIG.get("novel_min_words", 2000) or 0)
            mode_desc = MODE_DESCS.get(self.mode, MODE_DESCS["write_next"])
            if self.mode in self.WRITE_MODES:
                wc = f"；本章正文不得少于 {min_words} 字" if min_words else ""
                mode_desc += wc
            sys_msg = (
                f"你是《{self.novel.meta.get('title', self.novel.name)}》的资深签约网文作家，"
                f"拥有『全章节记忆系统』，已记住本小说已写的全部章节、人物、世界观与伏笔。\n"
                f"{mode_desc}\n"
                "【创作铁律】\n"
                "1. 严格遵循下方记忆库，禁止与任何已写章节、人物设定、世界观冲突；"
                "前文已发生的剧情、已出场人物、已埋伏笔都不得推翻或遗忘。\n"
                "2. 保持文风、人物性格、叙事节奏、时间线与场景一致。\n"
                "3. 尊重并推进既有伏笔与钩子；续写时先回应上一章结尾的钩子，"
                "本章结尾必须留下清晰的新钩子或升级悬念（钩子要具体、可感知，不能含糊带过）。\n"
                "4. 直接输出可用的正文/内容，不要解释创作思路，不要寒暄。\n"
                "5. 全程使用中文。\n"
            )
            if self.mode in self.WRITE_MODES:
                sys_msg += "\n" + ANTI_AI_STYLE
                sys_msg += "\n\n" + LOGIC_RULES
                sys_msg += "\n\n" + DIVERGE_RULES
                tk = self.trope
                if tk and tk in TROPES:
                    t = TROPES[tk]
                    sys_msg += ("\n\n【套路引擎·" + t["name"] + "】（用于增强吸引力；"
                                "须与本书世界观和剧情自然融合，不生硬套用）\n" + t["block"])
            sys_msg += "\n\n【全章节记忆】\n" + context
            # —— 参考相似文注入 ——
            if self.mode in self.WRITE_MODES:
                # 本地参考文库（风格参考，仅借鉴写法不抄内容）
                if APP_CONFIG.get("ref_lib_enable"):
                    q = (context[:1500] + " " + self.instruction)[:2000]
                    try:
                        refs = ref_snippets(q, top=3)
                    except Exception:
                        refs = []
                    if refs:
                        blk = ("\n\n【参考文风片段】（来自本地参考文库。仅借鉴其叙事节奏、描写方式、对话腔调与文笔风格，"
                               "严禁复制其情节、人物、设定、台词或原句）\n")
                        for title, txt, score in refs:
                            blk += f"--- {title}（相关度 {score}）---\n{txt[:300]}\n"
                        sys_msg += blk
                # 联网参考（用户通过「联网参考」搜索并启用）
                if _PENDING_WEB_REFS:
                    blk = ("\n\n【联网参考片段】（用户检索到的相关资料。可作为剧情走向/设定/知识参考，"
                           "允许借鉴事实与灵感，但须化为本书自己的表述，严禁整段照搬）\n")
                    for r in _PENDING_WEB_REFS:
                        blk += f"--- {r.get('title') or '资料'} ---\n{r.get('snippet') or ''}\n"
                    sys_msg += blk
            messages = [{"role": "system", "content": sys_msg}]
            if self.mode == "qa":
                messages.append({"role": "user", "content": f"【问题】{self.instruction or '请介绍本书目前的剧情进展。'}"})
            elif self.mode == "polish":
                messages.append({"role": "user", "content":
                    f"请润色改写下面这段文字，直接输出改写后的正文（不得少于 {min_words} 字）：\n\n"
                    f"{self.current_text[:12000]}\n\n"
                    f"润色要求：{self.instruction or '提升文笔与画面感，保持剧情不变'}"})
            elif self.mode == "write_current":
                messages.append({"role": "user", "content":
                    f"【用户要求】{self.instruction or '创作本章'}\n\n"
                    f"【当前章节已有内容】\n{self.current_text[:12000]}\n\n"
                    f"请直接输出本章完整正文（不得少于 {min_words} 字），结尾彰显钩子。"})
            elif self.mode == "fix_conflict":
                messages.append({"role": "user", "content":
                    f"【一致性冲突清单】\n{self.instruction or '（无）'}\n\n"
                    f"【待修正正文】\n{self.current_text[:12000]}\n\n"
                    "请严格依据冲突清单修正正文中的矛盾（人物关系、能力、伏笔、时间线等），"
                    f"输出【修正后的完整章节正文】（不得少于 {min_words} 字，保持原有文风与剧情走向），"
                    "结尾保留并彰显钩子。不要输出解释。"})
            elif self.mode == "logic_fix":
                messages.append({"role": "user", "content":
                    f"【逻辑问题清单】\n{self.instruction or '（无）'}\n\n"
                    f"【待修正正文】\n{self.current_text[:12000]}\n\n"
                    "请严格依据清单修正正文中的逻辑硬伤（因果、时间线、动机、能力边界、设定自洽），"
                    f"输出【修正后的完整章节正文】（不得少于 {min_words} 字，保持原有文风与剧情走向），"
                    "结尾保留并彰显钩子。不要输出解释。"})
            elif self.mode == "stuck_help":
                messages.append({"role": "user", "content":
                    f"【作者卡在哪里】{self.instruction or '作者没说具体卡点，请基于剧情找最可能的卡点'}\n\n"
                    f"【当前章节未完成正文】\n{self.current_text[:8000]}\n\n"
                    "请给出 3 个可立即落笔的破局方向，每个方向按以下格式（不要解释）：\n"
                    "## 破局N · 一句话方向名\n"
                    "- 立刻发生：本场马上要写的一个具体动作/冲突/对话\n"
                    "- 新变量：引入一个意外因素让局面转向\n"
                    "- 下一句怎么写：直接给一句可接在卡点后面的正文开头\n"
                    "三个方向要风格各异（一个推进主线、一个制造突发、一个抖包袱/埋新钩子）。"})
            elif self.mode == "diverge":
                messages.append({"role": "user", "content":
                    f"【用户补充方向】{self.instruction or '无，自由发挥'}\n\n"
                    "请基于全章节记忆发散性地给出 5 个下一章的差异化走向。\n"
                    "每个走向按以下格式（不要代码块、不要解释）：\n"
                    "## 走向N · 走向名\n"
                    "- 核心事件：一句话说清这一章发生什么（要反套路/有意外变量）\n"
                    "- 新脑洞：可引入的新设定/道具/规则（须与世界观自洽）\n"
                    "- 冲突与代价：主角会付出什么/遇到什么麻烦\n"
                    "- 结尾钩子：这一章会留下什么悬念\n"
                    "五个走向尽量拉开差异：一个稳扎稳打、一个极端脑洞、一个反转、一个多线、一个悬疑。"})
            elif self.mode == "logic_check":
                messages.append({"role": "user", "content":
                    f"【用户补充关注点】{self.instruction or '全面检查'}\n\n"
                    f"【待检查正文】\n{self.current_text[:12000]}\n\n"
                    "请从以下维度检查逻辑硬伤：因果闭环、时间线、人物动机、能力边界、设定自洽、"
                    "信息有据。\n输出格式（不要解释）：\n"
                    "【逻辑问题N】\n- 位置：摘录原文 1 句\n- 问题：一句话说清硬伤\n- 修复：给出一句修复方向\n"
                    "若没有发现硬伤，直接输出【逻辑问题0】当前正文逻辑自洽。"
                    "（注：此模式只诊断不改写）"})
            elif self.mode == "outline":
                messages.append({"role": "user", "content":
                    f"请为《{self.novel.meta.get('title', self.novel.name)}》生成一份完整的小说大纲。\n\n"
                    "【要求】\n"
                    "1. 结合下方【全章节记忆】中已有的章节、人物、世界观与伏笔：先给出全书分卷规划（2-5 卷），"
                    "再逐章给出：章节号、标题、内容梗概（30-60 字）、结尾钩子；已写章节按实际剧情写，"
                    "未写章节按合理走向规划。\n"
                    "2. 必须直接输出一个 YAML 列表，不要任何解释、不要用代码块包裹，结构如下：\n"
                    "- title: 卷名\n"
                    "  desc: 卷概要\n"
                    "  chapters:\n"
                    "    - num: 1\n"
                    "      title: 章标题\n"
                    "      outline: 内容梗概\n"
                    "      hook: 结尾钩子\n"
                    f"【用户补充要求】{self.instruction or '按常规网文节奏规划'}"})
            elif self.mode == "characters":
                messages.append({"role": "user", "content":
                    f"【用户要求】{self.instruction or '整理人物设定'}\n\n"
                    "请基于下方【全章节记忆】中已写的剧情与人物，输出一部结构化【人物卡 YAML 列表】。\n"
                    "每条记录字段如下（不要代码块、不要任何解释，直接输出 YAML）：\n"
                    "- name: 姓名\n  role: 定位（主角/女主/反派/导师/配角…）\n"
                    "  identity: 身份/背景\n  relations: 人物关系（与他人/势力的关联）\n"
                    "  arc: 成长弧/目标/性格\n  notes: 备注/当前状态\n"
                    "已出场且重要的角色都要覆盖；已有角色保持原有设定，只做补充完善。"})
            elif self.mode == "world":
                messages.append({"role": "user", "content":
                    f"【用户要求】{self.instruction or '整理世界观'}\n\n请基于已写剧情输出世界观说明（Markdown）。"})
            else:  # write_next
                last = self.novel.chapters[-1] if self.novel.chapters else None
                last_hook = ""
                if last:
                    summ = (last.get("summary") or "").strip()
                    # 从上一章摘要中提取"伏笔与悬念"部分
                    m = re.search(r'###\s*伏笔与悬念\s*\n(.*?)(?=\n###|\Z)', summ, re.S)
                    if m and m.group(1).strip() not in ("无", ""):
                        last_hook = m.group(1).strip()
                hook_line = ""
                if last_hook:
                    hook_line = (f"【上一章结尾钩子】{last_hook}\n"
                                 "务必先承接以上钩子，再展开新剧情；本章结尾继续彰显并升级它。\n")
                messages.append({"role": "user", "content":
                    f"{hook_line}【用户对下一章的要求】{self.instruction or '继续创作下一章'}\n\n"
                    f"请直接输出下一章完整正文（不得少于 {min_words} 字），结尾留下新的钩子。"})

            if self.mode == "diverge":
                temperature = 0.9
            elif self.mode in ("logic_check", "stuck_help"):
                temperature = 0.3
            elif self.mode in ("write_next", "write_current"):
                temperature = 0.85
            else:
                temperature = 0.5

            def do_call(msgs):
                buf = []
                def _c(t):
                    buf.append(t)
                    self.chunk_signal.emit(t)
                res = call_llm_stream(msgs, tools=None,
                                      timeout=APP_CONFIG.get("timeout", 180),
                                      temperature=temperature, on_chunk=_c)
                return (res.get("content") or "").strip()

            # 单次生成字数下限：用户要求 AI 单次至少 2000 字
            target = max(int(APP_CONFIG.get("novel_min_words", 2000) or 0), 2000)
            produce_chapter = self.mode in self.WRITE_MODES

            text = do_call(messages)
            if not text:
                self.error_signal.emit("AI 未返回内容，请重试")
                return
            if self.mode == "characters":
                items = parse_characters_yaml(text)
                if items:
                    existing = self.novel.load_characters()
                    by_name = {}
                    for c in existing:
                        if c.get("name"):
                            by_name[c["name"].strip()] = c
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        name = (it.get("name") or "").strip()
                        if not name:
                            continue
                        if name in by_name:
                            for k in ("role", "identity", "relations", "arc", "notes"):
                                if it.get(k):
                                    by_name[name][k] = str(it[k]).strip()
                        else:
                            card = {"name": name, "role": "", "identity": "", "relations": "",
                                    "arc": "", "notes": ""}
                            for k in card:
                                if it.get(k):
                                    card[k] = str(it[k]).strip()
                            existing.append(card)
                            by_name[name] = card
                    self.novel._characters = existing
                    self.novel.save_characters()
                    self.done_signal.emit(
                        f"已生成 {len(items)} 张人物卡并合并保存（共 {len(existing)} 张）。\n\n"
                        f"请切到左侧「人物」标签查看与编辑。\n\n" + text)
                else:
                    self.done_signal.emit("（AI 返回的内容未能解析为人物卡 YAML，未保存。可重试）\n\n" + text)
                return
            if self.mode == "outline":
                items = parse_outline_yaml(text)
                if items:
                    self.novel.save_outline(items)
                    nvol = len(items)
                    nch = sum(len(v.get("chapters", []) or []) for v in items)
                    self.done_signal.emit(
                        f"已生成完整大纲并保存：{nvol} 卷 / {nch} 章\n\n请切到左侧「大纲」标签查看与修改。\n\n" + text)
                else:
                    self.done_signal.emit("（AI 返回的内容未能解析为大纲 YAML，未保存。可重试或在要求中写得更具体）\n\n" + text)
                return
            if produce_chapter and count_words(text) < target:
                # 自动续写拼接，直到达到字数下限
                round_no = 0
                while count_words(text) < target and round_no < 4:
                    round_no += 1
                    self.state_signal.emit(f"已生成 {count_words(text)} 字，自动续写至 {target} 字…")
                    cont = [{"role": "system", "content": sys_msg},
                            {"role": "user", "content":
                                f"请继续续写/扩写下面的章节正文，使全文达到至少 {target} 字。\n"
                                f"直接从【接续处】继续写：承接上文末尾的场景、人物与悬念推进剧情，"
                                f"不要重复已有内容，不要总结，不要输出任何解释，直接输出接续正文。\n\n"
                                f"【已生成正文】\n{text}"}]
                    more = do_call(cont)
                    if not more or more.strip() in ("无", "完", "结束"):
                        break
                    text = (text.rstrip() + "\n\n" + more.strip())
                if count_words(text) < target:
                    self.state_signal.emit(f"已尽力续写，当前约 {count_words(text)} 字（目标 {target} 字），可再次生成补充")
            # 一致性体检：写作类模式生成后自动比对全书记忆（多一次 LLM 调用，稍慢）
            if produce_chapter and self.mode not in ("fix_conflict", "logic_fix"):
                self.state_signal.emit("正在做一致性体检…")
                try:
                    rep = check_consistency(self.novel, text)
                    if rep:
                        self.consistency_signal.emit(rep)
                except Exception:
                    pass
            self.done_signal.emit(text)
        except Exception as e:
            self.error_signal.emit(f"AI 创作失败：{e}")


# ===================== 记忆更新线程 =====================
class NovelMemoryThread(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(bool, str)

    def __init__(self, novel, mode="all", chapter_num=None, parent=None):
        super().__init__(parent)
        self.novel = novel
        self.mode = mode          # all / one
        self.chapter_num = chapter_num

    def run(self):
        try:
            chapters = self.novel.refresh_chapters()
            if self.mode == "one":
                targets = [c for c in chapters if c["num"] == self.chapter_num]
            else:
                targets = chapters
            if not targets:
                self.finished_ok.emit(False, "没有可更新的章节")
                return
            for c in targets:
                content = self.novel.load_chapter_content(c["path"])
                summ = gen_chapter_summary(c["title"], content)
                self.novel.set_summary(c["num"], summ)
                self.progress.emit(f"第{c['num']}章《{c['title']}》记忆已生成")
            self.novel.save_memory()
            self.novel.refresh_chapters()
            self.finished_ok.emit(True, f"记忆已更新（{len(targets)} 章）")
        except Exception as e:
            LOG.error(f"记忆更新失败: {e}")
            self.finished_ok.emit(False, f"记忆更新失败：{e}")


# ===================== 大纲批量生成线程 =====================
class NovelBatchGenThread(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(bool, str)

    def __init__(self, novel, chapters, trope="", instruction="", parent=None):
        super().__init__(parent)
        self.novel = novel
        self.chapters = chapters          # [(num, title, outline, hook)]
        self.trope = (trope or "").strip()
        self.instruction = (instruction or "").strip()

    def run(self):
        try:
            if not self.chapters:
                self.finished_ok.emit(False, "没有可批量生成的章节")
                return
            if not APP_CONFIG.get("api_key"):
                self.finished_ok.emit(False, "尚未配置 API Key，请在「设置」中填写后再批量生成。")
                return
            target = max(int(APP_CONFIG.get("novel_min_words", 2000) or 0), 2000)
            total = len(self.chapters)
            ok_count = 0
            for idx, (num, title, outline, hook) in enumerate(self.chapters, 1):
                self.progress.emit(f"正在生成第{num}章《{title}》（{idx}/{total}）…")
                context = build_creation_context(self.novel)
                trope_blk = ""
                if self.trope in TROPES:
                    t = TROPES[self.trope]
                    trope_blk = ("\n\n【套路引擎·" + t["name"] + "】（用于增强吸引力，"
                                 "须与本书世界观自然融合，不生硬套用）\n" + t["block"])
                sys_msg = (
                    f"你是《{self.novel.meta.get('title', self.novel.name)}》的资深签约网文作家，"
                    f"拥有『全章节记忆系统』。\n"
                    "【创作铁律】\n"
                    "1. 严格遵循下方记忆库，禁止与已写章节、人物、世界观、伏笔冲突；"
                    "2. 保持文风、人物性格、叙事节奏与时间线一致；"
                    "3. 尊重并推进待回收伏笔，本章结尾必须留下清晰、具体的新钩子；"
                    "4. 直接输出可用的完整正文，不要解释、不要寒暄；5. 全程中文。\n"
                    + ANTI_AI_STYLE + trope_blk + "\n\n【全章节记忆】\n" + context)
                prompt = (f"请按大纲创作第{num}章《{title}》。\n"
                          f"【本章梗概】{outline or '（无，请按整体走向自然推进）'}\n"
                          f"【本章结尾钩子】{hook or '（无，请自行设计一个具体、可感知的新钩子）'}\n"
                          f"{('【用户补充要求】' + self.instruction) if self.instruction else ''}\n\n"
                          f"请直接输出本章完整正文（不得少于 {target} 字），结尾彰显钩子。")
                text = _llm_generate(prompt, system=sys_msg, temperature=0.85, timeout=300, max_chars=16000)
                if not text:
                    self.progress.emit(f"第{num}章生成失败（AI 未返回内容），跳过")
                    continue
                # 续写至目标字数（最多 3 轮）
                rnd = 0
                while count_words(text) < target and rnd < 3:
                    rnd += 1
                    more = _llm_generate(
                        f"请继续续写/扩写下面的章节正文，使全文达到至少 {target} 字。\n"
                        f"直接从接续处继续写，承接上文场景、人物与悬念推进剧情，不重复、不总结、直接输出接续正文。\n\n"
                        f"【已生成正文】\n{text}",
                        system=sys_msg, temperature=0.85, timeout=300, max_chars=16000)
                    if not more or more.strip() in ("无", "完", "结束"):
                        break
                    text = text.rstrip() + "\n\n" + more.strip()
                # 一键去AI味
                cleaned = de_ai_text(text)
                if count_words(cleaned) < 200:
                    cleaned = text
                # 创建/覆盖真实章节（create 存在则跳过，save 统一写盘）
                self.novel.create_chapter_at(num, title)
                self.novel.save_chapter(num, title, cleaned.strip(), record=True)
                # 本地提取该章记忆摘要
                content = self.novel.load_chapter_content(self.novel.chapter_path(num))
                self.novel.set_summary(num, gen_chapter_summary(title, content))
                self.novel.save_memory()
                ok_count += 1
                self.progress.emit(f"第{num}章《{title}》完成：约 {count_words(cleaned)} 字（{idx}/{total}）")
            self.finished_ok.emit(True, f"批量生成完成：成功 {ok_count}/{total} 章")
        except Exception as e:
            LOG.error(f"批量生成失败: {e}")
            self.finished_ok.emit(False, f"批量生成失败：{e}")


# ===================== 参考文库索引线程 =====================
class RefIndexThread(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(bool, str)

    def __init__(self, lib_dir, parent=None):
        super().__init__(parent)
        self.lib_dir = lib_dir

    def run(self):
        try:
            self.progress.emit("正在扫描参考文库并建立索引…")
            _idx, files, nchunks = build_ref_index(self.lib_dir)
            self.finished_ok.emit(True, f"索引完成：{_REF_INDEX_STATS}。\n生成章节时会自动检索并参考其文风（仅借鉴写法，不抄内容）。")
        except Exception as e:
            LOG.error(f"建索引失败: {e}")
            self.finished_ok.emit(False, f"建索引失败：{e}")


# ===================== 批量章节处理线程 =====================
class NovelBatchPolishThread(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(bool, str)

    def __init__(self, novel, do_deai=False, do_polish=False, update_memory=False, parent=None):
        super().__init__(parent)
        self.novel = novel
        self.do_deai = do_deai
        self.do_polish = do_polish
        self.update_memory = update_memory

    def run(self):
        try:
            if self.do_polish and not APP_CONFIG.get("api_key"):
                self.finished_ok.emit(False, "AI 润色需要先在「设置」配置 API Key；可仅用去AI味。")
                return
            chapters = self.novel.refresh_chapters()
            changed = 0
            for c in chapters:
                body = self.novel.load_chapter_content(c["path"])
                title = c["title"]
                lines = body.split("\n")
                if lines and lines[0].strip().startswith("#"):
                    head_line = lines[0].rstrip()
                    content = "\n".join(lines[1:]).strip()
                else:
                    head_line = ""
                    content = body.strip()
                orig = content
                if self.do_deai:
                    content = de_ai_text(content)
                if self.do_polish and content:
                    prompt = ("请润色改写下面这段小说正文：保持原意、人物、剧情与文风，"
                              "提升画面感与文学性，不得缩短正文。直接输出润色后的正文，不要解释。\n\n"
                              + content[:12000])
                    res = call_llm_stream(
                        [{"role": "system", "content": "你是一位风格鲜明的中文小说家。"},
                         {"role": "user", "content": prompt}],
                        tools=None, timeout=APP_CONFIG.get("timeout", 180), temperature=0.6)
                    new = (res.get("content") or "").strip()
                    if new:
                        content = new
                if content != orig:
                    new_body = (head_line + "\n\n" + content) if head_line else content
                    self.novel.save_chapter(c["num"], title, new_body, record=False)
                    if self.update_memory:
                        summ = gen_chapter_summary(title, content)
                        self.novel.set_summary(c["num"], summ)
                        self.novel.save_memory()
                    changed += 1
                    self.progress.emit(f"第{c['num']}章《{title}》已处理（{len(content)} 字）")
            self.novel.refresh_chapters()
            self.finished_ok.emit(True, f"批量处理完成：共处理 {changed}/{len(chapters)} 章")
        except Exception as e:
            LOG.error(f"批量处理失败: {e}")
            self.finished_ok.emit(False, f"批量处理失败：{e}")


# ===================== 界面样式 =====================
FROST_QSS = """
QMainWindow, QWidget#root { background: #1e1f24; color: #d5d7dd; }
QWidget { color: #d5d7dd; font-size: 13px; font-family: "Microsoft YaHei UI","Microsoft YaHei","PingFang SC","Segoe UI"; }
QLabel { background: transparent; color: #d5d7dd; }
QLabel#dim { color: #9aa0ab; font-size: 12px; }
QToolTip { background: #2b2d33; color: #e6e8ee; border: 1px solid #3d414a; padding: 4px 8px; border-radius: 5px; font-size: 12px; }

/* ---- 统一输入控件 ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser, QSpinBox, QComboBox {
    background: #26282e; border: 1px solid #383b43; border-radius: 8px;
    padding: 6px 8px; color: #e6e8ee; selection-background-color: #3a5f9e;
    selection-color: #ffffff;
}
QLineEdit, QSpinBox { min-height: 26px; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
QComboBox:focus { border: 1px solid #5c8ad4; }
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled { color: #666b75; background: #232429; }

/* ---- 统一按钮 ---- */
QPushButton {
    background: #2f323a; border: 1px solid #3d414a; border-radius: 7px;
    padding: 4px 14px; min-height: 28px; color: #d5d7dd; font-size: 13px;
}
QPushButton:hover { background: #3a3e47; border-color: #5c8ad4; color: #ffffff; }
QPushButton:pressed { background: #2a2d34; }
QPushButton:disabled { color: #666b75; background: #26282e; border-color: #33363e; }
QPushButton:checked { background: #3a5f9e; border-color: #5c8ad4; color: #ffffff; }
QPushButton#primary { background: #3a5f9e; border-color: #4a73b8; color: #ffffff; font-weight: 600; }
QPushButton#primary:hover { background: #4a73b8; border-color: #5c8ad4; }
QPushButton#danger { background: #7a3a3a; border-color: #a05050; color: #ffd7d7; }
QPushButton#danger:hover { background: #964a4a; }
/* 紧凑小按钮（章节操作区） */
QPushButton#mini { padding: 4px 6px; font-size: 12px; min-height: 24px; }
QPushButton#mini_danger { padding: 4px 6px; font-size: 12px; min-height: 24px; background: #7a3a3a; border-color: #a05050; color: #ffd7d7; }
QPushButton#mini_danger:hover { background: #964a4a; }
QPushButton#mini_primary { padding: 4px 6px; font-size: 12px; min-height: 24px; background: #3a5f9e; border-color: #4a73b8; color: #ffffff; }
QPushButton#mini_primary:hover { background: #4a73b8; }

/* ---- 标签页（章节/大纲） ---- */
QTabWidget::pane { border: 1px solid #33363e; border-radius: 8px; background: #232429; top: -1px; }
QTabBar::tab { background: #2f323a; color: #9aa0ab; padding: 5px 16px; border: 1px solid #3d414a;
    border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }
QTabBar::tab:selected { background: #232429; color: #ffffff; }
QTabBar::tab:hover:!selected { color: #d5d7dd; }

/* ---- 大纲树 ---- */
QTreeWidget { background: #232429; border: 1px solid #33363e; border-radius: 8px; padding: 4px; outline: none; }
QTreeWidget::item { padding: 5px 6px; color: #d5d7dd; border-radius: 4px; }
QTreeWidget::item:selected { background: #3a5f9e; color: #ffffff; }
QTreeWidget::item:hover:!selected { background: #2f323a; }
QTreeWidget::branch { background: transparent; }

/* ---- 下拉框 ---- */
QComboBox { min-height: 26px; padding: 4px 10px; }
QComboBox:hover { border-color: #5c8ad4; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid #9aa0ab; margin-right: 6px; }
QComboBox QAbstractItemView {
    background: #2b2d33; border: 1px solid #3d414a; border-radius: 6px;
    selection-background-color: #3a5f9e; selection-color: #ffffff; color: #d5d7dd;
    padding: 4px; outline: none;
}
QComboBox QAbstractItemView::item { padding: 6px 10px; border-radius: 4px; }

/* ---- 章节列表 ---- */
QListWidget {
    background: #232429; border: 1px solid #33363e; border-radius: 8px;
    padding: 4px; outline: none;
}
QListWidget::item { padding: 8px 10px; border-radius: 6px; color: #c9ccd4; }
QListWidget::item:hover { background: #2f323a; }
QListWidget::item:selected { background: #3a5f9e; color: #ffffff; }

/* ---- 统一滚动条 ---- */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #3d414a; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #5c8ad4; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #3d414a; border-radius: 5px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: #5c8ad4; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---- 面板统一 ---- */
QFrame#card, QFrame#panel { background: #232429; border: 1px solid #33363e; border-radius: 10px; }

QToolButton {
    background: transparent; border: none; border-radius: 6px; padding: 4px 10px;
    color: #c8c8cc; font-size: 12px; min-height: 24px;
}
QToolButton:hover { background: rgba(120,130,150,45); color: #ffffff; }
QToolButton:checked { background: #3a5f9e; color: #ffffff; }
QCheckBox { color: #c9ccd4; spacing: 6px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px;
    border: 1.5px solid #555a64; background: #26282e; }
QCheckBox::indicator:hover { border-color: #5c8ad4; }
QCheckBox::indicator:checked { background: #3a5f9e; border-color: #5c8ad4; }

/* ---- 分割条 ---- */
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: #3a5f9e; }
QSplitter::handle:vertical { height: 2px; }
QSplitter::handle:horizontal { width: 2px; }

/* ---- 菜单 ---- */
QMenu { background: #2b2d33; border: 1px solid #3d414a; border-radius: 8px; padding: 5px; color: #d5d7dd; }
QMenu::item { padding: 7px 18px; border-radius: 5px; }
QMenu::item:selected { background: #3a5f9e; color: #ffffff; }
QMenu::separator { height: 1px; background: #33363e; margin: 4px 8px; }

/* ---- 顶部工具栏（与主体同色） ---- */
QToolBar { background: #1e1f24; border: none; border-bottom: 1px solid #2a2c33;
    spacing: 6px; padding: 6px 4px; }
QToolBar::separator { background: #33363e; width: 1px; margin: 4px 6px; }
QToolBar QLabel { color: #c9ccd4; }
QToolBar QPushButton { min-height: 24px; padding: 3px 10px; }

/* ---- 状态栏 ---- */
QStatusBar { background: #1a1b20; color: #9aa0ab; border-top: 1px solid #2a2c33; font-size: 12px; }
QStatusBar::item { border: none; }

QDialog { background: #1e1f24; }
"""


# ===================== 设置对话框 =====================
class SettingDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置 · AI 模型")
        self.resize(620, 540)
        self.cfg = APP_CONFIG.copy()
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(list(PROVIDER_PRESETS.keys()))
        cur_base = self.cfg.get("api_base", "").rstrip("/")
        cur_provider = self.cfg.get("provider", "")
        if cur_provider and cur_provider in PROVIDER_PRESETS:
            self.provider_combo.setCurrentText(cur_provider)
        else:
            matched = "自定义（OpenAI 兼容）"
            for name, preset in PROVIDER_PRESETS.items():
                if preset["api_base"] and cur_base == preset["api_base"].rstrip("/"):
                    matched = name
                    break
                if not preset["api_base"] and not cur_base:
                    matched = name
            self.provider_combo.setCurrentText(matched)
        self.provider_combo.currentTextChanged.connect(self._on_provider)

        self.mdl = QComboBox()
        self.mdl.setEditable(True)
        self.mdl.setCurrentText(self.cfg["ollama_model"])
        self.ab = QLineEdit(self.cfg["api_base"])
        self.ab.setPlaceholderText("如 https://api.deepseek.com/v1")
        self.ak = QLineEdit(self.cfg["api_key"])
        self.ak.setEchoMode(QLineEdit.EchoMode.Password)
        self.ak.setPlaceholderText("填写 API Key")
        self.px = QLineEdit(self.cfg["proxy"])
        self.px.setPlaceholderText("http://127.0.0.1:7890，无则留空")
        self.to = QSpinBox()
        self.to.setRange(10, 600)
        self.to.setValue(self.cfg["timeout"])

        form.addRow("提供商：", self.provider_combo)
        form.addRow("模型：", self.mdl)
        form.addRow("API 地址：", self.ab)
        form.addRow("API Key：", self.ak)
        form.addRow("网络代理：", self.px)
        form.addRow("超时(秒)：", self.to)

        test_w = QHBoxLayout()
        self.btn_test = QPushButton("测试连接")
        self.btn_test.clicked.connect(self._do_test)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        test_w.addWidget(self.btn_test)
        test_w.addWidget(self.test_result, 1)
        form.addRow("", test_w)

        self.rf = QSpinBox()
        self.rf.setRange(1, 8)
        self.rf.setValue(int(self.cfg.get("novel_recent_full", 3)))
        self.rf.setToolTip("AI 创作时注入的最近完整章节数（配合章节摘要构成全章节记忆）")
        form.addRow("注入最近N章全文：", self.rf)

        self.dtarget = QSpinBox()
        self.dtarget.setRange(0, 100000)
        self.dtarget.setSingleStep(500)
        self.dtarget.setValue(int(self.cfg.get("novel_daily_target", 0)))
        self.dtarget.setToolTip("每日目标字数，在工具栏实时显示进度；0 表示不启用")
        form.addRow("每日目标字数：", self.dtarget)

        self.minw = QSpinBox()
        self.minw.setRange(0, 100000)
        self.minw.setSingleStep(200)
        self.minw.setValue(int(self.cfg.get("novel_min_words", 2000)))
        self.minw.setToolTip("每章最少字数：保存时不足会提醒；AI 创作时会强制按此字数输出")
        form.addRow("每章最少字数：", self.minw)

        self.mem_ck = QCheckBox("保存章节后自动更新该章记忆摘要")
        self.mem_ck.setChecked(bool(self.cfg.get("novel_auto_memory", True)))
        form.addRow("", self.mem_ck)

        # —— 参考相似文 ——
        refrow = QHBoxLayout()
        self.ref_dir = QLineEdit(self.cfg.get("ref_lib_dir") or REF_LIB_DIR)
        self.ref_dir.setToolTip("把想模仿风格的作品（.txt/.md）放进该文件夹，点「参考文库」按钮建索引后，"
                                "AI 续写时会自动检索并参考其文风")
        refrow.addWidget(self.ref_dir, 1)
        b_ref_browse = QPushButton("浏览…")
        b_ref_browse.clicked.connect(
            lambda: self.ref_dir.setText(QFileDialog.getExistingDirectory(self, "选择参考文库文件夹", self.ref_dir.text())))
        refrow.addWidget(b_ref_browse)
        form.addRow("参考文库路径：", refrow)

        self.ref_ck = QCheckBox("生成章节时自动参考参考文库文风（仅借鉴写法，不抄内容）")
        self.ref_ck.setChecked(bool(self.cfg.get("ref_lib_enable", False)))
        form.addRow("", self.ref_ck)

        self.web_key = QLineEdit(self.cfg.get("web_search_key", ""))
        self.web_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.web_key.setPlaceholderText("选填：博查 BoCha API Key（https://open.bochaai.com 注册，有免费额度）")
        self.web_key.setToolTip("用于「联网参考」按钮检索网络相似文；不填则联网参考不可用")
        form.addRow("联网搜索 Key：", self.web_key)

        root.addLayout(form)

        hint = QLabel("提示：AI 创作与记忆更新都通过上方模型调用。\n"
                      "豆包/火山引擎需在火山控制台创建推理接入点，模型名填 ep-xxx。\n"
                      "未配置 Key 时，章节摘要会自动使用本地启发式提取（软件仍可正常写作）。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#9aa0ab;font-size:12px;")
        root.addWidget(hint)
        root.addStretch()

        bar = QHBoxLayout()
        bar.addStretch()
        bc = QPushButton("取消")
        bc.clicked.connect(self.reject)
        bs = QPushButton("保存设置")
        bs.setObjectName("primary")
        bs.clicked.connect(self._save)
        bar.addWidget(bc)
        bar.addWidget(bs)
        root.addLayout(bar)

        self._on_provider(self.provider_combo.currentText())

    def _on_provider(self, name):
        preset = PROVIDER_PRESETS.get(name, {})
        self.mdl.clear()
        models = preset.get("models", [])
        if models:
            self.mdl.addItems(models)
        cur_model = self.cfg.get("ollama_model", "")
        if cur_model and cur_model in models:
            self.mdl.setCurrentText(cur_model)
        elif models:
            self.mdl.setCurrentIndex(0)
        elif cur_model:
            self.mdl.setCurrentText(cur_model)
        preset_base = preset.get("api_base", "")
        if preset_base:
            self.ab.setText(preset_base)

    def _do_test(self):
        global APP_CONFIG
        saved = APP_CONFIG.copy()
        APP_CONFIG.update({
            "ollama_model": self.mdl.currentText().strip(),
            "api_base": self.ab.text().strip(),
            "api_key": self.ak.text().strip(),
            "proxy": self.px.text().strip(),
        })
        self.btn_test.setEnabled(False)
        self.test_result.setText("测试中…")
        QApplication.processEvents()
        ok, msg = test_llm_connection()
        APP_CONFIG = saved
        self.btn_test.setEnabled(True)
        self.test_result.setStyleSheet("color:#7ee787;" if ok else "color:#ff7b72;")
        self.test_result.setText(msg)

    def _save(self):
        global APP_CONFIG
        self.cfg.update({
            "provider": self.provider_combo.currentText(),
            "ollama_model": self.mdl.currentText().strip(),
            "api_base": self.ab.text().strip(),
            "api_key": self.ak.text().strip(),
            "proxy": self.px.text().strip(),
            "timeout": self.to.value(),
            "novel_recent_full": self.rf.value(),
            "novel_daily_target": self.dtarget.value(),
            "novel_min_words": self.minw.value(),
            "novel_auto_memory": self.mem_ck.isChecked(),
            "ref_lib_dir": self.ref_dir.text().strip(),
            "ref_lib_enable": self.ref_ck.isChecked(),
            "web_search_key": self.web_key.text().strip(),
        })
        save_config(self.cfg)
        APP_CONFIG = self.cfg
        QMessageBox.information(self, "提示", "设置已保存并立即生效。")
        self.accept()


# ===================== 小说设定对话框 =====================
class NovelMetaDialog(QDialog):
    def __init__(self, parent=None, novel=None):
        super().__init__(parent)
        self.novel = novel
        self.setWindowTitle("小说设定" if novel else "新建小说")
        self.resize(660, 620)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        form = QFormLayout(inner)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        m = novel.meta if novel else {}
        self.e_title = QLineEdit(m.get("title", ""))
        self.e_genre = QLineEdit(m.get("genre", ""))
        self.e_syn1 = QLineEdit(m.get("synopsis_one", ""))
        self.e_syn = QPlainTextEdit(m.get("synopsis", ""))
        self.e_syn.setPlaceholderText("完整梗概：故事主线、冲突、结局方向…")
        self.e_world = QPlainTextEdit(m.get("world", ""))
        self.e_world.setPlaceholderText("世界观设定：时代背景、力量体系、地图、势力、关键区域…")
        self.e_chars = QPlainTextEdit(m.get("characters", ""))
        self.e_chars.setPlaceholderText("人物设定：姓名、性格、动机、关系、成长弧…")
        self.e_plots = QPlainTextEdit(m.get("plot_points", ""))
        self.e_plots.setPlaceholderText("伏笔 · 大纲 · 关键物品，逐条列出…")

        for w in (self.e_syn, self.e_world, self.e_chars, self.e_plots):
            w.setFixedHeight(96)
        form.addRow("书名 *：", self.e_title)
        form.addRow("类型：", self.e_genre)
        form.addRow("一句话梗概：", self.e_syn1)
        form.addRow("完整梗概：", self.e_syn)
        form.addRow("世界观：", self.e_world)
        form.addRow("人物：", self.e_chars)
        form.addRow("伏笔/大纲：", self.e_plots)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        bar = QHBoxLayout()
        bar.addStretch()
        bc = QPushButton("取消")
        bc.clicked.connect(self.reject)
        bs = QPushButton("保存")
        bs.setObjectName("primary")
        bs.clicked.connect(self._ok)
        bar.addWidget(bc)
        bar.addWidget(bs)
        root.addLayout(bar)

    def _ok(self):
        if not self.e_title.text().strip():
            QMessageBox.warning(self, "提示", "请填写书名")
            return
        self.accept()

    def data(self):
        return {
            "title": self.e_title.text().strip(),
            "genre": self.e_genre.text().strip(),
            "synopsis_one": self.e_syn1.text().strip(),
            "synopsis": self.e_syn.toPlainText().strip(),
            "world": self.e_world.toPlainText().strip(),
            "characters": self.e_chars.toPlainText().strip(),
            "plot_points": self.e_plots.toPlainText().strip(),
        }


# ===================== 记忆库对话框 =====================
class MemoryDialog(QDialog):
    def __init__(self, parent=None, novel=None):
        super().__init__(parent)
        self.novel = novel
        self.setWindowTitle(f"全章节记忆库 · {novel.meta.get('title', novel.name)}")
        self.resize(720, 720)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        top = QHBoxLayout()
        self.state_lbl = QLabel("")
        self.state_lbl.setObjectName("dim")
        top.addWidget(self.state_lbl)
        top.addStretch()
        b_refresh = QPushButton("刷新")
        b_refresh.clicked.connect(self._refresh)
        b_one = QPushButton("更新本章记忆")
        b_one.clicked.connect(self._update_one)
        b_all = QPushButton("重建全部记忆")
        b_all.setObjectName("primary")
        b_all.clicked.connect(self._update_all)
        top.addWidget(b_refresh)
        top.addWidget(b_one)
        top.addWidget(b_all)
        root.addLayout(top)

        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        root.addWidget(self.view, 1)
        self._refresh()

    def _refresh(self):
        self.novel.refresh_chapters()
        html = md_to_html(self.novel.load_memory())
        plans = [(c["num"], self.novel.get_plan(c["num"])) for c in self.novel.chapters if self.novel.get_plan(c["num"])]
        if plans:
            ps = "\n\n".join(f"### 第{num}章\n{plan}" for num, plan in plans)
            html += ("<hr style='border:none;border-top:1px solid #33363e;margin:12px 0;'>"
                     "<h3 style='margin:8px 0 4px;'>六、章节创作提示</h3>" + md_to_html(ps))
        self.view.setHtml(html)
        with_summary = sum(1 for c in self.novel.chapters if c.get("summary"))
        with_plan = len(plans)
        self.state_lbl.setText(
            f"已收录 {len(self.novel.chapters)} 章记忆（含摘要 {with_summary} 章 · 创作提示 {with_plan} 章）"
            f" · 最近注入全文 {APP_CONFIG.get('novel_recent_full', 3)} 章")

    def _update_one(self):
        if not self.novel.chapters:
            return
        num = self.novel.chapters[-1]["num"]
        self._run_thread("one", num)

    def _update_all(self):
        self._run_thread("all", None)

    def _run_thread(self, mode, num):
        self.state_lbl.setText("正在生成记忆摘要…（需 API Key；未配置则用本地提取）")
        t = NovelMemoryThread(self.novel, mode=mode, chapter_num=num)
        t.progress.connect(lambda s: self.state_lbl.setText(s))
        t.finished_ok.connect(self._on_done)
        self._thread = t
        t.start()

    def _on_done(self, ok, msg):
        self.state_lbl.setText(msg)
        self._refresh()


# ===================== 人物关系图谱对话框 =====================
class CharacterGraphDialog(QDialog):
    """人物关系网图：人物=彩色节点、关系=分色连线；点节点跳到出场章节。"""

    def __init__(self, parent=None, novel=None):
        super().__init__(parent)
        self.novel = novel
        self.setWindowTitle("人物关系图谱")
        self.resize(860, 620)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(14)
        for text, color in (("主角", "#3a5f9e"), ("女主", "#d05f9b"), ("反派", "#c0392b"),
                            ("导师", "#3a6ea5"), ("配角", "#2e8b57")):
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{color};font-size:13px;")
            lb = QLabel(text)
            lb.setStyleSheet("color:#9aa0ab;font-size:12px;")
            top.addWidget(dot)
            top.addWidget(lb)
        top.addSpacing(18)
        for text, color in (("敌对", "#c0392b"), ("师徒", "#3a6ea5"), ("恋人", "#d05f9b"),
                            ("亲人", "#2e8b57"), ("朋友", "#16a2b8"), ("主仆", "#e67e22")):
            dot = QLabel("━")
            dot.setStyleSheet(f"color:{color};font-size:13px;")
            lb = QLabel(text)
            lb.setStyleSheet("color:#9aa0ab;font-size:12px;")
            top.addWidget(dot)
            top.addWidget(lb)
        top.addStretch()
        tip = QLabel("左键按住拖动节点 · 单击跳转出场章节 · 滚轮缩放")
        tip.setStyleSheet("color:#6b7280;font-size:11px;")
        top.addWidget(tip)
        root.addLayout(top)
        self.view = MindMapView()
        root.addWidget(self.view, 1)
        nodes, edges = build_character_graph(novel)
        self.view.show_graph(nodes, edges, node_click=self._on_node)
        self._stat = QLabel(f"共 {len(nodes)} 个角色 · {len(edges)} 条关系（关系来自人物卡 relations 字段，可到「人物」页补充）")
        self._stat.setStyleSheet("color:#9aa0ab;font-size:12px;")
        root.addWidget(self._stat)

    def _on_node(self, item):
        key = item.data(0)
        if not key or not self.novel:
            return
        num = self._first_occurrence(key)
        if num:
            self.accept()
            if self.parent() is not None:
                self.parent()._load_chapter(num)
        else:
            QMessageBox.information(self, "提示", f"「{key}」尚未在章节正文中出现。\n可到「人物」页编辑其卡片补充剧情。")

    def _first_occurrence(self, name):
        for c in self.novel.chapters:
            if name in (self.novel.load_chapter_content(c["path"]) or ""):
                return c["num"]
        return None


# ===================== 章节历史版本对话框 =====================
class VersionsDialog(QDialog):
    """列出某章自动保存的历史版本，可预览并一键回滚。"""

    def __init__(self, parent=None, novel=None, num=None):
        super().__init__(parent)
        self.novel = novel
        self.num = num
        self.setWindowTitle(f"第{num}章 · 历史版本")
        self.resize(680, 460)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        self.lst = QListWidget()
        self.lst.currentRowChanged.connect(self._preview)
        root.addWidget(self.lst, 1)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(150)
        self.preview.setPlaceholderText("选中版本可预览开头")
        root.addWidget(self.preview)
        self.lbl = QLabel("")
        self.lbl.setStyleSheet("color:#9aa0ab;font-size:12px;")
        root.addWidget(self.lbl)
        bar = QHBoxLayout()
        bar.addStretch()
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.reject)
        self.b_restore = QPushButton("恢复到该版本")
        self.b_restore.setObjectName("primary")
        self.b_restore.clicked.connect(self._restore)
        bar.addWidget(b_close)
        bar.addWidget(self.b_restore)
        root.addLayout(bar)
        self._versions = self.novel.list_versions(num)
        for v in self._versions:
            t = v["time"] or "未知时间"
            it = QListWidgetItem(f"{t}　·　约 {v['words']} 字")
            it.setData(Qt.ItemDataRole.UserRole, v["path"])
            it.setToolTip(v["content"][:60].replace("\n", " "))
            self.lst.addItem(it)
        if not self._versions:
            self.lbl.setText("暂无历史版本（每次「保存」章节前会自动保留一份旧版）")
            self.b_restore.setEnabled(False)

    def _preview(self, row):
        if row < 0 or row >= len(self._versions):
            return
        self.preview.setPlainText(self._versions[row]["content"][:600])

    def _restore(self):
        item = self.lst.currentItem()
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if QMessageBox.question(self, "确认回滚",
                                "将把当前章节替换为该历史版本。当前版本会先自动备份。\n确定继续？") != QMessageBox.StandardButton.Yes:
            return
        content = self.novel.restore_backup(self.num, path)
        if content is None:
            QMessageBox.warning(self, "失败", "回滚失败，请检查备份文件。")
            return
        parent = self.parent()
        if parent is not None:
            parent._load_chapter(self.num)
        QMessageBox.information(self, "完成", "已恢复到该历史版本。")
        self.accept()


# ===================== 参考文库对话框 =====================
class ReferenceLibraryDialog(QDialog):
    """管理本地参考文库：查看/打开路径、建立索引、启用自动参考。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("参考文库（离线文风参考）")
        self.resize(620, 380)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        intro = QLabel(
            "把想模仿风格的作品（.txt / .md）放进「参考文库」文件夹，点「建立索引」后，\n"
            "AI 生成章节时会自动检索文风最接近的片段作为参考（仅借鉴写法，不抄内容，完全离线）。")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#9aa0ab;font-size:12px;")
        root.addWidget(intro)

        pathrow = QHBoxLayout()
        self.e_dir = QLineEdit(APP_CONFIG.get("ref_lib_dir") or REF_LIB_DIR)
        pathrow.addWidget(self.e_dir, 1)
        b_br = QPushButton("浏览…")
        b_br.clicked.connect(self._browse)
        b_open = QPushButton("打开文件夹")
        b_open.clicked.connect(self._open_dir)
        pathrow.addWidget(b_br)
        pathrow.addWidget(b_open)
        root.addLayout(pathrow)

        self.lbl_status = QLabel()
        self.lbl_status.setStyleSheet("color:#c9ccd4;")
        root.addWidget(self.lbl_status)

        row = QHBoxLayout()
        self.ck_enable = QCheckBox("生成章节时自动参考文库文风")
        self.ck_enable.setChecked(bool(APP_CONFIG.get("ref_lib_enable", False)))
        self.ck_enable.stateChanged.connect(self._toggle_enable)
        row.addWidget(self.ck_enable)
        row.addStretch()
        b_build = QPushButton("建立索引")
        b_build.setObjectName("primary")
        b_build.clicked.connect(self._build)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_build)
        row.addWidget(b_close)
        root.addLayout(row)
        self._refresh_status()

    def _refresh_status(self):
        if _REF_INDEX_READY and _REF_INDEX is not None:
            self.lbl_status.setText(
                f"索引已就绪：{_REF_INDEX_STATS}　（本次运行可用；新建/修改文件后请重新建索引）")
            self.ck_enable.setEnabled(True)
        else:
            self.lbl_status.setText("尚未建立索引。请先点「建立索引」。（示例文件夹已自动创建）")
            self.ck_enable.setEnabled(False)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择参考文库文件夹", self.e_dir.text())
        if d:
            self.e_dir.setText(d)

    def _open_dir(self):
        d = self.e_dir.text().strip() or REF_LIB_DIR
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)
        except Exception:
            pass

    def _toggle_enable(self, _state):
        APP_CONFIG["ref_lib_enable"] = self.ck_enable.isChecked()
        APP_CONFIG["ref_lib_dir"] = self.e_dir.text().strip()
        save_config(APP_CONFIG)

    def _build(self):
        d = self.e_dir.text().strip() or REF_LIB_DIR
        APP_CONFIG["ref_lib_dir"] = d
        save_config(APP_CONFIG)
        self.lbl_status.setText("正在建立索引…")
        self._thread = RefIndexThread(d)
        self._thread.progress.connect(lambda s: self.lbl_status.setText(s))
        self._thread.finished_ok.connect(self._on_done)
        self._thread.start()

    def _on_done(self, ok, msg):
        self.lbl_status.setText(msg)
        if ok:
            self.ck_enable.setEnabled(True)
            APP_CONFIG["ref_lib_dir"] = self.e_dir.text().strip()
            save_config(APP_CONFIG)


# ===================== 联网参考对话框 =====================
class WebRefDialog(QDialog):
    """搜索网络相似文，可注入到下次 AI 生成作为参考。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("联网参考（搜索相似文）")
        self.resize(680, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)
        if not APP_CONFIG.get("web_search_key"):
            tip = QLabel("尚未配置「联网搜索 Key」。请先到「设置」填写博查 BoCha Key（open.bochaai.com 注册，有免费额度）。")
            tip.setWordWrap(True)
            tip.setStyleSheet("color:#ffb86c;font-size:12px;")
            root.addWidget(tip)
        qrow = QHBoxLayout()
        self.e_q = QLineEdit()
        self.e_q.setPlaceholderText("搜索词：如“废土 网文 章节 风格” / “反套路 网文 写法” / 想参考的主题")
        self.e_q.returnPressed.connect(self._search)
        qrow.addWidget(self.e_q, 1)
        b_go = QPushButton("搜索")
        b_go.setObjectName("primary")
        b_go.clicked.connect(self._search)
        qrow.addWidget(b_go)
        root.addLayout(qrow)
        self.lst = QListWidget()
        root.addWidget(self.lst, 1)
        self.lbl_hint = QLabel("未注入任何联网参考。搜索后勾选需要的条目，点「注入本次生成」。")
        self.lbl_hint.setStyleSheet("color:#9aa0ab;font-size:12px;")
        root.addWidget(self.lbl_hint)
        row = QHBoxLayout()
        b_inject = QPushButton("注入本次生成")
        b_inject.setObjectName("primary")
        b_inject.clicked.connect(self._inject)
        b_clear = QPushButton("清空已注入")
        b_clear.clicked.connect(self._clear)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        row.addStretch()
        row.addWidget(b_clear)
        row.addWidget(b_inject)
        row.addWidget(b_close)
        root.addLayout(row)
        if _PENDING_WEB_REFS:
            self.lbl_hint.setText(f"已注入 {len(_PENDING_WEB_REFS)} 条联网参考（下次 AI 生成时生效）。")

    def _search(self):
        q = self.e_q.text().strip()
        if not q:
            return
        if not APP_CONFIG.get("web_search_key"):
            QMessageBox.information(self, "提示", "请先到「设置」配置联网搜索 Key。")
            return
        self.lst.clear()
        self.lbl_hint.setText("正在搜索…")
        QApplication.processEvents()
        try:
            results = web_search(q, APP_CONFIG.get("web_search_key"), top=6)
        except Exception as e:
            self.lbl_hint.setText(f"搜索失败：{e}")
            return
        self._results = results
        for r in results:
            item = QListWidgetItem(f"● {r.get('title') or '(无标题)'}\n{r.get('snippet') or ''}\n{r.get('url') or ''}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.lst.addItem(item)
        self.lbl_hint.setText(f"找到 {len(results)} 条，勾选需要的后点「注入本次生成」。（仅作灵感/知识参考，勿整段照搬）")

    def _inject(self):
        global _PENDING_WEB_REFS
        picked = []
        for i in range(self.lst.count()):
            if self.lst.item(i).checkState() == Qt.CheckState.Checked and i < len(getattr(self, "_results", [])):
                picked.append(self._results[i])
        if not picked:
            QMessageBox.information(self, "提示", "请先勾选要注入的条目。")
            return
        for p in picked:
            if p not in _PENDING_WEB_REFS:
                _PENDING_WEB_REFS.append(p)
        self.lbl_hint.setText(f"已注入 {len(picked)} 条（共 {len(_PENDING_WEB_REFS)} 条待用）。"
                              "下次点「生成 / 续写下一章」时自动参考；本窗口可关闭。")

    def _clear(self):
        global _PENDING_WEB_REFS
        _PENDING_WEB_REFS = []
        self.lbl_hint.setText("已清空注入的联网参考。")


# ===================== 批量处理对话框 =====================
class BatchPolishDialog(QDialog):
    """批量章节处理：去AI味 / AI润色 / 同步记忆。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量处理全书章节")
        self.resize(520, 320)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        intro = QLabel(
            "对全书所有章节批量处理。处理前会自动把旧版本留到「备份」目录，可回滚。\n"
            "去AI味为本地处理（快、不消耗 AI）；AI 润色逐章调用模型（慢、需 API Key）。")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#9aa0ab;font-size:12px;")
        root.addWidget(intro)
        self.ck_deai = QCheckBox("去AI味清洗（替换 AI 高频词与冗余连接词）")
        self.ck_deai.setChecked(True)
        root.addWidget(self.ck_deai)
        self.ck_polish = QCheckBox("AI 润色（逐章调用模型提升文笔，保持剧情不变）")
        root.addWidget(self.ck_polish)
        self.ck_mem = QCheckBox("处理后同步更新各章记忆摘要")
        self.ck_mem.setChecked(True)
        root.addWidget(self.ck_mem)
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color:#c9ccd4;")
        root.addWidget(self.lbl_status)
        bar = QHBoxLayout()
        bar.addStretch()
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        self.b_go = QPushButton("开始处理")
        self.b_go.setObjectName("primary")
        self.b_go.clicked.connect(self._go)
        bar.addWidget(b_cancel)
        bar.addWidget(self.b_go)
        root.addLayout(bar)

    def _go(self):
        if not self.ck_deai.isChecked() and not self.ck_polish.isChecked():
            QMessageBox.information(self, "提示", "请至少勾选一种处理方式。")
            return
        self.b_go.setEnabled(False)
        self.lbl_status.setText("正在处理…（可在下方状态栏看进度）")
        parent = self.parent()
        self._thread = NovelBatchPolishThread(
            parent.novel, do_deai=self.ck_deai.isChecked(),
            do_polish=self.ck_polish.isChecked(), update_memory=self.ck_mem.isChecked())
        self._thread.progress.connect(lambda s: parent.statusBar().showMessage(s, 5000))
        self._thread.finished_ok.connect(self._on_done)
        self._thread.start()

    def _on_done(self, ok, msg):
        self.lbl_status.setText(msg)
        self.b_go.setEnabled(True)
        parent = self.parent()
        if parent is not None:
            parent._refresh_chapter_list()
            parent._refresh_characters(quiet=True)
        QMessageBox.information(self, "完成", msg)


# ===================== 人物卡编辑对话框 =====================
class CharacterEditDialog(QDialog):
    FIELDS = [
        ("name", "姓名"),
        ("role", "定位（主角/反派/导师…）"),
        ("identity", "身份 / 背景"),
        ("relations", "人物关系"),
        ("arc", "成长弧 / 目标"),
        ("notes", "备注 / 状态 / 性格"),
    ]

    def __init__(self, parent=None, data=None):
        super().__init__(parent)
        self.setWindowTitle("人物卡" if data else "新增人物卡")
        self.resize(520, 440)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        form = QFormLayout()
        form.setSpacing(8)
        self._edits = {}
        d = data or {}
        for key, label in self.FIELDS:
            if key in ("relations", "arc", "notes"):
                w = QPlainTextEdit(d.get(key, ""))
                w.setFixedHeight(66)
            else:
                w = QLineEdit(d.get(key, ""))
            form.addRow(f"{label}：", w)
            self._edits[key] = w
        root.addLayout(form)
        btns = QHBoxLayout()
        btns.addStretch()
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        b_ok = QPushButton("保存")
        b_ok.setObjectName("primary")
        b_ok.clicked.connect(self.accept)
        btns.addWidget(b_cancel)
        btns.addWidget(b_ok)
        root.addLayout(btns)

    def data(self):
        out = {}
        for key, _label in self.FIELDS:
            w = self._edits[key]
            if isinstance(w, QPlainTextEdit):
                out[key] = w.toPlainText().strip()
            else:
                out[key] = w.text().strip()
        return out


# ===================== 创作统计 =====================
class BarChart(QWidget):
    """深色柱状图：label 轴 + 数值标注。"""

    def __init__(self, title, labels, values, color="#3a6ea5", parent=None):
        super().__init__(parent)
        self.chart_title = title
        self.labels = labels or []
        self.values = values or []
        self.color = QColor(color)
        self.setMinimumHeight(200)
        self.setMinimumWidth(360)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#1e1f24"))
        p.setPen(QColor("#c9ccd4"))
        f = QFont()
        f.setPointSize(9)
        p.setFont(f)
        p.drawText(10, 20, self.chart_title)
        if not self.values:
            p.drawText(20, h // 2, "暂无数据")
            p.end()
            return
        max_v = max(self.values) or 1
        left, top, right, bottom = 46, 40, 10, 30
        chart_w, chart_h = w - left - right, h - top - bottom
        n = len(self.values)
        bw = chart_w / n * 0.62
        fm = QFontMetrics(f)
        for i, v in enumerate(self.values):
            cx = left + (chart_w / n) * (i + 0.5)
            bh = chart_h * (v / max_v)
            bar_h = max(1, int(bh))
            x0 = cx - bw / 2
            y0 = top + chart_h - bar_h
            p.fillRect(int(x0), int(y0), int(bw), int(bar_h), self.color)
            # 数值
            p.setPen(QColor("#e6e8ee"))
            vtxt = str(int(v))
            p.drawText(int(cx - fm.horizontalAdvance(vtxt) / 2), int(y0 - 4), vtxt)
            # 标签（隔 5 或最后）
            if n <= 12 or i % 5 == 0 or i == n - 1:
                lbl = str(self.labels[i]) if i < len(self.labels) else str(i)
                p.setPen(QColor("#9aa0ab"))
                p.drawText(int(cx - fm.horizontalAdvance(lbl) / 2), h - 8, lbl)
        p.end()


class StatsDialog(QDialog):
    def __init__(self, novel, parent=None):
        super().__init__(parent)
        self.novel = novel
        self.setWindowTitle(f"创作统计 · {novel.meta.get('title', novel.name)}")
        self.resize(760, 720)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        chapters = novel.refresh_chapters()
        total = sum(c["words"] for c in chapters)
        # 每日字数趋势（最近 30 天）
        daily = getattr(novel, "_daily", {}) or {}
        dates = sorted(daily.keys())
        last30 = dates[-30:]
        dlabels, dvals = [], []
        for d in last30:
            dlabels.append(d[5:])
            dvals.append(int(daily[d].get("total", 0)))
        # 章节字数分布
        clabels = [f"第{c['num']}章" for c in chapters]
        cvals = [c["words"] for c in chapters]
        summ = QLabel()
        summ.setWordWrap(True)
        avg = total // len(chapters) if chapters else 0
        mx = max(cvals) if cvals else 0
        mn = min(cvals) if cvals else 0
        days = len(last30)
        summ.setText(
            f"全本 {total} 字 · {len(chapters)} 章 · 平均 {avg} 字/章 · 最长 {mx} 字 · 最短 {mn} 字"
            f" · 最近 {days} 天有创作记录")
        summ.setStyleSheet("color:#c9ccd4;")
        root.addWidget(summ)
        chart1 = BarChart("最近 30 天每日新增字数（字）", dlabels, dvals, "#3a6ea5")
        chart2 = BarChart("各章字数分布（字）", clabels, cvals, "#2e8b57")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        vl = QVBoxLayout(inner)
        vl.addWidget(chart1)
        vl.addWidget(chart2)
        vl.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        bh = QHBoxLayout()
        bh.addStretch()
        bh.addWidget(b_close)
        root.addLayout(bh)


# ===================== 思维导图组件 =====================
class MapNode(QGraphicsItem):
    """圆角彩色节点，自动换行，可拖动；用于思维导图与关系图谱。"""

    def __init__(self, text, color, width=200, bold=False, font_size=11,
                 parent=None, on_click=None, movable=True):
        super().__init__(parent)
        self._text = text or ""
        self._color = QColor(color)
        self._width = width
        self._pad = 9
        self._on_click = on_click
        self._press = None
        f = QFont()
        f.setFamily("Microsoft YaHei")
        f.setPointSize(font_size)
        f.setBold(bold)
        self._font = f
        fm = QFontMetrics(f)
        self._line_h = fm.height() + 4
        self._lines = self._wrap(fm, self._text, self._width - 2 * self._pad)
        self._height = max(self._line_h * len(self._lines) + 2 * self._pad, 30)
        self._rect = QRectF(0, 0, self._width, self._height)
        if movable:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event):
        self._press = event.scenePos()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        moved = False
        if self._press is not None:
            moved = (event.scenePos() - self._press).manhattanLength() > 10
        self._press = None
        # 只有"没拖动"才算点击（左键长按拖动后松开不算点击）
        if not moved and self._on_click is not None and event.button() == Qt.MouseButton.LeftButton:
            self._on_click(self)
        super().mouseReleaseEvent(event)

    @staticmethod
    def _wrap(fm, text, maxw):
        lines = []
        for para in (text or "").split("\n"):
            para = para.strip()
            if not para:
                continue
            cur = ""
            for ch in para:
                if cur and fm.horizontalAdvance(cur + ch) > maxw:
                    lines.append(cur)
                    cur = ch
                else:
                    cur += ch
            if cur:
                lines.append(cur)
        return lines or [""]

    def boundingRect(self):
        return self._rect.adjusted(-2, -2, 2, 2)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self._rect, 9, 9)
        painter.setPen(QPen(self._color.lighter(150), 1.4))
        painter.setBrush(QBrush(self._color))
        painter.drawPath(path)
        painter.setFont(self._font)
        painter.setPen(QColor("#ffffff"))
        y = self._pad + 2
        for ln in self._lines:
            painter.drawText(
                QRectF(self._pad, y, self._width - 2 * self._pad, self._line_h),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, ln)
            y += self._line_h


class MindMapView(QGraphicsView):
    """可缩放/拖拽的思维导图画布：中央根节点 + 分类彩色分支。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#1a1b20"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumHeight(200)

    def wheelEvent(self, event):
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        self.scale(factor, factor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 视图尺寸确定后再适配（避免刚切换标签时尺寸为 0 导致黑屏）
        if getattr(self, "_pending_fit", False):
            self._pending_fit = False
            QTimer.singleShot(0, self._fit_now)

    def show_mindmap(self, root_title, groups):
        """groups: [(分类名, 颜色, [条目...])]，root 居左，分支向右展开。"""
        self._scene.clear()
        if not groups:
            groups = [("提示", "#7a8190", ["（暂无内容）"])]
        # 计算每个分类子树的纵向高度
        node_w, leaf_w = 210, 220
        leaf_gap = 34
        cat_gap = 26
        subtree = []          # (label, color, items, height)
        for label, color, items in groups:
            n = max(len(items), 1)
            h = n * (36) + (n - 1) * leaf_gap + 20
            subtree.append((label, color, items, h))
        total = sum(h for *_ , h in subtree) + cat_gap * max(len(subtree) - 1, 0)
        y0 = -total / 2.0

        root = MapNode(root_title, "#3a5f9e", width=node_w, bold=True, font_size=12)
        root.setPos(0, -root.boundingRect().height() / 2)
        self._scene.addItem(root)

        cat_pos = []
        y = y0
        for label, color, items, h in subtree:
            cat = MapNode(label, color, width=node_w, bold=True, font_size=11)
            cat_cy = y + h / 2 - cat.boundingRect().height() / 2
            cat.setPos(250, cat_cy)
            self._scene.addItem(cat)
            cat_pos.append((cat, cat_cy))
            # 叶子
            leaf_cy = y + h / 2 - (len(items) - 1) * leaf_gap / 2 - 18
            for it in items:
                leaf = MapNode(it, color.darker(115) if isinstance(color, QColor) else QColor(color).darker(115),
                               width=leaf_w, font_size=10)
                leaf.setPos(520, leaf_cy)
                self._scene.addItem(leaf)
                self._scene.addItem(self._edge(QPointF(250, cat_cy + cat.boundingRect().height() / 2),
                                               QPointF(520, leaf_cy + leaf.boundingRect().height() / 2),
                                               color))
                leaf_cy += leaf.boundingRect().height() + leaf_gap
            y += h + cat_gap

        # 根→分类 连线
        root_right = QPointF(root.boundingRect().width(), root.y() + root.boundingRect().height() / 2)
        for cat, cat_cy in cat_pos:
            p1 = root_right
            p2 = QPointF(250, cat_cy + cat.boundingRect().height() / 2)
            self._scene.addItem(self._edge(p1, p2, "#5c8ad4"))

        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-40, -40, 40, 40))
        # 延迟到视图真正布局/显示后再适配（避免刚切换标签时尺寸为 0 会黑屏）
        self.resetTransform()
        self._pending_fit = True
        QTimer.singleShot(0, self._fit_now)
        self.viewport().update()

    def show_graph(self, nodes, edges, node_click=None):
        """关系图谱：nodes=[(key, label, color)]，edges=[(a, b, 关系标签, color)]。
        圆形自由布局，节点可左键拖动、单击触发 node_click。"""
        self._scene.clear()
        if not nodes:
            self._scene.addItem(MapNode("（暂无人物，可先到「人物」页生成人物卡）", "#666b75",
                                        width=280, movable=False))
            self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-40, -40, 40, 40))
            self.resetTransform()
            self._pending_fit = True
            QTimer.singleShot(0, self._fit_now)
            return
        n = len(nodes)
        r = max(190, 34 * n / 1.6)
        placed = []
        for i, (key, label, color) in enumerate(nodes):
            ang = 2 * math.pi * i / n - math.pi / 2
            x = r * math.cos(ang)
            y = r * math.sin(ang)
            node = MapNode(label, color, width=176, bold=False, font_size=10, on_click=node_click)
            node.setData(0, key)
            node.setPos(x - node.boundingRect().width() / 2, y - node.boundingRect().height() / 2)
            self._scene.addItem(node)
            node.setZValue(1)
            placed.append((key, node))
        pos = {k: v for k, v in placed}
        for a, b, lab, color in edges:
            if a not in pos or b not in pos:
                continue
            na, nb = pos[a], pos[b]
            p1 = na.pos() + QPointF(na.boundingRect().width() / 2, na.boundingRect().height() / 2)
            p2 = nb.pos() + QPointF(nb.boundingRect().width() / 2, nb.boundingRect().height() / 2)
            edge = self._edge(p1, p2, color)
            edge.setZValue(-1)
            self._scene.addItem(edge)
            if lab:
                mid = (p1 + p2) / 2
                txt = QGraphicsTextItem(lab)
                txt.setDefaultTextColor(QColor("#c9ccd4"))
                tf = QFont("Microsoft YaHei", 8)
                txt.setFont(tf)
                br = txt.boundingRect()
                txt.setPos(mid.x() - br.width() / 2, mid.y() - br.height() / 2 - 9)
                txt.setZValue(0)
                self._scene.addItem(txt)
        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-60, -60, 60, 60))
        self.resetTransform()
        self._pending_fit = True
        QTimer.singleShot(0, self._fit_now)
        self.viewport().update()

    def _fit_now(self):
        if not self._scene.items():
            return
        rect = self._scene.itemsBoundingRect().adjusted(-30, -30, 30, 30)
        if rect.width() < 1 or rect.height() < 1:
            return
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.centerOn(rect.center())
        self._pending_fit = False
        self.viewport().update()

    @staticmethod
    def _edge(p1, p2, color):
        path = QPainterPath(p1)
        dx = (p2.x() - p1.x()) * 0.5
        path.cubicTo(p1.x() + dx, p1.y(), p2.x() - dx, p2.y(), p2.x(), p2.y())
        pen = QPen(QColor(color if isinstance(color, QColor) else color), 1.6)
        pen.setCosmetic(True)
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        return item


def parse_summary_sections(summ):
    """把章节记忆摘要拆成 {小节标题: [条目]}。"""
    sections = {}
    cur = None
    for line in (summ or "").split("\n"):
        m = re.match(r'^#{2,5}\s*(.+)', line)
        if m:
            cur = m.group(1).strip()
            sections.setdefault(cur, [])
        elif cur and line.strip():
            t = line.strip().lstrip("-*•·0123456789.、 ").strip()
            if t and not t.startswith("#"):
                sections[cur].append(t)
    return sections


# ===================== 主窗口 =====================
class NovelWorkspace(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} · 小说生产工坊")
        self.resize(1280, 820)
        self.setMinimumSize(960, 640)
        self.setStyleSheet(FROST_QSS)

        self.novel = None
        self.current_num = None
        self._mem_thread = None
        self._agent_thread = None
        self._batch_thread = None
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(2500)
        self._autosave_timer.timeout.connect(self._auto_save)
        self._dirty = False
        self._restore_chapter = 0
        self._logic_mode = ""
        self._logic_src = ""
        self._logic_report = ""

        self._build_ui()
        self._setup_shortcuts()
        self._load_novel_list()

        QTimer.singleShot(0, self._welcome)
        # 应用窗口图标
        _ico = _resource_path("app.ico")
        if os.path.exists(_ico):
            self.setWindowIcon(QIcon(_ico))
        # 深色标题栏：先在本线程设置一次，再在窗口真正显示后再设一次（winId 此时才有效）
        set_window_titlebar(self, False)

    def showEvent(self, event):
        super().showEvent(event)
        # 窗口显示后 winId 才稳定，此时应用深色标题栏才生效
        QTimer.singleShot(120, lambda: set_window_titlebar(self, False))

    # ---------- UI 构建 ----------
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("root")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # 顶部工具栏（QToolBar 支持窄窗口自动折叠溢出，与主体同色）
        tb = QToolBar()
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setObjectName("topbar")
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        # 直接作用于工具栏自身的样式，确保背景与主体一致（杜绝白色工具栏）
        tb.setStyleSheet(
            "QToolBar { background: #1e1f24; border: none; border-bottom: 1px solid #2a2c33; spacing: 6px; padding: 6px 4px; }"
            "QToolBar::separator { background: #33363e; width: 1px; margin: 4px 6px; }"
            "QToolBar QLabel { color: #c9ccd4; }"
            "QToolBar QPushButton { min-height: 24px; padding: 3px 10px; }")

        title = QLabel("NovelForge")
        title.setStyleSheet("font-size:15px;font-weight:600;color:#e6e8ee;padding:0 4px;")
        tb.addWidget(title)
        tb.addSeparator()
        tb.addWidget(QLabel("小说："))
        self.novel_combo = QComboBox()
        self.novel_combo.setMinimumWidth(180)
        self.novel_combo.currentIndexChanged.connect(self._on_novel_selected)
        tb.addWidget(self.novel_combo)

        self.btn_new = QPushButton("新建小说")
        self.btn_new.clicked.connect(self._new_novel)
        self.btn_rename = QPushButton("重命名")
        self.btn_rename.setToolTip("重命名当前小说（书名与文件夹）")
        self.btn_rename.clicked.connect(self._rename_novel)
        self.btn_open = QPushButton("刷新")
        self.btn_open.setToolTip("重新扫描小说列表")
        self.btn_open.clicked.connect(self._load_novel_list)
        self.btn_setting = QPushButton("设置")
        self.btn_setting.clicked.connect(self._open_settings)
        self.btn_del_novel = QPushButton("删除小说")
        self.btn_del_novel.setObjectName("danger")
        self.btn_del_novel.setToolTip("永久删除当前整本小说（含全部章节与记忆，不可恢复）")
        self.btn_del_novel.clicked.connect(self._delete_novel)
        tb.addWidget(self.btn_new)
        tb.addWidget(self.btn_rename)
        tb.addWidget(self.btn_open)
        tb.addWidget(self.btn_setting)
        tb.addSeparator()
        tb.addWidget(self.btn_del_novel)
        tb.addSeparator()
        self.btn_import = QPushButton("导入")
        self.btn_import.setToolTip("从「整本备份」zip 恢复一部小说到小说库")
        self.btn_import.clicked.connect(self._import_novel)
        tb.addWidget(self.btn_import)
        tb.addSeparator()

        self.btn_preview = QPushButton("预览")
        self.btn_preview.setCheckable(True)
        self.btn_preview.clicked.connect(self._toggle_preview)
        self.btn_backup_dir = QPushButton("备份")
        self.btn_backup_dir.setToolTip("打开自动备份目录（每章保存时保留最近 N 份快照）")
        self.btn_backup_dir.clicked.connect(self._open_backup_dir)
        self.btn_memory = QPushButton("全章节记忆")
        self.btn_memory.clicked.connect(self._open_memory)
        self.btn_export = QPushButton("导出全本")
        self.btn_export.clicked.connect(self._export_novel)
        self.btn_stats = QPushButton("统计")
        self.btn_stats.setToolTip("每日字数趋势与章节字数分布")
        self.btn_stats.clicked.connect(self._open_stats)
        self.btn_publish = QPushButton("发布包")
        self.btn_publish.setToolTip("生成投稿包（投稿信息 + 全文），方便投稿起点/番茄等平台")
        self.btn_publish.clicked.connect(self._export_publish)
        tb.addWidget(self.btn_preview)
        tb.addWidget(self.btn_backup_dir)
        tb.addWidget(self.btn_memory)
        tb.addWidget(self.btn_export)
        tb.addWidget(self.btn_stats)
        tb.addWidget(self.btn_publish)
        self.btn_batch = QPushButton("批量处理")
        self.btn_batch.setToolTip("一次处理全书：批量去AI味 / AI润色 / 同步更新记忆")
        self.btn_batch.clicked.connect(self._open_batch_polish)
        tb.addWidget(self.btn_batch)

        self.addToolBar(tb)

        # 字数统计放到状态栏（工具栏更简洁，窄窗口不挤压）
        self.lbl_tot_words = QLabel("  全本 0 字  ")
        self.lbl_tot_words.setStyleSheet("color:#9aa0ab;")
        self.lbl_today_words = QLabel("  今日 +0 字  ")
        self.lbl_today_words.setStyleSheet("color:#9aa0ab;")
        sb = self.statusBar()
        sb.addPermanentWidget(self.lbl_today_words)
        sb.addPermanentWidget(self.lbl_tot_words)

        # 主体
        split = QSplitter(Qt.Orientation.Horizontal)

        # ---- 左：章节 / 大纲 双标签 ----
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.setSpacing(6)
        self.left_tabs = QTabWidget()
        self.left_tabs.setDocumentMode(True)
        def _on_left_tab(i):
            if i == 1:
                self._refresh_outline_tab()
            elif i == 2:
                self._refresh_foreshadows()
            elif i == 3:
                self._refresh_characters()
            elif i == 4:
                self.search_inp.setFocus()
        self.left_tabs.currentChanged.connect(_on_left_tab)

        # --- 章节页 ---
        ch_tab = QWidget()
        chl = QVBoxLayout(ch_tab)
        chl.setContentsMargins(4, 6, 4, 4)
        chl.setSpacing(6)
        h = QHBoxLayout()
        self.lbl_stats = QLabel("")
        self.lbl_stats.setObjectName("dim")
        h.addWidget(self.lbl_stats)
        h.addStretch()
        chl.addLayout(h)
        self.ch_list = QListWidget()
        self.ch_list.currentRowChanged.connect(self._on_chapter_selected)
        self.ch_list.itemDoubleClicked.connect(self._rename_chapter_dialog)
        self.ch_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ch_list.customContextMenuRequested.connect(self._chapter_context_menu)
        self.ch_list.setToolTip("双击章节可重命名；右键更多操作")
        chl.addWidget(self.ch_list, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        b_add = QPushButton("新章节")
        b_add.setObjectName("mini")
        b_add.clicked.connect(self._add_chapter)
        b_del = QPushButton("删除")
        b_del.setObjectName("mini_danger")
        b_del.clicked.connect(self._delete_chapter)
        b_up = QPushButton("上移")
        b_up.setObjectName("mini")
        b_up.clicked.connect(lambda: self._move_chapter(-1))
        b_down = QPushButton("下移")
        b_down.setObjectName("mini")
        b_down.clicked.connect(lambda: self._move_chapter(1))
        btn_row.addWidget(b_add, 3)
        btn_row.addWidget(b_del, 2)
        btn_row.addWidget(b_up, 2)
        btn_row.addWidget(b_down, 2)
        chl.addLayout(btn_row)
        ver_row = QHBoxLayout()
        self.btn_versions = QPushButton("历史版本")
        self.btn_versions.setObjectName("mini")
        self.btn_versions.setToolTip("查看当前章节自动保存的历史版本（每次保存前自动保留，默认 5 份），可一键回滚")
        self.btn_versions.clicked.connect(self._open_versions)
        ver_row.addWidget(self.btn_versions, 1)
        chl.addLayout(ver_row)
        self.left_tabs.addTab(ch_tab, "章节")

        # --- 大纲页 ---
        out_tab = QWidget()
        ol = QVBoxLayout(out_tab)
        ol.setContentsMargins(4, 6, 4, 4)
        ol.setSpacing(6)
        oth = QHBoxLayout()
        btn_og = QPushButton("AI 生成大纲")
        btn_og.setObjectName("mini_primary")
        btn_og.setToolTip("让 AI 基于全章节记忆生成/更新完整大纲")
        btn_og.clicked.connect(self._start_gen_outline)
        btn_os = QPushButton("同步章节")
        btn_os.setObjectName("mini")
        btn_os.setToolTip("把现有章节自动补进大纲（标题+首句梗概）")
        btn_os.clicked.connect(self._sync_outline)
        oth.addWidget(btn_og, 3)
        oth.addWidget(btn_os, 2)
        ol.addLayout(oth)
        oth2 = QHBoxLayout()
        btn_om = QPushButton("按大纲补齐章节")
        btn_om.setObjectName("mini")
        btn_om.setToolTip("把大纲里规划但尚未创建的章节，批量生成为真实章节")
        btn_om.clicked.connect(self._materialize_outline)
        oth2.addWidget(btn_om, 1)
        btn_batch = QPushButton("批量生成")
        btn_batch.setObjectName("mini_primary")
        btn_batch.setToolTip("让 AI 按大纲逐章批量生成完整正文（每章 ≥2000 字，自动续写+去AI味+生成记忆），"
                             "可一次生成多章")
        btn_batch.clicked.connect(self._batch_generate)
        oth2.addWidget(btn_batch, 1)
        btn_or = QPushButton("按大纲重排章节")
        btn_or.setObjectName("mini_primary")
        btn_or.setToolTip("按大纲树的顺序重排真实章节（重编号，记忆自动跟随）")
        btn_or.clicked.connect(self._apply_outline_order)
        oth2.addWidget(btn_or, 1)
        ol.addLayout(oth2)
        self.outline_tree = QTreeWidget()
        self.outline_tree.setHeaderHidden(True)
        self.outline_tree.itemClicked.connect(self._on_outline_item_clicked)
        self.outline_tree.setToolTip("点击章节节点：在编辑器中打开该章并显示其大纲；右键可编辑大纲；拖拽可调整顺序")
        self.outline_tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.outline_tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.outline_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.outline_tree.customContextMenuRequested.connect(self._outline_context_menu)
        self.outline_tree.model().rowsMoved.connect(self._on_outline_rows_moved)
        ol.addWidget(self.outline_tree, 3)
        self.outline_detail = QTextBrowser()
        self.outline_detail.setPlaceholderText("点击大纲节点查看详情…")
        ol.addWidget(self.outline_detail, 2)
        self.left_tabs.addTab(out_tab, "大纲")

        # --- 伏笔台账页 ---
        fs_tab = QWidget()
        fsl = QVBoxLayout(fs_tab)
        fsl.setContentsMargins(4, 6, 4, 4)
        fsl.setSpacing(6)
        fsh = QHBoxLayout()
        self.lbl_fs_stats = QLabel("")
        self.lbl_fs_stats.setObjectName("dim")
        fsh.addWidget(self.lbl_fs_stats)
        fsh.addStretch()
        fsl.addLayout(fsh)
        self.fs_list = QListWidget()
        self.fs_list.itemDoubleClicked.connect(self._jump_foreshadow_chapter)
        self.fs_list.setToolTip("伏笔台账：双击条目跳转到对应章节；选中后可标为已回收/删除")
        fsl.addWidget(self.fs_list, 1)
        fsb = QHBoxLayout()
        fsb.setSpacing(4)
        btn_fs_refresh = QPushButton("重新提取")
        btn_fs_refresh.setObjectName("mini_primary")
        btn_fs_refresh.setToolTip("从各章记忆摘要的『伏笔与悬念/结尾钩子』+ 大纲钩子重新提取伏笔，并自动识别已回收")
        btn_fs_refresh.clicked.connect(self._refresh_foreshadows)
        btn_fs_done = QPushButton("标为已回收")
        btn_fs_done.setObjectName("mini")
        btn_fs_done.setToolTip("切换选中伏笔的状态（待回收 ↔ 已回收）")
        btn_fs_done.clicked.connect(self._mark_foreshadow_resolved)
        btn_fs_del = QPushButton("删除")
        btn_fs_del.setObjectName("mini_danger")
        btn_fs_del.setToolTip("删除选中的伏笔条目")
        btn_fs_del.clicked.connect(self._delete_foreshadow)
        fsb.addWidget(btn_fs_refresh, 2)
        fsb.addWidget(btn_fs_done, 2)
        fsb.addWidget(btn_fs_del, 2)
        fsl.addLayout(fsb)
        self.left_tabs.addTab(fs_tab, "伏笔")

        # --- 人物卡片页 ---
        char_tab = QWidget()
        chl = QVBoxLayout(char_tab)
        chl.setContentsMargins(4, 6, 4, 4)
        chl.setSpacing(6)
        chh = QHBoxLayout()
        self.lbl_char_stats = QLabel("")
        self.lbl_char_stats.setObjectName("dim")
        chh.addWidget(self.lbl_char_stats)
        chh.addStretch()
        chl.addLayout(chh)
        self.char_list = QListWidget()
        self.char_list.itemDoubleClicked.connect(lambda it: self._edit_character(it))
        self.char_list.setToolTip("人物卡：双击编辑；选中后可 AI 生成/删除")
        chl.addWidget(self.char_list, 1)
        chb = QHBoxLayout()
        chb.setSpacing(4)
        btn_char_add = QPushButton("新增")
        btn_char_add.setObjectName("mini_primary")
        btn_char_add.setToolTip("手动新增人物卡")
        btn_char_add.clicked.connect(lambda: self._add_character())
        btn_char_ai = QPushButton("AI 生成")
        btn_char_ai.setObjectName("mini")
        btn_char_ai.setToolTip("让 AI 基于已写剧情生成/补充人物卡")
        btn_char_ai.clicked.connect(self._ai_generate_characters)
        btn_char_edit = QPushButton("编辑")
        btn_char_edit.setObjectName("mini")
        btn_char_edit.setToolTip("编辑选中的人物卡")
        btn_char_edit.clicked.connect(lambda: self._edit_character())
        btn_char_del = QPushButton("删除")
        btn_char_del.setObjectName("mini_danger")
        btn_char_del.setToolTip("删除选中的人物卡")
        btn_char_del.clicked.connect(self._delete_character)
        chb.addWidget(btn_char_add, 2)
        chb.addWidget(btn_char_ai, 3)
        chb.addWidget(btn_char_edit, 2)
        chb.addWidget(btn_char_del, 2)
        chl.addLayout(chb)
        chb2 = QHBoxLayout()
        btn_char_graph = QPushButton("关系图谱")
        btn_char_graph.setObjectName("mini_primary")
        btn_char_graph.setToolTip("把人物卡的关系生成彩色关系网图：人物=节点、关系=连线（敌对/师徒/恋人分色），点节点跳到出场章节")
        btn_char_graph.clicked.connect(self._open_char_graph)
        chb2.addWidget(btn_char_graph, 1)
        chl.addLayout(chb2)
        self.left_tabs.addTab(char_tab, "人物")

        # --- 跨章检索页 ---
        src_tab = QWidget()
        sl = QVBoxLayout(src_tab)
        sl.setContentsMargins(4, 6, 4, 4)
        sl.setSpacing(6)
        srow = QHBoxLayout()
        self.search_inp = QLineEdit()
        self.search_inp.setPlaceholderText("检索人名/道具/地名…（全书章节）")
        self.search_inp.returnPressed.connect(self._do_search)
        srow.addWidget(self.search_inp, 1)
        btn_search = QPushButton("检索")
        btn_search.setObjectName("mini_primary")
        btn_search.clicked.connect(self._do_search)
        srow.addWidget(btn_search)
        sl.addLayout(srow)
        self.lbl_search_stats = QLabel("输入关键词回车检索，双击结果跳到对应章节")
        self.lbl_search_stats.setObjectName("dim")
        sl.addWidget(self.lbl_search_stats)
        self.search_list = QListWidget()
        self.search_list.itemDoubleClicked.connect(self._jump_search_result)
        self.search_list.setToolTip("检索结果：双击跳到对应章节并定位关键词")
        sl.addWidget(self.search_list, 1)
        self.left_tabs.addTab(src_tab, "检索")

        ll.addWidget(self.left_tabs)
        left.setMaximumWidth(300)
        left.setMinimumWidth(210)
        split.addWidget(left)

        # ---- 右：编辑器 + AI ----
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)
        rl.setSpacing(6)

        vsplit = QSplitter(Qt.Orientation.Vertical)

        # 编辑器
        edit_panel = QFrame()
        edit_panel.setObjectName("panel")
        el = QVBoxLayout(edit_panel)
        el.setContentsMargins(10, 8, 10, 8)
        el.setSpacing(6)
        eh = QHBoxLayout()
        self.ed_title = QLineEdit()
        self.ed_title.setPlaceholderText("章节标题")
        self.ed_title.textChanged.connect(self._mark_dirty)
        self.btn_save = QPushButton("保存")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(lambda: self._save_current(True, True))
        self.btn_plan = QPushButton("本章创作提示")
        self.btn_plan.setCheckable(True)
        self.btn_plan.setChecked(True)
        self.btn_plan.setToolTip("为本章写创作要点/结局钩子，续写下一章时 AI 会自动读取")
        self.btn_plan.clicked.connect(lambda: self.plan_edit.setVisible(self.btn_plan.isChecked()))
        self.lbl_words = QLabel("")
        self.lbl_words.setObjectName("dim")
        eh.addWidget(self.ed_title, 1)
        eh.addWidget(self.btn_plan)
        eh.addWidget(self.lbl_words)
        eh.addWidget(self.btn_save)
        el.addLayout(eh)

        # 本章创作提示（存入记忆库，AI 续写时注入）
        self.plan_edit = QTextEdit()
        self.plan_edit.setFixedHeight(60)
        self.plan_edit.setPlaceholderText("本章创作提示（可选）：本章要达成的目标、关键事件、结尾钩子… 保存后自动进入全章节记忆，续写下一章时 AI 会读到。")
        self.plan_edit.textChanged.connect(self._on_plan_changed)
        el.addWidget(self.plan_edit)

        # 编辑 / 预览 切换
        self.ed_stack = QStackedWidget()
        self.editor = QTextEdit()
        self.editor.setPlaceholderText("在此写作本章正文……\n\n（标题已单独输入，正文开头无需重复写 # 标题）")
        self.editor.textChanged.connect(self._on_edit_changed)
        self.ed_stack.addWidget(self.editor)
        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)
        self.ed_stack.addWidget(self.preview)
        el.addWidget(self.ed_stack, 1)
        vsplit.addWidget(edit_panel)

        # AI 创作面板
        ai_panel = QFrame()
        ai_panel.setObjectName("card")
        al = QVBoxLayout(ai_panel)
        al.setContentsMargins(10, 8, 10, 8)
        al.setSpacing(6)
        ah = QHBoxLayout()
        ah.addWidget(QLabel("AI 创作（记忆所有章节）"))
        self.mode_combo = QComboBox()
        for key, label in MODE_LABELS.items():
            self.mode_combo.addItem(label, key)
        self.mode_combo.setToolTip("续写下一章：自动承接全书记忆与上一章结尾")
        ah.addWidget(self.mode_combo)
        self.btn_gen = QPushButton("生成")
        self.btn_gen.setObjectName("primary")
        self.btn_gen.clicked.connect(self._start_generate)
        self.btn_gen.setEnabled(False)
        ah.addWidget(self.btn_gen)
        ah.addStretch()
        self.btn_apply = QPushButton("插入到编辑器")
        self.btn_apply.clicked.connect(self._apply_to_editor)
        self.btn_apply.setEnabled(False)
        self.btn_next = QPushButton("另存为下一章")
        self.btn_next.clicked.connect(self._save_as_next)
        self.btn_next.setEnabled(False)
        self.btn_fix = QPushButton("修复冲突")
        self.btn_fix.setObjectName("mini_danger")
        self.btn_fix.setToolTip("根据一致性体检结果，让 AI 针对性修正矛盾（无冲突时不可用）")
        self.btn_fix.clicked.connect(self._start_fix_conflict)
        self.btn_fix.setEnabled(False)
        ah.addWidget(self.btn_apply)
        ah.addWidget(self.btn_next)
        ah.addWidget(self.btn_fix)
        al.addLayout(ah)

        # 套路引擎：套路选择 + 套路判定 + 去AI味
        th = QHBoxLayout()
        th.setSpacing(6)
        th.addWidget(QLabel("套路"))
        self.trope_combo = QComboBox()
        self.trope_combo.addItem("不套用套路", "")
        self.trope_combo.addItem("自动推荐套路", "auto")
        for key, t in TROPES.items():
            self.trope_combo.addItem(t["name"], key)
        self.trope_combo.setToolTip("选择套用的网文套路，AI 生成时自动按该套路的节奏/手法增强吸引力")
        th.addWidget(self.trope_combo, 1)
        btn_judge = QPushButton("套路判定")
        btn_judge.setObjectName("mini_primary")
        btn_judge.setToolTip("分析当前小说的套路结构、吸引力短板与 AI 腔，并给出下一步套路动作")
        btn_judge.clicked.connect(self._start_trope_judge)
        th.addWidget(btn_judge)
        btn_deai = QPushButton("去AI味")
        btn_deai.setObjectName("mini")
        btn_deai.setToolTip("一键替换当前编辑器正文中的 AI 高频词与冗余连接词为自然表达（Ctrl+Z 可回退）")
        btn_deai.clicked.connect(self._scan_ai_cliches)
        th.addWidget(btn_deai)
        al.addLayout(th)

        # 参考相似文：本地参考文库 + 联网参考
        rh = QHBoxLayout()
        rh.setSpacing(6)
        rh.addWidget(QLabel("参考"))
        btn_ref_lib = QPushButton("参考文库")
        btn_ref_lib.setObjectName("mini")
        btn_ref_lib.setToolTip("管理本地参考文库（离线文风库）：把想模仿的作品放进文件夹→建立索引→"
                               "生成时自动检索参考其文风")
        btn_ref_lib.clicked.connect(self._open_ref_library)
        rh.addWidget(btn_ref_lib, 1)
        btn_web_ref = QPushButton("联网参考")
        btn_web_ref.setObjectName("mini")
        btn_web_ref.setToolTip("搜索网络相似文/资料，勾选后注入到下次 AI 生成作为参考（需先在设置配置搜索 Key）")
        btn_web_ref.clicked.connect(self._open_web_ref)
        rh.addWidget(btn_web_ref, 1)
        al.addLayout(rh)

        # 逻辑优化 + 发散灵感
        lh = QHBoxLayout()
        lh.setSpacing(6)
        lh.addWidget(QLabel("逻辑"))
        btn_logic = QPushButton("逻辑优化")
        btn_logic.setObjectName("mini")
        btn_logic.setToolTip("对当前编辑器正文做逻辑体检（因果/时间线/动机/能力/设定），"
                             "发现问题后可一键让 AI 重写修复")
        btn_logic.clicked.connect(self._logic_optimize)
        lh.addWidget(btn_logic, 1)
        btn_stuck = QPushButton("卡文助手")
        btn_stuck.setObjectName("mini")
        btn_stuck.setToolTip("写到一半卡住了？让 AI 基于当前剧情给 3 个可立即落笔的破局方向")
        btn_stuck.clicked.connect(self._start_stuck_help)
        lh.addWidget(btn_stuck, 1)
        btn_diverge = QPushButton("发散灵感")
        btn_diverge.setObjectName("mini_primary")
        btn_diverge.setToolTip("基于全书记忆让 AI 脑洞式给出 5 个差异化剧情走向，挑一个写到要求框再生成")
        btn_diverge.clicked.connect(self._start_diverge)
        lh.addWidget(btn_diverge, 1)
        al.addLayout(lh)

        self.ai_inp = QLineEdit()
        self.ai_inp.setPlaceholderText("创作要求（可选）… 例：写主角在苦水湖发现铀母遗迹；结尾留下教廷登场的悬念")
        self.ai_inp.returnPressed.connect(self._start_generate)
        al.addWidget(self.ai_inp)
        self.ai_out = QTextBrowser()
        self.ai_out.setOpenLinks(False)
        self.ai_out.setMinimumHeight(150)
        al.addWidget(self.ai_out, 1)

        # 底部：AI 创作 / 思维导图 双标签
        bottom_tabs = QTabWidget()
        bottom_tabs.setDocumentMode(True)
        bottom_tabs.addTab(ai_panel, "AI 创作")
        mm_tab = QWidget()
        mml = QVBoxLayout(mm_tab)
        mml.setContentsMargins(4, 6, 4, 4)
        mml.setSpacing(6)
        legend = QHBoxLayout()
        legend.setSpacing(14)
        for text, color in (("摘要", "#7f8c8d"), ("情节", "#3a6ea5"), ("人物", "#2e8b57"),
                            ("伏笔", "#8a63d2"), ("结尾钩子", "#c0392b")):
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{color};font-size:13px;")
            lb = QLabel(text)
            lb.setStyleSheet("color:#9aa0ab;font-size:12px;")
            legend.addWidget(dot)
            legend.addWidget(lb)
        legend.addStretch()
        tip = QLabel("滚轮缩放 · 拖拽平移 · 左键按住节点可拖动")
        tip.setStyleSheet("color:#6b7280;font-size:11px;")
        legend.addWidget(tip)
        mml.addLayout(legend)
        self.mindmap = MindMapView()
        mml.addWidget(self.mindmap, 1)
        bottom_tabs.addTab(mm_tab, "思维导图")
        self.bottom_tabs = bottom_tabs
        vsplit.addWidget(bottom_tabs)

        vsplit.setSizes([420, 380])
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 2)
        rl.addWidget(vsplit, 1)
        right.setMinimumWidth(640)
        split.addWidget(right)
        split.setSizes([250, 1000])
        root.addWidget(split, 1)

        self.statusBar().showMessage("就绪")

    # ---------- 小说列表 ----------
    def _load_novel_list(self):
        cur = None
        if self.novel:
            cur = self.novel.root
        last = APP_CONFIG.get("last_novel", "") or cur
        self.novel_combo.blockSignals(True)
        self.novel_combo.clear()
        self._novels = list_novels()
        if not self._novels:
            demo = ensure_demo_novel()
            if demo:
                self._novels = [demo]
        idx = 0
        for i, n in enumerate(self._novels):
            self.novel_combo.addItem(f"《{n.meta.get('title', n.name)}》· {n.meta.get('genre', '')}", n.root)
            if last and n.root == last:
                idx = i
        self.novel_combo.blockSignals(False)
        if self._novels:
            self.novel_combo.setCurrentIndex(idx)
            self._on_novel_selected(idx)

    def _on_novel_selected(self, idx):
        if idx < 0 or idx >= len(self._novels):
            return
        self._open_novel(self._novels[idx])

    def _open_novel(self, novel):
        self._save_current(force=False)
        self.novel = novel
        # 关键：重置当前章节号，避免切换小说后因章节号相同而跳过加载（内容串书）
        self.current_num = None
        self._dirty = False
        self.novel.refresh_chapters()
        self._restore_chapter = APP_CONFIG.get("last_chapter", 0) or 0
        restore_num = self._restore_chapter
        self._refresh_chapter_list(restore=True)
        # 显式加载恢复的章节（重建列表不再自动触发加载）
        if self.novel.chapters:
            if not any(c["num"] == restore_num for c in self.novel.chapters):
                restore_num = self.novel.chapters[0]["num"]
            self._load_chapter(restore_num)
        self._refresh_outline_tab()
        self._update_stats()
        self.statusBar().showMessage(f"已打开《{novel.meta.get('title', novel.name)}》 · {len(novel.chapters)} 章")
        self.btn_gen.setEnabled(bool(self.novel))
        APP_CONFIG["last_novel"] = novel.root
        save_config(APP_CONFIG)

    def _welcome(self):
        if self.novel:
            # 若全部章节都还没有摘要，提示一键生成
            if self.novel.chapters and not any(c.get("summary") for c in self.novel.chapters):
                self.ai_out.append(
                    "<div style='color:#9aa0ab;'>首次使用提示：本小说章节尚未生成记忆摘要。"
                    "点击上方「全章节记忆」→「重建全部记忆」即可让 AI 记住全部章节（无 Key 时自动用本地提取）。</div>")

    # ---------- 章节列表 ----------
    def _refresh_chapter_list(self, restore=False):
        # 全程 blockSignals：重建列表 + 恢复选中都不触发 currentRowChanged，
        # 避免每次刷新都被误加载到第 1 章
        self.ch_list.blockSignals(True)
        self.ch_list.clear()
        for c in self.novel.chapters:
            item = QListWidgetItem(f"第{c['num']}章 · {c['title']}")
            item.setData(Qt.ItemDataRole.UserRole, c["num"])
            if c.get("summary"):
                item.setToolTip(c["summary"][:120])
            self.ch_list.addItem(item)
        row = 0
        if self.novel.chapters:
            if restore and getattr(self, "_restore_chapter", 0):
                rnum = self._restore_chapter
                self._restore_chapter = 0
                row = next((i for i, c in enumerate(self.novel.chapters) if c["num"] == rnum), 0)
            else:
                cur = self.current_num
                row = next((i for i, c in enumerate(self.novel.chapters) if c["num"] == cur), 0)
            self.ch_list.setCurrentRow(row)
        self.ch_list.blockSignals(False)
        self._update_stats()

    def _update_stats(self):
        if not self.novel:
            return
        total = sum(c["words"] for c in self.novel.chapters)
        mem = sum(1 for c in self.novel.chapters if c.get("summary"))
        self.lbl_stats.setText(f"{len(self.novel.chapters)} 章 · 记忆 {mem}/{len(self.novel.chapters)}")
        self.lbl_tot_words.setText(f"全本 {total:,} 字")
        target = int(APP_CONFIG.get("novel_daily_target", 0) or 0)
        tw = self.novel.today_words()
        if target > 0:
            self.lbl_today_words.setText(f"今日 {tw:,} / {target:,} 字")
        else:
            self.lbl_today_words.setText(f"今日 +{tw:,} 字")

    # ---------- 章节编辑 ----------
    def _on_chapter_selected(self, row):
        if row < 0:
            return
        item = self.ch_list.item(row)
        num = item.data(Qt.ItemDataRole.UserRole)
        if num == self.current_num:
            return
        self._save_current(force=False)
        self._load_chapter(num)

    def _load_chapter(self, num):
        content = self.novel.load_chapter_by_num(num)
        lines = content.split("\n")
        title = ""
        if lines and lines[0].strip().startswith("# "):
            title = lines[0].strip()[2:].strip()
            content = "\n".join(lines[1:]).lstrip("\n")
        self.current_num = num
        self._dirty = False
        self.ed_title.blockSignals(True)
        self.ed_title.setText(title)
        self.ed_title.blockSignals(False)
        self.editor.blockSignals(True)
        self.editor.setPlainText(content)
        self.editor.blockSignals(False)
        self.plan_edit.blockSignals(True)
        self.plan_edit.setPlainText(self.novel.get_plan(num))
        self.plan_edit.blockSignals(False)
        # 章节列表同步高亮（blockSignals 避免回环触发 _on_chapter_selected）
        self.ch_list.blockSignals(True)
        for i in range(self.ch_list.count()):
            it = self.ch_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == num:
                self.ch_list.setCurrentRow(i)
                self.ch_list.scrollToItem(it)
                break
        self.ch_list.blockSignals(False)
        self._update_word_count()
        self.statusBar().showMessage(f"正在编辑：第{num}章 · {title or '未命名'}")
        APP_CONFIG["last_chapter"] = num
        save_config(APP_CONFIG)

    # ---------- 预览 / 备份 / 快捷键 ----------
    def _toggle_preview(self):
        if self.btn_preview.isChecked():
            doc = f"# {self.ed_title.text()}\n\n{self.editor.toPlainText()}"
            self.preview.setHtml(md_to_html(doc))
            self.ed_stack.setCurrentIndex(1)
            self.btn_preview.setText("编辑")
        else:
            self.ed_stack.setCurrentIndex(0)
            self.btn_preview.setText("预览")

    def _open_backup_dir(self):
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        os.makedirs(self.novel.backups_dir, exist_ok=True)
        try:
            os.startfile(self.novel.backups_dir)
        except Exception as e:
            QMessageBox.information(self, "提示", f"备份目录：{self.novel.backups_dir}\n（无法自动打开：{e}）")

    def _setup_shortcuts(self):
        for key, fn in [
            ("Ctrl+S", lambda: self._save_current(True, True)),
            ("Ctrl+N", self._add_chapter),
            ("Ctrl+E", self._export_novel),
            ("Ctrl+P", self._toggle_preview),
            ("Ctrl+Shift+M", self._open_memory),
            ("F5", self._load_novel_list),
        ]:
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(fn)

    def _on_edit_changed(self):
        self._mark_dirty()
        self._update_word_count()

    def _on_plan_changed(self):
        self._dirty = True
        self._autosave_timer.start()

    def _mark_dirty(self):
        self._dirty = True
        self._autosave_timer.start()

    def _update_word_count(self):
        if not self.novel:
            return
        text = count_words(self.editor.toPlainText())
        minw = int(APP_CONFIG.get("novel_min_words", 2000) or 0)
        if minw and text < minw:
            self.lbl_words.setText(f"{text} / {minw} 字")
            self.lbl_words.setStyleSheet("color:#ff7b72;font-size:12px;")
            self.lbl_words.setToolTip(f"本章尚未达到每章最少 {minw} 字")
        else:
            self.lbl_words.setText(f"{text} 字")
            self.lbl_words.setStyleSheet("color:#9aa0ab;font-size:12px;")
            self.lbl_words.setToolTip("")

    def _save_current(self, force=True, interactive=False):
        if not self.novel or self.current_num is None:
            return
        if not self._dirty and not force:
            return
        title = self.ed_title.text().strip() or "未命名"
        content = self.editor.toPlainText()
        # 手动保存时检查每章最少字数
        minw = int(APP_CONFIG.get("novel_min_words", 2000) or 0)
        if interactive and minw and count_words(content) < minw:
            ans = QMessageBox.question(
                self, "字数不足",
                f"本章当前 {count_words(content)} 字，未达到每章最少 {minw} 字。\n\n仍要保存吗？"
                f"（建议继续写作或让 AI 扩写后再保存）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                self.statusBar().showMessage(f"未保存（本章不足 {minw} 字）", 5000)
                return
        body = self.novel.save_chapter(self.current_num, title, content)
        # 保存本章创作提示
        self.novel.set_plan(self.current_num, self.plan_edit.toPlainText())
        self.novel.ensure_outline_sync()
        self._dirty = False
        self._update_word_count()
        self._update_stats()
        # 更新列表显示
        for i in range(self.ch_list.count()):
            item = self.ch_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == self.current_num:
                item.setText(f"第{self.current_num}章 · {title}")
                break
        self.statusBar().showMessage(f"第{self.current_num}章已保存 · {datetime.now().strftime('%H:%M:%S')}")
        # 自动更新本章记忆
        if APP_CONFIG.get("novel_auto_memory", True):
            self._start_memory_update(mode="one", num=self.current_num)

    def _auto_save(self):
        if self._dirty and self.novel:
            self._save_current(force=False)

    # ---------- 章节管理 ----------
    def _add_chapter(self):
        if not self.novel:
            return
        title, ok = QInputDialog.getText(self, "新建章节", "章节标题：")
        if not ok:
            return
        title = title.strip() or f"第{self.novel.next_chapter_num()}章"
        num = self.novel.add_chapter(title, "")
        self._refresh_chapter_list()
        self._load_chapter(num)
        self._refresh_outline_tab()

    def _delete_chapter(self):
        if not self.novel or self.current_num is None:
            return
        if len(self.novel.chapters) <= 1:
            QMessageBox.information(self, "提示", "至少保留一个章节")
            return
        if QMessageBox.question(self, "确认删除", f"确定删除第{self.current_num}章？") != QMessageBox.StandardButton.Yes:
            return
        self.novel.delete_chapter(self.current_num)
        self.novel.delete_outline_chapter(self.current_num)
        self.current_num = None
        self._refresh_chapter_list()
        self._refresh_outline_tab()
        if self.novel.chapters:
            self._load_chapter(self.novel.chapters[0]["num"])

    def _move_chapter(self, delta):
        if not self.novel or self.current_num is None:
            return
        if self.novel.move_chapter(self.current_num, delta):
            self.current_num = None
            self._refresh_chapter_list()
            self._refresh_outline_tab()
            if self.novel.chapters:
                self._load_chapter(self.novel.chapters[0]["num"])

    def _rename_chapter_dialog(self, item=None):
        """双击章节列表项：重命名章节标题。"""
        if not self.novel or self.current_num is None:
            return
        old = self.ed_title.text().strip() or "未命名"
        new_title, ok = QInputDialog.getText(self, "重命名章节", "新的章节标题：", text=old)
        if not ok:
            return
        new_title = new_title.strip()
        if new_title and new_title != old:
            self.ed_title.setText(new_title)
            self._save_current(force=True, interactive=False)

    def _chapter_context_menu(self, pos):
        if not self.novel:
            return
        item = self.ch_list.itemAt(pos)
        if item is None:
            return
        num = item.data(Qt.ItemDataRole.UserRole)
        if num is None:
            return
        self._save_current(force=False)
        self._load_chapter(num)
        menu = QMenu(self)
        act_rename = menu.addAction("重命名章节")
        act_up = menu.addAction("上移")
        act_down = menu.addAction("下移")
        act_del = menu.addAction("删除章节")
        act = menu.exec(self.ch_list.mapToGlobal(pos))
        if act == act_rename:
            self._rename_chapter_dialog(item)
        elif act == act_up:
            self._move_chapter(-1)
        elif act == act_down:
            self._move_chapter(1)
        elif act == act_del:
            self._delete_chapter()

    # ---------- 大纲 ----------
    def _refresh_outline_tab(self):
        """按当前章节重建大纲树（自动补缺章条目）。"""
        if not self.novel:
            self.outline_tree.clear()
            self.outline_detail.clear()
            return
        items = self.novel.ensure_outline_sync()
        self.outline_tree.clear()
        for vol in items or []:
            vt = (vol.get("title") or "未命名卷").strip()
            vitem = QTreeWidgetItem([vt])
            vdesc = (vol.get("desc") or "").strip()
            if vdesc:
                vitem.setToolTip(0, vdesc[:200])
            vitem.setData(0, Qt.ItemDataRole.UserRole, {"type": "vol", "title": vt, "desc": vdesc})
            for c in vol.get("chapters", []) or []:
                ctitle = (c.get("title") or f"第{c.get('num', '?')}章").strip()
                citem = QTreeWidgetItem([f"第{c.get('num', '?')}章 {ctitle}"])
                citem.setData(0, Qt.ItemDataRole.UserRole, {
                    "type": "ch", "num": c.get("num"),
                    "title": ctitle,
                    "outline": (c.get("outline") or "").strip(),
                    "hook": (c.get("hook") or "").strip(),
                })
                vitem.addChild(citem)
            self.outline_tree.addTopLevelItem(vitem)
        self.outline_tree.expandAll()

    def _on_outline_item_clicked(self, item, col):
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("type") == "ch":
            num = data.get("num")
            title = (data.get("title") or "").strip()
            if not isinstance(num, int):
                return
            self._save_current(force=False)
            exists = any(c["num"] == num for c in self.novel.chapters)
            if not exists:
                # 大纲规划中尚未创建真实章节的节点：只展示详情与思维导图，不打断、不弹窗；
                # 创建走「按大纲补齐章节」按钮或右键菜单
                self.outline_detail.setPlainText(
                    f"第{num}章 · {title or '未命名'}\n\n【内容梗概】\n{data.get('outline') or '（无）'}\n\n"
                    f"【结尾钩子】\n{data.get('hook') or '（无）'}\n\n"
                    f"（该章尚未创建为真实章节，点上方「按大纲补齐章节」或右键→创建真实章节）")
                self.bottom_tabs.setCurrentIndex(1)
                self._mindmap_for_planned(num, title, data)
                return
            self._load_chapter(num)
            detail = (f"第{num}章 · {title or '未命名'}\n\n"
                      f"【内容梗概】\n{data.get('outline') or '（无，可点「AI 生成大纲」自动生成）'}\n\n"
                      f"【结尾钩子】\n{data.get('hook') or '（无）'}")
            self.outline_detail.setPlainText(detail)
            self.bottom_tabs.setCurrentIndex(1)
            self._mindmap_for_chapter(num, title)
        elif data.get("type") == "vol":
            self.outline_detail.setPlainText(
                f"卷 · {data.get('title', '')}\n\n{data.get('desc') or '（暂无卷概要）'}")
            chapters = []
            for i in range(item.childCount()):
                cd = item.child(i).data(0, Qt.ItemDataRole.UserRole) or {}
                chapters.append(cd)
            self.bottom_tabs.setCurrentIndex(1)
            self._mindmap_for_volume(data.get("title", ""), chapters)

    def _mindmap_for_planned(self, num, title, data):
        """规划章节（未创建）的思维导图：从大纲的梗概/钩子生成。"""
        groups = []
        if data.get("outline"):
            groups.append(("内容梗概", QColor("#3a6ea5"), [data["outline"]]))
        if data.get("hook"):
            groups.append(("结尾钩子", QColor("#c0392b"), [data["hook"]]))
        if not groups:
            groups = [("提示", QColor("#666b75"), ["该规划章节暂无梗概与钩子，可在右侧右键编辑或点「AI 生成大纲」"])]
        self.mindmap.show_mindmap(f"第{num}章 · {title or ''}（规划）".strip(), groups)

    def _mindmap_for_chapter(self, num, title):
        """章节思维导图：从记忆摘要提取 摘要/情节/人物/伏笔/钩子，彩色分支。"""
        groups = []
        sections = parse_summary_sections(self.novel.get_summary(num) or "")

        def add(label, color, items):
            items = [x for x in items if x and x.strip()]
            if items:
                groups.append((label, QColor(color), items))

        add("摘要", "#7f8c8d", sections.get("摘要", []))
        add("情节", "#3a6ea5", sections.get("关键事件", []) or sections.get("情节", []))
        add("人物", "#2e8b57", sections.get("出场人物", []) or sections.get("人物", []))
        add("伏笔", "#8a63d2", sections.get("伏笔与悬念", []) or sections.get("伏笔", []) or sections.get("悬念", []))
        add("结尾钩子", "#c0392b", sections.get("结尾钩子", []) or sections.get("钩子", []))
        # 大纲补充（梗概→情节、钩子→结尾钩子）
        outline_txt = hook_txt = ""
        for vol in self.novel._outline:
            for c in vol.get("chapters", []) or []:
                if c.get("num") == int(num):
                    outline_txt = (c.get("outline") or "").strip()
                    hook_txt = (c.get("hook") or "").strip()
        if outline_txt and not any(g[0] == "情节" for g in groups):
            groups.append(("情节", QColor("#3a6ea5"), [outline_txt]))
        if hook_txt:
            if any(g[0] == "结尾钩子" for g in groups):
                for g in groups:
                    if g[0] == "结尾钩子" and hook_txt not in g[2]:
                        g[2].append(hook_txt)
            else:
                groups.append(("结尾钩子", QColor("#c0392b"), [hook_txt]))
        if not groups:
            # 无记忆也无大纲时，自动取正文首句作为情节
            body = re.sub(r'^#.*', '', self.novel.load_chapter_by_num(num) or "").strip()
            body = re.sub(r'\s+', '', body)
            if body:
                groups.append(("情节", QColor("#3a6ea5"), [body[:50] + ("…" if len(body) > 50 else "")]))
        if not groups:
            groups = [("提示", QColor("#7a8190"),
                       ["该章暂无记忆摘要，可在「全章节记忆」中生成后再查看思维导图"])]
        self.mindmap.show_mindmap(f"第{num}章 · {title or ''}".strip(), groups)

    def _mindmap_for_volume(self, vol_title, chapters):
        """卷思维导图：每章为分支，汇总卷内钩子。"""
        groups = []
        hooks = []
        for c in chapters or []:
            label = f"第{c.get('num', '?')}章 {c.get('title', '')}".strip()
            outline = (c.get("outline") or "").strip()
            groups.append((label, QColor("#4a6fa5"),
                           [outline] if outline else ["（无梗概）"]))
            hook = (c.get("hook") or "").strip()
            if hook:
                hooks.append(f"第{c.get('num', '?')}章：{hook}")
        if hooks:
            groups.append(("卷内钩子", QColor("#8a63d2"), hooks))
        if not groups:
            groups = [("提示", QColor("#666b75"), ["本卷暂无章节"])]
        self.mindmap.show_mindmap(vol_title or "未命名卷", groups)

    def _sync_outline(self):
        if not self.novel:
            return
        self.novel.ensure_outline_sync()
        self._refresh_outline_tab()
        self.statusBar().showMessage("大纲已按当前章节同步", 3000)

    def _materialize_outline(self):
        """把大纲里规划但尚未创建的章节，批量创建为真实章节（大纲驱动章节目录）。"""
        if not self.novel:
            return
        items = self.novel.ensure_outline_sync()
        existing = {c["num"] for c in self.novel.chapters}
        planned = []
        seen = set()
        for vol in items or []:
            for c in vol.get("chapters", []) or []:
                n = c.get("num")
                if isinstance(n, int) and n not in existing and n not in seen:
                    seen.add(n)
                    planned.append((n, (c.get("title") or f"第{n}章").strip()))
        if not planned:
            QMessageBox.information(self, "提示", "大纲中所有章节都已是真实章节，无需补齐。")
            return
        names = "、".join(f"第{n}章《{t}》" for n, t in planned[:8]) + ("…" if len(planned) > 8 else "")
        if QMessageBox.question(
                self, "按大纲补齐章节",
                f"将把以下大纲规划章节创建为真实章节（先为空内容，可随后逐一创作）：\n{names}\n\n共 {len(planned)} 章，是否继续？") != QMessageBox.StandardButton.Yes:
            return
        for n, t in planned:
            self.novel.create_chapter_at(n, t)
        self._refresh_chapter_list()
        self._refresh_outline_tab()
        if self.novel.chapters:
            self._load_chapter(self.novel.chapters[0]["num"])
        self.statusBar().showMessage(f"已按大纲补齐 {len(planned)} 个章节", 6000)

    # ---------- 大纲树双向编辑 ----------
    def _read_outline_tree(self):
        """读取大纲树的当前结构（含拖拽后的顺序）：[(卷名, 卷desc, [(num,title,outline,hook),...])]"""
        out = []
        for vi in range(self.outline_tree.topLevelItemCount()):
            v = self.outline_tree.topLevelItem(vi)
            vd = v.data(0, Qt.ItemDataRole.UserRole) or {}
            chs = []
            for ci in range(v.childCount()):
                c = v.child(ci)
                cd = c.data(0, Qt.ItemDataRole.UserRole) or {}
                chs.append((cd.get("num"), cd.get("title", ""), cd.get("outline", ""), cd.get("hook", "")))
            out.append(((vd.get("title", "") or "第一卷"), (vd.get("desc", "") or ""), chs))
        return out

    def _save_outline_from_tree(self):
        """拖拽后：按树的当前顺序保存大纲（保留章号，仅改顺序）。"""
        if not self.novel:
            return
        new_outline = []
        for vol_title, vol_desc, chs in self._read_outline_tree():
            entries = []
            for n, title, outline, hook in chs:
                if not isinstance(n, int):
                    continue
                entries.append({"num": n, "title": title or f"第{n}章", "outline": outline, "hook": hook})
            new_outline.append({"title": vol_title, "desc": vol_desc, "chapters": entries})
        if new_outline:
            self.novel.save_outline(new_outline)

    def _on_outline_rows_moved(self, *args):
        self._save_outline_from_tree()
        self.statusBar().showMessage("大纲顺序已保存（点「按大纲重排章节」可应用到真实章节）", 4000)

    def _outline_context_menu(self, pos):
        if not self.novel:
            return
        item = self.outline_tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        menu = QMenu(self)
        if data.get("type") == "ch":
            menu.addAction("编辑标题", lambda: self._outline_edit_chapter(item, "title"))
            menu.addAction("编辑内容梗概", lambda: self._outline_edit_chapter(item, "outline"))
            menu.addAction("编辑结尾钩子", lambda: self._outline_edit_chapter(item, "hook"))
            menu.addSeparator()
            menu.addAction("从大纲移除（真实章节保留）", lambda: self._outline_remove_chapter(item))
            menu.addAction("删除章节（连真实章节）", lambda: self._outline_delete_chapter(item))
        else:
            menu.addAction("编辑卷名", lambda: self._outline_edit_vol(item))
            menu.addAction("本卷新增规划章节", lambda: self._outline_add_planned(item))
        menu.exec(self.outline_tree.viewport().mapToGlobal(pos))

    def _outline_edit_chapter(self, item, field):
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        num = data.get("num")
        if not isinstance(num, int):
            return
        cur = {"title": data.get("title", ""), "outline": data.get("outline", ""),
               "hook": data.get("hook", "")}[field]
        label = {"title": "章节标题", "outline": "内容梗概", "hook": "结尾钩子"}[field]
        new, ok = QInputDialog.getText(self, f"编辑{label}", f"第{num}章 {label}：", text=cur or "")
        if not ok:
            return
        new = new.strip()
        self.novel.update_outline_chapter(num, **{field: new})
        # 标题同步到真实章节文件（若该章已存在）
        if field == "title" and new:
            c = next((c for c in self.novel.chapters if c["num"] == num), None)
            if c:
                content = self.novel.load_chapter_content(c["path"])
                self.novel.save_chapter(num, new, content, record=False)
        self._refresh_chapter_list()
        self._refresh_outline_tab()
        self.statusBar().showMessage(f"已更新第{num}章{label}", 3000)

    def _outline_edit_vol(self, item):
        row = self.outline_tree.indexOfTopLevelItem(item)
        self.novel.load_outline()
        if not (0 <= row < len(self.novel._outline)):
            return
        old = (self.novel._outline[row].get("title") or "").strip()
        new, ok = QInputDialog.getText(self, "编辑卷名", "卷名：", text=old or "第一卷")
        if not ok:
            return
        new = new.strip() or "第一卷"
        self.novel._outline[row]["title"] = new
        self.novel.save_outline(self.novel._outline)
        self._refresh_outline_tab()
        self.statusBar().showMessage("已更新卷名", 3000)

    def _outline_add_planned(self, item):
        row = self.outline_tree.indexOfTopLevelItem(item)
        self.novel.load_outline()
        if not (0 <= row < len(self.novel._outline)):
            return
        all_nums = [c.get("num") for v in self.novel._outline
                    for c in v.get("chapters", []) or [] if isinstance(c.get("num"), int)]
        nnum = (max(all_nums) + 1) if all_nums else 1
        title, ok = QInputDialog.getText(self, "新增规划章节", "章节标题：", text=f"第{nnum}章")
        if not ok:
            return
        title = title.strip() or f"第{nnum}章"
        self.novel._outline[row].setdefault("chapters", []).append(
            {"num": nnum, "title": title, "outline": "", "hook": ""})
        self.novel.save_outline(self.novel._outline)
        self._refresh_outline_tab()
        self.outline_tree.expandAll()
        self.statusBar().showMessage(f"已在大纲新增规划章节第{nnum}章（尚未创建真实章节）", 3000)

    def _outline_remove_chapter(self, item):
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        num = data.get("num")
        if not isinstance(num, int):
            return
        self.novel.delete_outline_chapter(num)
        self._refresh_outline_tab()
        self.statusBar().showMessage(f"已从大纲移除第{num}章（真实章节未动）", 3000)

    def _outline_delete_chapter(self, item):
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        num = data.get("num")
        if not isinstance(num, int):
            return
        if len(self.novel.chapters) <= 1:
            QMessageBox.information(self, "提示", "至少保留一个真实章节")
            return
        if QMessageBox.question(self, "确认删除", f"确定删除第{num}章（真实章节文件将被删除）？") != QMessageBox.StandardButton.Yes:
            return
        self.novel.delete_chapter(num)
        self.novel.delete_outline_chapter(num)
        if self.current_num == num:
            self.current_num = None
        self._refresh_chapter_list()
        self._refresh_outline_tab()
        if self.novel.chapters and self.current_num is None:
            self._load_chapter(self.novel.chapters[0]["num"])
        self.statusBar().showMessage(f"已删除第{num}章", 3000)

    def _apply_outline_order(self):
        """按大纲树顺序重排真实章节（重编号，记忆跟随），并重建大纲。"""
        if not self.novel:
            return
        vol_chs = self._read_outline_tree()
        existing = {c["num"] for c in self.novel.chapters}
        desired = [n for _, _, chs in vol_chs for n, *_ in chs
                   if isinstance(n, int) and n in existing]
        mapping = None
        if desired:
            mapping = self.novel.reorder_chapters_by_nums(desired)
        max_real = max((c["num"] for c in self.novel.chapters), default=0)
        planned_counter = max_real + 1
        new_outline = []
        for vol_title, vol_desc, chs in vol_chs:
            entries = []
            for n, title, outline, hook in chs:
                if isinstance(n, int) and mapping and n in mapping:
                    nn = mapping[n]
                elif isinstance(n, int) and not mapping and n in existing:
                    nn = n
                else:
                    nn = planned_counter
                    planned_counter += 1
                entries.append({"num": nn, "title": title or f"第{nn}章", "outline": outline, "hook": hook})
            new_outline.append({"title": vol_title, "desc": vol_desc, "chapters": entries})
        self.novel.save_outline(new_outline)
        # 当前编辑的章号跟随重映射
        if mapping and self.current_num and self.current_num in mapping:
            self.current_num = mapping[self.current_num]
        self._refresh_chapter_list()
        self._refresh_outline_tab()
        if mapping and self.current_num:
            self._load_chapter(self.current_num)
        if mapping:
            self.statusBar().showMessage("已按大纲顺序重排真实章节（编号与记忆已跟随）", 5000)
        else:
            self.statusBar().showMessage("章节顺序未变化，仅同步了大纲", 4000)

    # ---------- 大纲批量生成 ----------
    def _batch_generate(self):
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        if getattr(self, "_batch_thread", None) and self._batch_thread.isRunning():
            return
        if not APP_CONFIG.get("api_key"):
            QMessageBox.warning(self, "未配置 AI", "请先在「设置」中填写 API Key 与模型，才能批量生成章节。")
            self._open_settings()
            return
        vol_chs = self._read_outline_tree()
        targets = []
        for _vt, _vd, chs in vol_chs:
            for n, title, outline, hook in chs:
                if isinstance(n, int):
                    targets.append((n, title or f"第{n}章", outline, hook))
        if not targets:
            QMessageBox.information(self, "提示", "大纲里还没有章节，请先「AI 生成大纲」或「同步章节」。")
            return
        existing = {c["num"] for c in self.novel.chapters}
        planned = [t for t in targets if t[0] not in existing]
        overwrite = QMessageBox.question(
            self, "批量生成章节",
            f"大纲共 {len(targets)} 章，其中 {len(planned)} 章尚未创建为真实章节。\n\n"
            f"是否【重新生成并覆盖】已存在的章节？\n（选“否”则只生成未创建的 {len(planned)} 章）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        todo = targets if overwrite == QMessageBox.StandardButton.Yes else planned
        if not todo:
            QMessageBox.information(self, "提示", "没有需要生成的章节。")
            return
        self.btn_gen.setEnabled(False)
        self._stream_buf = ""
        self.ai_out.clear()
        self.ai_out.append(
            f"<div style='color:#8ab4f8;font-weight:600;'>批量生成 {len(todo)} 章（按大纲，每章 ≥2000 字）</div>"
            f"<div style='color:#9aa0ab;font-size:12px;'>逐章生成中…可在状态栏查看进度，完成后自动保存章节并生成记忆。</div>")
        t = NovelBatchGenThread(self.novel, todo,
                                trope=self.trope_combo.currentData() or "",
                                instruction=self.ai_inp.text())
        t.progress.connect(self._on_batch_progress)
        t.finished_ok.connect(self._on_batch_done)
        self._batch_thread = t
        t.start()

    def _on_batch_progress(self, msg):
        self.statusBar().showMessage(msg)
        self.ai_out.append(f"<div style='color:#9aa0ab;font-size:12px;'>{esc(msg)}</div>")
        self.ai_out.verticalScrollBar().setValue(self.ai_out.verticalScrollBar().maximum())

    def _on_batch_done(self, ok, msg):
        self._batch_thread = None
        self.btn_gen.setEnabled(True)
        if self.novel:
            self._refresh_chapter_list()
            self._refresh_outline_tab()
        color = "#7ee787" if ok else "#ff7b72"
        self.ai_out.append(f"<div style='color:{color};font-weight:600;'>批量生成：{esc(msg)}</div>")
        self.statusBar().showMessage(msg, 8000)

    # ---------- 伏笔台账 ----------
    def _refresh_foreshadows(self, quiet=False):
        if not self.novel:
            return
        try:
            self.novel.extract_foreshadows()
            self.novel.resolve_foreshadows()
        except Exception as e:
            LOG.error(f"伏笔提取失败: {e}")
        self._fill_foreshadow_list()
        if not quiet:
            self.statusBar().showMessage("伏笔台账已更新", 3000)

    def _fill_foreshadow_list(self):
        self.fs_list.blockSignals(True)
        self.fs_list.clear()
        items = self.novel.load_foreshadows()
        stats = {"待回收": 0, "已回收": 0}
        max_ch = self.novel.chapters[-1]["num"] if self.novel.chapters else 0
        buried = []  # (age, item) 待回收按埋的章数降序排
        for it in items:
            st = it.get("status", "待回收")
            stats[st] = stats.get(st, 0) + 1
            ch = int(it.get("chapter", 0) or 0)
            age = max(0, max_ch - ch) if st == "待回收" else 0
            text = (it.get("text") or "").replace("\n", " ")
            if len(text) > 60:
                text = text[:60] + "…"
            if st == "待回收":
                buried.append((age, it, text))
            else:
                label = f"[第{ch}章] [已回收] {text}"
                item = QListWidgetItem(label)
                item.setForeground(QColor("#7ee787"))
                item.setData(Qt.ItemDataRole.UserRole, it)
                self.fs_list.addItem(item)
        buried.sort(key=lambda x: -x[0])
        for age, it, text in buried:
            ch = int(it.get("chapter", 0) or 0)
            age_txt = f"已埋{age}章" if age > 0 else "本章"
            if age >= int(APP_CONFIG.get("foreshadow_max_age", 8)):
                label = f"[第{ch}章] [待回收·{age_txt}·注意] {text}"
                item = QListWidgetItem(label)
                item.setForeground(QColor("#ff7b72"))      # 埋太久标红提醒回收
            else:
                label = f"[第{ch}章] [待回收·{age_txt}] {text}"
                item = QListWidgetItem(label)
                item.setForeground(QColor("#ffb86c"))
            item.setData(Qt.ItemDataRole.UserRole, it)
            self.fs_list.addItem(item)
        self.fs_list.blockSignals(False)
        self.lbl_fs_stats.setText(f"待回收 {stats.get('待回收', 0)} · 已回收 {stats.get('已回收', 0)} · 共 {len(items)}"
                                  "　（红色=伏笔埋太久，建议尽快回收）")

    def _mark_foreshadow_resolved(self):
        item = self.fs_list.currentItem()
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        if data.get("status") == "已回收":
            data["status"] = "待回收"
            data.pop("resolved_ch", None)
        else:
            data["status"] = "已回收"
        self.novel.save_foreshadows()
        self._fill_foreshadow_list()
        self.statusBar().showMessage(f"已标记：{data.get('status')}", 3000)

    def _delete_foreshadow(self):
        item = self.fs_list.currentItem()
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        if QMessageBox.question(self, "确认删除", "确定删除这条伏笔？") != QMessageBox.StandardButton.Yes:
            return
        self.novel.load_foreshadows()
        self.novel._foreshadows = [x for x in self.novel._foreshadows if x is not data]
        self.novel.save_foreshadows()
        self._fill_foreshadow_list()
        self.statusBar().showMessage("已删除该伏笔", 3000)

    def _jump_foreshadow_chapter(self, item):
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        num = data.get("chapter")
        if not isinstance(num, int):
            return
        if not any(c["num"] == num for c in self.novel.chapters):
            self.statusBar().showMessage(f"第{num}章尚未创建为真实章节", 4000)
            return
        self._save_current(force=False)
        self.left_tabs.setCurrentIndex(0)
        self._load_chapter(num)
        self.ch_list.blockSignals(True)
        for i in range(self.ch_list.count()):
            it = self.ch_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == num:
                self.ch_list.setCurrentRow(i)
                self.ch_list.scrollToItem(it)
                break
        self.ch_list.blockSignals(False)

    # ---------- 跨章检索 ----------
    def _do_search(self):
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        kw = self.search_inp.text().strip()
        self.search_list.blockSignals(True)
        self.search_list.clear()
        if not kw:
            self.lbl_search_stats.setText("请输入关键词")
            self.search_list.blockSignals(False)
            return
        results = self.novel.search_all(kw)
        for r in results:
            title = r["title"] or "未命名"
            label = f"第{r['num']}章 · {title}（命中 {r['count']} 处）"
            it = QListWidgetItem(label)
            it.setToolTip(r["snippet"])
            it.setData(Qt.ItemDataRole.UserRole, {"num": r["num"], "snippet": r["snippet"]})
            self.search_list.addItem(it)
        self.search_list.blockSignals(False)
        if results:
            self.lbl_search_stats.setText(f"「{kw}」命中 {len(results)} 章")
        else:
            self.lbl_search_stats.setText(f"「{kw}」全书无命中")

    def _jump_search_result(self, item):
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        num = data.get("num")
        if not isinstance(num, int):
            return
        self._load_chapter(num)
        # 定位关键词
        kw = self.search_inp.text().strip()
        if kw:
            cur = self.editor.textCursor()
            cur.setPosition(0)
            self.editor.setTextCursor(cur)
            if self.editor.find(kw):
                pass
            else:
                self.statusBar().showMessage(f"第{num}章未定位到关键词（可能在标题区）", 5000)

    # ---------- 人物卡片 ----------
    def _open_char_graph(self):
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        dlg = CharacterGraphDialog(self, self.novel)
        dlg.exec()

    def _refresh_characters(self, quiet=False):
        if not self.novel:
            return
        try:
            self.novel.ensure_characters()
        except Exception as e:
            LOG.error(f"人物卡刷新失败: {e}")
        self._fill_character_list()
        if not quiet:
            self.statusBar().showMessage("人物卡已加载", 3000)

    def _fill_character_list(self):
        self.char_list.blockSignals(True)
        self.char_list.clear()
        chars = self.novel.load_characters()
        for c in chars:
            name = (c.get("name") or "").strip() or "（无名）"
            bits = []
            if c.get("role"):
                bits.append(c["role"])
            if c.get("identity"):
                bits.append(c["identity"])
            label = name + (f" — {' · '.join(bits)[:40]}" if bits else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, c)
            self.char_list.addItem(item)
        self.char_list.blockSignals(False)
        self.lbl_char_stats.setText(f"共 {len(chars)} 位角色")

    def _add_character(self):
        dlg = CharacterEditDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.novel.load_characters()
            self.novel._characters.append(dlg.data())
            self.novel.save_characters()
            self._fill_character_list()
            self.statusBar().showMessage("已新增人物卡", 3000)

    def _edit_character(self, item=None):
        if item is None:
            item = self.char_list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先选中一个角色")
            return
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        dlg = CharacterEditDialog(self, data)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data.update(dlg.data())
            self.novel.save_characters()
            self._fill_character_list()
            self.statusBar().showMessage("已保存人物卡", 3000)

    def _delete_character(self):
        item = self.char_list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先选中一个角色")
            return
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        name = (data.get("name") or "").strip() or "该角色"
        if QMessageBox.question(self, "确认删除", f"确定删除人物卡「{name}」？") != QMessageBox.StandardButton.Yes:
            return
        self.novel.load_characters()
        self.novel._characters = [x for x in self.novel._characters if x is not data]
        self.novel.save_characters()
        self._fill_character_list()
        self.statusBar().showMessage("已删除人物卡", 3000)

    def _ai_generate_characters(self):
        if not self.novel:
            return
        if self._agent_thread and self._agent_thread.isRunning():
            return
        if not APP_CONFIG.get("api_key"):
            QMessageBox.warning(self, "未配置 AI", "请先在「设置」中填写 API Key 与模型，才能 AI 生成人物卡。")
            self._open_settings()
            return
        self.statusBar().showMessage("正在让 AI 整理/生成人物卡…")
        t = NovelAgentThread(self.novel, mode="characters", instruction="")
        t.done_signal.connect(self._on_characters_done)
        t.error_signal.connect(self._on_gen_error)
        t.state_signal.connect(lambda s: self.statusBar().showMessage(s))
        self._agent_thread = t
        t.start()

    def _on_characters_done(self, text):
        self._agent_thread = None
        self._fill_character_list()
        self.statusBar().showMessage("人物卡已更新，可在左侧「人物」查看", 6000)

    # ---------- 统计可视化 ----------
    def _open_stats(self):
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        dlg = StatsDialog(self.novel, self)
        dlg.exec()

    def _open_batch_polish(self):
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        dlg = BatchPolishDialog(self)
        dlg.exec()
        self._refresh_chapter_list()

    def _open_versions(self):
        if not self.novel or self.current_num is None:
            QMessageBox.information(self, "提示", "请先打开一章（当前章）再查看历史版本")
            return
        dlg = VersionsDialog(self, self.novel, self.current_num)
        dlg.exec()
        self._refresh_chapter_list()

    def _open_ref_library(self):
        dlg = ReferenceLibraryDialog(self)
        dlg.exec()

    def _open_web_ref(self):
        dlg = WebRefDialog(self)
        dlg.exec()

    # ---------- 逻辑优化 / 发散灵感 ----------
    def _launch_ai(self, mode, instruction, current_text, header):
        """通用 AI 线程启动：用于发散灵感/逻辑体检/逻辑修复等工具型模式。"""
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        if not APP_CONFIG.get("api_key"):
            QMessageBox.warning(self, "未配置 AI", "请先在「设置」中填写 API Key 与模型，才能使用 AI 创作。")
            self._open_settings()
            return
        self.btn_gen.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.btn_next.setEnabled(False)
        self.btn_fix.setEnabled(False)
        self._conflict_report = ""
        self._stream_buf = ""
        self.ai_out.clear()
        self.ai_out.append(f"<div style='color:#8ab4f8;font-weight:600;'>{esc(header)}</div>")
        self.ai_out.append("<div style='color:#9aa0ab;font-size:12px;'>正在分析…</div>")
        t = NovelAgentThread(self.novel, mode, instruction, current_text)
        t.chunk_signal.connect(self._on_chunk)
        t.state_signal.connect(lambda s: self.statusBar().showMessage(s))
        t.done_signal.connect(self._on_tool_done)
        t.error_signal.connect(self._on_gen_error)
        t.consistency_signal.connect(self._on_consistency)
        self._agent_thread = t
        t.start()

    def _start_diverge(self):
        """发散灵感：基于全书记忆让 AI 给出 5 个差异化剧情走向。"""
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        self._logic_mode = "diverge"
        ins = self.ai_inp.text().strip()
        self._launch_ai("diverge", ins, "", "发散灵感")

    def _start_stuck_help(self):
        """卡文助手：基于当前剧情与卡点给 3 个破局方向。"""
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        self._logic_mode = "stuck"
        cur = self.editor.toPlainText().strip()
        ins = self.ai_inp.text().strip() or "作者没说具体卡点"
        self._launch_ai("stuck_help", ins, cur, "卡文助手 · 破局方向")

    def _logic_optimize(self):
        """逻辑优化：对当前编辑器正文做逻辑体检，发现问题可一键让 AI 重写修复。"""
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        txt = self.editor.toPlainText().strip()
        if not txt:
            QMessageBox.information(self, "提示", "编辑器当前没有正文。请先打开/写入一章内容再体检。")
            return
        self._logic_src = txt
        self._logic_mode = "check"
        self._launch_ai("logic_check", "", txt, "逻辑优化 · 体检")

    def _on_tool_done(self, text):
        """发散灵感 / 逻辑体检 / 逻辑修复的完成处理。"""
        text = text or ""
        self.ai_out.clear()
        self.ai_out.setHtml(md_to_html(text))
        self.btn_gen.setEnabled(True)
        self.btn_apply.setEnabled(True)
        self.btn_next.setEnabled(True)
        self.btn_fix.setEnabled(True)
        mode = getattr(self, "_logic_mode", "")
        if mode == "diverge":
            self.ai_out.append(
                "<div style='color:#9aa0ab;font-size:12px;'>挑一个走向，把它的「核心事件」粘贴到上方要求框，再点「生成」即可按该走向续写。</div>")
        elif mode == "check":
            m = re.search(r"【逻辑问题(\d+)】", text)
            n = int(m.group(1)) if m else 0
            if n > 0:
                self._logic_report = text
                self._logic_mode = "fix_ready"
                self.ai_out.append(
                    f"<div style='color:#ff7b72;font-size:12px;'>发现 {n} 个逻辑问题。</div>")
                if QMessageBox.question(self, "逻辑优化",
                                        f"发现 {n} 个逻辑问题，是否让 AI 重写修复当前章节？") == QMessageBox.StandardButton.Yes:
                    self._logic_mode = "fix"
                    self._launch_ai("logic_fix", text, getattr(self, "_logic_src", ""), "逻辑优化 · AI 重写修复")
            else:
                self.statusBar().showMessage("逻辑体检：当前正文逻辑自洽", 6000)
        elif mode == "fix":
            # 修复完成：把新正文直接应用回编辑器（可 Ctrl+Z 撤销）
            self.editor.setPlainText(text)
            self.editor.document().setModified(True)
            self._dirty = True
            self.statusBar().showMessage("逻辑修复完成，已应用回当前章节编辑器（Ctrl+Z 可回退）", 6000)
            self._logic_mode = ""

    # ---------- 发布包 ----------
    def _export_publish(self):
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        title = self.novel.meta.get("title", self.novel.name)
        d = QFileDialog.getExistingDirectory(self, "选择投稿包保存目录", APP_DIR)
        if not d:
            return
        target = os.path.join(d, sanitize_filename(title) + "-投稿包")
        try:
            summary = self.novel.export_for_publish(target)
        except Exception as e:
            QMessageBox.warning(self, "生成失败", f"投稿包生成失败：{e}")
            return
        QMessageBox.information(self, "投稿包已生成",
                                f"已生成投稿包：{summary}\n目录：\n{target}\n"
                                "内含：投稿信息.txt（书名/简介/标签/大纲）+ 全文.txt")

    # ---------- 导入恢复 ----------
    def _import_novel(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入整本小说备份", APP_DIR, "整本备份 (*.zip)")
        if not path:
            return
        import zipfile
        tmp = os.path.join(NOVELS_DIR, "_import_tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            with zipfile.ZipFile(path) as z:
                z.extractall(tmp)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"无法解压备份：{e}")
            shutil.rmtree(tmp, ignore_errors=True)
            return
        meta_path = None
        for root, _dirs, files in os.walk(tmp):
            if "meta.yaml" in files:
                meta_path = os.path.join(root, "meta.yaml")
                break
        if not meta_path:
            QMessageBox.warning(self, "导入失败", "备份中未找到 meta.yaml，不是有效的整本备份。")
            shutil.rmtree(tmp, ignore_errors=True)
            return
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                title = (yaml.safe_load(f) or {}).get("title") or os.path.basename(os.path.dirname(meta_path))
        except Exception:
            title = os.path.basename(os.path.dirname(meta_path))
        final = os.path.join(NOVELS_DIR, sanitize_filename(title))
        if os.path.exists(final):
            resp = QMessageBox.question(self, "已存在同名小说",
                                        f"小说库已有《{title}》，是否覆盖？")
            if resp != QMessageBox.StandardButton.Yes:
                shutil.rmtree(tmp, ignore_errors=True)
                return
            shutil.rmtree(final, ignore_errors=True)
        try:
            shutil.move(os.path.dirname(meta_path), final)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"移动失败：{e}")
            shutil.rmtree(tmp, ignore_errors=True)
            return
        shutil.rmtree(tmp, ignore_errors=True)
        APP_CONFIG["last_novel"] = final
        APP_CONFIG["last_chapter"] = 1
        save_config(APP_CONFIG)
        self._load_novel_list()
        for i, n in enumerate(self._novels):
            if n.root == final:
                self.novel_combo.setCurrentIndex(i)
                self._open_novel(n)
                break
        self.statusBar().showMessage(f"已导入《{title}》", 5000)

    def _start_gen_outline(self):
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        if self._agent_thread and self._agent_thread.isRunning():
            return
        if not APP_CONFIG.get("api_key"):
            QMessageBox.warning(self, "未配置 AI", "请先在「设置」中填写 API Key 与模型，才能生成大纲。")
            self._open_settings()
            return
        req, ok = QInputDialog.getText(self, "AI 生成大纲", "大纲要求（可选，留空按常规网文节奏）：", text="")
        if not ok:
            return
        self.outline_detail.setPlainText("正在生成大纲…")
        t = NovelAgentThread(self.novel, mode="outline", instruction=req)
        t.done_signal.connect(self._on_outline_done)
        t.error_signal.connect(self._on_gen_error)
        t.state_signal.connect(lambda s: self.statusBar().showMessage(s))
        self._agent_thread = t
        t.start()

    def _on_outline_done(self, text):
        self._agent_thread = None
        self._refresh_outline_tab()
        self.outline_detail.setPlainText(text)
        self.statusBar().showMessage("大纲已生成", 5000)

    def _rename_novel(self):
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        old_root = self.novel.root
        old_title = self.novel.meta.get("title", self.novel.name)
        new_title, ok = QInputDialog.getText(self, "重命名小说", "新的书名：", text=old_title)
        if not ok or not new_title.strip():
            return
        new_title = new_title.strip()
        new_dir = os.path.join(NOVELS_DIR, sanitize_filename(new_title))
        if new_dir == old_root:
            # 仅改书名不改目录
            self.novel.update_meta(title=new_title)
            self._load_novel_list()
            self.statusBar().showMessage(f"书名已更新为《{new_title}》", 5000)
            return
        if os.path.exists(new_dir):
            QMessageBox.warning(self, "提示", "已存在同名小说，无法重命名")
            return
        try:
            os.rename(old_root, new_dir)
        except Exception as e:
            QMessageBox.warning(self, "重命名失败", f"无法重命名：{e}")
            return
        np_ = NovelProject(new_dir)
        np_.update_meta(title=new_title)
        APP_CONFIG["last_novel"] = new_dir
        save_config(APP_CONFIG)
        self._load_novel_list()
        for i, n in enumerate(self._novels):
            if n.root == new_dir:
                self.novel_combo.setCurrentIndex(i)
                self._open_novel(n)
                break
        self.statusBar().showMessage(f"已重命名为《{new_title}》", 5000)

    def _delete_novel(self):
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        name = self.novel.meta.get("title", self.novel.name)
        root = self.novel.root
        if not os.path.isdir(root):
            QMessageBox.warning(self, "提示", "小说目录不存在")
            return
        # 输入书名二次确认，防止误删
        typed, ok = QInputDialog.getText(
            self, "删除整个小说",
            f"此操作将永久删除《{name}》的全部章节与记忆，无法恢复。\n\n"
            f"目录：{root}\n\n请输入书名以确认：")
        if not ok:
            return
        if typed.strip() != name:
            QMessageBox.information(self, "已取消", "书名不匹配，未删除")
            return
        try:
            self._save_current(force=False)
        except Exception:
            pass
        shutil.rmtree(root, ignore_errors=True)
        if APP_CONFIG.get("last_novel") == root:
            APP_CONFIG["last_novel"] = ""
            APP_CONFIG["last_chapter"] = 0
            save_config(APP_CONFIG)
        self.novel = None
        self.current_num = None
        self._dirty = False
        self._load_novel_list()
        self.statusBar().showMessage(f"已删除《{name}》", 5000)

    # ---------- 记忆 ----------
    def _start_memory_update(self, mode, num=None):
        if self._mem_thread and self._mem_thread.isRunning():
            return
        t = NovelMemoryThread(self.novel, mode=mode, chapter_num=num)
        t.finished_ok.connect(self._on_memory_done)
        self._mem_thread = t
        t.start()

    def _on_memory_done(self, ok, msg):
        if ok:
            self.statusBar().showMessage(msg, 6000)
        else:
            self.statusBar().showMessage(msg, 8000)
        if self.novel:
            self._refresh_chapter_list()

    def _open_memory(self):
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        dlg = MemoryDialog(self, self.novel)
        dlg.exec()
        self._refresh_chapter_list()

    # ---------- AI 创作 ----------
    def _start_generate(self):
        if not self.novel:
            return
        if not APP_CONFIG.get("api_key"):
            QMessageBox.warning(self, "未配置 AI", "请先在「设置」中填写 API Key 与模型，才能使用 AI 创作。\n\n"
                                "（章节记忆摘要仍可在「全章节记忆」中使用本地提取方式生成）")
            self._open_settings()
            return
        mode = self.mode_combo.currentData()
        instruction = self.ai_inp.text().strip()
        current_text = self.editor.toPlainText()
        self.btn_gen.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.btn_next.setEnabled(False)
        self.btn_fix.setEnabled(False)
        self._conflict_report = ""
        self._stream_buf = ""
        self._stream_msg_id = None
        self.ai_out.clear()
        self.ai_out.append(f"<div style='color:#8ab4f8;font-weight:600;'>{MODE_LABELS.get(mode, mode)}</div>")
        self.ai_out.append("<div style='color:#9aa0ab;font-size:12px;'>正在装载全章节记忆并生成…</div>")
        trope = self.trope_combo.currentData() or ""
        if trope == "auto":
            trope = auto_trope(self.novel) or ""
            if trope:
                self.ai_out.append(f"<div style='color:#8ab4f8;font-size:12px;'>已自动套用套路：{TROPES[trope]['name']}</div>")
        elif trope:
            self.ai_out.append(f"<div style='color:#8ab4f8;font-size:12px;'>已套用套路：{TROPES[trope]['name']}</div>")

        t = NovelAgentThread(self.novel, mode, instruction, current_text, trope=trope)
        t.chunk_signal.connect(self._on_chunk)
        t.state_signal.connect(lambda s: self.statusBar().showMessage(s))
        t.done_signal.connect(self._on_gen_done)
        t.error_signal.connect(self._on_gen_error)
        t.consistency_signal.connect(self._on_consistency)
        self._agent_thread = t
        t.start()

    def _on_chunk(self, text):
        self._stream_buf += text
        # 简单流式：在输出区末尾追加
        cur = self.ai_out.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertText(text)
        self.ai_out.setTextCursor(cur)
        self.ai_out.ensureCursorVisible()

    def _on_gen_done(self, text):
        text = text or ""
        # 生成后自动去AI味：纯冗余连接词直接清除，其余 AI 高频词提示保留
        filler_hits = [w for w in AI_FILLERS if w in text]
        if filler_hits:
            cleaned = remove_fillers(text)
            if cleaned != text:
                text = cleaned
                self._stream_buf = text
        non_filler = [(w, c) for w, c in scan_ai_cliches(text) if w not in AI_FILLERS]
        self.ai_out.clear()
        self.ai_out.setHtml(md_to_html(text))
        self.btn_gen.setEnabled(True)
        self.btn_apply.setEnabled(True)
        self.btn_next.setEnabled(True)
        if filler_hits:
            self.ai_out.append(
                f"<div style='color:#7ee787;font-size:12px;'>已自动清除 {len(filler_hits)} 种纯冗余连接词"
                f"（{'、'.join(filler_hits)}）。如需原始版本，重新生成即可。</div>")
        if non_filler:
            top = "、".join(f"{w}×{c}" for w, c in non_filler[:8])
            self.ai_out.append(
                f"<div style='color:#ffb86c;font-size:12px;'>另有 {sum(c for _, c in non_filler)} 处 AI 高频词"
                f"（{top}）已保留，可点「去AI味」查看处理。</div>")
        minw = int(APP_CONFIG.get("novel_min_words", 2000) or 0)
        n = count_words(text)
        if minw and n < minw:
            self.statusBar().showMessage(f"生成完成：约 {n} 字（不足每章最少 {minw} 字，建议再次生成续写或扩写）", 8000)
            self.ai_out.append(
                f"<div style='color:#ffb86c;font-size:12px;'>本次生成约 {n} 字，未达每章最少 {minw} 字。"
                "可回到输入框填写“继续续写/扩写至 2000 字”再次生成，或先插入后再手动补充。</div>")
        else:
            self.statusBar().showMessage(f"生成完成：约 {n} 字，可插入编辑器或另存为下一章", 6000)

    def _on_consistency(self, report):
        """一致性体检发现冲突：红色警告 + 启用"修复冲突"。"""
        if not report:
            return
        self._conflict_report = report
        lines = [f"<div style='color:#ff7b72;font-weight:600;'>一致性体检：发现剧情冲突</div>",
                 f"<pre style='color:#ffa7a0;background:#3b1e1e;padding:8px;border-radius:6px;font-size:12px;'>{esc(report[:2000])}</pre>",
                 "<div style='color:#9aa0ab;font-size:12px;'>可点「修复冲突」让 AI 针对性修正后再插入。</div>"]
        self.ai_out.append("".join(lines))
        self.btn_fix.setEnabled(True)
        self.statusBar().showMessage("一致性体检发现冲突，可点「修复冲突」", 8000)

    def _start_fix_conflict(self):
        """根据一致性体检报告，让 AI 针对性重写修正矛盾。"""
        if not self.novel or not getattr(self, "_conflict_report", ""):
            return
        if not APP_CONFIG.get("api_key"):
            return
        text = getattr(self, "_stream_buf", "") or ""
        if not text.strip():
            return
        self.btn_gen.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.btn_next.setEnabled(False)
        self.btn_fix.setEnabled(False)
        self.ai_out.clear()
        self.ai_out.append("<div style='color:#8ab4f8;font-weight:600;'>修复冲突</div>")
        self.ai_out.append("<div style='color:#9aa0ab;font-size:12px;'>正在按冲突清单针对性重写…</div>")
        t = NovelAgentThread(self.novel, "fix_conflict", instruction=self._conflict_report,
                             current_text=text, trope=self.trope_combo.currentData() or "")
        t.chunk_signal.connect(self._on_chunk)
        t.state_signal.connect(lambda s: self.statusBar().showMessage(s))
        t.done_signal.connect(self._on_gen_done)
        t.error_signal.connect(self._on_gen_error)
        self._agent_thread = t
        t.start()

    def _on_gen_error(self, err):
        self.ai_out.append(f"<div style='color:#ff7b72;'>{esc(err)}</div>")
        self.btn_gen.setEnabled(True)
        self.statusBar().showMessage("生成失败", 6000)

    def _start_trope_judge(self):
        """套路判定：分析当前小说的套路结构、吸引力短板与 AI 腔。"""
        if not self.novel:
            return
        if getattr(self, "_agent_thread", None) and self._agent_thread.isRunning():
            return
        self.btn_gen.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.btn_next.setEnabled(False)
        self._stream_buf = ""
        self.ai_out.clear()
        self.ai_out.append("<div style='color:#8ab4f8;font-weight:600;'>套路判定</div>")
        self.ai_out.append("<div style='color:#9aa0ab;font-size:12px;'>正在分析套路结构、钩子密度、AI 腔与吸引力短板…</div>")
        t = NovelAgentThread(self.novel, "trope_judge")
        t.state_signal.connect(lambda s: self.statusBar().showMessage(s))
        t.done_signal.connect(self._on_gen_done)
        t.error_signal.connect(self._on_gen_error)
        self._agent_thread = t
        t.start()

    def _scan_ai_cliches(self):
        """一键去AI味：直接替换当前编辑器正文中的 AI 高频词为自然表达（可用 Ctrl+Z 回退）。"""
        text = self.editor.toPlainText()
        if not text.strip():
            self.statusBar().showMessage("编辑器当前为空，无内容可处理", 5000)
            return
        cleaned = de_ai_text(text)
        hits = scan_ai_cliches(text)
        n = sum(c for _, c in hits)
        if cleaned == text:
            self.ai_out.clear()
            self.ai_out.setHtml("<div style='color:#7ee787;font-weight:600;'>去AI味扫描：未发现明显 AI 高频词。</div>")
            self.statusBar().showMessage("未发现明显 AI 腔词汇", 5000)
            return
        # 直接替换进编辑器（用光标操作保留撤销栈，Ctrl+Z 可回退）
        cur = self.editor.textCursor()
        cur.select(QTextCursor.SelectionType.Document)
        cur.insertText(cleaned)
        self._mark_dirty()
        self._update_word_count()
        top = "、".join(f"{w}×{c}" for w, c in hits[:8])
        self.ai_out.clear()
        self.ai_out.setHtml(
            f"<div style='color:#7ee787;font-weight:600;'>已直接替换 <b>{n}</b> 处 AI 腔表达</div>"
            f"<div style='color:#9aa0ab;font-size:12px;'>命中：{top or '冗余连接词'}。"
            f"如需回退请按 Ctrl+Z。</div>")
        self.statusBar().showMessage(f"已替换 {n} 处 AI 腔表达（Ctrl+Z 可回退）", 5000)

    def _apply_to_editor(self):
        text = getattr(self, "_stream_buf", "") or self.ai_out.toPlainText()
        if not text.strip():
            return
        self.editor.setPlainText(text)
        self._mark_dirty()
        self._save_current()
        self.btn_fix.setEnabled(False)
        self.statusBar().showMessage("已插入编辑器并保存", 5000)

    def _save_as_next(self):
        text = getattr(self, "_stream_buf", "") or self.ai_out.toPlainText()
        if not text.strip():
            return
        if not self.novel:
            return
        title, ok = QInputDialog.getText(self, "另存为下一章", "章节标题：",
                                         text=f"第{self.novel.next_chapter_num()}章")
        if not ok:
            return
        title = title.strip() or f"第{self.novel.next_chapter_num()}章"
        num = self.novel.add_chapter(title, text.strip())
        # 立即生成该章记忆
        c = next((c for c in self.novel.chapters if c["num"] == num), None)
        if c:
            content = self.novel.load_chapter_content(c["path"])
            self.novel.set_summary(num, gen_chapter_summary(title, content))
            self.novel.save_memory()
        self._refresh_chapter_list()
        self._load_chapter(num)
        self._refresh_outline_tab()
        self.ai_out.clear()
        self._stream_buf = ""
        self.btn_apply.setEnabled(False)
        self.btn_next.setEnabled(False)
        self.btn_fix.setEnabled(False)
        self.statusBar().showMessage(f"已保存为第{num}章", 5000)

    # ---------- 小说管理 ----------
    def _new_novel(self):
        dlg = NovelMetaDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        d = dlg.data()
        title = d["title"]
        root = os.path.join(NOVELS_DIR, sanitize_filename(title))
        if os.path.exists(root):
            QMessageBox.warning(self, "提示", "已存在同名小说，已打开现有项目。")
            self._load_novel_list()
            return
        novel = NovelProject.create(root, **d)
        self._load_novel_list()
        for i, n in enumerate(self._novels):
            if n.root == novel.root:
                self.novel_combo.setCurrentIndex(i)
                break
        self._open_novel(novel)

    def _open_settings(self):
        dlg = SettingDialog(self)
        dlg.exec()

    def _export_novel(self):
        if not self.novel:
            QMessageBox.information(self, "提示", "请先选择一部小说")
            return
        title = self.novel.meta.get('title', self.novel.name)
        default = os.path.join(APP_DIR, f"{sanitize_filename(title)}")
        path, sel = QFileDialog.getSaveFileName(
            self, "导出全本", default,
            "Markdown (*.md);;电子书 EPUB (*.epub);;文本文件 (*.txt);;整本备份 (*.zip)")
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        if not ext:
            ext = ".md" if "Markdown" in sel else (".txt" if "文本" in sel else ".md")
            path += ext
        if ext == ".epub":
            ok = self.novel.export_epub(path)
            if ok:
                QMessageBox.information(self, "导出成功",
                                        f"已导出 EPUB 电子书：\n{path}\n（共 {len(self.novel.chapters)} 章）")
            else:
                QMessageBox.warning(self, "导出失败", "EPUB 导出失败，请查看日志。")
            return
        if ext == ".zip":
            ok = self.novel.pack_backup(path)
            if ok:
                QMessageBox.information(self, "备份成功",
                                        f"已整本备份（含章节+记忆+大纲+伏笔）：\n{path}")
            else:
                QMessageBox.warning(self, "备份失败", "整本备份失败，请查看日志。")
            return
        text = self.novel.export_all()
        # 询问是否附带记忆库
        include_mem = QMessageBox.question(
            self, "导出选项", "是否在文件末尾附带「全章节记忆库」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        out = text
        if include_mem:
            out += "\n\n---\n\n" + self.novel.load_memory()
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
        QMessageBox.information(self, "导出成功",
                                f"共 {len(self.novel.chapters)} 章，{sum(c['words'] for c in self.novel.chapters)} 字"
                                f"{'（含记忆库）' if include_mem else ''}。\n已保存：\n{path}")

    # ---------- 关闭 ----------
    def closeEvent(self, event):
        self._save_current(force=False)
        event.accept()


# ===================== 入口 =====================
if __name__ == "__main__":
    def _global_excepthook(exc_type, exc_value, exc_tb):
        import traceback
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        LOG.error(f"未捕获异常: {tb}")
        try:
            QMessageBox.critical(None, "程序异常",
                                 f"发生错误：\n{exc_type.__name__}: {exc_value}\n\n详细信息见日志")
        except Exception:
            pass
    sys.excepthook = _global_excepthook

    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NovelForge.App.1")
    except Exception:
        pass

    app = QApplication(sys.argv)
    _icon = _resource_path("app.ico")
    if os.path.exists(_icon):
        app.setWindowIcon(QIcon(_icon))

    win = NovelWorkspace()
    win.show()
    sys.exit(app.exec())
