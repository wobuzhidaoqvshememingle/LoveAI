# LoveAI Gateway

多模型聚合网关, 对外暴露 **OpenAI 兼容** 接口, 对内自动做选路 / fallback / 限流 / 熔断 / 用量统计 / Key 加密存储。
LoveAI 主程序只要把 `OPENAI_BASE_URL` 指过来就能直接用, 不改一行业务代码。

---

## 快速开始 (本地)

```bash
# 1. 装依赖 (Python 3.11+, 推荐 3.11/3.12)
pip install -r requirements.txt

# 2. 准备配置
cp config.example.yaml config.yaml
# 编辑 config.yaml, 至少配一个 provider; Key 建议走环境变量, 或启动后在仪表盘粘贴

# 3. 启动
uvicorn app.main:app --host 0.0.0.0 --port 8787
```

打开浏览器访问 <http://127.0.0.1:8787/> 即可看到暗色控制台:
- 顶部: 今日请求数 / 总 Token / 成功率
- 中部: 每个 provider 的健康状态、成功率、延迟、RPM/TPM 用量条, 可一键禁用/启用
- 下部: 最近 50 条请求日志
- 右上角: 粘贴任意厂商 Key 提交, 网关自动识别厂商并加密落库
- 「获取 API Key」按钮: 弹窗列出 34 家主流厂商的免费注册入口

---

## Docker 启动

```bash
# 构建并启动 (命名卷持久化 data/)
docker compose up -d --build

# 可选: 显式指定主密钥 (32 字节 hex)
# export GATEWAY_MASTER_KEY=$(python -c "import secrets;print(secrets.token_hex(16))")
# docker compose up -d
```

镜像内默认监听 `8787`, 容器外映射到同端口。数据持久化在 `gateway-data` 卷里。

---

## 配置说明

所有字段见 `config.example.yaml` 注释, 要点:

| 字段 | 说明 |
|---|---|
| `host` / `port` | 监听地址端口 |
| `default_strategy` | 路由策略: `auto` / `priority` / `cost_first` / `latency_first` / `quality_first` / `load_balance` |
| `providers[]` | 厂商列表, 每个含 `name`/`type`/`base_url`/`models`/`priority`/`cost_tier`/`rpm_limit` 等 |
| `security.gateway_api_key` | 调用方访问网关的 Bearer Key, 留空 = 不鉴权 (仅本机) |
| `security.master_key_env` | AES-256-GCM 主密钥来源环境变量名 (默认 `GATEWAY_MASTER_KEY`) |
| `compression.*` | 长对话自动压缩, 省 token |

**Key 管理两种方式:**
1. 环境变量: `GATEWAY_PROVIDER_<NAME大写>_API_KEY`, 例如 `GATEWAY_PROVIDER_GROQ_API_KEY`
2. 仪表盘右上角粘贴, 网关按前缀自动识别 (Anthropic/Gemini/Groq/xAI/智谱...), 模糊 sk- 开头会并发探测候选 base_url, 命中即定, 密文落 SQLite。

---

## OpenAI SDK 调用示例

网关完全兼容 OpenAI 官方 SDK, 只要改 `base_url`:

```bash
python -c "from openai import OpenAI; client=OpenAI(base_url='http://127.0.0.1:8787/v1', api_key='xxx'); print(client.chat.completions.create(model='gpt-4o-mini', messages=[{'role':'user','content':'hi'}]))"
```

`api_key` 填什么取决于你是否在 `config.yaml` 里设了 `security.gateway_api_key`:
- 没设 → 随便填个非空字符串即可
- 设了 → 必须填那个值, 否则 401

流式调用 (SDK 默认支持):

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8787/v1", api_key="any")
stream = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role":"user","content":"写首短诗"}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")
```

列模型:

```python
print(client.models.list())
```

---

## 跟 LoveAI 主程序联动

把 LoveAI 主程序里的两个环境变量改成网关地址即可:

```bash
export OPENAI_BASE_URL="http://127.0.0.1:8787/v1"
export OPENAI_API_KEY="你在 config.yaml 里设的 gateway_api_key, 没设就随便填"
```

LoveAI 不需要知道背后接了几家厂商、用了什么策略, 网关自动:
- 按 `default_strategy` 选最优 provider
- 挂了自动切下一个 (fallback_max_tries 次)
- 限流/熔断自动绕行
- 所有用量落 SQLite, 仪表盘可看

---

## 目录结构

```
gateway/
├── app/
│   ├── main.py              # FastAPI 入口 + lifespan + 后台 catalog 采集
│   ├── config.py            # 配置加载
│   ├── db.py                # SQLite 持久层
│   ├── models.py            # OpenAI 兼容 Pydantic 模型
│   ├── security.py           # AES-256-GCM 密钥加密
│   ├── core/engine.py       # 引擎门面 (选路/fallback/熔断)
│   ├── providers/          # 各厂商适配器 + registry + key_detect
│   ├── routes/
│   │   ├── chat.py          # POST /v1/chat/completions
│   │   ├── models.py        # GET  /v1/models
│   │   └── admin.py         # /admin/* 管理 API
│   └── web/dashboard.html   # 单文件暗色控制台
├── config.example.yaml
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## API 速查

| Method | Path | 说明 |
|---|---|---|
| POST | `/v1/chat/completions` | OpenAI 兼容对话 (支持 stream) |
| GET | `/v1/models` | 汇总所有 provider 的模型 |
| GET | `/admin/providers` | provider 健康状态 |
| GET | `/admin/usage` | 全局用量 + 最近 50 条日志 |
| POST | `/admin/keys` | `{api_key}` 自动识别厂商并加密落库 |
| GET | `/admin/catalog` | 模型目录 + 34 家免费厂商注册引导 |
| POST | `/admin/providers/{name}/disable` | 禁用 |
| POST | `/admin/providers/{name}/enable` | 启用 |
| GET | `/admin/stats/summary` | 今日总请求 / 总 token / 成功率 |
