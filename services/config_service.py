"""
LoveAI v2.0 - 配置服务
读写config.json，支持实时生效（除模型路径变更外），变更即时写入文件。
"""
import json
import os
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ConfigService:
    """配置管理服务"""

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self._config = {}
        self._load()

    def _load(self):
        """从文件加载配置"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self._config = json.load(f)
                logger.info(f"配置加载完成: {self.config_path}")
            except Exception as e:
                logger.error(f"配置加载失败，使用默认: {e}")
                self._config = self._default_config()
        else:
            self._config = self._default_config()
            self._save()

    def _save(self):
        """保存配置到文件"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"配置保存失败: {e}")

    def _default_config(self) -> dict:
        return {
            "app": {"name": "LoveAI", "version": "2.0.0", "first_run": True, "language": "zh_CN"},
            "ui": {"theme": "dark", "font_size": "medium", "pet_enabled": True,
                   "pet_always_on_top": True, "pet_idle_animation": True, "portrait_style": "chibi"},
            "llm": {"api_key": "", "api_base": "https://api.openai.com/v1", "model": "gpt-4o-mini",
                    "offline_mode": False, "local_model_path": "models_local/qwen2.5-0.5b-instruct-q4_k_m.gguf",
                    "local_model_enabled": True, "auto_unload_timeout": 300,
                    "cloud": {"api_key": "", "api_base": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
                    "local": {
                        "l1": {"onnx_path": "models_local/smollm2-135m/model_q4.onnx",
                               "tokenizer_path": "models_local/smollm2-135m/tokenizer.json"},
                        "l2": {"gguf_path": "models_local/qwen2.5-0.5b-instruct-q4_k_m.gguf",
                               "lora_adapter": "loveai-core", "lora_dir": "models_local/lora"},
                    },
                    "routing": {"mode": "auto"}},
            "wechat": {"monitor_enabled": False, "auto_switch_person": True,
                       "auto_save_images": False, "monitor_interval_ms": 1500},
            "reply": {"style_preference": "balanced", "sensitive_word_replace": True, "auto_send": False},
            "privacy": {"encrypt_chat_history": True, "upload_anonymous_data": False,
                        "hide_pet_on_lock": False, "boss_key": "Ctrl+Alt+H"},
            "notification": {"new_message": True, "task_reminder": True,
                             "medal_notification": True, "sound_enabled": True},
            "data": {"auto_backup": True, "backup_path": "data/backups",
                     "db_path": "data/loveai.db", "fts_db_path": "data/unified_search.db",
                     "chroma_path": "data/chroma_db"},
            "shortcuts": {"main_window": "Ctrl+Alt+L", "voice_input": "Ctrl+Alt+V",
                          "mini_status": "Ctrl+Alt+M", "quit": "Ctrl+Alt+Q"},
            "window": {"state_path": "data/window_state.json", "main_size": [1100, 750],
                       "main_pos": [100, 100], "consult_mini_size": [340, 500],
                       "float_ball_size": [64, 64]},
            "startup": {"auto_launch": False, "minimize_to_tray": True, "max_persons": 10},
            "proxy": {
                "enabled": False,
                "type": "http",
                "host": "127.0.0.1",
                "port": 7890,
                "username": "",
                "password": "",
                "subscribe_url": "",
                "auto_start": False,
                "use_for_llm": True,
                "use_for_wechat": False,
                "use_for_rag": False
            },
        }

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值，支持点分隔路径如 'llm.api_key'"""
        keys = key.split('.')
        value = self._config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def set(self, key: str, value: Any):
        """设置配置值，支持点分隔路径，即时写入文件"""
        keys = key.split('.')
        config = self._config
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value
        self._save()
        logger.debug(f"配置更新: {key} = {value}")

    def get_all(self) -> dict:
        """获取完整配置字典（深拷贝）"""
        return json.loads(json.dumps(self._config))

    def set_all(self, config: dict):
        """批量设置配置"""
        self._config.update(config)
        self._save()

    def is_first_run(self) -> bool:
        return self._config.get('app', {}).get('first_run', True)

    def set_first_run_done(self):
        self.set('app.first_run', False)
