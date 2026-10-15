# -*- coding: utf-8 -*-
"""LocalAIScanner 单元测试（全部离线：fake system + fake http_get + tmp sqlite，
不联网、不碰真实注册表 / 真实 Cherry db）。"""
import json, os, sys, sqlite3, tempfile, unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.local_ai_scanner import LocalAIScanner  # noqa: E402

SECRET = "sk-SECRET-DO-NOT-LEAK-1234567890"


class FakeSystem:
    """罐装系统快照，完全可替换 WindowsSystem。"""

    def __init__(self, programs=None, dirs=None, ports=None, procs=None,
                 cherry_db=None):
        self._programs = programs or []
        self._dirs = dirs or []
        self._ports = ports or []
        self._procs = procs or []
        self._cherry = cherry_db

    def list_installed_programs(self):
        return [dict(p) for p in self._programs]

    def list_program_directories(self):
        return [dict(d) for d in self._dirs]

    def list_listen_ports(self):
        return [dict(p) for p in self._ports]

    def list_processes(self):
        return [dict(p) for p in self._procs]

    def cherry_studio_db_path(self):
        return self._cherry


def make_http(routes):
    """routes: {url: body(str) 或 Exception}。返回 (http_get, calls)。"""
    calls = []

    def _get(url, timeout=None, headers=None):
        calls.append(url)
        r = routes.get(url)
        if isinstance(r, Exception):
            raise r
        return r
    return _get, calls


def _ids(installed):
    return {i["id"] for i in installed}


def _make_cherry_db(tmp):
    db = os.path.join(tmp, "cherrystudio.sqlite")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE user_provider(provider_id TEXT, preset_provider_id TEXT,"
        " name TEXT, endpoint_configs TEXT, default_chat_endpoint TEXT,"
        " api_keys TEXT, auth_config TEXT, provider_settings TEXT, is_enabled INTEGER)")
    conn.execute(
        "CREATE TABLE user_model(provider_id TEXT, model_id TEXT,"
        " is_enabled INTEGER, is_hidden INTEGER)")
    # A: https + 有 key（云端）
    conn.execute(
        "INSERT INTO user_provider VALUES(?,?,?,?,?,?,?,?,?)",
        ("openai", "", "OpenAI",
         json.dumps({"chat": {"baseUrl": "https://api.openai.com"}}),
         "chat", json.dumps([SECRET]), "", "", 1))
    conn.execute("INSERT INTO user_model VALUES(?,?,?,?)", ("openai", "gpt-4", 1, 0))
    # B: https 云端但无 key
    conn.execute(
        "INSERT INTO user_provider VALUES(?,?,?,?,?,?,?,?,?)",
        ("deepseek", "", "DeepSeek",
         json.dumps({"chat": {"baseUrl": "https://api.deepseek.com"}}),
         "chat", "[]", "", "", 1))
    conn.execute("INSERT INTO user_model VALUES(?,?,?,?)", ("deepseek", "ds-llm", 1, 0))
    # C: 本地免密
    conn.execute(
        "INSERT INTO user_provider VALUES(?,?,?,?,?,?,?,?,?)",
        ("ollama", "", "Ollama",
         json.dumps({"chat": {"baseUrl": "http://127.0.0.1:11434"}}),
         "chat", "[]", "", "", 1))
    conn.execute("INSERT INTO user_model VALUES(?,?,?,?)", ("ollama", "llama3", 1, 0))
    # 隐藏模型：不应进入
    conn.execute("INSERT INTO user_model VALUES(?,?,?,?)", ("openai", "hidden-m", 1, 1))
    conn.commit()
    conn.close()
    return db


class TestLocalAIScanner(unittest.TestCase):

    # 1) 云客户端识别 + DoubaoIME 不误判
    def test_cloud_clients_and_doubaoime_excluded(self):
        sys = FakeSystem(
            programs=[{"name": "Kimi", "vendor": "Moonshot AI", "install_location": ""}],
            dirs=[{"path": "F:\\Doubao"}, {"path": "F:\\Yuanbao"},
                  {"path": "F:\\DoubaoIME"}],
            ports=[], procs=[])
        http_get, calls = make_http({})
        r = LocalAIScanner(system=sys, http_get=http_get).scan()
        ids = _ids(r["installed"])
        self.assertIn("doubao", ids)
        self.assertIn("yuanbao", ids)
        self.assertIn("kimi", ids)
        by_id = {i["id"]: i for i in r["installed"]}
        self.assertEqual(by_id["doubao"]["kind"], "cloud_client")
        self.assertEqual(by_id["yuanbao"]["kind"], "cloud_client")
        self.assertEqual(by_id["kimi"]["kind"], "cloud_client")
        # DoubaoIME 不得被识别为豆包（也不产生任何条目）
        self.assertNotIn("doubaoime", ids)
        self.assertEqual([i for i in r["installed"] if i["id"] == "doubao"].__len__(), 1)

    # 2) 端口握手成功 → openai_endpoints + usable_models
    def test_openai_endpoint_handshake(self):
        sys = FakeSystem(
            programs=[], dirs=[],
            ports=[{"address": "127.0.0.1", "port": 12345, "pid": 99,
                    "process_name": "someengine.exe"}],
            procs=[])
        body = '{"data":[{"id":"m1"},{"id":"m2"}]}'
        http_get, calls = make_http(
            {"http://127.0.0.1:12345/v1/models": body})
        r = LocalAIScanner(system=sys, http_get=http_get).scan()
        self.assertEqual(len(r["openai_endpoints"]), 1)
        ep = r["openai_endpoints"][0]
        self.assertEqual(ep["port"], 12345)
        self.assertEqual(ep["models"], ["m1", "m2"])
        models = {u["model"] for u in r["usable_models"]}
        self.assertIn("m1", models)
        self.assertIn("m2", models)

    # 3) 私有端口握手失败 → 云客户端 access=vendor_private_no_open_access，不进 usable
    def test_cloud_client_no_open_access(self):
        sys = FakeSystem(
            programs=[], dirs=[{"path": "F:\\Doubao"}],
            ports=[{"address": "127.0.0.1", "port": 49853, "pid": 1,
                    "process_name": "doubao.exe"}],
            procs=[{"name": "doubao.exe", "pid": 1}])
        http_get, calls = make_http(
            {"http://127.0.0.1:49853/v1/models": Exception("boom")})
        r = LocalAIScanner(system=sys, http_get=http_get).scan()
        by_id = {i["id"]: i for i in r["installed"]}
        self.assertEqual(by_id["doubao"]["access"], "vendor_private_no_open_access")
        self.assertEqual(r["openai_endpoints"], [])
        self.assertEqual(r["usable_models"], [])

    # 4a) Ollama 已装未服务 → local_not_serving
    def test_ollama_installed_not_serving(self):
        sys = FakeSystem(
            programs=[{"name": "Ollama", "vendor": "Ollama", "install_location": ""}],
            dirs=[], ports=[], procs=[])
        http_get, calls = make_http(
            {"http://127.0.0.1:11434/v1/models": Exception("refused")})
        r = LocalAIScanner(system=sys, http_get=http_get).scan()
        by_id = {i["id"]: i for i in r["installed"]}
        self.assertIn("ollama", by_id)
        self.assertEqual(by_id["ollama"]["access"], "local_not_serving")
        self.assertEqual(r["openai_endpoints"], [])

    # 4b) Ollama 11434 握手成功 → openai_endpoints + usable
    def test_ollama_serving(self):
        sys = FakeSystem(
            programs=[{"name": "Ollama", "vendor": "Ollama", "install_location": ""}],
            dirs=[],
            ports=[{"address": "127.0.0.1", "port": 11434, "pid": 7,
                    "process_name": "ollama.exe"}],
            procs=[{"name": "ollama.exe", "pid": 7}])
        body = '{"models":[{"id":"llama3"}]}'
        http_get, calls = make_http(
            {"http://127.0.0.1:11434/v1/models": body})
        r = LocalAIScanner(system=sys, http_get=http_get).scan()
        ports = [e["port"] for e in r["openai_endpoints"]]
        self.assertIn(11434, ports)
        by_id = {i["id"]: i for i in r["installed"]}
        self.assertEqual(by_id["ollama"]["access"], "openai_compatible")
        llama = [u for u in r["usable_models"] if u["model"] == "llama3"]
        self.assertTrue(llama)
        self.assertEqual(llama[0]["provider_kind"], "local_engine")

    # 5) Cherry sqlite：读 name/base_url/has_key/models/enabled；无密钥明文；
    #    有 key+https 进 usable；云端无 key 不进。
    def test_cherry_providers_and_usable(self):
        tmp = tempfile.mkdtemp(prefix="lai_test_")
        db = _make_cherry_db(tmp)
        sys = FakeSystem(programs=[], dirs=[], ports=[], procs=[], cherry_db=db)
        http_get, calls = make_http({})
        r = LocalAIScanner(system=sys, http_get=http_get).scan()

        prov = {p["name"]: p for p in r["aggregator_providers"]}
        self.assertIn("OpenAI", prov)
        oa = prov["OpenAI"]
        self.assertEqual(oa["base_url"], "https://api.openai.com")
        self.assertTrue(oa["has_key"])
        self.assertTrue(oa["enabled"])
        self.assertIn("gpt-4", oa["models"])
        # 隐藏模型不出现
        self.assertNotIn("hidden-m", oa["models"])

        # 全局无密钥明文
        dumped = json.dumps(r, ensure_ascii=False)
        self.assertNotIn(SECRET, dumped)

        # usable：gpt-4 (https+key) 在；ds-llm (云端无 key) 不在；llama3 (本地免密) 在
        models = {u["model"] for u in r["usable_models"]}
        self.assertIn("gpt-4", models)
        self.assertIn("llama3", models)
        self.assertNotIn("ds-llm", models)
        gpt4 = [u for u in r["usable_models"] if u["model"] == "gpt-4"][0]
        self.assertEqual(gpt4["provider_kind"], "cloud_client")
        self.assertTrue(gpt4["key_present"])

    # 6) 全空 FakeSystem → 不抛错，各列表为空
    def test_empty_system(self):
        sys = FakeSystem(programs=[], dirs=[], ports=[], procs=[], cherry_db=None)
        http_get, calls = make_http({})
        r = LocalAIScanner(system=sys, http_get=http_get).scan()
        self.assertEqual(r["installed"], [])
        self.assertEqual(r["openai_endpoints"], [])
        self.assertEqual(r["aggregator_providers"], [])
        self.assertEqual(r["usable_models"], [])
        self.assertTrue(isinstance(r["notes"], list) and len(r["notes"]) >= 1)

    # 加固：注册表中文 DisplayName 正确归类 + DoubaoIME(注册表+目录) 排除
    #       + DoubaoCache/KimiData 缓存目录不误报
    def test_chinese_registry_and_ime_and_cache_dirs(self):
        sys = FakeSystem(
            programs=[
                {"name": "豆包", "vendor": "Beijing Chuntian Zhiyun",
                 "install_location": ""},
                {"name": "豆包输入法", "vendor": "Beijing Chuntian Zhiyun",
                 "install_location": r"C:\Program Files\DoubaoIME"},
                {"name": "腾讯元宝", "vendor": "腾讯科技",
                 "install_location": r"F:\Yuanbao"},
                {"name": "夸克", "vendor": "广州市动悦",
                 "install_location": r"%LOCALAPPDATA%\Programs\Quark"},
                {"name": "通义千问", "vendor": "Alibaba",
                 "install_location": r"%LOCALAPPDATA%\Programs\QianwenApp"},
                {"name": "Kimi", "vendor": "Moonshot", "install_location": ""},
                {"name": "Ollama", "vendor": "Ollama", "install_location": ""},
                {"name": "Cherry Studio", "vendor": "Cherry", "install_location": ""},
            ],
            dirs=[{"path": r"F:\Doubao"}, {"path": r"F:\Yuanbao"},
                  {"path": r"C:\Program Files\DoubaoIME"},
                  {"path": r"E:\DoubaoCache"}, {"path": r"F:\KimiData"},
                  {"path": r"F:\kimi"}],
            ports=[], procs=[])
        http_get, calls = make_http({
            "http://127.0.0.1:11434/v1/models": Exception("refused"),
            "http://127.0.0.1:1234/v1/models": Exception("refused"),
        })
        r = LocalAIScanner(system=sys, http_get=http_get).scan()
        ids = _ids(r["installed"])
        # 应恰好识别这 7 个
        self.assertEqual(ids, {"cherry_studio", "doubao", "kimi", "ollama",
                               "qianwen", "quark", "yuanbao"})
        by_id = {i["id"]: i for i in r["installed"]}
        self.assertEqual(by_id["doubao"]["kind"], "cloud_client")
        self.assertEqual(by_id["quark"]["kind"], "cloud_client")
        self.assertEqual(by_id["qianwen"]["kind"], "cloud_client")
        # DoubaoIME 绝不出现；缓存/数据目录不产生额外 id
        self.assertNotIn("doubaoime", ids)
        self.assertEqual(len(ids), 7)

    # 7) 全局无泄密：注入假密钥，断言任何返回字段不含它
    def test_no_secret_leak(self):
        tmp = tempfile.mkdtemp(prefix="lai_test_")
        db = _make_cherry_db(tmp)
        sys = FakeSystem(
            programs=[{"name": "Kimi", "vendor": "Moonshot", "install_location": ""}],
            dirs=[{"path": "F:\\Doubao"}],
            ports=[{"address": "127.0.0.1", "port": 49853, "pid": 1,
                    "process_name": "doubao.exe"}],
            procs=[{"name": "doubao.exe", "pid": 1}],
            cherry_db=db)
        http_get, calls = make_http(
            {"http://127.0.0.1:49853/v1/models": Exception("x"),
             "http://127.0.0.1:11434/v1/models": Exception("x"),
             "http://127.0.0.1:1234/v1/models": Exception("x")})
        r = LocalAIScanner(system=sys, http_get=http_get).scan()
        dumped = json.dumps(r, ensure_ascii=False)
        self.assertNotIn(SECRET, dumped)
        # 任何字段不得等于密钥
        def walk(o):
            if isinstance(o, dict):
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
            else:
                self.assertNotEqual(o, SECRET)
        walk(r)


if __name__ == "__main__":
    unittest.main()
