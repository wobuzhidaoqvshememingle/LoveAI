# -*- mode: python ; coding: utf-8 -*-
# ruff: noqa
"""
LoveAI v3.0 PyInstaller 打包配置（onedir）—— 原生QWidget桌面端。

【封包基础设施】本 spec 通过环境变量开关决定是否把「可选随包组件」打入：

    LOVEAI_BUNDLE_MODELS=1     打入 models_local/（gguf+onnx+HF基座+lora，约1.67GB）
    LOVEAI_BUNDLE_OLLAMA=1     打入 dist_bundle/payload/ollama_runtime/（便携Ollama，约72MB）
    LOVEAI_BUNDLE_KNOWLEDGE=1  打入 data/unified_search.db + data/chroma_db（预置知识库，约317MB）
    LOVEAI_BUNDLE_VOICE=1      预留：打入 voice/ 与语音运行时隐藏导入（离线权重缺失时为空操作）

四个开关默认全部关闭 → 产出「精简核心版」：仅核心程序 + Qt + 只读资源，
体积最小；可选组件由 Inno 安装器（tools/installer/LoveAI.iss）按勾选物理分发。
终端用户无需自装 Python / Ollama / pip。

=====================================================================
 两种可执行构建命令（项目根目录、venv 激活后）
=====================================================================
【A. 精简核心版】（默认，组件走安装器勾选）：
    pyinstaller LoveAI.spec --noconfirm
    产物：dist/LoveAI/LoveAI.exe + dist/LoveAI/_internal/

【B. 全组件版】（单文件夹自包含，模型/Ollama/知识库全部打入）：
    PowerShell:
      $env:LOVEAI_BUNDLE_MODELS="1"; $env:LOVEAI_BUNDLE_OLLAMA="1";
      $env:LOVEAI_BUNDLE_KNOWLEDGE="1"; $env:LOVEAI_BUNDLE_VOICE="1";
      pyinstaller LoveAI.spec --noconfirm
    CMD:
      set LOVEAI_BUNDLE_MODELS=1 && set LOVEAI_BUNDLE_OLLAMA=1 && set LOVEAI_BUNDLE_KNOWLEDGE=1 && set LOVEAI_BUNDLE_VOICE=1 && pyinstaller LoveAI.spec --noconfirm

注意：本阶段（引擎修复合入前）**不要**用本 spec 打真实 main.py；
最终封包由组织者在引擎合入后统一触发。本文件已加固完毕待用。
=====================================================================
"""
import os
from PyInstaller.utils.hooks import collect_submodules, collect_all

ROOT = os.path.abspath(os.getcwd())

# ----------------------------------------------------------------------
# 浏览器 AI：playwright（含 node driver）+ greenlet 全量收集，保证冻结后
# `import playwright` 可直接用；浏览器二进制另由安装器释放 + PLAYWRIGHT_BROWSERS_PATH。
# ----------------------------------------------------------------------
_extra_datas = []
_extra_binaries = []
_extra_hidden = []
for _pkg in ('playwright', 'greenlet'):
    try:
        d, b, h = collect_all(_pkg)
        _extra_datas += d
        _extra_binaries += b
        _extra_hidden += h
        print(f"[LoveAI.spec] collect_all({_pkg}): {len(d)} datas, "
              f"{len(b)} binaries, {len(h)} hidden")
    except Exception as e:  # noqa: BLE001
        print(f"[LoveAI.spec] collect_all({_pkg}) 失败(忽略): {e}")

# ----------------------------------------------------------------------
# 组件开关（环境变量）
# ----------------------------------------------------------------------
def _on(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "on")

BUNDLE_MODELS = _on("LOVEAI_BUNDLE_MODELS")
BUNDLE_OLLAMA = _on("LOVEAI_BUNDLE_OLLAMA")
BUNDLE_KNOWLEDGE = _on("LOVEAI_BUNDLE_KNOWLEDGE")
BUNDLE_VOICE = _on("LOVEAI_BUNDLE_VOICE")

print(f"[LoveAI.spec] 组件开关: MODELS={BUNDLE_MODELS} OLLAMA={BUNDLE_OLLAMA} "
      f"KNOWLEDGE={BUNDLE_KNOWLEDGE} VOICE={BUNDLE_VOICE}")

# ----------------------------------------------------------------------
# 数据文件：(源路径, 打包后相对目录)
# 核心只读资源永远打入；可选组件按开关追加。
# ----------------------------------------------------------------------
datas = [
    ('resources', 'resources'),
    ('styles', 'styles'),
    ('prompts', 'prompts'),
    ('skills', 'skills'),
    ('plugins', 'plugins'),
    ('assets', 'assets'),
    ('knowledge', 'knowledge'),
    ('config.json', '.'),
    # 3D 桌宠资源（float_ball.py 通过 QWebEngineView 加载）
    ('ui/pet_3d.html', 'ui'),
    ('ui/js/three.min.js', os.path.join('ui', 'js')),
    ('ui/js/GLTFLoader.js', os.path.join('ui', 'js')),
]
# playwright/greenlet 的数据（含 node driver）
datas += _extra_datas

# R3.3-76：Electron 桌宠运行资源（win-unpacked 整目录）随主包分发。
# 冻结态下 PyInstaller onedir 会落到 _internal\desktop-pet-electron\dist\win-unpacked；
# pet_launcher._find_packaged_exe() 同时兜底 _internal 与 {app} 两处查找。
# Inno 安装器另会物理分发到 {app}\desktop-pet-electron（首选路径）。
_pet_unpacked = os.path.join(ROOT, 'desktop-pet-electron', 'dist', 'win-unpacked')
if os.path.isdir(_pet_unpacked):
    datas.append((_pet_unpacked, os.path.join('desktop-pet-electron', 'dist', 'win-unpacked')))
    print("[LoveAI.spec]   + Electron 桌宠运行资源 desktop-pet-electron/dist/win-unpacked")
else:
    print("[LoveAI.spec]   ! 未找到 desktop-pet-electron/dist/win-unpacked，"
          "请先在 desktop-pet-electron 跑 npm run build:win")

# 可选：预置知识库（data/unified_search.db、data/chroma_db）
if BUNDLE_KNOWLEDGE:
    _f = os.path.join(ROOT, 'data', 'unified_search.db')
    if os.path.exists(_f):
        datas.append((_f, 'data'))
    _c = os.path.join(ROOT, 'data', 'chroma_db')
    if os.path.isdir(_c):
        datas.append((_c, os.path.join('data', 'chroma_db')))
    print("[LoveAI.spec]   + 预置知识库 data/unified_search.db, data/chroma_db")

# 可选：本地大模型 models_local/（含 gguf / onnx / HF 基座 / lora）
if BUNDLE_MODELS and os.path.isdir(os.path.join(ROOT, 'models_local')):
    datas.append((os.path.join(ROOT, 'models_local'), 'models_local'))
    print("[LoveAI.spec]   + models_local/（本地大模型，约1.67GB）")

# 可选：便携 Ollama 运行时（先经 scripts/collect_payload.py 归集）
if BUNDLE_OLLAMA:
    _oll = os.path.join(ROOT, 'dist_bundle', 'payload', 'ollama_runtime')
    if os.path.isdir(_oll):
        datas.append((_oll, 'ollama_runtime'))
        print("[LoveAI.spec]   + ollama_runtime/（便携Ollama，约72MB）")
    else:
        print("[LoveAI.spec]   ! 未找到 dist_bundle/payload/ollama_runtime，"
              "请先运行 scripts/collect_payload.py")

# 可选：语音组件骨架（权重离线缺失，预留）
if BUNDLE_VOICE:
    _v = os.path.join(ROOT, 'dist_bundle', 'payload', 'voice')
    if os.path.isdir(_v):
        datas.append((_v, 'voice'))
        print("[LoveAI.spec]   + voice/（语音骨架）")

# ----------------------------------------------------------------------
# 隐式导入：服务/应用/模型全部子模块（含新增的 services.runtime_paths）
# ----------------------------------------------------------------------
hiddenimports = []
hiddenimports += collect_submodules('services')
hiddenimports += collect_submodules('app')
hiddenimports += collect_submodules('models')
hiddenimports += _extra_hidden  # playwright/greenlet 隐藏导入

# Qt3D 模块（3D骨骼桌宠）
hiddenimports += [
    'PySide6.Qt3DCore',
    'PySide6.Qt3DRender',
    'PySide6.Qt3DExtras',
    'PySide6.Qt3DAnimation',
    'PySide6.Qt3DInput',
    'PySide6.Qt3DLogic',
]
# QtWebEngine 模块（3D 桌宠 QWebEngineView 加载 pet_3d.html）
hiddenimports += [
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebChannel',
]
hiddenimports += ['unicodedata']

# L1 本地模型运行时（onnxruntime + tokenizers 已在 venv，确保被收集）
hiddenimports += ['onnxruntime', 'tokenizers']

# 语音组件（可选）：仅当对应库实际安装时才加入，避免 PyInstaller 报错
if BUNDLE_VOICE:
    for _m in ('faster_whisper', 'pyaudio', 'sounddevice'):
        try:
            __import__(_m)
            hiddenimports.append(_m)
        except ImportError:
            print(f"[LoveAI.spec]   ! 语音运行时 {_m} 未安装，跳过隐藏导入（离线预期）")

# ----------------------------------------------------------------------
# 排除：明确不需要的模块（减小体积、避免误用旧桥接）
# 核对：不排除 onnxruntime/tokenizers（L1 需要）；llama_cpp 离线未装故保持排除，
#       本地 L2 推理走随包便携 Ollama（HTTP），不依赖 llama_cpp。
# ----------------------------------------------------------------------
excludes = [
    'pywebview',        # 技术硬约束：禁止 pywebview
    'pytest',
    'tkinter',
    'matplotlib',
    'IPython',
    'jupyter',
    'llama_cpp',        # 未安装；本地推理走便携 Ollama，不打 llama.cpp
    # 语音运行时默认不打（离线缺失；BUNDLE_VOICE=1 且已装时再由上方显式收集）
    'faster_whisper',
    'pyaudio',
]
# sounddevice 已装且 speech_service 用它录音；仅在未开启语音时排除
if not BUNDLE_VOICE:
    excludes.append('sounddevice')

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[ROOT],
    binaries=_extra_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LoveAI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,             # 桌面应用，不显示控制台
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=(os.path.join(ROOT, 'assets', 'icons', 'app.ico')
          if os.path.exists(os.path.join(ROOT, 'assets', 'icons', 'app.ico'))
          else None),
)

# 排除系统已有的 ucrtbase.dll（避免杀毒软件锁定导致封包失败）
binaries = [(dst, src, typ) for (dst, src, typ) in a.binaries if 'ucrtbase.dll' not in dst.lower()]

coll = COLLECT(
    exe,
    binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='LoveAI',
)
