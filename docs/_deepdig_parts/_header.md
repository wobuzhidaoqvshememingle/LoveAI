# 重点项目代码深度扒取（2026-10-10）

> 与昨天 `外部项目深度调研报告-20261009.md`（方向对标）不同，本篇是**下钻到真实源码**的代码级扒取：每个项目都读了它的真实文件（GitHub raw / clone 下来的仓库 / 线上 JS bundle 反编译），给出真实文件路径、函数名、数据结构、阈值数字，以及"LoveAI 具体搬哪个模块、哪个函数"。
>
> 方法纪律：
> - **只搬设计，不抄源码**。SillyTavern 为 AGPL-3.0、DSM 为 AGPL-3.0，这两家严禁逐行复制，只取算法骨架与数据结构；MIT/Apache 项目（AIta / PsyDI / 3d-force-graph / UncommonRoute）可更自由地借鉴。
> - **确证与推测分开标注**。凡 bundle 反推、文档推断的，正文都标了"确证/推测"；凡认真搜过但找不到公开仓库的，如实写明并用真实开源同类项目替代，**没有任何编造的代码或路径**。
> - 好的坏的都记：每块末尾都有"坏设计/避坑"。

---

## 〇、六个项目速查（实际扒到的真实仓库）

| 项目 | 我们要学它什么 | 真实仓库 / 入口 | 协议 | 扒到的深度 |
|---|---|---|---|---|
| **SillyTavern** | 记忆四层怎么做 | github.com/SillyTavern/SillyTavern | AGPL-3.0 | 实读 `world-info.js`、`extensions/vectors/index.js`、`src/endpoints/vectors.js`、`extensions/memory/index.js` 四个文件到函数级 |
| **AIta** | 关系卡点怎么诊断 | github.com/rd-0701/aita-chat-assistant | MIT | 实读单文件 `index.html` 内联引擎：warmthScore、关系阶段级联、7 类卡点四件套 |
| **PsyDI** | 渐进式人格探测 | github.com/opendilab/PsyDI（论文 arXiv:2408.03337） | Apache-2.0 | 实读 `backend/psydi_mini_agent.py`、`reward_model_inference.py`、`prompt.txt` |
| **NRD Studio** | 3D 关系星图怎么渲染 | 真身 `nrdstudio.cn`（nrd.studio 已被 GoDaddy 停放） | 闭源 Web 应用 | **反编译线上 bundle**（main.32286.chunk.js）：确证是 d3-force + zrender(2D Canvas)，**零 WebGL/Three.js**，所谓 3D 是伪 3D |
| **d3graph** | 力导向图参数 | github.com/erdogant/d3graph | 开源(Python) | 实读 `d3graph/d3graph.py`：charge=-600、collision=0.5、Louvain 聚类、边权 minmax 归一 |
| **DSM (Deep Self Mirror)** | 侧写引擎+标签体系 | GitLab `cognitive-functors/dsm`（v0.6.2） | AGPL-3.0 | **git clone 后逐行读**：`src/layers/`、`src/engine/`、`src/lexicons/`、`pipeline.py` |
| **UncommonRoute** | 模型难度分级路由 | github.com/anjieyang/UncommonRoute（PyPI `uncommon-route`） | MIT | 读 PyPI 官方文档全文 + 对照读 RouteLLM `routellm/routers/routers.py`（GitHub 页被 robots 拦，已标注） |
| （对照参考）RouteLLM | 难度路由的另一条路 | github.com/lm-sys/RouteLLM | Apache-2.0 | 实读五种路由器（Random/MF/CausalLLM/BERT/SWRanking）真实源码 |

> 说明：AIta、PsyDI、DSM、UncommonRoute 这几个小众项目**全部真实找到了公开仓库**，没有靠替代项目硬凑；只有 UncommonRoute 的 GitHub 源码页被 robots 拦，改用 PyPI 文档 + RouteLLM 源码交叉印证。

---

## 一、跨项目合并的总优先级表（先看这张）

把五份正文的 P0/P1/P2 拉通，按"投入小、闭环快、直接补 LoveAI 缺口"重排。**P0 合计约 11~13 人日**，可在不动架构的前提下全部落地。

### P0 —— 立刻搬（纯逻辑、低风险、直接补缺口）

| # | 搬什么 | 来自 | 落到 LoveAI 哪个文件 | 人日 |
|---|---|---|---|---|
| 1 | **难度路由三板斧**：把"硬关键词"拆成「难度特征 vs 任务类型特征」两组（别再把 NPD/挽回这类业务词当难度词）+ Session「只升不降/30min TTL」状态机 + tier→模型表接 fallback 链 | UncommonRoute | 改 `services/difficulty_router.py`、`services/llm_gateway.py`；新建 `services/difficulty_session.py` | ~3 |
| 2 | **侧写骨架**：`ProfileTag{维度, high/low极性分, sparse门, ≤10条原话证据, 按文案量置信度分带}` + 否定感知证据 + `mask_non_self_spans`（把"他说的话"从用户特质里剥掉） | DSM | 落 `services/digital_twin.py` / `persona_analyzer.py` | ~3 |
| 3 | **记忆常驻层 + 摘要层**：LoreEntry 数据结构（keys/secondary_keys/constant/selective/order/depth）+ `budget≈25%·maxContext` 逐条 token 预算 + 双门触发（每 N 条 + 词数门）增量滚动摘要 | SillyTavern | 落 `services/memory_service.py` | ~3 |
| 4 | **关系卡点分类器**：手工统计特征（平均字数/追问数/亲密表情/主动占比）→ 优先级级联 if/else → 命中 7 类卡点之一，输出 `{challenge, rootCause, breakthrough, avoid}` 四件套 | AIta | 落 `services/analysis_orchestrator.py` / `persona_analyzer.py` | ~2 |

> P0 之间相对独立，可并行；其中 #2 的"自他者遮蔽"和 #4 的"卡点诊断"建议连着做（同一个证据层喂两个消费方）。

### P1 —— 一周内，需要一点设计或标定

| # | 搬什么 | 来自 | 说明 |
|---|---|---|---|
| 5 | 向量**软**召回（topK + score_threshold=0.25 + 长消息先摘要再入库 + 字符串哈希去重） | SillyTavern | SillyTavern 原版是"物理 splice 删掉原消息再插到 depth"，LoveAI 要改成软召回，别照搬 |
| 6 | 两阶段"候选→裁决"：便宜规则抽候选 → LLM 只许 keep/drop/uncertain，**fail-closed 解析失败一律 drop**，prompt 锁死"不许发明新标签/不许诊断" | DSM | 直接服务关系卡点，防 AI 瞎贴标签 |
| 7 | 路由升级到 12~15 维聚合特征（手调权重），预留感知机注入点 | UncommonRoute/RouteLLM | 比"命中即 hard"准一档；真正训感知机要先攒 500~1000 条中文标注数据 |
| 8 | 探测自适应权重：维护类型概率表，专挑"最纠结"的 Top-2 维度追问，自适应学习率（越确信权重越小，clamp[3,60]），max_turn 双闸停止 | PsyDI | 我们已有 `services/psydi_probe.py` 骨架，补算法即可 |
| 9 | 3D 关系星图 MVP：vendored `3d-force-graph` UMD（我们前端已自带 three.min.js，零工程化成本），透明背景 + 拖拽缩放 + 点击出详情 + nodes/links JSON 数据桥 | 3d-force-graph / NRD | ~3~4 人日跑通；按类型着色、力参数调优、>300 节点降级随其后 |

### P2 —— 中期 / 观察，别现在投入

- SillyTavern：selective recursion 链式多跳召唤、sticky/cooldown/delay 时序状态机（体验优化）。
- DSM：常模百分位校正（**它自己都标 `calibrated=False`，中文无现成常模，别硬上**）、完整 8 层体系、C4 ONNX 神经分类器、多格式报告导出。
- UncommonRoute：REASONING 子档、spend control 限额、bandit 选择器、step-aware routing（等 agentic 工作流铺开再做）。
- AIta/PsyDI：PsyDI 的奖励模型和 CDF 统计量是它私有微调出来的，**只能搬方法、不能搬数值**；脱离那个奖励模型，PsyDI 就是个空壳。
- 3D 星图：边方向粒子流、hover 节点联动记忆摘要（依赖记忆系统先做完）。

---

## 二、六条跨项目共性工程纪律（比单个算法更值钱）

1. **规则/词典负责可复现的分，LLM 只负责写人话总结**（DSM 的命根子，也是它不幻觉的原因）。LoveAI 凡是要"敢下结论"的分，都别让 LLM 直接打分。
2. **阈值全是别人拍的脑袋，必须用 LoveAI 自己的对话数据重标定**。AIta 的 `<8 字`、PsyDI 的 clamp、UncommonRoute 的特征权重，直接搬过来在中文关系场景都会偏。
3. **fail-closed**：任何"AI 裁决"解析失败/越界，一律 drop/hold，绝不"宁可信其有"（DSM 两阶段裁决、PsyDI 停止条件都该这么改）。
4. **自他者遮蔽是关系分析的命门**：不剥掉"他说/引语"，就会把对方的话算成用户自己的特质（DSM 的 `mask_non_self_spans` 必须搬）。
5. **会话级"只升不降"**：一次会话里难度档位只升不降、30min TTL、连续三次相同请求才升级（UncommonRoute），能解决来回切模型的跳戏和浪费。
6. **选型先认我们自己的栈**：前端是原生 HTML/JS 且已 vendored three.min.js → 直接排除 react-force-graph，用 UMD 版 3d-force-graph；NRD 用 2D Canvas 伪 3D 都能交付，证明我们不必上重型 WebGL 框架。

---

## 三、诚实缺口（这次没能确证的）

- UncommonRoute 的 classifier.py 逐行源码：GitHub 页被 robots 拦截，分级阈值/特征权重来自 PyPI 官方文档，已在正文标注为"文档确证、非逐行源码"。
- DSM 的 C4 神经分类器（27 态 ONNX）：只看了 README，未读训练代码。
- DSM 只有 en/ru 双语 marker，**没有中文词典**，中文标签要 LoveAI 自造（正文已列为坑）。
- NRD Studio 是闭源产品，"3D 物理参数"是从压缩 bundle 反推的，库的选择是铁证（WebGL=0 / zrender=144），但具体业务层参数属推断。
- PsyDI 的奖励模型权重与统计量：未开源，只搬了探测流程与状态机方法。

---

## 四、正文分册

- 分册 1：[SillyTavern 记忆四层](./_deepdig_parts/01_sillytavern_memory.md)
- 分册 2：[AIta 关系卡点诊断 + PsyDI 渐进探测](./_deepdig_parts/02_aita_psychdi.md)
- 分册 3：[NRD Studio / d3graph / 3d-force-graph 关系星图渲染](./_deepdig_parts/03_3d_graph.md)
- 分册 4：[DSM 侧写引擎与标签体系](./_deepdig_parts/04_dsm_profiling.md)
- 分册 5：[UncommonRoute 模型难度分级路由](./_deepdig_parts/05_uncommonroute.md)

> 各分册原始文件同目录 `_deepdig_parts/` 下；DSM 本次 clone 的源码留存于 `_deepdig_parts/_dsm_src/`。
