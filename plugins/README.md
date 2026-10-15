# LoveAI 插件开发规范

## 概述
LoveAI 支持 Python 插件扩展，插件放在 `plugins/` 目录下。

## 插件结构
每个插件是一个 `.py` 文件，必须实现 `register()` 函数：

```python
def register(app):
    """
    注册插件到LoveAI应用。
    
    Args:
        app: LoveAIApplication实例，可访问:
            - app.services: 服务字典
            - app.signals: 全局信号
            - app.config: 配置服务
            - app.window_manager: 窗口管理器
    
    Returns:
        dict: 插件信息 {name, version, description, callbacks}
    """
    return {
        'name': 'MyPlugin',
        'version': '1.0',
        'description': '插件描述',
        'callbacks': {
            'on_message': handle_message,
            'on_analysis': handle_analysis,
        }
    }
```

## 可用回调
- `on_message(message_dict)`: 收到新微信消息时
- `on_analysis(analysis_result)`: 聊天分析完成时
- `on_task(task_dict)`: 任务状态变化时
- `on_affection(affection_dict)`: 好感度变化时
- `on_startup()`: 应用启动时
- `on_quit()`: 应用退出时

## 沙箱限制
- 插件只能访问 `app` 对象暴露的接口
- 文件系统访问限制在项目目录内
- 禁止修改核心模块代码
- 禁止访问网络（如需网络请在描述中声明）

## 安装
将 `.py` 文件放入 `plugins/` 目录，重启应用即可加载。

---

## 样例子目录插件（2026-10-07 新增）
1. **custom_phrases/**：自定义话术库（10条示例 JSON），接口 `initialize/get_phrases/cleanup`
2. **custom_analysis/**：自定义分析维度（幽默感/真诚度），接口 `get_extra_dimensions/on_after_analysis`
3. **custom_notification/**：分析完成通知（日志），接口 `send_notification/on_analysis_complete`

每个子目录含 `manifest.json`（name/version/author/description/entry）+ `plugin.py`（Plugin 类）。
