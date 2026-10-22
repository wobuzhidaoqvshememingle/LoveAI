# 微信密钥获取 · DLL 控制器法 · 移植说明书

> 来源：千问缓存对话（2026-10 用户花约 200 元 API 跑通）→ 对应开源工程 `ycccccccy/wx_key`（MIT）→ 源码已在本机 `%USERPROFILE%\Doubao\chats\2026-10-04\new-chat-28\wx_key_src\wx_key-main\`
> 本文件用途：把「怎么拿到微信密钥」这个方法本体固化下来，供 LoveAI-v2 直接移植成产品内一键流程。
> 落盘时间：2026-10-17

---

## 〇、一句话结论（推翻此前错误判断）

**新版 `wx_key.dll` 不是「注入到微信进程里的 DLL」，而是「跑在你自己进程里的控制器 DLL」。**

它导出 5 个标准 C 函数，**Python 用 ctypes 直接就能调**，不需要：
- ❌ 自己写注入器（CreateRemoteThread / LoadLibraryA）
- ❌ 自动化操作 `wx_key.exe` 那个 Flutter GUI
- ❌ 按微信版本准备一堆不同的 DLL

**此前「本机微信 4.1.15.13 与 DLL 适配的 4.1.2.11 不匹配，是个硬障碍」的判断——错误，作废。**

> ⚠️ **2026-10-17 实机更正（原句保留，便于追溯）**：上面这句「作废」**本身已被推翻**。
> 版本不匹配**确实是硬障碍**。实测：`>=4.1.4` 档特征码在 `F:\Weixin\4.1.15.13\Weixin.dll`
> （201,552,944 B）上 **0 命中**（完整扫过 192.2 MB）；实机 `InitializeHook` 报「模式匹配失败，找到 0 个结果」。
> 原判断方向正确，只是理由表述不准：不是「适配 4.1.2.11 的 DLL 与 4.1.15.13 不匹配」，
> 而是**上游特征码库覆盖不到 4.1.15.13**。
> **正确结论与全部证据：`docs/微信版本适配与已知限制.md` §3。**

---

## 一、已本机验证的事实（不是推断）

对 `LoveAI-v2\tools\wx_key\wx_key.dll` 与 `wx_key_src\wx_key-main\assets\dll\wx_key.dll` 做 PE 导出表二进制解析，结果：

| 项 | 值 |
| --- | --- |
| 文件大小 | 131,072 B |
| SHA256（前 16 位） | `482f02ac9fbdf9c3` |
| Machine | `0x8664` = **x64** |
| PE Magic | `0x20b` = PE32+ |
| 导出函数数 | 5 |
| 导出名单 | `InitializeHook` / `PollKeyData` / `GetStatusMessage` / `CleanupHook` / `GetLastErrorMsg` |
| 两份文件关系 | **SHA 完全一致 = 同一文件** |

→ **工程内那份 DLL 就是新版控制器 DLL，能力完整。**

验证脚本：`%USERPROFILE%\Doubao\chats\2026-10-04\new-chat-28\_verify_wxkey_api.py`

---

## 二、DLL 导出 API（C 风格，Cdecl 调用约定）

声明头文件在 `wx_key-main\wx_key\include\hook_controller.h`。

| 函数 | 签名 | 说明 |
| --- | --- | --- |
| `InitializeHook` | `bool InitializeHook(DWORD targetPid)` | **启动入口**。执行远程扫描、分配共享内存、注入 Shellcode。成功 true；失败调 `GetLastErrorMsg`。**PID 由调用方自己找，DLL 不枚举进程。** |
| `PollKeyData` | `bool PollKeyData(char* keyBuf, int size)` | **取密钥**。非阻塞。抓到就把 32 字节密钥格式化成 **64 位 HEX 字符串**写入缓冲区并返回 true。**读一次后自动清空。** |
| `GetStatusMessage` | `bool GetStatusMessage(char* msgBuf, int size, int* outLevel)` | **取 DLL 内部日志**（"扫描成功"/"特征码未找到"等）。`outLevel`：`0=Info, 1=Success, 2=Error`。可循环取多条。 |
| `CleanupHook` | `bool CleanupHook()` | **清理资源**。卸载远程 Hook、释放共享内存、关句柄。**退出前必须调，否则残留 Shellcode 会让微信崩。** |
| `GetLastErrorMsg` | `const char* GetLastErrorMsg()` | 错误诊断，返回最近一次失败原因。 |

### 共享内存结构（DLL 内部，供理解）

```c
typedef struct {
    DWORD dataSize;        // 固定 32
    BYTE  keyBuffer[32];   // 32 字节密钥
    DWORD sequenceNumber;  // 每次写入 +1，用于去重
} SharedKeyData;
```

Shellcode 仅在 `dataSize == 32` 时写入并递增 `sequenceNumber`；上层可持有上次 `sequenceNumber` 做去重。

---

## 三、工作原理（为什么不需要硬编码 RVA）

1. **调用方**自己找到 `Weixin.exe` 的 PID，加载 `wx_key.dll` 到**当前进程**，调 `InitializeHook(pid)`。
2. **DLL 内部**用 `RemoteScanner`（`src/remote_scanner.cpp`）**远程扫描微信进程内存**，用**特征码**匹配定位「密钥获取函数」入口。
3. 定位成功后分配**共享内存**，写入一段 **x64 Shellcode** 做 Hook（不是 MinHook 那种 IAT/inline hook，是远程 shellcode + VEH）。
4. 微信尝试读数据库时，Shellcode 拦截 **32 字节密钥**，拷进共享内存，`sequenceNumber++`。
5. **宿主**每 ~100ms 轮询 `PollKeyData` 拿密钥；同一循环里用 `GetStatusMessage` 拿进度日志。
6. 结束调 `CleanupHook()`。

内存读取走 `IndirectSyscalls::NtReadVirtualMemory`（间接系统调用），字符串有 `string_obfuscator`，属反检测设计——这些是 DLL 内部实现，移植时**不必重写**，直接用现成 DLL 即可。

---

## 四、版本适配机制（关键，之前的担心就在这消解）

`remote_scanner.cpp` 的 `VersionConfigManager::InitializeConfigs()` 里**只有两套特征码**：

| 适用范围 | Pattern（24 字节） | Mask | 偏移 |
| --- | --- | --- | --- |
| `>= 4.1.4`（含 4.1.4.x / 4.1.5.x / …） | `24 08 48 89 6C 24 10 48 89 74 00 18 48 89 7C 00 20 41 56 48 83 EC 50 41` | `xxxxxxxxxx?xxxx?xxxxxxxx` | **−3** |
| `< 4.1.4`（4.1.0–4.1.3 与 4.0.x） | `24 50 48 C7 45 00 FE FF FF FF 44 89 CF 44 89 C3 49 89 D6 48 89 CE 48 89` | `xxxxxxxxxxxxxxxxxxxxxxxx` | **−0xF** |

分界点是 **4.1.4**（version 数组比较，baseline `{4,1,4,0}`）。

**本机微信 4.1.15.13 → 走第一套 `>=4.1.4` → 现有 DLL 直接支持。**

> ⚠️ **2026-10-17 实机更正：本句已被实测证伪（保留原句，便于追溯）**。
> 分档确实走第一套 `>=4.1.4`（`4.1.15.13 ≥ 4.1.4`，分档没错），
> 但**该档特征码在 4.1.15.13 上 0 命中**（完整扫过 192.2 MB，无 `MemoryError`），实机 `InitializeHook` 同样失败。
> **正确结论：现有 `wx_key.dll` 在 4.1.15.13 上取不到密钥。**
> 证据：`…\agents\s_000Ed2kCsxc\artifacts\evidence_79_scan_signature.txt`；
> 三条替代路线的拍板状态（定稿＝只走缓存路线）：`docs/微信版本适配与已知限制.md` §4/§5。

README 亦写明「支持所有微信 4.x 版本」，已实测版本含 4.1.5.11 / 4.1.4.17 / 4.1.4.15 / 4.1.2.18 / 4.1.2.17 / 4.1.0.30 / 4.0.5.17。

> ⚠️ **2026-10-17 更正**：上游这句「支持所有 4.x」**与实机不符**——本机 4.1.15.13 实测 **0 命中**。
> 另：《微信密钥接入方法总表.md》L67 记的已适配列表（4.1.2.17 / … / 4.1.0.34）与本行的列表口径不一致，
> 两份都不含 4.1.15.13。**以 `微信版本适配与已知限制.md` §3 的实测口径为准。**

> **小版本变动不用管**；只有**大版本内部结构变了**（如 4.1.2 → 4.1.4）才需要更新特征码库并重编 DLL。
> 特征码生成工具：`wx_key-main\findbyrva.cpp` —— 用 IDA 确认某个版本的函数偏移后，跑它可**自动生成唯一特征码**（内部按 `E8/E9` 相对跳转、`70-7F` 短跳、`0F 8x` 条件跳转做掩码，并用 `SafeReadMemory` + SEH 防崩）。用法注释写得很清楚：**只有大版本变动才需要走一次这个流程**。

---

## 五、微信版本号怎么可靠地读（此前踩过注册表坑）

**不要用注册表 `Version` 值**（此前实测不可靠解码）。有两条可靠路径：

### 路径 A（推荐，取自微信进程内模块的文件版本资源）

`RemoteScanner::GetWeChatVersion()`：
1. 在微信进程里枚举模块，找到 `Weixin.dll`
2. `GetModuleFileNameExW` 取该模块的**磁盘文件路径**
3. `GetFileVersionInfoSizeW` → `GetFileVersionInfoW` → `VerQueryValueW(\\)` → `VS_FIXEDFILEINFO`
4. 组装 `major.minor.build.revision`

**这是从「正在运行的那个 Weixin.dll」本身读的，最准。**

### 路径 B（备用，扫安装目录）

`dll_injector.dart::getWeChatVersion()`：在微信安装目录下用正则 `^4\.\d+\.\d+\.\d+$` 找版本号子目录。

---

## 六、微信生命周期编排（用户点名的「关微信 → 重启 → 扫码/代点登录」）

`wx_key-main\lib\services\dll_injector.dart` 里**已有完整参考实现**，逐条列出供移植：

| 能力 | 参考实现 | 要点 |
| --- | --- | --- |
| 找 PID | `findProcessIds('Weixin.exe')` | `EnumProcesses` → `OpenProcess(QUERY_INFORMATION\|VM_READ)` → `GetModuleBaseName` 比对进程名 |
| 是否在跑 | `isProcessRunning('Weixin.exe')` | 同上，非空即在跑 |
| **关闭微信** | `killWeChatProcesses()` | `OpenProcess(PROCESS_TERMINATE)` → `TerminateProcess` → 关句柄；**等 1 秒** |
| **启动微信** | `launchWeChat()` | 先解析微信目录，再 `Process.start(..., mode: detached)`，**等 2 秒**，再确认进程在跑 |
| 找微信目录 | `getWeChatDirectory()` | 三级：①用户手设目录（`KeyStorage`）②注册表（Uninstall 子键扫 `wechat/weixin/tencent` → `InstallLocation`/`InstallPath`/`DisplayIcon`/`UninstallString`/`InstallDir`；`App Paths\WeChat.exe|Weixin.exe`；`Software\Tencent\WeChat|Weixin`）③盘符遍历 `C/D/E/F` + `\Program Files\Tencent\Weixin\Weixin.exe` 等常见路径 |
| 等主窗口 | `waitForWeChatWindow(maxWaitSeconds=10)` | 每 500ms 查一次主 PID |
| **等界面组件加载完** | `waitForWeChatWindowComponents(maxWaitSeconds=25)` | 每 500ms：找主 PID → `EnumWindows` 收集窗口 → `EnumChildWindows` 收子窗口 → 判定就绪 |
| 找主 PID（**传给 InitializeHook 的那个**） | `findMainWeChatPid()` | `EnumWindows` 里挑**窗口标题含「微信」或「Weixin」**的窗口，取其 PID |

### 界面「就绪」判据（`_hasReadyComponents`，三级放宽）

1. **关键文本**：子窗口标题含 `聊天` / `登录` / `账号` 之一
2. **关键类名**：子窗口类名含 `WeChat` / `Weixin` / `TXGuiFoundation` / `Qt5` / `ChatList` / `MainWnd` / `BrowserWnd` / `ListView` 之一
3. **计数兜底**：类名匹配 ≥3，或标题匹配 ≥2，或**子窗口数 ≥ 14**，或「有效类名且子窗口数 ≥5」
4. 默认**视为可注入**（宁可放宽，不阻塞流程）

> 「登录」这个标记就是**用户扫码那一步的窗口**——扫完码进主界面才会出现「聊天」标记。**所以「等组件就绪」天然等价于「等用户完成登录」。**
> 若要「代点登录」，再补一步 UI Automation 找微信窗口里的登录/进入按钮点它即可；不点也不影响，用户手动扫即可。

---

## 七、Python ctypes 调用骨架（供移植直接用）

```python
# -*- coding: utf-8 -*-
"""wx_key.dll 控制器调用骨架 —— LoveAI-v2 移植用"""
import ctypes
from ctypes import c_uint32, c_bool, c_int, c_char_p, POINTER, byref

DLL_PATH = r"...\LoveAI-v2\tools\wx_key\wx_key.dll"   # 路径不能含中文！

class WeChatKeyController:
    """控制器 DLL 封装：加载 → InitializeHook → 轮询 PollKeyData → CleanupHook"""

    def __init__(self, dll_path: str = DLL_PATH):
        self._dll = ctypes.WinDLL(dll_path)          # Cdecl
        d = self._dll
        d.InitializeHook.argtypes  = [c_uint32]
        d.InitializeHook.restype   = c_bool
        d.PollKeyData.argtypes     = [c_char_p, c_int]
        d.PollKeyData.restype      = c_bool
        d.GetStatusMessage.argtypes = [c_char_p, c_int, POINTER(c_int)]
        d.GetStatusMessage.restype  = c_bool
        d.CleanupHook.argtypes     = []
        d.CleanupHook.restype      = c_bool
        d.GetLastErrorMsg.argtypes = []
        d.GetLastErrorMsg.restype  = c_char_p
        self._inited = False

    def last_error(self) -> str:
        try:
            b = self._dll.GetLastErrorMsg()
            return b.decode("utf-8", "ignore") if b else ""
        except Exception:
            return ""

    def initialize(self, pid: int) -> bool:
        ok = bool(self._dll.InitializeHook(pid))
        self._inited = ok
        return ok

    def poll_key(self) -> str | None:
        buf = ctypes.create_string_buffer(128)        # 给 128，防踩踏（文档：至少 65）
        if self._dll.PollKeyData(buf, 128):
            s = buf.value.decode("ascii", "ignore").strip()
            return s or None
        return None

    def drain_status(self, max_items: int = 8):
        """取 DLL 内部日志，产出 (level, msg) 列表；level 0=Info 1=Success 2=Error"""
        out = []
        for _ in range(max_items):
            buf = ctypes.create_string_buffer(512)
            lvl = c_int(0)
            if not self._dll.GetStatusMessage(buf, 512, byref(lvl)):
                break
            out.append((lvl.value, buf.value.decode("utf-8", "ignore")))
        return out

    def cleanup(self):
        if self._inited:
            try:
                self._dll.CleanupHook()
            finally:
                self._inited = False
```

**调用顺序（严格照此，否则会崩）**：

```
找 PID → WinDLL(DLL_PATH) → InitializeHook(pid)
      → 循环 { PollKeyData / GetStatusMessage }（建议 100ms 间隔）
      → 拿到密钥 → CleanupHook()  ← 必须
```

---

## 八、避坑清单（原文 + 本机注意事项）

1. **必须调 `CleanupHook()`** —— 不清理，残留 Shellcode 会在微信后续运行时导致**崩溃**。（→ 意味着新版架构下**「抓完微信就崩」不是必然**，干净卸载即可）
2. **可能需要管理员权限** —— 远程操作微信进程，`InitializeHook` 失败先试提权。
3. **仅 x64** —— Shellcode 是 x64 汇编；必须是 64 位系统 + 64 位微信。（本机 DLL machine=`0x8664` ✓）
4. **DLL 所在路径不能含中文** —— README 明写，否则 DLL 加载失败。（`wx_key-main` 路径本身是英文，但要注意别把工具放进中文目录）
5. **缓冲区 ≥65 字节，建议 128** —— 返回的是 HEX 字符串 + 结束符，分配小了会内存踩踏。
6. **单例原则** —— 同一个微信进程只能 Hook 一次；要重扫必须先 `CleanupHook` 再 `InitializeHook`。
7. **不要在 UI 线程死循环轮询** —— 后台线程 / 定时器，间隔 ~100ms。
8. **`PollKeyData` 读后即清空** —— 需要去重就用 `sequenceNumber`（上层自持上次值）。
9. **`GetStatusMessage` 循环取直到返回 false** —— 一次可能积压多条日志。

---

## 九、图片密钥（另一条独立流程）

`README` 的「图片密钥获取建议流程」+ `lib/services/image_key_service.dart`：

1. **彻底关闭**当前登录的微信
2. **重新启动**微信并登录
3. 打开**朋友圈**，找带图片的
4. 点图片 → 再点右上角**打开大图**
5. 重复 3–4 约 **2–3 次**
6. **迅速回到工具**内获取图片密钥

> 这一步和数据库密钥是**两套**：数据库密钥靠 Hook「读库函数」，图片密钥靠**触发图片解密路径**后抓。
> 用户说的「关微信 → 重启 → 让用户扫码」正是本流程第 1–2 步的通用化表述。

---

## 十、移植到 LoveAI-v2 的对接点

### 完整一条龙（用户口径：拿密钥 → 解密 → 入库）

| # | 环节 | 移植方式 | 落点 |
| --- | --- | --- | --- |
| 1 | 检测微信 + 定版本 | 路径 A（Weixin.dll 文件版本）或路径 B（目录正则） | 新增 / `wechat_oneclick.py` |
| 2 | 关闭微信 | `TerminateProcess`，参考 `killWeChatProcesses()` | 同上 |
| 3 | 重启微信 | `Process.start(weixin.exe, detached)` + 等 2s，参考 `launchWeChat()` | 同上 |
| 4 | 前台化 / 出二维码 | 找主窗口 `SetForegroundWindow`（登录窗标题含「登录」，见第六节判据 2.1） | 同上 |
| 5 | 扫码 / 代点登录 | 等「聊天」标记出现 = 登录完成；如需代点，补 UI Automation | 同上 |
| 6 | 取密钥 | **ctypes 调 `InitializeHook(pid)` + 轮询 `PollKeyData`**（第七节骨架） | 新模块，建议 `services/wechat_key_controller.py` |
| 7 | 密钥落库 | 复用既有 `wechat_key_extractor.import_key_plain()` 全局加密缓存 | 已有 |
| 8 | 解密数据库 | 复用既有 `wechat_db_decryptor.py`（PBKDF2-HMAC-SHA512, salt=前 16 字节, 256000 迭代, dklen=32） | 已有 |
| 9 | 导入入库 | 复用既有 `wechat_oneclick.collect_messages()` | 已有（冒烟通过：52 库 / 2 账号） |

**缺口仍是 1–6；7–9 已通，不要推倒重做。**

### 两条路径收敛

- **已有密钥的账号**：跳过 1–7，走 8 → 9
- **无密钥的账号**：走完 1–7 拿到密钥，再 8 → 9
- 用户侧都只看到：**点一次「导入」→ 完成**

### 需要产品内做的一次知情确认

注入类动作在产品界面弹一次显式确认，写清：**将向微信进程写入 Hook 以拦截密钥；流程结束后会自动卸载；若中途异常退出，微信可能需要重启**。用户确认后全程自动，不再让用户碰命令行。

---

## 十一、明确不用做的事（省掉此前规划的两条弯路）

- ❌ **不用自己写注入器** —— DLL 的 `InitializeHook(pid)` 就是注入入口。
- ❌ **不用自动化驱动 `wx_key.exe`（Flutter GUI）** —— 那是可有可无的壳，Python 直接加载 DLL 即可。
- ❌ **不用按微信版本准备多个 DLL** —— 两套特征码覆盖全部 4.x。
- ❌ **不用读注册表 Version 判断版本** —— 走 Weixin.dll 文件版本或安装目录。
- ❌ **不用怕「抓完必崩」** —— 正常 `CleanupHook()` 即可干净退出。

---

## 十二、仍未验证 / 待实测（诚实标注）

- 上述机制来自**源码与二进制导出表**，**尚未在本机对真实微信进程跑过一次完整 `InitializeHook`**。首次实机验证要做：
  1. 微信处于**已登录**状态
  2. 以**管理员**身份加载 DLL
  3. `findMainWeChatPid()` 拿到的 PID 传入 `InitializeHook`
  4. 检查 `GetStatusMessage` 是否报「扫描成功」还是「特征码未找到」
  5. **无论成败都必须 `CleanupHook()`**
- 若报「特征码未找到」：说明 4.1.15.13 的函数内部结构相对 4.1.4 基线有变，需用 `findbyrva.cpp` 走一次特征码重生成流程（IDA 定偏移 → 跑工具 → 更新 `remote_scanner.cpp` → 重编 DLL）。
- 本机是否存在**管理员权限**未确认。
- 微信进程是 `Weixin.exe` 还是 `WeChat.exe` 需按实际（本机 4.1.15.13 时代应为 `Weixin.exe`，DLL 内部找的是 `Weixin.dll` 模块）。

---

## 附：本机相关路径与哈希

| 项 | 路径 / 值 |
| --- | --- |
| 工程内 DLL | `LoveAI-v2\tools\wx_key\wx_key.dll`（131,072 B / `482f02ac…`） |
| 源码副本 | `...\new-chat-28\wx_key_src\wx_key-main\assets\dll\wx_key.dll`（同一文件） |
| DLL 源码 | `...\wx_key_src\wx_key-main\wx_key\`（`dllmain.cpp` / `src\` / `include\`） |
| 特征码定义 | `...\wx_key-main\wx_key\src\remote_scanner.cpp`（`VersionConfigManager`） |
| 特征码生成工具 | `...\wx_key-main\findbyrva.cpp` |
| API 文档 | `...\wx_key-main\docs\dll_usage.md` |
| 前端参考实现 | `...\wx_key-main\lib\services\remote_hook_controller.dart`（Dart FFI 轮询） |
| 进程/窗口控制参考 | `...\wx_key-main\lib\services\dll_injector.dart` |
| Flutter GUI 壳（可弃用） | `LoveAI-v2\tools\wx_key\wx_key.exe`（91,648 B） |
| 导出的校验脚本 | `...\new-chat-28\_verify_wxkey_api.py` |
| 校验输出 | `...\new-chat-28\_verify_wxkey_api_out.txt` |
