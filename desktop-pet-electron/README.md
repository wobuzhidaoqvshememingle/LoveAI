# LoveAI 桌宠（Electron 版）

系统精灵桌面宠物（关系分析助手）：常驻桌面、透明背景、Live2D 立绘、会说话会有表情，通过 WebSocket 与 LoveAI-v2 主程序联动。

> 定位：它是「地球online恋爱小游戏」里的**系统精灵**——引导、分析、陪伴、反馈，帮你推进现实恋爱主线（看任务/进度/成就、给游戏化反馈），**不是恋爱对象，也不是陪练**；数字分身只是预测工具，一切最终回归到你与真实的人互动。

## 技术栈

| 模块 | 选型 | 版本 |
|------|------|------|
| 桌面容器 | Electron | ^30.5.1 |
| 渲染引擎 | PixiJS | ^7.4.2 |
| 角色模型 | pixi-live2d-display（Live2D Cubism moc3） | ^0.4.0 |
| 通信 | WebSocket（连 LoveAI 主程序，默认端口 9876） | - |
| 设置 | JSON（存 userData/settings.json） | - |
| 打包 | electron-builder | ^24.13.3 |

## 目录结构

```
desktop-pet-electron/
├── package.json
├── .gitignore
├── README.md
├── electron/                  # 主进程（Node.js 侧）
│   ├── main.js                # 入口：创建窗口、生命周期
│   ├── preload.js             # 上下文桥接（window.petAPI）
│   ├── settings-store.js      # JSON 设置持久化
│   ├── tray.js                # 系统托盘及右键菜单
│   ├── ipc.js                 # IPC 处理器（拖拽/设置/窗口控制）
│   └── auto-launch.js         # 开机启动（app.setLoginItemSettings）
├── renderer/                  # 渲染进程（另一模块负责）
│   └── index.html
└── assets/                    # 图标等资源
    └── tray-icon.png
```

## 安装与运行

```bash
# 1. 安装依赖
npm install

# 2. 开发运行（自动打开 DevTools）
npm run dev

# 3. 正常启动
npm start
```

启动后角色会出现在屏幕右下角，透明背景、置顶显示。

## 打包

```bash
# 打包当前平台
npm run build

# 专门打包 Windows exe
npm run build:win
```

产物在 `dist/` 目录，生成 NSIS 安装包。

## 主进程暴露给渲染进程的 API（window.petAPI）

```js
// 设置
await window.petAPI.getSettings()                 // 获取完整设置对象
await window.petAPI.setSetting('pet.scale', 0.2)  // 点路径写入并持久化
window.petAPI.onSettingsChanged((settings) => {}) // 监听设置变化

// 窗口控制
window.petAPI.dragWindow(dx, dy)       // 拖拽窗口（相对位移）
window.petAPI.setWindowPosition(x, y) // 设置绝对位置
window.petAPI.setAlwaysOnTop(true)     // 置顶开关
window.petAPI.setOpacity(0.8)         // 透明度 0.1~1.0

// 托盘命令回调
window.petAPI.onTrayCommand((cmd) => {
  // cmd: 'toggle-settings' | 'next-character' | 'quit'
})

// 应用信息
window.petAPI.getVersion()  // 版本号
window.petAPI.platform      // 'win32' / 'darwin' ...
window.petAPI.isDev         // 是否开发模式
```

## 默认设置（settings.json）

```json
{
  "window": { "width": 300, "height": 420, "opacity": 1.0, "alwaysOnTop": true, "x": null, "y": null },
  "pet": { "scale": 0.15, "currentModel": "haru", "volume": 0.8, "clickReaction": true },
  "ws": { "url": "ws://localhost:9876", "enabled": true, "reconnectInterval": 3000 },
  "behavior": { "affection": 50, "mood": 50, "satiety": 50, "idleInterval": 8000, "randomTalkInterval": 30000 },
  "system": { "autoLaunch": false, "startMinimized": false }
}
```

设置文件位置：`%APPDATA%/loveai-desktop-pet/settings.json`（Windows）。

## 托盘菜单

- 显示/隐藏宠物
- 切换角色（发 `next-character` 给渲染进程）
- 设置（发 `toggle-settings` 给渲染进程）
- 开机启动（checkbox）
- 退出

## 与 LoveAI 主程序联动

桌宠通过 WebSocket 连接 LoveAI-v2 主程序（默认 `ws://localhost:9876`），接收 AI 事件实时驱动角色动作：

- AI 思考中 → 角色切换「思考」动作
- AI 回复完成 → 角色切换「说话/开心」动作，并在头顶气泡显示回复内容

桌宠只是 UI 层形象展示，不影响 LoveAI 后台策略引擎。

## 窗口规格

- 300 × 420，无边框、透明、置顶、不可拉伸、不进任务栏
- Windows 下 `backgroundColor: '#00000000'`
- 初始位置：屏幕右下角（或上次拖拽保存的位置）
- 关闭所有窗口不退出，仅托盘「退出」才退出
