# fail-closed 测试证据（v2 产物轨，2026-10-11，东莞，Windows，venv Python 3.14.7）

> v2 变更：client.py 由 D-ID API 客户端改为「豆包 Seedance 产物校验器」。
> 本测试为替换后的四场景真实实测，全部无网络请求、无密钥、纯标准库。

测试命令：
```
& "<PROJECT_ROOT>\.venv\Scripts\python.exe" ^
  "<PROJECT_ROOT>\desktop-pet-electron\digital-human\cloud\client.py" ^
  verify --video <产物路径>
```

## TEST 1: enabled=false（默认配置）→ disabled，立即返回
- 输入：--video C:\nonexistent.mp4
- exit=1, elapsed≈50ms（同批冒烟；下同量级）
- stdout: `{"ok": false, "code": "disabled", "reason": "cloud digital-human disabled in config.json (enabled != true)"}`
- 结论：✅ 未启用时不读取任何产物，不 crash 不挂起

## TEST 2: enabled=true + 产物不存在 → no_source，立即返回
- 输入：--video C:\nonexistent_product.mp4
- exit=1, elapsed=124ms
- stdout: `{"ok": false, "code": "no_source", "reason": "product not found: C:\\nonexistent_product.mp4"}`
- 结论：✅ 产物缺失时 fail-closed，不尝试播放

## TEST 3: enabled=true + 假产物（master.png 改名 fake.mp4）→ bad_video
- 输入：--video <cloud>\test_out\fake.mp4（实际为 PNG 字节）
- exit=1, elapsed=126ms
- stdout: `{"ok": false, "code": "bad_video", "reason": "product is not a valid mp4 (no ISO BMFF box header): ...\\fake.mp4"}`
- 结论：✅ ISO BMFF box 头校验识破伪装，不尝试播放

## TEST 4: enabled=true + 真产物（本地 LivePortrait demo.mp4）→ ok
- 输入：--video <digital-human>\local\out\demo.mp4（321,217B，真实可解码 mp4）
- exit=0, elapsed=122ms
- stdout: `{"ok": true, "video_path": "C:\\...\\local\\out\\demo.mp4", "bytes": 321217}`
- 结论：✅ 合法 mp4 产物通过校验，返回可播放路径

## 端到端真实生成状态
- ✅ fail-closed 全路径（TEST 1-4）实测通过，替换后的产物轨行为与文档一致。
- ⏳ **豆包 Seedance 产物尚未真实生成**（生成动作属豆包会员额度消耗，需用户确认后
  通过 doubao-creative-video 流程执行）；生成后放到 product_path 并置 enabled=true 即可端到端验证。
- 无任何第三方 API / 密钥 / 计费字段残留（可 grep config.json / client.py 确认无 "api_key"/"did" 等）。
