# LoveAI-v2 桌宠视觉升级说明

## 解决了什么问题

1. **"模型躲在角落只看到几根头发"**：原代码硬编码 `scale=0.15` + `position=(150,410)`，但 Haru Live2D 模型的自然尺寸是 ~2000+ px，按 0.15 缩放后坐标计算和锚点不匹配，导致模型偏移到画布外，只剩头顶。
2. **"没真人感"**：原默认角色是静态 PNG（cybergf），不会动。现在默认角色切换为 Haru Live2D（Cubism 4），有完整骨骼、物理摆动、自动眨眼、口型同步。
3. **"跟视频里完全不一样"**：沉浸式模式原本只在右侧贴一张静态 PNG。现在沉浸式模式用同一个 Live2D 模型，全屏放大偏右显示，左侧留给语音球和字幕，对齐"天工开帧"赛博小雅的布局。

## 改了哪些文件

| 文件 | 改动 |
|---|---|
| `config/characters.json` | `current` 从 `cybergf` 改为 `haru`，默认加载真 Live2D |
| `electron/settings-store.js` | 默认窗口尺寸 300x420 → 400x600 |
| `electron/ipc.js` | 退出沉浸式时恢复的角落窗口尺寸同步为 400x600 |
| `renderer/js/model-loader.js` | 重写：加载后读取模型自然尺寸、按视口高度自适应缩放、水平居中/偏右、底部对齐；表情映射到 Haru 的 F01~F08；口型参数改为 `ParamMouthOpenY`（原代码用的 `MouthOpen` 根本不存在）；加呼吸相位驱动 |
| `renderer/js/app.js` | PIXI 画布初始化 400x600；沉浸式切换时 resize PIXI 并重新适配模型；窗口 resize 监听；暴露 `window.__character` 方便调试 |
| `renderer/js/image-character.js` | 暴露 `_maskGraphics` 供重新适配 |
| `renderer/js/atmosphere.js` | 粒子活动范围 300x420 改为动态传入的视口尺寸 |
| `renderer/css/style.css` | `#stage` 400x600；沉浸式模式下 PIXI 画布铺满窗口（不再 display:none）；静态立绘 `.immersive-portrait` 默认隐藏；补了 `.hidden` 规则修复沉浸式控件在角落模式误显示 |

所有原文件同目录下都有 `.bak` 备份。

## 动画与接线

- **呼吸**：`model-loader.update(dt)` 每帧推进正弦相位，y 方向 ±3px 浮动（~2.8 秒一个周期）。
- **眨眼**：Haru 模型自带 `EyeBlink` 参数组（ParamEyeLOpen/ROpen），pixi-live2d-display 自动播放，不需要手动驱动。
- **表情**：`setExpression('idle'|'happy'|'surprised'|'thinking'|'sleeping'|'sad'|'angry'|'shy')` 映射到 F01~F08。WS `emotion` 事件、状态机切换都会触发。
- **口型**：`setMouthOpen(v)` 同时写 `ParamMouthOpenY` 和 `MouthOpen`（兼容）；`audio-player.js` 的实时振幅驱动（原接线保留）。
- **歪头/晃动**：`updateIdleBehaviors` 已有的歪头和左右晃动代码保留，现在通过 `setRotation` / `setOffsetX` 正确叠加在 Live2D 上。
- **WS 联动**：`ws-client.js` / `state-machine.js` / `interaction.js` / `settings-panel.js` 未改动，原有事件通路全部保留。

## 怎么验证

1. 启动：`npm start`（或 `npx electron electron/main.js`）
2. 角落模式：右下角 400x600 透明窗，Haru 从头到脚完整居中可见，会呼吸、会眨眼。
3. 双击画布：切换沉浸式模式，窗口全屏，Haru 放大到 ~70% 视口高度偏右站，左侧是语音球，底部字幕条。
4. 截图脚本（开发用）：
   - `node screenshot.js` → `screenshot.png`（角落模式）
   - `node screenshot-immersive.js` → `screenshot-immersive.png`（沉浸式）

## 已知问题

1. **设置面板里的"模型大小"slider**：现在读的是 `petScale`，默认值 1.0；如果用户之前 settings.json 里存过 0.15 这种旧值，会被当成"0.15/0.15=1.0"处理，等于不缩放。首次运行会用新默认值 400x600，老用户需在设置里重置一次大小。
2. **沉浸式模型底部**：底部字幕条会稍微盖住鞋尖，已通过 `bottomMargin:80` 留空间。
3. **Haru 表情映射**：F01~F08 的具体情绪是按 Cubism 官方样例的常见顺序猜的（F02=开心、F04=惊讶、F08=困倦等），如果实际 Haru.exp3.json 的情绪顺序不同，表情会错位但不会崩。
4. **WS 后端没起时**：`ws://localhost:9876` 连不上会自动重连，不影响桌宠本身渲染。
5. **透明窗口**：截图脚本为了看清模型用了不透明底色，实际运行时角落窗口是透明的。
