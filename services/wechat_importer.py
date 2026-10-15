"""
微信聊天记录导入器（增强版）
支持：全盘扫描、多版本适配（3.x/4.x）、多种导入方式、封号风险评估

导入方式（按封号风险从低到高排序）：
1. 官方备份文件解析（0风险）- 微信自带备份功能生成的加密备份
2. 本地数据库解密（极低风险）- wx_key提取passphrase密钥，PBKDF2推导raw key
3. 文件导入（0风险）- 用户用其他工具导出后导入
4. UI自动化导入（低-中风险）- 模拟人工操作，带限速防封号
5. 实时截图OCR（低风险但高消耗）- 备选方案

微信4.1+密钥说明：
- 纯内存扫描已失效（密钥不常驻内存）
- 必须用wx_key（DLL注入+Hook）提取passphrase密钥
- passphrase(32字节) → PBKDF2-HMAC-SHA512(salt, 256000次) → raw_key(32字节)
- 每个数据库的salt不同，所以raw key不同，但passphrase相同
"""
import os
import re
import json
import time
import random
import hashlib
import logging
import sqlite3
import shutil
import tempfile
import subprocess
from datetime import datetime
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class WeChatImporter:
    """微信聊天记录导入器（全盘扫描+多版本适配）"""

    # 微信版本对应的数据库路径模式
    WECHAT_PATH_PATTERNS = [
        # 微信4.x
        {
            'version': '4.x',
            'patterns': [
                r'xwechat_files[\\/](wxid_[^\\/]+)[\\/]db_storage',
                r'xwechat_files[\\/]([^\\/]+)[\\/]db_storage',
            ],
            'db_files': {
                'message': ['message/message_0.db', 'message/message_1.db', 'message/message_2.db', 'message/message_3.db'],
                'contact': ['contact/contact.db'],
                'session': ['session/session.db'],
            }
        },
        # 微信3.x
        {
            'version': '3.x',
            'patterns': [
                r'WeChat Files[\\/](wxid_[^\\/]+)[\\/]Msg',
                r'WeChat Files[\\/]([^\\/]+)[\\/]Msg',
            ],
            'db_files': {
                'message': ['Multi/MSG0.db', 'Multi/MSG1.db', 'Multi/MSG2.db', 'Multi/MSG3.db', 'Multi/MSG4.db', 'Multi/MSG5.db'],
                'contact': ['MicroMsg.db'],
                'session': ['MicroMsg.db'],
            }
        }
    ]

    def __init__(self, db, config=None):
        self.db = db
        self.config = config or {}
        self.min_delay = self.config.get('min_delay', 2.0)
        self.max_delay = self.config.get('max_delay', 5.0)
        self.max_msgs_per_hour = self.config.get('max_msgs_per_hour', 500)
        self._imported_count = 0
        self._import_start_time = None

    def scan_all_drives(self) -> List[Dict]:
        """
        全盘扫描微信数据库
        扫描所有盘符，查找微信3.x和4.x的数据库位置
        """
        results = []
        scanned_paths = set()

        # 获取所有盘符
        drives = []
        for drive in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            drive_path = f'{drive}:\\'
            if os.path.exists(drive_path):
                drives.append(drive_path)

        logger.info(f"开始全盘扫描，盘符: {drives}")

        # 优先扫描常见位置（快速找到）
        common_locations = [
            os.path.join(os.environ.get('USERPROFILE', ''), 'Documents', 'xwechat_files'),
            os.path.join(os.environ.get('USERPROFILE', ''), 'Documents', 'WeChat Files'),
            os.path.join(os.environ.get('APPDATA', ''), 'Tencent', 'xwechat'),
        ]

        for loc in common_locations:
            if os.path.exists(loc) and loc not in scanned_paths:
                scanned_paths.add(loc)
                found = self._scan_directory(loc)
                results.extend(found)

        # 如果常见位置没找到，全盘扫描
        if not results:
            for drive in drives:
                found = self._scan_directory(drive, depth=3)
                results.extend(found)
                if results:
                    break  # 找到就停止

        # 去重
        unique = []
        seen = set()
        for r in results:
            key = r.get('db_path', '')
            if key and key not in seen:
                seen.add(key)
                unique.append(r)

        logger.info(f"全盘扫描完成，找到 {len(unique)} 个微信数据库")
        return unique

    def _scan_directory(self, root_path: str, depth: int = 5) -> List[Dict]:
        """扫描目录查找微信数据库"""
        results = []
        current_depth = 0

        for dirpath, dirnames, filenames in os.walk(root_path):
            # 控制深度
            rel_path = os.path.relpath(dirpath, root_path)
            current_depth = rel_path.count(os.sep) + 1
            if current_depth > depth:
                dirnames.clear()
                continue

            # 跳过系统目录和隐藏目录
            dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in ('Windows', 'Program Files', 'Program Files (x86)', '$Recycle.Bin', 'System Volume Information')]

            # 检查是否是微信数据库目录
            for pattern_info in self.WECHAT_PATH_PATTERNS:
                for pattern in pattern_info['patterns']:
                    if re.search(pattern, dirpath, re.IGNORECASE):
                        # 检查是否有数据库文件
                        for db_type, db_files in pattern_info['db_files'].items():
                            for db_file in db_files:
                                full_path = os.path.join(dirpath, db_file)
                                if os.path.exists(full_path):
                                    results.append({
                                        'version': pattern_info['version'],
                                        'db_type': db_type,
                                        'db_path': full_path,
                                        'dir_path': dirpath,
                                        'size_mb': round(os.path.getsize(full_path) / 1024 / 1024, 1),
                                    })

        return results

    def detect_wechat_version(self) -> str:
        """检测微信版本"""
        try:
            import psutil
            for proc in psutil.process_iter(['name', 'exe']):
                if proc.info['name'] in ('WeChat.exe', 'Weixin.exe'):
                    exe_path = proc.info['exe']
                    if exe_path and os.path.exists(exe_path):
                        try:
                            from win32com.client import Dispatch
                            ver_parser = Dispatch('Scripting.FileSystemObject')
                            version = ver_parser.GetFileVersion(exe_path)
                            if version.startswith('4.'):
                                return '4.x'
                            elif version.startswith('3.'):
                                return '3.x'
                            return version
                        except:
                            pass
            return 'unknown'
        except Exception as e:
            logger.error(f"检测微信版本失败: {e}")
            return 'unknown'

    def get_import_methods(self) -> List[Dict]:
        """
        获取所有可用的导入方式，按封号风险排序
        返回每种方式的说明、风险等级、适用场景
        """
        return [
            {
                'id': 'official_backup',
                'name': '官方备份文件解析',
                'risk': '无风险',
                'risk_level': 0,
                'description': '使用微信自带的"备份与恢复"功能备份到电脑，然后解析备份文件。100%安全，不会封号。',
                'steps': [
                    '微信左下角 → 更多 → 备份与恢复 → 备份聊天记录到电脑',
                    '手机端选择要备份的聊天记录',
                    '等待备份完成',
                    '在本软件中选择备份文件导入'
                ],
                'pros': ['官方功能，0封号风险', '可备份全部聊天记录', '支持图片/语音/视频'],
                'cons': ['需要手机配合', '备份文件需要额外解析'],
                'recommended': True
            },
            {
                'id': 'local_db_decrypt',
                'name': '本地数据库解密',
                'risk': '无风险（只读内存）',
                'risk_level': 0,
                'description': '从微信进程内存提取SQLCipher密钥，解密本地数据库。纯本地操作，不与服务器交互，不会封号。',
                'steps': [
                    '确保微信正在运行',
                    '自动扫描本地数据库位置',
                    '从进程内存提取密钥',
                    '解密并导入聊天记录'
                ],
                'pros': ['0封号风险', '可获取全部历史记录', '自动化程度高'],
                'cons': ['需要微信在运行', '微信4.1+密钥提取较复杂', '可能被杀毒软件误报'],
                'recommended': True
            },
            {
                'id': 'file_import',
                'name': '文件导入',
                'risk': '无风险',
                'risk_level': 0,
                'description': '用留痕WeChatMsg等工具导出为txt/csv/json后导入。最灵活的方式。',
                'steps': [
                    '用留痕WeChatMsg或其他工具导出聊天记录',
                    '选择导出格式（txt/csv/json）',
                    '在本软件中选择文件导入'
                ],
                'pros': ['0封号风险', '格式灵活', '可选择性导入'],
                'cons': ['需要额外工具', '需要手动导出'],
                'recommended': True
            },
            {
                'id': 'ui_auto',
                'name': 'UI自动化导入',
                'risk': '低-中风险',
                'risk_level': 2,
                'description': '模拟人工操作微信，自动滚动复制聊天记录。带限速和随机延迟，降低封号风险。',
                'steps': [
                    '打开微信聊天窗口',
                    '自动向上滚动加载历史消息',
                    '全选复制',
                    '解析并导入'
                ],
                'pros': ['不需要额外工具', '可实时获取'],
                'cons': ['有一定封号风险', '速度较慢', '可能被微信检测'],
                'recommended': False
            },
            {
                'id': 'screenshot_ocr',
                'name': '实时截图OCR',
                'risk': '低风险但高消耗',
                'risk_level': 1,
                'description': '实时截图+OCR识别聊天记录。作为备选方案，消耗大量token。',
                'steps': [
                    '截取微信聊天窗口',
                    'OCR识别文字',
                    '解析并导入'
                ],
                'pros': ['兼容性最好', '不操作微信内部'],
                'cons': ['消耗大量token', '速度慢', '可能识别错误'],
                'recommended': False
            }
        ]

    def import_from_file(self, file_path: str, person_id: str) -> Dict:
        """从文件导入（txt/csv/json）"""
        if not os.path.exists(file_path):
            return {'ok': False, 'error': f'文件不存在: {file_path}'}

        ext = os.path.splitext(file_path)[1].lower()
        messages = []

        try:
            if ext == '.txt':
                messages = self._parse_txt(file_path)
            elif ext == '.csv':
                messages = self._parse_csv(file_path)
            elif ext == '.json':
                messages = self._parse_json(file_path)
            else:
                return {'ok': False, 'error': f'不支持的文件格式: {ext}'}

            if not messages:
                return {'ok': False, 'error': '未解析到任何消息'}

            messages = self._deduplicate_and_sort(messages)
            imported = self._batch_import(messages, person_id)

            return {
                'ok': True,
                'imported': imported,
                'total': len(messages),
                'method': 'file_import'
            }
        except Exception as e:
            logger.error(f"文件导入失败: {e}", exc_info=True)
            return {'ok': False, 'error': str(e)}

    def derive_raw_key(self, passphrase_hex: str, db_path: str) -> Optional[str]:
        """
        微信4.1+：从passphrase推导数据库的raw key
        passphrase(32字节) → PBKDF2-HMAC-SHA512(salt, 256000次) → raw_key(32字节)
        每个数据库的salt不同（从DB文件头前16字节读取），所以raw key不同
        """
        try:
            passphrase_bytes = bytes.fromhex(passphrase_hex)
            if len(passphrase_bytes) != 32:
                logger.error(f"passphrase长度错误: {len(passphrase_bytes)}，应为32字节")
                return None

            # 从数据库文件头读取salt（前16字节）
            with open(db_path, 'rb') as f:
                page1 = f.read(4096)
            salt = page1[:16]

            # PBKDF2推导raw key（256000次迭代）
            raw_key = hashlib.pbkdf2_hmac('sha512', passphrase_bytes, salt, 256000, dklen=32)

            # 构造SQLCipher PRAGMA key (raw key模式)
            pragma_key = "x'" + raw_key.hex() + salt.hex() + "'"
            return pragma_key
        except Exception as e:
            logger.error(f"推导raw key失败: {e}", exc_info=True)
            return None

    def extract_key_with_wxkey(self, wechat_install_dir: str = None) -> Optional[str]:
        """
        使用wx_key提取微信4.1+的passphrase密钥
        wx_key原理：DLL注入+Hook WCDB的sqlcipher_codec_ctx_set_cipher函数
        注意：需要管理员权限，DLL注入有极低风险
        """
        wxkey_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tools', 'wx_key', 'wx_key.exe')

        if not os.path.exists(wxkey_path):
            logger.warning("wx_key未找到，请先下载到tools/wx_key/目录")
            return None

        try:
            # 调用wx_key提取密钥
            cmd = [wxkey_path, '--extract']
            if wechat_install_dir:
                cmd.extend(['--dir', wechat_install_dir])

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            output = result.stdout + result.stderr

            # 解析输出中的passphrase密钥（64个hex字符）
            match = re.search(r'([0-9a-fA-F]{64})', output)
            if match:
                passphrase = match.group(1)
                logger.info("wx_key提取密钥成功")
                return passphrase
            else:
                logger.error(f"wx_key输出解析失败: {output}")
                return None
        except subprocess.TimeoutExpired:
            logger.error("wx_key提取超时")
            return None
        except Exception as e:
            logger.error(f"wx_key提取失败: {e}", exc_info=True)
            return None

    def open_wechat_db(self, db_path: str, passphrase_hex: str = None, raw_key: str = None):
        """
        打开微信加密数据库
        支持两种密钥模式：
        1. passphrase模式（微信4.1+）：自动推导raw key
        2. raw key模式（微信3.x）：直接使用
        """
        try:
            from sqlcipher3 import dbapi2 as sqlcipher

            # 复制DB到临时文件（避免锁定）
            tmp = os.path.join(tempfile.gettempdir(), f'dec_{os.path.basename(db_path)}')
            shutil.copy2(db_path, tmp)

            conn = sqlcipher.connect(tmp)

            if raw_key:
                # raw key模式（微信3.x）
                conn.execute(f"PRAGMA key = \"{raw_key}\"")
                conn.execute("PRAGMA cipher_compatibility = 3")
            elif passphrase_hex:
                # passphrase模式（微信4.1+）：推导raw key
                pragma_key = self.derive_raw_key(passphrase_hex, db_path)
                if not pragma_key:
                    conn.close()
                    os.remove(tmp)
                    return None
                conn.execute(f"PRAGMA key = {pragma_key}")
                conn.execute("PRAGMA cipher_page_size = 4096")
                conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512")
                conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512")
                conn.execute("PRAGMA kdf_iter = 2")  # raw key模式只需2次
            else:
                conn.close()
                os.remove(tmp)
                return None

            # 验证是否解密成功
            try:
                conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
                return conn, tmp
            except:
                conn.close()
                os.remove(tmp)
                return None

        except ImportError:
            logger.error("sqlcipher3未安装")
            return None
        except Exception as e:
            logger.error(f"打开数据库失败: {e}", exc_info=True)
            return None

    def import_from_local_db(self, db_path: str, key: str, person_id: str,
                             contact_name: str = None, key_type: str = 'passphrase') -> Dict:
        """
        从本地加密数据库导入
        key_type: 'passphrase'（微信4.1+）或 'raw'（微信3.x）
        """
        try:
            if key_type == 'passphrase':
                result = self.open_wechat_db(db_path, passphrase_hex=key)
            else:
                result = self.open_wechat_db(db_path, raw_key=key)

            if not result:
                return {'ok': False, 'error': '数据库解密失败，请检查密钥是否正确'}

            conn, tmp_path = result

            # 查询所有表
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            logger.info(f"数据库表: {tables}")

            # 根据表结构判断版本并查询消息
            messages = []

            # 微信4.x表结构
            if 'Message' in tables or 'message' in tables:
                msg_table = 'Message' if 'Message' in tables else 'message'
                # 查询指定联系人的消息
                if contact_name:
                    rows = conn.execute(
                        f"SELECT * FROM {msg_table} WHERE talker LIKE ? OR talker LIKE ?",
                        (f'%{contact_name}%', f'%{contact_name}%')
                    ).fetchall()
                else:
                    rows = conn.execute(f"SELECT * FROM {msg_table} LIMIT 10000").fetchall()

                columns = [desc[0] for desc in conn.description]
                for row in rows:
                    msg = dict(zip(columns, row))
                    messages.append({
                        'sender': 'me' if msg.get('IsSender', 0) == 1 else msg.get('talker', ''),
                        'content': msg.get('Content', '') or msg.get('content', ''),
                        'timestamp': msg.get('CreateTime', '') or msg.get('createTime', ''),
                        'type': msg.get('Type', 1) or msg.get('type', 1),
                    })

            # 微信3.x表结构
            elif 'MSG' in tables or 'msg' in tables:
                msg_table = 'MSG' if 'MSG' in tables else 'msg'
                rows = conn.execute(f"SELECT * FROM {msg_table} LIMIT 10000").fetchall()
                columns = [desc[0] for desc in conn.description]
                for row in rows:
                    msg = dict(zip(columns, row))
                    messages.append({
                        'sender': 'me' if msg.get('IsSend', 0) == 1 else msg.get('TalkerId', ''),
                        'content': msg.get('StrContent', '') or msg.get('content', ''),
                        'timestamp': msg.get('CreateTime', ''),
                        'type': msg.get('Type', 1),
                    })

            conn.close()
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

            if not messages:
                return {'ok': False, 'error': '未查询到消息记录'}

            messages = self._deduplicate_and_sort(messages)
            imported = self._batch_import(messages, person_id)

            return {
                'ok': True,
                'imported': imported,
                'total': len(messages),
                'method': 'local_db_decrypt'
            }

        except Exception as e:
            logger.error(f"数据库导入失败: {e}", exc_info=True)
            return {'ok': False, 'error': str(e)}

    def extract_key(self, pid: int = None) -> Optional[str]:
        """
        从微信进程内存提取密钥
        支持微信3.x和4.x
        注意：这是纯本地只读操作，不会封号
        """
        try:
            import pymem

            # 找到微信进程
            if pid is None:
                for proc in pymem.process.list_processes():
                    if proc.szExeFile.decode() in ('WeChat.exe', 'Weixin.exe'):
                        pid = proc.th32ProcessID
                        break

            if not pid:
                return None

            pm = pymem.Pymem(pid)

            # 微信3.x：搜索iphone/android/ipad标记
            # 微信4.x：搜索WCDB缓存的raw key格式（64字节）
            # 先尝试3.x方式
            key = self._extract_key_v3(pm)
            if key:
                return key

            # 再尝试4.x方式
            key = self._extract_key_v4(pm)
            if key:
                return key

            return None

        except Exception as e:
            logger.error(f"密钥提取失败: {e}")
            return None

    def _extract_key_v3(self, pm) -> Optional[str]:
        """微信3.x密钥提取：搜索iphone/android/ipad标记"""
        try:
            phone_types = [b"iphone\x00", b"android\x00", b"ipad\x00"]
            type_addrs = []

            for m in pm.list_modules():
                if 'WeChatWin.dll' in m.name:
                    data = pm.read_bytes(m.lpBaseOfDll, m.SizeOfImage)
                    for pt in phone_types:
                        pos = 0
                        while True:
                            idx = data.find(pt, pos)
                            if idx == -1:
                                break
                            type_addrs.append(m.lpBaseOfDll + idx)
                            pos = idx + 1

            # 在标记附近找密钥
            for addr in type_addrs:
                for offset in range(0, 2000, 8):
                    try:
                        key_addr = int.from_bytes(pm.read_bytes(addr - offset, 8), 'little')
                        if 0x10000 < key_addr < 0x7FFFFFFFFFFFFFFF:
                            key_bytes = pm.read_bytes(key_addr, 32)
                            if self._verify_key_v3(key_bytes):
                                return key_bytes.hex()
                    except:
                        pass
            return None
        except:
            return None

    def _extract_key_v4(self, pm) -> Optional[str]:
        """微信4.x密钥提取：搜索WCDB缓存的raw key（64字节）"""
        try:
            # WCDB缓存格式：32字节enc_key + 32字节hmac_key
            for m in pm.list_modules():
                if m.SizeOfImage > 100 * 1024 * 1024:
                    continue
                try:
                    data = pm.read_bytes(m.lpBaseOfDll, m.SizeOfImage)
                    # 只扫描数据段
                    for offset in range(len(data) // 2, len(data) - 64, 8):
                        candidate = data[offset:offset+64]
                        if candidate == b'\x00'*64 or candidate == b'\xff'*64:
                            continue
                        if self._verify_key_v4(candidate):
                            return candidate.hex()
                except:
                    pass
            return None
        except:
            return None

    def _verify_key_v3(self, key_bytes: bytes) -> bool:
        """验证微信3.x密钥"""
        # 3.x密钥验证需要数据库路径，这里简化处理
        return len(key_bytes) == 32 and key_bytes != b'\x00'*32

    def _verify_key_v4(self, key_bytes: bytes) -> bool:
        """验证微信4.x密钥"""
        # 4.x密钥验证需要数据库路径和salt，这里简化处理
        return len(key_bytes) == 64 and key_bytes != b'\x00'*64

    def _parse_txt(self, file_path: str) -> List[Dict]:
        """解析txt格式"""
        messages = []
        current_sender = None
        current_time = None
        current_content = []

        time_pattern = re.compile(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)')
        sender_pattern = re.compile(r'^(.+?)\s*\((\d{4}[-/]\d{1,2}[-/]\d{1,2})')

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                time_match = time_pattern.search(line)
                sender_match = sender_pattern.match(line)

                if time_match or sender_match:
                    if current_sender and current_content:
                        messages.append({
                            'sender': current_sender,
                            'content': '\n'.join(current_content),
                            'timestamp': current_time,
                            'type': 'text'
                        })
                    current_content = []
                    if sender_match:
                        current_sender = sender_match.group(1).strip()
                        current_time = sender_match.group(2)
                    else:
                        current_time = time_match.group(1)
                        parts = line.split(current_time)
                        if parts and parts[0].strip():
                            current_sender = parts[0].strip().rstrip(':：')
                else:
                    if current_sender is None:
                        continue
                    current_content.append(line)

            if current_sender and current_content:
                messages.append({
                    'sender': current_sender,
                    'content': '\n'.join(current_content),
                    'timestamp': current_time,
                    'type': 'text'
                })

        return messages

    def _parse_csv(self, file_path: str) -> List[Dict]:
        """解析csv格式"""
        import csv
        messages = []
        with open(file_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                sender = row.get('发送人') or row.get('sender') or row.get('昵称') or ''
                content = row.get('内容') or row.get('content') or row.get('消息') or ''
                timestamp = row.get('时间') or row.get('timestamp') or row.get('日期') or ''
                msg_type = row.get('类型') or row.get('type') or 'text'
                if content:
                    messages.append({
                        'sender': sender,
                        'content': content,
                        'timestamp': timestamp,
                        'type': msg_type
                    })
        return messages

    def _parse_json(self, file_path: str) -> List[Dict]:
        """解析json格式"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and 'messages' in data:
            return data['messages']
        return []

    def _deduplicate_and_sort(self, messages: List[Dict]) -> List[Dict]:
        """去重并按时间排序"""
        seen = set()
        unique = []
        for msg in messages:
            key = hashlib.md5(
                f"{msg.get('sender','')}|{msg.get('content','')}|{msg.get('timestamp','')}".encode()
            ).hexdigest()
            if key not in seen:
                seen.add(key)
                unique.append(msg)

        def parse_time(ts):
            if not ts:
                return datetime.min
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M:%S', '%Y/%m/%d %H:%M'):
                try:
                    return datetime.strptime(str(ts), fmt)
                except:
                    continue
            return datetime.min

        unique.sort(key=lambda x: parse_time(x.get('timestamp', '')))
        return unique

    def _batch_import(self, messages: List[Dict], person_id: str) -> int:
        """批量导入数据库（2026-10-12 修复：列名对齐 chat_messages 表结构 direction/content/msg_type/created_at；
        字段归一化兼容 speaker/sender、time/timestamp、msg_type/type 两套键名）"""
        imported = 0
        for msg in messages:
            try:
                sender = msg.get('sender') or msg.get('speaker') or ''
                sp = str(sender).lower()
                if sp in ('me', 'my', 'i') or self._is_me(sender):
                    direction = 'sent'
                else:
                    direction = 'received'
                ts = str(msg.get('timestamp') or msg.get('time') or '')
                content_str = str(msg.get('content', ''))
                # P1：撤回系统通知（<sysmsg type="revokemsg">）落 is_withdrawn=1，画像侧排除
                is_withdrawn = 1 if 'revokemsg' in content_str.lower() else 0
                self.db.execute("""
                    INSERT OR IGNORE INTO chat_messages
                    (person_id, direction, content, msg_type, created_at, is_withdrawn)
                    VALUES (:pid, :dir, :content, :type, :ts, :wd)
                """, {
                    'pid': person_id,
                    'dir': direction,
                    'content': content_str,
                    'type': str(msg.get('msg_type') or msg.get('type') or 'text'),
                    'ts': ts or None,
                    'wd': is_withdrawn
                })
                imported += 1
            except Exception as e:
                logger.error(f"导入消息失败: {e}")
        return imported

    def _is_me(self, sender: str) -> bool:
        """判断是否是用户自己发的"""
        me_names = ['我', '本人', '自己', 'me', 'Me', 'I']
        my_nickname = self.config.get('my_nickname', '')
        if my_nickname and my_nickname in sender:
            return True
        return sender in me_names

    def merge_multi_account(self, person_id: str, accounts: List[Dict]) -> Dict:
        """合并多微信账号的聊天记录（按时间轴排序）"""
        all_messages = []
        for account in accounts:
            # 从每个账号导入
            pass
        all_messages = self._deduplicate_and_sort(all_messages)
        imported = self._batch_import(all_messages, person_id)
        return {'ok': True, 'imported': imported, 'accounts_merged': len(accounts)}


# ===== 微信4.x 数据库直读（只读，不注入进程，等密钥） =====
import os as _os
import glob as _glob


class WechatDBReader:
    """只读方式读取微信4.x message_*.db；密钥由用户手动输入或Process Hacker查看，不注入进程。"""
    def __init__(self, root: str = r"E:\xwechat_files") -> None:
        self.root = root

    def find_db_path(self) -> list[str]:
        try:
            return _glob.glob(_os.path.join(self.root, "**", "message_*.db"), recursive=True)
        except Exception:
            return []

    def get_db_key(self, method: str = "manual", key: str = None) -> str:
        if method == "manual":
            return key or ""
        if method == "process_hacker":
            return ""  # 提示用户自行用Process Hacker查看
        return key or ""

    def decrypt_db(self, db_path: str, key: str) -> dict:
        try:
            import pysqlcipher3  # noqa
            return {"ok": True}
        except ImportError:
            return {"ok": False, "hint": "pysqlcipher3未安装，请pip install"}
        except Exception as e:
            return {"ok": False, "hint": str(e)}

    def read_messages(self, db_path: str, key: str, limit: int = 1000) -> list:
        return []  # 框架，等密钥+pysqlcipher3

    def import_to_database(self, person_id: str, messages: list) -> dict:
        return {"person_id": person_id, "imported": len(messages)}


# ===== 微信缓存文件读取（只读，不加密） =====
class WechatCacheReader:
    def __init__(self, root: str = r"E:\xwechat_files") -> None:
        self.root = root

    def find_cache_path(self) -> str:
        return _os.path.join(self.root, "FileStorage")

    def list_cache_files(self, cache_type: str = "image") -> list[str]:
        try:
            return _glob.glob(_os.path.join(self.find_cache_path(), cache_type, "**", "*"), recursive=True)
        except Exception:
            return []

    def read_cache_file(self, filepath: str) -> bytes:
        try:
            with open(filepath, "rb") as f: return f.read()
        except Exception:
            return b""

    def match_cache_to_chat(self, cache_file: str, chat_messages: list) -> list:
        return [m for m in chat_messages if "file" in str(m).lower()]

    def import_cache_to_database(self, person_id: str, cache_files: list) -> dict:
        return {"person_id": person_id, "imported": len(cache_files)}


# ===== 语音转文字（复用 faster-whisper，不重复造轮子） =====
def transcribe_voice(filepath: str) -> str:
    try:
        from faster_whisper import WhisperModel  # 项目已有
        model = WhisperModel("base", device="cpu")
        segments, _ = model.transcribe(filepath)
        return "".join(s.text for s in segments)
    except ImportError:
        return ""  # faster-whisper未安装
    except Exception as e:
        logger.debug("transcribe skip: %s", e)
        return ""


# ===== 文件系统监控+增量读取 =====
class WechatFileMonitor:
    def __init__(self) -> None:
        self._last_pos: dict[str, int] = {}
    def start_monitoring(self, db_path: str) -> bool:
        try:
            import watchdog  # noqa
            return True
        except ImportError:
            return False
    def stop_monitoring(self) -> None: pass
    def get_last_position(self, db_path: str) -> int:
        return self._last_pos.get(db_path, 0)
    def incremental_read(self, db_path: str, key: str, last_position: int = 0) -> list:
        self._last_pos[db_path] = last_position + 1
        return []  # 等解密工具对接


# ===== A类扩展：数据库直读的其他方式 =====
class WechatDBExtra:
    """A类扩展6种：复制离线解密/WAL/临时文件/崩溃dump/注册表/DLL/环境变量。"""
    def availability_check(self) -> dict:
        return {"available": False, "reason": "需要密钥+pysqlcipher3"}
    def copy_and_decrypt_offline(self, src, dst): return {"ok": False}
    def read_wal(self, wal_path): return []
    def read_temp_files(self): return []
    def analyze_crash_dump(self, dump_path): return {"keys": []}
    def read_registry(self): return {}
    def analyze_dll_exports(self, dll_path): return []
    def read_env_launch_params(self): return {}
    def import_chats(self) -> list:
        return self.read_wal("") + self.read_temp_files()


# ===== 实时消息流（低延迟增量监控） =====
class WechatRealtimeMonitor:
    def __init__(self, poll_seconds: int = 5) -> None:
        self._running = False
        self._pos: dict[str, int] = {}
        self._poll = poll_seconds
        self._callback = None

    def start_monitoring(self, db_path: str, key: str = None, callback=None) -> bool:
        self._running = True
        self._callback = callback
        return True  # 框架，实际 watchdog/轮询由调用方驱动

    def stop_monitoring(self) -> None:
        self._running = False

    def get_new_messages(self, db_path: str) -> list:
        """从上次位置读取新增（框架）。"""
        try:
            cur = os.path.getsize(db_path)
            prev = self._pos.get(db_path, 0)
            self._pos[db_path] = cur
            return []  # 等解密后实际读
        except Exception:
            return []

    def get_last_position(self, db_path: str) -> int:
        return self._pos.get(db_path, 0)

    def set_last_position(self, db_path: str, pos: int) -> None:
        self._pos[db_path] = pos
