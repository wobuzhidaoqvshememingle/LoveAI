"""
LoveAI v2.0 - 代理服务
内置代理管理，支持HTTP/SOCKS5代理，用于访问外部AI服务
"""
import os
import json
import logging
import subprocess
import threading
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class ProxyService:
    """代理管理服务"""

    def __init__(self, config):
        self.config = config
        self._proxy_process = None
        self._current_proxy = None

    def get_proxy_config(self) -> Dict[str, Any]:
        """获取当前代理配置"""
        return {
            'enabled': self.config.get('proxy.enabled', False),
            'type': self.config.get('proxy.type', 'http'),
            'host': self.config.get('proxy.host', '127.0.0.1'),
            'port': self.config.get('proxy.port', 7890),
            'username': self.config.get('proxy.username', ''),
            'password': self.config.get('proxy.password', ''),
            'subscribe_url': self.config.get('proxy.subscribe_url', ''),
            'auto_start': self.config.get('proxy.auto_start', False),
            'use_for_llm': self.config.get('proxy.use_for_llm', True),
            'use_for_wechat': self.config.get('proxy.use_for_wechat', False),
            'use_for_rag': self.config.get('proxy.use_for_rag', False),
        }

    def set_proxy_config(self, **kwargs):
        """设置代理配置"""
        for key, value in kwargs.items():
            self.config.set(f'proxy.{key}', value)
        logger.info(f"代理配置已更新: {kwargs}")

    def get_proxy_url(self) -> Optional[str]:
        """获取代理URL（用于requests等库）"""
        if not self.config.get('proxy.enabled', False):
            return None

        proxy_type = self.config.get('proxy.type', 'http')
        host = self.config.get('proxy.host', '127.0.0.1')
        port = self.config.get('proxy.port', 7890)
        username = self.config.get('proxy.username', '')
        password = self.config.get('proxy.password', '')

        if username and password:
            return f"{proxy_type}://{username}:{password}@{host}:{port}"
        else:
            return f"{proxy_type}://{host}:{port}"

    def get_requests_proxies(self) -> Optional[Dict[str, str]]:
        """获取requests库格式的代理配置"""
        proxy_url = self.get_proxy_url()
        if not proxy_url:
            return None
        return {
            'http': proxy_url,
            'https': proxy_url,
        }

    def test_proxy(self, test_url: str = "https://www.google.com") -> Dict[str, Any]:
        """测试代理连接"""
        proxy_url = self.get_proxy_url()
        if not proxy_url:
            return {'ok': False, 'error': '代理未启用'}

        try:
            import requests
            response = requests.get(test_url, proxies=self.get_requests_proxies(), timeout=10)
            return {
                'ok': True,
                'status_code': response.status_code,
                'response_time': response.elapsed.total_seconds(),
                'proxy_url': proxy_url
            }
        except Exception as e:
            return {'ok': False, 'error': str(e), 'proxy_url': proxy_url}

    def import_subscribe(self, url: str) -> Dict[str, Any]:
        """从订阅链接导入代理节点（支持sing-box/v2ray格式）"""
        try:
            import requests
            response = requests.get(url, timeout=30)
            content = response.text

            # 尝试解析为JSON（sing-box格式）
            try:
                config = json.loads(content)
                if 'outbounds' in config:
                    nodes = []
                    for outbound in config['outbounds']:
                        if outbound.get('type') in ('vless', 'vmess', 'trojan', 'hysteria2', 'shadowsocks'):
                            nodes.append({
                                'tag': outbound.get('tag', '未命名'),
                                'type': outbound.get('type'),
                                'server': outbound.get('server', ''),
                                'port': outbound.get('server_port', 0),
                            })
                    self.config.set('proxy.subscribe_url', url)
                    return {'ok': True, 'nodes': nodes, 'count': len(nodes)}
            except json.JSONDecodeError:
                pass

            # 尝试解析为base64编码的订阅
            import base64
            try:
                decoded = base64.b64decode(content).decode('utf-8')
                lines = decoded.strip().split('\n')
                nodes = []
                for line in lines:
                    if line.startswith(('vmess://', 'vless://', 'trojan://', 'ss://')):
                        nodes.append({'url': line[:50] + '...'})
                self.config.set('proxy.subscribe_url', url)
                return {'ok': True, 'nodes': nodes, 'count': len(nodes)}
            except:
                pass

            return {'ok': False, 'error': '无法解析订阅格式'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def start_system_proxy(self):
        """设置系统代理（Windows）"""
        if not self.config.get('proxy.enabled', False):
            return

        proxy_url = f"{self.config.get('proxy.host', '127.0.0.1')}:{self.config.get('proxy.port', 7890)}"

        try:
            import winreg
            # 设置系统代理
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r'Software\Microsoft\Windows\CurrentVersion\Internet Settings',
                                 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, 'ProxyEnable', 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, 'ProxyServer', 0, winreg.REG_SZ, proxy_url)
            winreg.CloseKey(key)
            logger.info(f"系统代理已设置: {proxy_url}")
            return {'ok': True, 'proxy': proxy_url}
        except Exception as e:
            logger.error(f"设置系统代理失败: {e}")
            return {'ok': False, 'error': str(e)}

    def stop_system_proxy(self):
        """关闭系统代理"""
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r'Software\Microsoft\Windows\CurrentVersion\Internet Settings',
                                 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, 'ProxyEnable', 0, winreg.REG_DWORD, 0)
            winreg.CloseKey(key)
            logger.info("系统代理已关闭")
            return {'ok': True}
        except Exception as e:
            logger.error(f"关闭系统代理失败: {e}")
            return {'ok': False, 'error': str(e)}

    def get_status(self) -> Dict[str, Any]:
        """获取代理状态"""
        return {
            'enabled': self.config.get('proxy.enabled', False),
            'proxy_url': self.get_proxy_url(),
            'system_proxy_enabled': self._check_system_proxy(),
        }

    def _check_system_proxy(self) -> bool:
        """检查系统代理是否启用"""
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r'Software\Microsoft\Windows\CurrentVersion\Internet Settings',
                                 0, winreg.KEY_READ)
            value, _ = winreg.QueryValueEx(key, 'ProxyEnable')
            winreg.CloseKey(key)
            return value == 1
        except:
            return False
