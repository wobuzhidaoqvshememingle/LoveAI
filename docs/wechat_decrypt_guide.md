# 微信聊天记录解密与扫盘指南

> 本文档从零开始教你：怎么找到微信聊天记录在哪个盘、怎么解密数据库、怎么读出消息。
> 照着本文档一步步跑，不需要猜。

---

## 1. 微信聊天记录在哪？

### 1.1 常见路径

微信 4.x（WeChatAppEx）的聊天记录默认存在用户文档目录下：

| 操作系统 | 默认路径 |
|---|---|
| Windows | `C:\Users\<用户名>\Documents\xwechat_files\` |
| Windows（自定义盘符） | `E:\xwechat_files\`、`D:\WeChat Files\`、`F:\xwechat_files\` |

### 1.2 多账号怎么区分

一个设备上可能登过多个微信号，每个账号一个目录：

```
xwechat_files/
├── wxid_01ahr1ba97o722_90eb/    ← 大号（wxid_xxx_随机后缀）
│   └── db_storage/
│       ├── message/             ← 聊天消息
│       ├── contact/             ← 联系人
│       └── general/             ← 个人设置
├── wxid_mtsqbk5540bm22_1749/    ← 小号
│   └── db_storage/
│       └── ...
└── all_users/                   ← 通用配置（不是聊天记录）
```

**怎么知道哪个是哪个号？** 进每个 `wxid_xxx_xxx/db_storage/contact/contact.db`，解密后查自己的昵称。

### 1.3 怎么扫盘自动找

如果不知道聊天记录在哪个盘，写脚本遍历所有盘符：

```python
import os, string

def find_wechat_dirs():
    """遍历所有盘符，找 xwechat_files 目录。"""
    candidates = []
    for drive in string.ascii_uppercase:
        drive_path = f"{drive}:\\"
        if not os.path.exists(drive_path):
            continue
        # 常见位置
        for sub in [
            os.path.join(drive_path, "xwechat_files"),
            os.path.join(drive_path, "WeChat Files"),
            os.path.join(drive_path, "Users", os.environ.get("USERNAME", ""), "Documents", "xwechat_files"),
            os.path.join(drive_path, "Users", os.environ.get("USERNAME", ""), "Documents", "WeChat Files"),
        ]:
            if os.path.isdir(sub):
                candidates.append(sub)
    return candidates

# 例子
dirs = find_wechat_dirs()
for d in dirs:
    print(f"找到: {d}")
    # 列出下面的账号目录
    for account in os.listdir(d):
        if account.startswith("wxid_"):
            print(f"  账号: {account}")
```

---

## 2. 数据库结构

### 2.1 微信 4.x 的 db_storage 目录

```
db_storage/
├── message/
│   ├── message_0.db          ← 消息分片 0
│   ├── message_1.db          ← 消息分片 1
│   ├── ...
│   ├── message_5.db          ← 消息分片 5
│   ├── message_fts.db        ← 全文索引（不用管）
│   └── message_resource.db    ← 消息资源（图片/文件元数据）
├── contact/
│   └── contact.db            ← 联系人数据库
├── general/
│   └── general.db            ← 个人设置、红包、转账记录
├── session/
│   └── session.db            ← 会话列表
└── MMKV/                      ← 键值配置（小文件）
```

### 2.2 消息表的命名规则

每个联系人一张表，表名是 `Msg_<md5(wxid)>`：

```python
import hashlib
wxid = "wxid_ke3ghgo2z1ok22"
table_name = "Msg_" + hashlib.md5(wxid.encode()).hexdigest()
# 结果: Msg_907c832510228b456859c7239d7ad918
```

### 2.3 消息表的关键字段

| 字段 | 说明 |
|---|---|
| `create_time` | 消息时间（Unix 时间戳） |
| `message_content` | 消息内容（文本） |
| `real_sender_id` | 发送人标记：**=2 是自己发的，其他是对方发的** |
| `local_type` | 消息类型（1=文本，3=图片，43=视频，...） |

**重要**：微信 4.x 用的是 `real_sender_id`，不是 `IsSender`。`real_sender_id=2` 代表自己发的消息。

---

## 3. 怎么解密数据库

### 3.1 微信 4.x 的加密方式

- **SQLCipher4**：AES-256-CBC + HMAC-SHA512
- 每页 4096 字节，预留 80 字节（IV 16 + HMAC 64）
- 密钥是 32 字节（64 位 hex 字符串）

### 3.2 密钥从哪来？

| 方法 | 说明 | 难度 |
|---|---|---|
| 方法0：读 wx_key.dll 日志 | 微信启动时会把密钥写到临时日志 | 简单 |
| 方法1：进程内存扫描 | 从微信进程内存里扫 32 字节密钥 | 中等 |
| 方法2：DLL 控制器法 | 注入 DLL 主动拿密钥 | 复杂 |

**推荐方法0**：先看临时目录有没有密钥日志。

```python
import os, glob

def find_key_log():
    """找微信密钥日志。"""
    temp = os.environ.get("TEMP", r"C:\Users\*\AppData\Local\Temp")
    patterns = [
        os.path.join(temp, "wx_key*.log"),
        os.path.join(temp, "*wechat*key*.log"),
    ]
    for p in patterns:
        for f in glob.glob(p):
            print(f"找到密钥日志: {f}")
            with open(f, "r", errors="ignore") as fh:
                print(fh.read()[:500])
```

### 3.3 解密示例

项目自带解密工具：`services/wechat_db_decryptor.py`

```python
from services.wechat_db_decryptor import WeChatDBDecryptor

dec = WeChatDBDecryptor()
key = "你的32字节hex密钥"  # 64 个字符
src_db = r"E:\xwechat_files\wxid_xxx\db_storage\message\message_0.db"
out_db = r"data\wechat_decrypted\4.x\db_storage\message\message_0.db"

dec.decrypt_db(src_db, key, out_db, encryption_type="sqlcipher4")
print(f"解密成功: {out_db}")
```

### 3.4 一键读消息（推荐）

项目自带 `WeChatDBReader`，自动解密+读消息，不用手动解密每个 db：

```python
from services.wechat_db_reader import WeChatDBReader

reader = WeChatDBReader(
    db_dir=r"data\wechat_decrypted\4.x",  # 解密后的明文库目录
    key="",                                 # 空字符串（明文库不需要密钥）
    version="4.x",
)

# 读某个联系人的所有消息
messages = list(reader.read_messages(
    contact_filter=["wxid_ke3ghgo2z1ok22"],  # 蛋卷的 wxid
    batch_size=500,
))

for m in messages[:5]:
    print(f"时间: {m['create_time']}")
    print(f"发送人: {'自己' if m['is_sender']==1 else '对方'}")
    print(f"内容: {m['content'][:50]}")
    print()
```

---

## 4. 多账号密钥通用吗？

**同一个设备上的所有微信账号，用同一个密钥。**

微信 4.x 的数据库加密密钥是跟设备绑定的，不是跟账号绑定的。所以：
- 同一个电脑上的大号、小号，用同一个密钥能解所有号的库
- 换一台电脑，密钥就不一样了

---

## 5. 常见问题

### Q1：解密失败，HMAC 不匹配？
- 密钥错了
- 数据库文件不完整（被微信锁了，先退出微信）
- 版本不对（微信 3.x 用 wxsqlite3，4.x 用 sqlcipher4）

### Q2：微信没在运行，怎么拿密钥？
- 方法0：找 wx_key.dll 日志（微信启动过就会留日志）
- 方法1：启动微信再拿密钥（最简单）
- 方法2：从注册表读

### Q3：联系人消息太少，是不是解密漏了？
- 先查 Msg 表的实际行数：
  ```sql
  SELECT COUNT(*) FROM Msg_<md5(wxid)>
  ```
- 如果表本身就只有 100 行，说明这个人真的只聊了 100 条，不是解密漏了

### Q4：is_sender 全是 0，谁发的？
- 微信 4.x 用 `real_sender_id` 字段
- `real_sender_id = 2` 是自己发的消息
- 其他值是对方发的

---

## 6. 项目里的相关文件

| 文件 | 说明 |
|---|---|
| `services/wechat_db_decryptor.py` | 数据库解密器（SQLCipher4 + wxsqlite3） |
| `services/wechat_db_reader.py` | 消息读取器（自动解密、自动分片合并） |
| `微信密钥接入方法总表.md` | 密钥获取方法汇总 |
| `微信版本适配与已知限制.md` | 支持的微信版本列表 |
| `data/wechat_decrypted/4.x/` | 解密后的明文库（运行后自动生成） |
