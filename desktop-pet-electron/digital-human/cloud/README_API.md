# 云端数字人产物轨 —— 接入说明（v2，2026-10-11）

> 本目录是「实时数字人 = 双轨·默认本地」架构的**云端轨**。
> 本地 LivePortrait 是默认路径；本模块仅在用户勾选「云端数字人」时启用。
> **fail-closed 铁律已写进代码逻辑，不是文档承诺。**

---

## 1. v2 变更：移除 D-ID，改为豆包大模型直接生成

**旧实现（已移除）**：D-ID API（https://api.d-id.com）—— 第三方付费服务，
需要国际信用卡注册、照片音频上传到海外服务器，且中国大陆支付宝/微信支付不支持。

**新实现（当前）**：**豆包 Seedance 图生视频产物轨** —— 大模型直接做：
- 生成动作由豆包完成（`doubao-creative-video` skill 的 General「照片动起来」，
  用母版照片生成 5 秒说话/动态数字人视频，模型 `seedance 2.0 fast`，最省额度档）。
- 产物是一个 mp4 文件，放到 `config.json` 的 `product_path`（默认
  `desktop-pet-electron/digital-human/cloud/output/dh_talk.mp4`）。
- 桌宠 cloud 模式说话时，主进程 `dh-manager.js` 调 `client.py verify --video <产物>` 做
  fail-closed 校验，通过则播放，失败则回落 2D 口型。

**优势**：无外部密钥、无第三方 API、无计费、无数据出境；生成属于豆包会员额度
（每次约 1 条 5 秒视频）。

## 2. 使用步骤

1. 用豆包图生视频能力生成母版数字人视频（照片动起来，5 秒，mp4）。
2. 把产物保存为 `product_path` 指定路径（默认 `cloud/output/dh_talk.mp4`）。
3. 编辑本目录 `config.json` 把 `enabled` 改为 `true`。
4. 桌宠设置面板勾选「实时数字人」→「云端数字人」，保存后重启桌宠生效。
5. 桌宠说话时校验产物并播放；产物缺失/损坏时自动回落 2D（气泡提示「数字人生成失败，已切回 2D」）。

## 3. CLI 契约

```
<.venv>\Scripts\python.exe cloud\client.py verify --video <产物mp4>
→ stdout 末行 JSON：
   {"ok":true,"video_path":"...","bytes":N}          成功
   {"ok":false,"code":"...","reason":"..."}          失败（fail-closed）
```

## 4. 失败模式表（fail-closed 语义）

| code | 触发条件 | 行为 |
|---|---|---|
| `disabled` | config.enabled != true | 立即返回，不做任何产物读取 |
| `no_source` | 产物路径未配置 / 文件不存在 | 立即返回，不尝试播放 |
| `bad_video` | 文件 <1KB 或非 mp4（无 ISO BMFF box 头） | 立即返回，不尝试播放 |
| `verify_error` | 校验过程 IO 异常 | 立即返回 |

产物有效但桌宠渲染异常时由渲染进程回落 2D，不阻塞正常对话播放。

## 5. 验证记录

见同目录 `TESTLOG_failclosed.md`（四场景实测，2026-10-11，东莞，Windows，venv Python 3.14.7）。
