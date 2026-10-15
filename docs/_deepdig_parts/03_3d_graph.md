# 03 · 3D 关系星图 / 关系网络可视化 — 深度扒取

> 扒取日期：2026-10-09
> 范围：NRD Studio（nrdstudio.cn）、d3graph（erdogant/d3graph）、vasturiano 3d-force-graph / react-force-graph
> 目标：LoveAI-v2 要做"人物关系星图"，选型 + 落地。
> LoveAI 前端现状（已确认）：**原生 HTML/CSS/JS，无 Vue/React 构建链**；`ui/js/` 下已本地 vendored `three.min.js`(603KB) + `GLTFLoader.js`；已有 `ui/pet_3d.html` 用 Three.js 做桌宠；`ui/js/components/` 目录存在。桌面壳疑似 Qt WebEngine（透明窗口、鼠标穿透注释）。

---

## 一、三个来源各自定位（URL / 技术栈）

| 来源 | URL | 技术栈 | 角色 |
|---|---|---|---|
| **NRD Studio** | 主站 `https://nrd.studio`（已停放，GoDaddy parking），实际产品在 `https://nrdstudio.cn/lp`（沪ICP备20002726号-2），公开 SDK 文档 `https://nrdstudio.cn/docs/api/#/space_view` | **React + webpack + d3-force + zrender（2D Canvas）+ lodash**；营销站叠 jQuery/Bootstrap。源码路径泄露：`/Users/sunzhenwei/nginx/chart/nrd_studio_pro/` | 对标对象：商业级人物/知识关系图产品，闭源 Web 应用 |
| **d3graph** | `https://github.com/erdogant/d3graph`（205★，MIT） | Python 包：networkx + python-louvain(community_louvain) + scikit-learn 缩放；输出**独立单文件 HTML**，内嵌 d3.js v3 | 借鉴：邻接矩阵→图的算法、节点聚类着色、边权归一化 |
| **3d-force-graph** | `https://github.com/vasturiano/3d-force-graph`；React 封装 `https://github.com/vasturiano/react-force-graph`（3.3k★，MIT） | **Three.js/WebGL** 渲染 + **d3-force-3d** 或 ngraph 物理引擎 | 候选方案：原生 JS/UMD 可直接用，是当前最主流的开源 3D 力导向图库 |

---

## 二、NRD Studio 反推结果（从 bundle 看到）

**方法**：`nrd.studio` 已停放（返回 GoDaddy parking 页），实际产品在 `nrdstudio.cn/lp`（React SPA）。直接下载它的 webpack runtime + main chunk（`static/js/main.32286.chunk.js`，838KB）做关键词扫描。

### 2.1 确证（bundle 文件名 / 字符串直接命中）

| 证据 | 出处 | 结论 |
|---|---|---|
| chunk 文件名 `npm.d3-force.02ed3.chunk.js`、`npm.d3-quadtree.7561e.chunk.js`、`npm.d3-dispatch.9525d.chunk.js`、`npm.d3-timer.bb975.chunk.js`、`npm.d3-collection.e3302.chunk.js` | `/lp` 页面 `<script src>` 列表 | 物理引擎确证用 **d3-force**（含 quadtree 空间索引） |
| chunk 文件名 `npm.zrender.441f2.chunk.js`；main chunk 内 `zrender` 字符串出现 **144 次** | main chunk 全文 grep | 渲染层确证用 **zrender**（ECharts 的 2D Canvas 渲染引擎） |
| main chunk 内 `WebGL`=0、`THREE`=0、`PerspectiveCamera`=0、`WebGLRenderer`=0 | main chunk 全文 grep | **没有用 Three.js / WebGL**，它的"3D"不是真 WebGL |
| main chunk 内 `forceSimulation`=1、`forceManyBody`=1、`forceLink`=1 | main chunk @546354 | 力导向初始化代码可读（见下） |
| 公开 SDK：`new nrdx.Space.View(container, {data, embed})`；另有 `nrdx.Diagram.View`（2D） | 官方 API 文档 | 全局命名空间 `nrdx`，embed 模式可隐藏导航内嵌 |

### 2.2 力参数初始化代码（从 minified bundle 还原的可读结构）

```js
// 还原自 nrd_main.32286.chunk.js @546354 附近
const f = o.forceSimulation();
f.nodes(e);
f.force("center", o.forceCenter(l[0], l[1]));          // 中心化，center 坐标从配置传入

const p = o.forceLink();
p.id(node => node[s]);                                  // link 的 source/target 按 id 字段映射
if (a) {
  if (isFunction(a)) p.distance(edge => a(edge));       // 距离可固定值，也可按边函数返回
  else p.distance(a);
}
f.force("link").links(t);

const E = o.forceManyBody();
if (h !== undefined) E.strength(h);                    // charge 强度（斥力）外部传入
if (r !== undefined) E.distanceMin(r);                 // 斥力生效最小距离
if (n !== undefined) E.distanceMax(n);                  // 斥力生效最大距离
f.force("charge", E);

const b = o.forceCollide();                            // ★ 碰撞力，防节点重叠
if (isFunction(d)) b.radius(node => d(node));
else if (d !== undefined) b.radius(d);
if (c) b.radius(node => node[c] || 10 + d);             // 按节点属性算半径，默认 10+d
f.force("charge2", b);
```

**设计要点（可直接搬）：**
1. **四力组合**：`center`（居中）+ `link`（连接）+ `charge`（斥力）+ `charge2`（collide 碰撞）。碰撞力是它节点不重叠的关键——3d-force-graph 默认不带 collide，要自己加。
2. **所有力参数都从外部 opts 传入**（h=strength, r=distanceMin, n=distanceMax, a=link distance, c=radius key），不在代码里写死。
3. **半径回退值 `10 + d`**：没指定半径的节点给个默认 10 加偏移，避免 NaN 塌陷。

### 2.3 推测（bundle 未直接确证）

- NRD 宣称的"3D Space"模块：主包（838KB main）内**零 WebGL 痕迹**，结合 zrender（2D Canvas）判断，它的"3D 星图"大概率是 **d3-force 在 2D 平面跑物理、用 zrender 的 transform 矩阵做伪 3D 透视/缩放视觉**，或 Space.View 在登录后另一个懒加载 chunk 里（runtime chunk map 里未见 three/webgl 命名的 chunk）。**这点标为推测**——要 100% 确证得登录后抓 Space 模块的懒加载 chunk，本次未登录无法拿到。
- 节点分组着色：NRD 文档提"支持分组/样式"，但具体配色算法在 main chunk 里未单独定位（可能在产品业务代码里）。**不强行推断**。

---

## 三、d3graph 源码要点（读的是真实 Python 源码）

来源：`https://raw.githubusercontent.com/erdogant/d3graph/master/d3graph/d3graph.py`

### 3.1 构造参数（默认值，确证）

```python
d3graph(adjmat,
        collision=0.5,        # 碰撞响应
        charge=600,           # ★ 内部存为 -abs(charge) = -600（斥力）
        link_tension=1,
        slider=None,          # 边权阈值滑杆范围，自动取 [floor(min)-1, ceil(max)]
        dark_mode=True,       # 默认暗色
        sticky=True)
```

### 3.2 节点着色算法（确证）

```python
# color='cluster' 时：
G = nx.from_pandas_edgelist(df, create_using=nx.MultiGraph).to_undirected()
cluster_labels = community_louvain.best_partition(G)   # ★ Louvain 社区发现
hex_colors, _ = cm.fromlist(y, cmap='Set2', scheme='hex')  # plotly cm 按聚类上色
# node size / opacity 默认 = 'degree'（按中心度/度数）
# cmap 默认 'Set2'，可选 Set1/Pastel1/Paired/RdBu 等
```

**可搬思路**：边权归一化用 `minmax` 缩放到 `[0.5,15]`（边宽）和 `[50,100]`（边长）；节点大小/透明度按 degree 中心度；颜色默认按 Louvain 社区聚类自动分组。

### 3.3 边属性（确证）

```python
set_edge_properties(
    edge_distance=None,        # None=按权归一到[50,100]；固定值则所有边等长
    edge_weight=None,          # 边宽，None=按权归一到[0.5,15]
    edge_color='#808080',      # 支持 'node_source' / 'node_target'（继承节点色）
    edge_opacity='weight',     # ★ 边透明度按权重从 0.2 到 1
    minmax=[0.5, 15],
    minmax_distance=[50, 100],
    directed=False,             # True 才显示箭头 marker
    marker_end='arrow',
)
```

### 3.4 定位（为什么它不适合直接搬）

d3graph 的产物是**一次性导出的独立 HTML**（内嵌 d3.js + 数据），用于存档/分享，**不是实时交互组件**。它的价值是：**邻接矩阵 → nodes/links 的数据转换、Louvain 分组着色、边权 minmax 归一化**这套 Python 侧预处理逻辑。LoveAI 后端（Python）可以直接借鉴这套预处理，再把结果喂给前端渲染器。

---

## 四、推荐方案对比表

| 维度 | **3d-force-graph**（原生 UMD） | **react-force-graph** | **d3graph** | **自研 Three.js + d3-force-3d** |
|---|---|---|---|---|
| 渲染 | Three.js/WebGL（真 3D） | Three.js/WebGL（真 3D） | d3.js v3 SVG/Canvas 2D | Three.js/WebGL（真 3D） |
| 物理引擎 | d3-force-3d / ngraph | d3-force-3d | d3-force 2D | d3-force-3d |
| LoveAI 集成成本 | **低**：UMD `<script>` 直接引，与现有 `three.min.js` 同栈 | **高**：要引入 React + 构建链，LoveAI 无此基础设施 | 中：Python 导出 HTML，嵌入 iframe | **高**：自己写节点/连线/拾取/相机 |
| 额外依赖体积 | ≈ 1~1.5MB UMD（three 已 vendored，增量主要是 d3-force-3d + three addons） | React 全家桶 ≈ 500KB+ | 0（Python 侧生成） | 0（复用现有 three） |
| 交互（拖拽/缩放/拾取/tooltip） | **开箱即用**：onNodeClick/Hover/Drag、zoomToFit、cameraPosition 全有 | 同左，声明式 | 仅静态导出后浏览器自带的拖拽/缩放 | 全要自己写 |
| 几百节点性能 | 好（WebGL InstancedMesh） | 好 | 差（SVG 节点一多就卡） | 可控但要自己优化 |
| License | MIT | MIT | MIT | — |
| **结论** | ✅ **首选** | ❌ 与现有技术栈冲突 | △ 仅借鉴数据预处理 | △ 兜底（若 UMD 包体积不可接受） |

---

## 五、LoveAI 落地方案

### 5.1 选型结论

**用 `3d-force-graph` 的 UMD 构建**，本地 vendored 到 `ui/js/`，与已有 `three.min.js` 同栈。理由：
- LoveAI 已是原生 JS + 本地 vendored 依赖（three.min.js 就是这么干的），无 npm/构建链，UMD `<script>` 引入零改造。
- 它底层就是 Three.js，和 `pet_3d.html` 共用渲染栈，相机/灯光可复用经验。
- API 完整（见下），不用自己写力导向和拾取。

### 5.2 文件落点（映射到 ui/）

| 文件 | 作用 |
|---|---|
| `ui/js/3d-force-graph.min.js` | 新增，vendored UMD（从 unpkg/jsdelivr 下载后本地化，离线可用） |
| `ui/js/d3-force-3d.min.js` | 新增，物理引擎依赖（UMD 包可能已含，未含则单独 vendored） |
| `ui/js/components/relation_graph.js` | 新增，封装 `initRelationGraph(container, data)`，对外暴露 setData/setFilter/focusNode |
| `ui/relation_graph.html` | 新增，关系星图独立窗口页（可被 Qt 壳打开），或作为 `index.html` 的一个 tab |
| 数据来源：Python 后端把人物/关系表转成 nodes/links JSON，经 `bridge.js` 传给前端 | 复用现有 `ui/js/bridge.js` |

### 5.3 数据结构（TypeScript 定义，Python 侧同构 dict）

```ts
// nodeId 默认 'id'；linkSource 默认 'source'；linkTarget 默认 'target'（与库约定一致）
interface RelationNode {
  id: string;            // 唯一 id，如 "person:zhangwei"
  name: string;          // 显示名
  type: 'self' | 'partner' | 'family' | 'friend' | 'colleague' | 'acquaintance';
  val: number;           // 中心度，决定球体积（self/partner 最大，如 10；普通人 1~3）
  color?: string;        // 可选；不给则用 nodeAutoColorBy('type') 自动分组着色
  note?: string;         // hover tooltip 内容
}
interface RelationLink {
  source: string;        // node.id
  target: string;       // node.id
  type: string;          // 关系类型：lover/family/friend/work/conflict
  weight: number;        // 亲密度 0~1，映射到边宽/不透明度
  color?: string;
  label?: string;        // 边 tooltip，如 "交往3年"
}
type GraphData = { nodes: RelationNode[]; links: RelationLink[] };
```

### 5.4 初始化参数（直接抄 API 默认值 + LoveAI 调优）

```js
import ForceGraph3D from '3d-force-graph';  // UMD 后是全局 ForceGraph3D

const g = ForceGraph3D(container)
  .backgroundColor('rgba(0,0,0,0)')        // ★ LoveAI 是透明 Qt 窗口，必须透明；默认 #000011 不行
  .graphData(data)
  .nodeId('id')
  .nodeLabel(n => `${n.name}<br>${n.type}`)   // HTML tooltip
  .nodeVal('val')
  .nodeRelSize(4)                          // 默认 4
  .nodeAutoColorBy('type')                 // ★ 按关系类型自动分组着色
  .nodeOpacity(0.85)
  .nodeResolution(6)                       // 默认 8，降到 6 省性能
  .linkSource('source').linkTarget('target')
  .linkAutoColorBy('type')                 // 边也按关系类型着色
  .linkWidth(l => Math.max(0.5, l.weight * 3))   // 亲密度→边宽
  .linkOpacity(0.4)
  .linkDirectionalParticles(0)             // P2 再开
  // —— 力引擎（核心调参）——
  .forceEngine('d3')
  .numDimensions(3)
  .d3AlphaDecay(0.0228)                    // 默认
  .d3VelocityDecay(0.4)                    // 默认
  .cooldownTicks(200)                      // 跑 200 tick 后冻结，省 CPU
  .warmupTicks(80)                         // 渲染前先干跑 80 帧，开局就成型
  .onNodeClick((node) => openDetailPanel(node))
  .onEngineStop(() => g.zoomToFit(600, 60));

// —— ★ 手动加四力组合（搬 NRD 的设计）——
g.d3Force('charge',  g.d3Force('charge').strength(-300));   // 斥力，d3graph 用 -600 偏散，LoveAI 人少用 -300
g.d3Force('link',    g.d3Force('link').distance(l => 60 + (1 - l.weight) * 60).strength(0.7));
// ★ 自己补 collide（NRD 有，3d-force-graph 默认没有）：
import { forceCollide } from 'd3-force-3d';
g.d3Force('collide', forceCollide().radius(n => Math.cbrt(n.val) * 6 + 4));
```

### 5.5 着色规则（业务映射）

| 节点 type | 建议色 | 含义 |
|---|---|---|
| self | 金色 #FFD700 | 自己，val 最大 |
| partner | 粉红 #FF6B9D | AI伴侣/现任，val 次大 |
| family | 蓝 #4A90D9 | 家人 |
| friend | 绿 #5BB85B | 朋友 |
| colleague | 灰紫 #8E7CC3 | 同事 |
| acquaintance | 灰 #999 | 泛泛之交 |

边色跟随源节点色（搬 d3graph 的 `edge_color='node_source'` 思路），冲突关系（type=conflict）边用红色虚线。

---

## 六、坏设计 / 避坑记录

1. **NRD 的"3D"是营销话术，底层是 zrender 2D Canvas**——别被名字误导去花精力逆向它的 WebGL，它根本没有。好处是：它证明了"几百节点关系图用 2D Canvas 也能跑顺"，给了我们性能兜底方案（真 WebGL 卡了就降级到 zrender/Canvas2D 伪 3D）。
2. **3d-force-graph 默认背景 `#000011` 深蓝不透明**——LoveAI 是透明桌宠窗口，必须显式 `backgroundColor('rgba(0,0,0,0)')`，否则一个大黑块。
3. **`enablePointerInteraction=true` 默认开**——鼠标拾取每帧 raycast，节点上千后掉帧明显。节点 >500 时关掉它，或自己节流。
4. **3D 力导向在 ~300 节点后开始掉帧**（WebGL draw call + 物理 tick）。对策：`nodeResolution=4`、`linkWidth=0`（用 Line 不用 Cylinder）、`cooldownTicks=200` 后冻结物理、`warmupTicks` 预跑。NRD 敢用 2D Canvas 就是这个原因。
5. **d3graph 只适合导出静态 HTML**——别指望拿它做实时交互组件，它的产物是一次性快照。
6. **react-force-graph 需要 React 构建链**——LoveAI 没有 npm/webpack，硬上会把整个项目拖进工程化泥潭，不值。
7. **闭源 bundle 反推的不确定性**：NRD 的 Space.View 3D 内部 chunk 未登录拿不到，本文标"推测"的部分不要当确证用；它的力参数是从 minified 代码还原的，变量名已丢失，数值默认值要在我们自己环境里实测微调。
8. **collide 力会让图变"鼓包"**——NRD 用 `10+d` 回退半径是防 NaN，不是最佳；我们按节点 val 算半径更合理，但 radius 太大会把节点顶散，要实测。

---

## 七、优先级初判

| 优先级 | 内容 | 理由 | 人日 |
|---|---|---|---|
| **P0** | vendored 3d-force-graph UMD + 透明背景渲染 + 静态 nodes/links 上图 + 拖拽/缩放/点击出详情面板 | 先跑通"能看能点"最小闭环，验证透明窗口兼容性 | 2~3 |
| **P0** | nodes/links 数据结构落地 + Python 后端把人物关系表转 JSON + bridge.js 喂给前端 | 没有数据一切白搭，与 P0 并行 | 1 |
| **P1** | 按 type 自动分组着色 + 力参数调优（charge/link distance/collide 半径）+ zoomToFit | 从"能看"到"好看"，参数要对着真实人物数据反复调 | 1~2 |
| **P1** | 性能兜底：cooldownTicks 冻结 + 节点 >300 降级策略（降 nodeResolution / 关 pointer interaction） | 现在人物少，但记忆系统喂起来后节点会涨 | 0.5 |
| **P2** | 边方向粒子流（linkDirectionalParticles）表"关系亲密度流动" + 搜索聚焦（click 节点 cameraPosition 飞过去） | 锦上添花，v1 不做 | 1~2 |
| **P2** | 与记忆四层联动：hover 节点时侧边栏显示该人物的记忆摘要 | 依赖记忆系统先做出来，不阻塞星图本体 | 2 |
| **P2**（备选） | 若 UMD 包体积/兼容性不可接受，降级方案：d3-force-3d + 现有 three.min.js 自研最小渲染器（节点球 + Line） | 兜底，NRD 已证明 2D Canvas 也能交付 | 3~4（只在 P0 走不通时启动） |

---

## 附：本文确证来源清单

- NRD chunk 列表：`https://nrdstudio.cn/lp` 实际 `<script src>`（确证 d3-force/zrender）
- NRD main chunk：`https://nrdstudio.cn/static/js/main.32286.chunk.js`（838KB，本地留存，grep 出 WebGL=0 / zrender=144 / forceSimulation 上下文）
- NRD SDK 文档：`https://nrdstudio.cn/docs/api/#/space_view`（`nrdx.Space.View`）
- d3graph 源码：`https://raw.githubusercontent.com/erdogant/d3graph/master/d3graph/d3graph.py`（charge=600、collision=0.5、Louvain、minmax[0.5,15]/[50,100]）
- 3d-force-graph API：`https://github.com/vasturiano/3d-force-graph`（graphData schema、d3Force、d3AlphaDecay=0.0228、d3VelocityDecay=0.4、cooldownTime=15000ms）
- react-force-graph：`https://github.com/vasturiano/react-force-graph`（确认是 React 封装，LoveAI 不适用）
