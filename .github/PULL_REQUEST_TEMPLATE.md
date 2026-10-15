## 改动说明

<!-- 这个 PR 做了什么? 为什么要做? 关联的 issue / 动机是什么 -->

关联 issue: <!-- #123, 或 "无" -->

## 改动类型

- [ ] Bug 修复 (fix)
- [ ] 新功能 (feat)
- [ ] 重构 / 代码质量 (refactor, 不改变对外行为)
- [ ] 性能 / 体积优化 (perf)
- [ ] 文档 / 注释 (docs)
- [ ] 构建 / CI / 依赖 (build / ci)
- [ ] 其他: <!-- 请说明 -->

## 影响范围

- [ ] 主程序 (`app/`, `services/`, `main.py`)
- [ ] 网关 (`gateway/`)
- [ ] 桌宠 (`desktop-pet-electron/`)
- [ ] 安装 / 启动脚本 (`install.bat`, `start.bat`)
- [ ] CI / 打包 (`.github/`, `LoveAI.spec`)
- [ ] 文档

## 自测清单

- [ ] 我在本地 (Windows) 跑过 `.\.venv\Scripts\python.exe -m pytest -m "not slow"`
- [ ] 涉及网关改动时, 我手动在 :8787 验证过接口 (例如 `/v1/models`、`/admin/stats/summary`)
- [ ] 涉及桌宠改动时, 我手动 `npm run build:win` / `npm start` 跑过
- [ ] 涉及 UI 改动时, 我截图确认过渲染正常
- [ ] 我没有把 `data/`、`models_local/`、`.env`、API Key 等私有/大文件提交进仓库

## 文档 / CHANGELOG

- [ ] 本 PR 需要更新 `CHANGELOG.md` (已更新)
- [ ] 本 PR 需要更新 README / QUICKSTART (已更新或无影响)
- [ ] 本 PR 不需要改文档

## 备注

<!-- 任何 reviewer 需要知道的事: 已知遗留问题、兼容性提示、需要人肉点一下的地方 -->
