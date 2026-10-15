# -*- coding: utf-8 -*-
"""DesktopAIConnector（R3.2-01）离线单元测试。

全部用 Fake 文件系统/注册表/解密器，不开真实 App、不读真实注册表、不联网；
shim 的活测试只绑 127.0.0.1:0 回环、请求后立即关闭。
"""
import json
import os
import sys
import threading
import unittest
import urllib.request

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.desktop_ai_connector import (  # noqa: E402
    APP_REGISTRY, MODEL_GROUPS, ONLINE_VERIFICATION_NEEDED,
    AppDecryptor, AppNotInstalled, CredentialDecryptUnavailable,
    CredentialRecord, CredentialVault, DesktopAIConnector, SecureShim,
    SourceDescriptor, UnavailableDecryptor, WindowsSystemOps,
    authorize_request, build_program_dir_candidates, build_storage_path_candidates,
    build_tool_result_message, openai_to_remote_chat, parse_sse_events,
    parse_storage_credentials, parse_usage, playwright_available,
    remote_events_to_openai, route_request,
)

SECRET_PLAINTEXT = "sk-PLAINTEXT-REFRESH-1234567890abcdef"
FAKE_BLOB = "v10" + "QUJDREVGRw==" * 10  # 形如 v10 base64 safeStorage blob


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeSystemOps:
    """罐装文件系统/注册表：显式给出存在的路径、storage 内容、卸载条目。"""

    def __init__(self, env=None, existing=None, storage_text=None,
                 uninstall=None):
        self._env = env or {"APPDATA": r"C:\Users\t\AppData\Roaming",
                            "LOCALAPPDATA": r"C:\Users\t\AppData\Local"}
        self._existing = set(existing or [])
        self._storage = storage_text or {}   # path -> text
        self._uninstall = uninstall or []

    def env(self):
        return dict(self._env)

    def exists(self, path):
        return path in self._existing

    def read_text(self, path):
        return self._storage.get(path)

    def uninstall_entries(self):
        return [dict(e) for e in self._uninstall]


class RecordingDecryptor:
    """Fake App 自带解密器：记录调用时传入的 App 身份，断言走 App 自身接口。"""

    def __init__(self):
        self.calls = []

    def decrypt(self, app, sealed_blob):
        self.calls.append({"app": app, "blob_len": len(sealed_blob)})
        return "decrypted-by-app-runtime"


def _entry(app_id):
    for e in APP_REGISTRY:
        if e["app_id"] == app_id:
            return e
    raise AssertionError(app_id)


# ---------------------------------------------------------------------------
# 1) APP_REGISTRY 覆盖度
# ---------------------------------------------------------------------------
class TestAppRegistry(unittest.TestCase):
    def test_named_apps_and_flavors_present(self):
        ids = {e["app_id"] for e in APP_REGISTRY}
        # 需求点名：Trae（CN / SOLO CN / 国际 trae-global）+ WorkBuddy/Qoder/CodeBuddy
        self.assertIn("trae_cn", ids)
        self.assertIn("trae_solo_cn", ids)
        self.assertIn("trae_global", ids)
        self.assertIn("workbuddy", ids)
        self.assertIn("qoder", ids)
        self.assertIn("codebuddy", ids)

    def test_required_fields_every_entry(self):
        required = ["app_id", "display", "flavor", "process_names", "exe_names",
                     "data_dir_spellings", "program_dir_spellings",
                     "storage_rel", "uninstall_display_keywords",
                     "remote_protocol", "model_group"]
        for e in APP_REGISTRY:
            for k in required:
                self.assertIn(k, e, f"{e.get('app_id')} 缺字段 {k}")
            self.assertTrue(e["data_dir_spellings"], e["app_id"])

    def test_no_chat_shells(self):
        # 不含豆包/元宝等聊天壳
        ids = {e["app_id"] for e in APP_REGISTRY}
        self.assertNotIn("doubao", ids)
        self.assertNotIn("yuanbao", ids)

    def test_trae_flavors_cn_and_global_coexist(self):
        flavors = {e["flavor"] for e in APP_REGISTRY if e["app_id"].startswith("trae")}
        self.assertIn("cn", flavors)
        self.assertIn("global", flavors)
        self.assertIn("cn_solo", flavors)

    def test_module_does_not_import_future_wired_modules(self):
        # desktop_ai_connector 自身不得连带 import free_model_router / local_ai_scanner。
        # 在全新子进程中“仅”导入该模块后检查，避免同进程内其它测试已导入造成的污染。
        import subprocess
        code = (
            "import sys; "
            "import services.desktop_ai_connector; "
            "print('FMR', 'services.free_model_router' in sys.modules); "
            "print('LAS', 'services.local_ai_scanner' in sys.modules)"
        )
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, cwd=_PROJECT_ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FMR False", r.stdout)
        self.assertIn("LAS False", r.stdout)


# ---------------------------------------------------------------------------
# 2) 候选路径拼装（纯函数）
# ---------------------------------------------------------------------------
class TestPathCandidates(unittest.TestCase):
    def test_storage_multi_spelling(self):
        env = {"APPDATA": r"C:\Roaming", "LOCALAPPDATA": r"C:\Local"}
        cands = build_storage_path_candidates(_entry("trae_cn"), env)
        self.assertTrue(cands)
        for spelling in _entry("trae_cn")["data_dir_spellings"]:
            self.assertTrue(any(spelling in c and c.endswith(r"User\globalStorage\storage.json")
                               for c in cands), spelling)

    def test_program_dir_before_registry_order(self):
        env = {"APPDATA": r"C:\Roaming", "LOCALAPPDATA": r"C:\Local"}
        progs = build_program_dir_candidates(_entry("workbuddy"), env)
        self.assertEqual(progs, [r"C:\Local\Programs\WorkBuddy"])

    def test_empty_spelling_skipped(self):
        env = {"APPDATA": r"C:\Roaming", "LOCALAPPDATA": r"C:\Local"}
        cands = build_storage_path_candidates(_entry("workbuddy_ai"), env)
        self.assertTrue(all(c for c in cands))


# ---------------------------------------------------------------------------
# 3) 安装/数据目录发现顺序（Fake 系统）
# ---------------------------------------------------------------------------
class TestDiscovery(unittest.TestCase):
    def _connector(self, ops):
        return DesktopAIConnector(system_ops=ops)

    def test_program_dir_found_first(self):
        env = {"APPDATA": r"C:\Roaming", "LOCALAPPDATA": r"C:\Local"}
        prog = r"C:\Local\Programs\Trae"
        ops = FakeSystemOps(env=env, existing={prog})
        desc = self._connector(ops).discover("trae_cn")
        self.assertIsNotNone(desc)
        self.assertEqual(desc.source_id, "trae_cn")

    def test_registry_fallback_when_no_program_dir(self):
        env = {"APPDATA": r"C:\Roaming", "LOCALAPPDATA": r"C:\Local"}
        ops = FakeSystemOps(
            env=env, existing=set(),
            uninstall=[{"name": "Trae", "install_location": r"C:\Trae"}])
        desc = self._connector(ops).discover("trae_cn")
        self.assertIsNotNone(desc)

    def test_not_installed_returns_none(self):
        ops = FakeSystemOps()
        self.assertIsNone(self._connector(ops).discover("qoder"))

    def test_storage_absent_status_needs_login(self):
        env = {"APPDATA": r"C:\Roaming", "LOCALAPPDATA": r"C:\Local"}
        prog = r"C:\Local\Programs\Qoder"
        ops = FakeSystemOps(env=env, existing={prog})  # 无 storage.json
        desc = self._connector(ops).discover("qoder")
        self.assertEqual(desc.status, "needs_login")
        self.assertNotEqual(desc.status, "available")  # 未探测不得宣称可用

    def test_creds_found_status_unknown_not_available(self):
        env = {"APPDATA": r"C:\Roaming", "LOCALAPPDATA": r"C:\Local"}
        entry = _entry("qoder")
        sp = build_storage_path_candidates(entry, env)[0]
        ops = FakeSystemOps(env=env, existing={sp},
                            storage_text={sp: json.dumps({"auth": {"refreshToken": SECRET_PLAINTEXT}})})
        desc = self._connector(ops).discover("qoder")
        self.assertEqual(desc.status, "unknown")
        self.assertNotEqual(desc.status, "available")

    def test_no_auto_switch_accounts_one_descriptor(self):
        # storage 里有两个账号 -> 每个 App 只出一个描述符（不替用户选号）
        env = {"APPDATA": r"C:\Roaming", "LOCALAPPDATA": r"C:\Local"}
        entry = _entry("trae_cn")
        sp = build_storage_path_candidates(entry, env)[0]
        payload = {"accounts": [{"refreshToken": SECRET_PLAINTEXT},
                                {"refreshToken": SECRET_PLAINTEXT + "X"}]}
        ops = FakeSystemOps(env=env, existing={sp}, storage_text={sp: json.dumps(payload)})
        conns = self._connector(ops)
        descs = [d for d in conns.discover_all() if d.source_id == "trae_cn"]
        self.assertEqual(len(descs), 1)
        self.assertFalse(hasattr(conns, "switch_account"))

    def test_plaintext_secret_never_in_descriptor(self):
        env = {"APPDATA": r"C:\Roaming", "LOCALAPPDATA": r"C:\Local"}
        entry = _entry("qoder")
        sp = build_storage_path_candidates(entry, env)[0]
        ops = FakeSystemOps(env=env, existing={sp},
                            storage_text={sp: json.dumps({"auth": {"refreshToken": SECRET_PLAINTEXT}})})
        desc = self._connector(ops).discover("qoder")
        self.assertNotIn(SECRET_PLAINTEXT, repr(desc))
        self.assertNotIn(SECRET_PLAINTEXT, desc.notes)


# ---------------------------------------------------------------------------
# 4) storage.json 凭据解析（明文/加密识别，明文不出返回值）
# ---------------------------------------------------------------------------
class TestCredentialParsing(unittest.TestCase):
    def test_plaintext_field_detected(self):
        text = json.dumps({"auth": {"refreshToken": SECRET_PLAINTEXT,
                                    "account": "user@example.com"}})
        recs = parse_storage_credentials(text, app_id="trae_cn", flavor="cn")
        names = {r.field_name for r in recs}
        self.assertTrue(any("refreshToken" in n for n in names), names)
        for r in recs:
            self.assertFalse(r.encrypted)
            self.assertEqual(r.app_id, "trae_cn")

    def test_v10_blob_recognized_encrypted(self):
        text = json.dumps({"token": FAKE_BLOB})
        recs = parse_storage_credentials(text)
        self.assertTrue(recs)
        self.assertTrue(all(r.encrypted for r in recs))

    def test_v11_blob_recognized_encrypted(self):
        text = json.dumps({"credential": "v11" + "QUJD" * 20})
        recs = parse_storage_credentials(text)
        self.assertTrue(recs)
        self.assertTrue(all(r.encrypted for r in recs))

    def test_plaintext_value_never_in_records(self):
        text = json.dumps({"auth": {"refreshToken": SECRET_PLAINTEXT}})
        recs = parse_storage_credentials(text)
        blob = json.dumps([r.__dict__ for r in recs], default=str)
        self.assertNotIn(SECRET_PLAINTEXT, blob)
        # CredentialRecord 没有 value 属性
        self.assertFalse(hasattr(recs[0], "value"))

    def test_garbage_json_returns_empty(self):
        self.assertEqual(parse_storage_credentials("not json at all {{{"), [])

    def test_non_credential_fields_ignored(self):
        text = json.dumps({"theme": "dark", "version": 3, "auth": None})
        self.assertEqual(parse_storage_credentials(text), [])


# ---------------------------------------------------------------------------
# 5) 解密必须走 App 自带接口（Fake 断言）
# ---------------------------------------------------------------------------
class TestDecryptor(unittest.TestCase):
    def test_unavailable_decryptor_raises_typed(self):
        dec = UnavailableDecryptor()
        with self.assertRaises(CredentialDecryptUnavailable):
            dec.decrypt(_entry("workbuddy"), FAKE_BLOB)

    def test_fake_decryptor_receives_app_identity(self):
        dec = RecordingDecryptor()
        entry = _entry("workbuddy")
        dec.decrypt(entry, FAKE_BLOB)
        self.assertEqual(len(dec.calls), 1)
        self.assertEqual(dec.calls[0]["app"]["app_id"], "workbuddy")

    def test_vault_handle_roundtrip(self):
        vault = CredentialVault()
        handle = vault.store(SECRET_PLAINTEXT)
        self.assertNotEqual(handle, SECRET_PLAINTEXT)
        self.assertEqual(vault.resolve(handle), SECRET_PLAINTEXT)
        vault.release(handle)
        with self.assertRaises(Exception):
            vault.resolve(handle)


# ---------------------------------------------------------------------------
# 6) shim 鉴权纯函数 + 活回环测试
# ---------------------------------------------------------------------------
class TestShimAuth(unittest.TestCase):
    def test_missing_secret_rejected(self):
        self.assertFalse(authorize_request({}, "abc"))
        self.assertFalse(authorize_request({"Authorization": "Bearer "}, "abc"))

    def test_wrong_secret_rejected(self):
        self.assertFalse(authorize_request({"Authorization": "Bearer wrong"}, "abc"))

    def test_correct_secret_bearer_accepted(self):
        self.assertTrue(authorize_request({"Authorization": "Bearer abc"}, "abc"))

    def test_custom_header_accepted(self):
        self.assertTrue(authorize_request({"X-LoveAI-Secret": "abc"}, "abc"))


class TestShimRoutePure(unittest.TestCase):
    def test_models_route(self):
        status, body = route_request("GET", "/v1/models", {}, "s",
                                     models=["GLM-5.3"])
        self.assertEqual(status, 200)
        self.assertEqual([m["id"] for m in body["data"]], ["GLM-5.3"])

    def test_chat_without_remote_caller_503(self):
        status, body = route_request("POST", "/v1/chat/completions", {}, "s")
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["type"], "connector_unavailable")

    def test_unknown_path_404(self):
        status, _ = route_request("GET", "/nope", {}, "s")
        self.assertEqual(status, 404)

    def test_chat_delegates_with_translated_request(self):
        seen = {}

        def fake_caller(remote_req, secret=None):
            seen["req"] = remote_req
            seen["secret"] = secret
            return {"ok": True}

        req = {"model": "GLM-5.3", "messages": [{"role": "user", "content": "hi"}]}
        status, body = route_request("POST", "/v1/chat/completions", req, "s",
                                      remote_caller=fake_caller,
                                      app=_entry("trae_cn"))
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})
        self.assertEqual(seen["secret"], "s")
        self.assertEqual(seen["req"]["endpoint"],
                         "https://trae-api-cn.mchost.guru/api/agent/v3/llm_utils_chat")


class TestShimLiveLoopback(unittest.TestCase):
    """绑定 127.0.0.1:0 的本机回环测试（不联网，请求后立即关闭）。"""

    def test_start_reject_wrong_secret_accept_correct(self):
        shim = SecureShim(_entry("trae_cn"), remote_caller=None)
        base = shim.start()
        try:
            self.assertRegex(base, r"^http://127\.0\.0\.1:\d+/v1$")
            # 错误 secret -> 401
            req = urllib.request.Request(base + "/models")
            req.add_header("Authorization", "Bearer wrong")
            try:
                urllib.request.urlopen(req, timeout=5)
                self.fail("应返回 401")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 401)
            # 正确 secret -> 200
            req2 = urllib.request.Request(base + "/models")
            req2.add_header("Authorization", f"Bearer {shim.client_secret}")
            with urllib.request.urlopen(req2, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            shim.stop()
        self.assertIsNone(shim._server)


# ---------------------------------------------------------------------------
# 7) SSE 解析
# ---------------------------------------------------------------------------
class TestSSE(unittest.TestCase):
    def test_data_lines_and_done(self):
        text = 'data: {"a":1}\r\n\r\ndata: [DONE]\r\n\r\n'
        events = parse_sse_events(text)
        self.assertEqual(len(events), 2)
        self.assertEqual(json.loads(events[0]["data"]), {"a": 1})
        self.assertTrue(events[1]["done"])

    def test_multi_data_line_event(self):
        text = 'event: delta\ndata: {"x":\ndata: 1}\n\n'
        events = parse_sse_events(text)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "delta")

    def test_comment_heartbeat_ignored(self):
        text = ': ping\n\ndata: {"ok":1}\n\n'
        events = parse_sse_events(text)
        self.assertEqual(len(events), 1)


# ---------------------------------------------------------------------------
# 8) OpenAI <-> 远程协议翻译
# ---------------------------------------------------------------------------
class TestBridge(unittest.TestCase):
    def test_openai_to_trae_mapping(self):
        req = {"model": "GLM-5.3",
               "messages": [{"role": "system", "content": "be brief"},
                            {"role": "user", "content": "hello"}],
               "tools": [{"type": "function",
                          "function": {"name": "bash",
                                       "description": "run cmd",
                                       "parameters": {"type": "object"}}}]}
        remote = openai_to_remote_chat(_entry("trae_cn"), req)
        self.assertEqual(remote["endpoint"],
                         "https://trae-api-cn.mchost.guru/api/agent/v3/llm_utils_chat")
        self.assertEqual(remote["body"]["query"], "hello")
        self.assertTrue(any(m["role"] == "system" for m in remote["body"]["chat_history"]))
        self.assertEqual(remote["body"]["tools"][0]["function"]["name"], "bash")
        self.assertTrue(remote["identity_headers"])

    def test_openai_to_passthrough_generic(self):
        req = {"model": "DeepSeek-V4-Pro",
               "messages": [{"role": "user", "content": "hi"}]}
        remote = openai_to_remote_chat(_entry("workbuddy"), req)
        self.assertEqual(remote["method"], "POST")
        self.assertTrue(remote["body"]["stream"])
        self.assertFalse(remote["identity_headers"])

    def test_remote_events_content_aggregation(self):
        events = parse_sse_events(
            'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            'data: [DONE]\n\n')
        resp = remote_events_to_openai(_entry("trae_cn"), events, model="GLM-5.3")
        msg = resp["choices"][0]["message"]
        self.assertEqual(msg["content"], "Hello")
        self.assertEqual(resp["choices"][0]["finish_reason"], "stop")

    def test_remote_events_pending_function_call_to_tool_calls(self):
        # pending function_call：name 先到、arguments 增量、id 后到
        chunk1 = {"choices": [{"delta": {"function_call": {"name": "bash",
                                                          "arguments": '{"cmd":'}}}]}
        chunk2 = {"choices": [{"delta": {"function_call": {"arguments": '"ls"}'}}}]}
        chunk3 = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1"}]}}]}
        body = "\n\n".join("data: " + json.dumps(c) for c in (chunk1, chunk2, chunk3))
        events = parse_sse_events(body + "\n\ndata: [DONE]\n\n")
        resp = remote_events_to_openai(_entry("trae_cn"), events)
        tc = resp["choices"][0]["message"]["tool_calls"]
        self.assertEqual(len(tc), 1)
        self.assertEqual(tc[0]["function"]["name"], "bash")
        self.assertEqual(json.loads(tc[0]["function"]["arguments"]), {"cmd": "ls"})
        self.assertEqual(tc[0]["id"], "call_1")
        self.assertEqual(resp["choices"][0]["finish_reason"], "tool_calls")

    def test_build_tool_result_message(self):
        msg = build_tool_result_message(_entry("trae_cn"), "call_1", "done")
        self.assertEqual(msg["role"], "tool")
        self.assertEqual(msg["tool_call_id"], "call_1")
        self.assertEqual(msg["content"], "done")


# ---------------------------------------------------------------------------
# 9) 模型组注册 + 用量解析
# ---------------------------------------------------------------------------
class TestModelsAndUsage(unittest.TestCase):
    def test_required_models_present(self):
        flat = {m for group in MODEL_GROUPS.values() for m in group}
        for needed in ("GLM-5.3", "DeepSeek-V4-Pro-Official",
                       "DeepSeek-V4-Flash-Official", "Kimi-K3",
                       "MiniMax-M3", "Doubao-Seed"):
            self.assertIn(needed, flat)
        self.assertTrue(any(m.startswith("Qwen") for m in flat))

    def test_cn_and_global_groups_coexist(self):
        self.assertIn("trae_solo_cn", MODEL_GROUPS)
        self.assertIn("trae_solo_global", MODEL_GROUPS)

    def test_discover_descriptor_models_listed(self):
        env = {"APPDATA": r"C:\Roaming", "LOCALAPPDATA": r"C:\Local"}
        sp = build_storage_path_candidates(_entry("trae_cn"), env)[0]
        ops = FakeSystemOps(env=env, existing={sp},
                            storage_text={sp: json.dumps({"auth": {"token": "x"}})})
        desc = DesktopAIConnector(system_ops=ops).discover("trae_cn")
        self.assertIn("GLM-5.3", desc.models)

    def test_parse_usage_extracts(self):
        out = parse_usage({"balance": 1200, "used": 300})
        self.assertEqual(out["balance"], 1200)
        self.assertEqual(out["used"], 300)

    def test_parse_usage_unknown_honest(self):
        out = parse_usage({"weird": {"foo": "bar"}})
        self.assertEqual(out["balance"], "unknown")
        self.assertEqual(out["used"], "unknown")

    def test_parse_usage_from_json_string(self):
        out = parse_usage('{"credits": 42}')
        self.assertEqual(out["balance"], 42)


# ---------------------------------------------------------------------------
# 10) 降级：playwright 缺失 / 错误清单 / 描述符约束
# ---------------------------------------------------------------------------
class TestDegradation(unittest.TestCase):
    def test_playwright_missing_degrades_false(self):
        def boom():
            raise ImportError("No module named playwright")
        self.assertFalse(playwright_available(importer=boom))

    def test_online_verification_list_nonempty(self):
        self.assertTrue(ONLINE_VERIFICATION_NEEDED)

    def test_descriptor_status_values(self):
        valid = {"unknown", "available", "needs_login", "failed", "unavailable"}
        env = {"APPDATA": r"C:\Roaming", "LOCALAPPDATA": r"C:\Local"}
        sp = build_storage_path_candidates(_entry("qoder"), env)[0]
        ops = FakeSystemOps(env=env, existing={sp},
                            storage_text={sp: json.dumps({"auth": {"token": "x"}})})
        desc = DesktopAIConnector(system_ops=ops).discover("qoder")
        self.assertIn(desc.status, valid)
        self.assertTrue(desc.zero_key)
        self.assertTrue(desc.requires_login)
        self.assertEqual(desc.kind, "desktop_app")

    def test_app_not_installed_typed_error(self):
        with self.assertRaises(AppNotInstalled):
            build_storage_path_candidates("no_such_app", {})

    def test_single_app_failure_does_not_break_batch(self):
        class BrokenOps(FakeSystemOps):
            def uninstall_entries(self):
                raise RuntimeError("boom")
        ops = BrokenOps()
        descs = DesktopAIConnector(system_ops=ops).discover_all()
        self.assertIsInstance(descs, list)


if __name__ == "__main__":
    unittest.main()
