# -*- coding: utf-8 -*-
"""离线测试：本地模型注册的同卷硬链接 blob 预置 / 幂等 / 回退。"""
import os
import tempfile

from services import local_model_manager as lmm


def _make_layout():
    tmp = tempfile.mkdtemp()
    ml = os.path.join(tmp, "models_local")
    store = os.path.join(tmp, "ollama_data", "models")
    os.makedirs(ml)
    sources = {}
    for tag, meta in lmm.REGISTRY.items():
        p = os.path.join(ml, meta["file"])
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        data = (b"fake-gguf-" + tag.encode()) * 100
        with open(p, "wb") as f:
            f.write(data)
        sources[tag] = (p, data)
    return ml, store, sources


def test_preseed_creates_hardlink_blob():
    ml, store, sources = _make_layout()
    created = []

    def fake_runner(tag, mf):
        created.append(tag)
        return 0, "ok"

    res = lmm.ensure_registered(
        models_local_root=ml, ollama_exe="ollama",
        create_runner=fake_runner, tags_lister=lambda: set(),
        models_store_dir=store)

    assert res["status"] == "OK"
    for tag, (p, data) in sources.items():
        dig = lmm.sha256_file(p)
        blob = os.path.join(store, "blobs", "sha256-" + dig)
        assert os.path.exists(blob), blob
        st_src = os.stat(p)
        st_blob = os.stat(blob)
        # 硬链接：同一 inode，链接数 >= 2，内容一致
        assert st_src.st_ino == st_blob.st_ino
        assert st_blob.st_nlink >= 2
        with open(blob, "rb") as f:
            assert f.read() == data
        assert res["models"][tag]["blob_preseeded"] is True
        assert res["models"][tag]["status"] == "created"


def test_idempotent_second_call_skips_create():
    ml, store, _ = _make_layout()
    calls = []

    def runner(tag, mf):
        calls.append(tag)
        return 0, "ok"

    present = set(lmm.REGISTRY.keys())
    res = lmm.ensure_registered(
        models_local_root=ml, create_runner=runner,
        tags_lister=lambda: present, models_store_dir=store)
    assert calls == []  # 已存在 -> 不再 create / 不拷贝
    for tag in lmm.REGISTRY:
        assert res["models"][tag]["status"] == "present"


def test_fallback_when_hardlink_fails():
    ml, store, sources = _make_layout()
    real_link = os.link

    def boom(src, dst):
        raise OSError("simulated cross-volume / unsupported link")

    os.link = boom
    try:
        res = lmm.ensure_registered(
            models_local_root=ml, create_runner=lambda t, m: (0, "ok"),
            tags_lister=lambda: set(), models_store_dir=store)
    finally:
        os.link = real_link

    assert res["status"] == "OK"
    for tag in lmm.REGISTRY:
        # 硬链接失败 -> seeded False，但常规 create 仍标记成功
        assert res["models"][tag]["blob_preseeded"] is False
        assert res["models"][tag]["status"] == "created"


def test_preseed_direct_helper():
    ml, store, sources = _make_layout()
    tag = next(iter(lmm.REGISTRY))
    p, _ = sources[tag]
    assert lmm.preseed_model_blob(p, store) is True
    dig = lmm.sha256_file(p)
    blob = os.path.join(store, "blobs", "sha256-" + dig)
    assert os.path.exists(blob)
    # 再次预置幂等
    assert lmm.preseed_model_blob(p, store, digest=dig) is True
    # 空 store -> False（不抛错）
    assert lmm.preseed_model_blob(p, None) is False
