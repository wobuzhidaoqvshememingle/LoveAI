# docs/packaged_refs —— 打包版（LoveAI_gate4）参照物落点说明

- 源：`<E盘>\LoveAI_gate4\_internal\prompts`、`<E盘>\LoveAI_gate4\_internal\plugins`
- 比对时间：2026-10-17
- 方法：逐文件 SHA-256 + 体量比对（非目录盲覆盖）
- 详细条目见：`_inventory/融入记录_补充融入.md`（第七节）

## 结论：**包内 14 个文件全部判定为「工程已有」，补入 0 个**

### 1. prompts（6 个文件）

| 包内文件 | 包内体量(B) | 包内 SHA-256(前16) | 工程对应文件 | 工程体量(B) | 工程 SHA-256(前16) | 判定 |
|---|---:|---|---|---:|---|---|
| chat_analysis.txt | 4418 | C90C0C97A41F032E | prompts\chat_analysis.txt | 5612 | 002218105C86CFC2 | 不一致；包内版 = 工程 `chat_analysis.txt.bak_20261010`（SHA-256 完全一致）→ **跳过** |
| digital_twin.txt | 424 | 71C4B39C501289D3 | prompts\digital_twin.txt | 424 | 71C4B39C501289D3 | 完全一致 → **跳过** |
| emotion_consult.txt | 479 | 9973B65D37BB510B | prompts\emotion_consult.txt | 479 | 9973B65D37BB510B | 完全一致 → **跳过** |
| profile_generation.txt | 1140 | B1BCDFA32F359113 | prompts\profile_generation.txt | 2891 | 5A91ABF4D81C12C5 | 不一致；包内版 = 工程 `profile_generation.txt.bak_20261010`（完全一致）→ **跳过** |
| reply_style.txt | 507 | C312B3BE6B8887EE | prompts\reply_style.txt | 1516 | ECF781383596FC57 | 不一致；包内版 = 工程 `reply_style.txt.bak_20261010`（完全一致）→ **跳过** |
| task_generation.txt | 1077 | 30C2A49F72AFB682 | prompts\task_generation.txt | 1077 | 30C2A49F72AFB682 | 完全一致 → **跳过** |

判读：打包版属于 **2026-10-10 之前的旧快照**——其 `chat_analysis / profile_generation / reply_style` 三份与工程留存的 `.bak_20261010` 字节完全一致，而工程现行版本均已扩写更新（体量更大）。即包内 6 份提示词**均已存在于工程中**（3 份为现行主文件、3 份为工程自带的历史备份），故无需在 `docs\packaged_refs\prompts\` 另存副本。

### 2. plugins（8 个文件：README + 3 插件）

| 包内文件 | 体量(B) | 工程 `plugins\` 对应文件 | 判定 |
|---|---:|---|---|
| README.md | 1923 | plugins\README.md | SHA-256 一致 → 跳过 |
| custom_analysis\manifest.json | 161 | plugins\custom_analysis\manifest.json | SHA-256 一致 → 跳过 |
| custom_analysis\plugin.py | 568 | plugins\custom_analysis\plugin.py | SHA-256 一致 → 跳过 |
| custom_notification\manifest.json | 140 | plugins\custom_notification\manifest.json | SHA-256 一致 → 跳过 |
| custom_notification\plugin.py | 536 | plugins\custom_notification\plugin.py | SHA-256 一致 → 跳过 |
| custom_phrases\manifest.json | 192 | plugins\custom_phrases\manifest.json | SHA-256 一致 → 跳过 |
| custom_phrases\plugin.py | 810 | plugins\custom_phrases\plugin.py | SHA-256 一致 → 跳过 |
| custom_phrases\sample_phrases.json | 803 | plugins\custom_phrases\sample_phrases.json | SHA-256 一致 → 跳过 |

判读：`<E盘>\LoveAI_gate4\_internal\plugins` 与工程 `plugins\` **逐文件完全一致（8/8）**，属同一份内容，无缺失、无更旧，故 **0 个文件需要补入**。

### 3. 备注

- 本目录（`docs\packaged_refs\`）本轮**未落任何 prompts/plugins 正文副本**，原因如上：包内无「工程缺失」或「工程内容不一致且更旧」的文件。
- 若后续需要留档打包版旧提示词，可直接取工程 `prompts\*.txt.bak_20261010`（与包内 3 份旧提示词字节一致），无需再从 E 盘复制。
