# LoveAI 推广材料（可直接复制使用）

> 仓库地址：https://github.com/wobuzhidaoqvshememingle/LoveAI
> 一句话：**把现实恋爱变成一场可经营、可复盘的小游戏。**

---

## 一、30 秒版（朋友圈 / 微信群 / 即刻）

我做了一个东西：**LoveAI — 地球Online 恋爱小游戏**。

它不是聊天机器人，也不是数字女友。
它读你自己的聊天记录，用心理学 + 大模型给你出一份"人物档案"：
对方是什么人、你们走到哪一步、哪里在升温、哪里在崩、下一句该怎么说。

桌面上有个常驻的小精灵，是分析助手，不是恋爱对象。
**主打增强现实——最终目的是让你和真实的人处得更好。**

Windows 本地跑，数据不出本机，已开源。
🔗 https://github.com/wobuzhidaoqvshememingle/LoveAI

---

## 二、长文版（V2EX / 知乎 / 少数派 / 掘金）

### 标题备选
- 《我把恋爱做成了一款"地球Online"小游戏，数据全在本地》
- 《一个本地优先的 AI 关系洞察工具：不做数字女友，只做恋爱陪练》
- 《我用 18 维好感度引擎 + 关系星图，把自己的人际关系复盘了一遍》

### 正文

**先说定位，避免误会。**

市面上"AI 恋人"很多，我不想再做第 101 个。LoveAI 的定位恰好相反：

> 桌宠和数字分身是**分析助手与预测工具**，不是恋爱对象。
> 一切分析最终都**回归现实**——帮你和真实的人建立更好的互动。

**它做什么**

1. **读聊天记录，出人物档案**
   支持微信 PC 聊天记录导入（也支持纯文本 / JSON / CSV），本地解析，不依赖微信密钥、不上传云端。生成"CHARACTER FILE"：性格倾向、依恋类型、关系阶段、危险信号。

2. **18 维好感度引擎 + 关系星图**
   每一维都由函数实时计算——不是"说了句情话 +5 分"。
   情绪、回应速度、主动投入、承诺兑现、边界感……综合成一张会动的星图。

3. **游戏化任务与勋章**
   把"该怎么推进关系"拆成可执行的分步任务，完成就推进、翻车就扣分，像经营游戏一样有正反馈。

4. **数字分身：先预测，再校准**
   让 AI 先预测对方会怎么回，然后拿真实回复对比，滚动修正模型。这才是"数字恋人"该干的活——**预测，而不是替代**。

5. **多模型聚合网关**
   自带 OpenAI 兼容网关（FastAPI），聚合多家厂商；四级降级链：付费云端 → 免费通道 → 本地 GGUF → 本地小模型 → 规则兜底。断网也能跑。

6. **桌宠是"系统小精灵"**
   Electron + Live2D，或内置全开源 Qt3D 程序化骨骼。透明置顶、会眨眼会呼吸、气泡说话。

**技术栈**

Python 3.11 / PySide6 / FastAPI / SQLCipher / FTS5 + Chroma RAG / Electron + Live2D / PyInstaller

**隐私**

本地优先。聊天库 SQLCipher 加密，一键老板键，默认不上传任何数据。
仓库里的所有演示数据都是占位符或合成数据。

**现状（如实说）**

v3.1.0 已发布，Windows 安装包可直接下载（3.14GB 超 GitHub 单文件上限，拆成 4 个分卷挂在 Release 上）。
测试基线 **1823 passed / 4 skipped / 0 failed**。

🔗 仓库：https://github.com/wobuzhidaoqvshememingle/LoveAI
🔗 安装包：https://github.com/wobuzhidaoqvshememingle/LoveAI/releases/tag/v3.1.0

---

## 三、英文版（Reddit / Hacker News / X）

**LoveAI — a local-first AI relationship coach that plays your love life as a game**

Not another AI girlfriend. The desktop pet is an *analyst*, not a partner — the whole point is to help you do better with **real people**.

**What it does**
- Imports your WeChat / text chat logs **locally** (no cloud, no keys), builds a "CHARACTER FILE" of the other person: attachment style, relationship stage, red flags
- **18-dimension affection engine** — every dimension is computed by a function, not a LLM vibe check
- Gamified relationship tasks + medals — treat dating like a management game
- **Digital twin** that predicts their reply, then recalibrates against what they actually said
- Multi-provider OpenAI-compatible gateway with a 4-level fallback chain (paid → free → local GGUF → local small model → rules)
- Desktop pet (Electron + Live2D, or built-in Qt3D), always-on-top, transparent

**Stack**: Python 3.11 · PySide6 · FastAPI · SQLCipher · FTS5+Chroma RAG · Electron/Live2D · PyInstaller
**Privacy**: local-first, encrypted DB, nothing uploaded by default.
**Status**: v3.1.0 released. Windows installer available for download (3.14 GB, split into 4 volumes — GitHub's per-file limit is 2 GiB). Test baseline: 1823 passed / 4 skipped / 0 failed.

🔗 https://github.com/wobuzhidaoqvshememingle/LoveAI
🔗 Download: https://github.com/wobuzhidaoqvshememingle/LoveAI/releases/tag/v3.1.0

---

## 四、渠道建议（按性价比排序）

| 渠道 | 适合版本 | 注意 |
|---|---|---|
| **V2EX / 创意工作者节点** | 长文版 | 标题别太营销，讲技术取舍更容易上热门 |
| **即刻** | 30 秒版 | 配桌宠截图，@相关圈子 |
| **少数派** | 长文版 | 强调"本地优先 + 隐私" |
| **掘金 / CSDN** | 长文版 | 偏技术：网关降级链、RAG 双引擎、Live2D 接入 |
| **小红书** | 30 秒版 | 换女性视角文案，图为主 |
| **Reddit r/SideProject · r/LocalLLaMA** | 英文版 | LocalLLaMA 对"本地降级链"极感兴趣 |
| **Hacker News (Show HN)** | 英文版 | 标题用 "Show HN: ..." |
| **X / Twitter** | 英文版 | 配一段 30 秒桌宠演示视频效果最好 |

## 五、注意事项（重要）

1. **"读微信聊天记录"是敏感点**。所有渠道都必须在开头讲清楚：**本地解析、不上传、不依赖密钥**，否则容易被误读成盗号工具。
2. **别用"数字女友""AI恋人"做标题**，会引来错误受众，也违背产品定位。
3. **安装包分发已解决**：3.14GB 超 GitHub Release 单文件 2GiB 上限，已用 7-Zip 分卷（4 卷）挂到 [Release v3.1.0](https://github.com/wobuzhidaoqvshememingle/LoveAI/releases/tag/v3.1.0)。宣传时可直接给下载链接，用户解压 `.001` 即可。
4. **GitHub Topics 没设上**（页面 React 组件在无头环境下点不开）。手动补一下能明显提升搜索曝光，建议填：`ai` `llm` `relationship` `local-first` `wechat` `psychology` `desktop-pet` `live2d` `pyside6` `electron` `rag`
5. **订阅代理凭据**（订阅链接里的密码/UUID）曾经公开暴露过一次，建议去服务商处重置。

---

## 六、宣传素材位置

- 演示截图 10 张：仓库 `docs/screenshots/`
- 仓库首页：https://github.com/wobuzhidaoqvshememingle/LoveAI
- Release v3.1.0（含安装包）：https://github.com/wobuzhidaoqvshememingle/LoveAI/releases/tag/v3.1.0
- Release v3.0.0（源码快照）：https://github.com/wobuzhidaoqvshememingle/LoveAI/releases/tag/v3.0.0
