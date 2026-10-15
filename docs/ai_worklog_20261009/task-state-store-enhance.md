# task-state · 预建便携模型仓库接入（2026-10-09 晚）

## 结论
- 硬链接 preseed blob 是**死路**：`ollama create` 必走 llama-quantize 校验并另建 canonical blob（仍拷约1.5GB），预置 raw blob 被忽略。
- 500 真根因 = **内存**：`failed to allocate buffer of size 485861888 (~463MB)`。源于手动 `Stop-Process ollama`（杀父不杀子）遗留 llama-server（2个占1.4GB）。清理后空闲2.95GB，就绪仓库 HTTP 200 中文连贯。
- 应用关闭无此问题：application.py L326-362 taskkill /PID /T /F 整树（父存活时）回收 llama-server。

## 已做
- 预建仓库：`E:\LoveAI_build\portable_models\models`（便携 runtime create，删未用 raw blob 后 8文件/1534.2MB；15b canonical=098cb、05b=969a）。
- collect_payload：+PORTABLE_STORE_SRC、collect_portable_store；collect_models 在仓库存在时排除两个原始 GGUF（源文件不删）。
- payload：junction `dist_bundle\payload\portable_models` -> E 仓库；移除 payload models_local 两个 gguf 硬链接（源保留）。logical total 仍 3954MB（净零）。
- LoveAI.iss：+portable_models 释放到 {app}\ollama_data\models（Components models, onlyifdoesntexist）；Dirs 补 models。
- gate4 v2：tools\gate4_terminal_verify_v2.ps1（装到 E:\LoveAI_gate4；查就绪仓库/启动不新增blob/关闭 ollama+llama-server 双归零）。

## 完成（最终）
- ISCC 1356.3s：E:\LoveAI_build\installer\LoveAI_Setup_v2.0.0.exe=2839.3MB 单文件；已替换回 installer_output\LoveAI_Setup_v2.0.0.exe。
- gate4 v2 干净通过：物理释放12/14（2 False 为设计移除的原始GGUF）、便携路径确证、两模型启动即现、NEW blobs=0（未create）、中文HTTP200无乱码、关闭 LoveAI/ollama/llama-server 全0。
- 收口报告：docs\封包收口与第四闸实证报告.md。任务全部完成。
