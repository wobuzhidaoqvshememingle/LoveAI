# -*- coding: utf-8 -*-
"""P0 卡11：RS K=3+M=1 多模型竞速共识 单元测试。

全部使用 fake callable，不发任何真实网络请求。覆盖：
1. 三块完全一致 → agreed=True，chosen_index=0
2. 一块明显错误（长度/语义极端偏离）→ 校验方选中正确 index，judge_reason 非空
3. 三块两两分歧（1 vs 1 vs 1）→ 校验方按启发式裁决，返回结构完整
4. 某数据块抛异常 → 被捕获并在 blocks 里标记 error，不炸 run()
5. 校验器抛异常 → 降级为"取主块"且 judge_reason == "judge_failed"
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.rs_consensus import RSConsensusK3M1  # noqa: E402
from services.consensus_signal import RSConsensusK3M1 as ExportedRSConsensusK3M1  # noqa: E402


# --------------------------------------------------------------------------- #
# 通用 fake 工具
# --------------------------------------------------------------------------- #
def _const(text):
    """返回一个永远吐 text 的 block fake。"""
    def _fn(prompt):
        return text
    _fn.__name__ = f"const[{text[:16]!r}]"
    return _fn


def _raise(exc):
    def _fn(prompt):
        raise exc
    _fn.__name__ = "raise"
    return _fn


def _heuristic_judge(prompt, blocks):
    """一个简单的"校验方"fake：
    - 全部成功且一致 → (0, "all_agree")
    - 否则跳过 error 块，取长度中位数块（启发式：极端短/长通常是跑偏块）
    """
    ok_idx = [i for i, b in enumerate(blocks) if isinstance(b, str)]
    if not ok_idx:
        return 0, "no_ok_block"
    oks = [(i, blocks[i]) for i in ok_idx]
    texts = [b for _, b in oks]
    # 完全一致
    if all(t.strip() == texts[0].strip() for t in texts):
        return 0, "all_agree"
    # 按长度排序取中位数
    oks_sorted = sorted(oks, key=lambda x: len(x[1]))
    median = oks_sorted[len(oks_sorted) // 2]
    return median[0], f"heuristic_median_len={len(median[1])}"


def _raise_judge(prompt, blocks):
    raise RuntimeError("judge boom")


# --------------------------------------------------------------------------- #
# 测试
# --------------------------------------------------------------------------- #
def test_all_three_blocks_identical():
    """场景1：三块完全一致 → agreed=True，chosen_index=0。"""
    same = "我们一起先做A再做B"
    rs = RSConsensusK3M1(_const(same), _const(same), _const(same),
                         _heuristic_judge)
    out = rs.run("prompt-x")
    assert out["agreed"] is True
    assert out["chosen_index"] == 0
    assert len(out["blocks"]) == 3
    assert all(isinstance(b, str) for b in out["blocks"])
    assert out["judge_reason"]  # 非空


def test_one_block_obviously_wrong():
    """场景2：一块明显跑偏（极短/乱码）→ 校验方选中多数派那块。"""
    good = "建议：先把共识信号拆成独立模块，再挂到罗盘上"
    good2 = "建议：先把共识信号拆成独立模块，再挂到罗盘上"
    bad = "嗯"  # 明显跑偏
    rs = RSConsensusK3M1(_const(good), _const(bad), _const(good2),
                         _heuristic_judge)
    out = rs.run("怎么重构")
    assert out["agreed"] is False  # 三块不一致
    # 中位数长度应该是 good（len(good) vs len(bad)=1 vs len(good2)）
    # 排序：bad(1), good(18), good2(18) → median 落在 index 0 或 2
    assert out["chosen_index"] in (0, 2)
    assert out["judge_reason"]  # 非空
    # 选中的块必须是 good 之一，不是 bad
    assert out["blocks"][out["chosen_index"]] == good


def test_three_blocks_pairwise_divergent():
    """场景3：三块两两不同 → 返回结构完整，chosen_index 在 0/1/2 内。"""
    b1 = "方案A：拆模块"
    b2 = "方案B：先写测试再改代码"
    b3 = "方案C：直接上 LLM 抽取"
    rs = RSConsensusK3M1(_const(b1), _const(b2), _const(b3),
                         _heuristic_judge)
    out = rs.run("选哪条路")
    assert out["agreed"] is False
    assert out["chosen_index"] in (0, 1, 2)
    assert len(out["blocks"]) == 3
    assert all(isinstance(b, str) for b in out["blocks"])
    assert isinstance(out["judge_reason"], str)


def test_block_exception_does_not_crash_run():
    """场景4：某个数据块抛异常 → blocks 里该位是 error dict，run() 不炸。"""
    good = "正常输出"
    rs = RSConsensusK3M1(_const(good),
                         _raise(ValueError("boom-block")),
                         _const(good),
                         _heuristic_judge)
    out = rs.run("p")
    # 不抛异常
    assert isinstance(out, dict)
    assert len(out["blocks"]) == 3
    # index=1 是 error
    err_block = out["blocks"][1]
    assert isinstance(err_block, dict)
    assert err_block.get("ok") is False
    assert "error" in err_block
    # 另两块是 str
    assert isinstance(out["blocks"][0], str)
    assert isinstance(out["blocks"][2], str)
    # 校验方应该跳过 error 块；0 和 2 内容一致
    assert out["agreed"] is True  # 2 个成功块一致
    assert out["chosen_index"] in (0, 2)


def test_judge_exception_falls_back_to_primary():
    """场景5：校验器抛异常 → 降级取主块，judge_reason == 'judge_failed'。"""
    rs = RSConsensusK3M1(_const("主块内容"),
                         _const("次块内容"),
                         _const("第三块"),
                         _raise_judge)
    out = rs.run("p")
    assert out["judge_reason"] == "judge_failed"
    # 主块是 index=0（首个非 error 块）
    assert out["chosen_index"] == 0
    assert out["blocks"][0] == "主块内容"


def test_thin_export_from_consensus_signal():
    """薄导出：从 services.consensus_signal 能直接拿到 RSConsensusK3M1。"""
    assert ExportedRSConsensusK3M1 is RSConsensusK3M1


# --------------------------------------------------------------------------- #
# from_llm_gateway 工厂（mock gateway，不真发请求）
# --------------------------------------------------------------------------- #
class _FakeCandidate:
    """模拟 free_model_router.Candidate 的鸭子类型。"""
    def __init__(self, provider, base_url, api_key, model):
        self.provider = provider
        self.base_url = base_url
        self.api_key = api_key
        self.model = model


class _FakeRouter:
    def __init__(self, candidates, chat_resp_map=None):
        self._cands = candidates
        # chat_resp_map: model_name -> 该模型被直打时返回的文本
        self._chat_resp = chat_resp_map or {}

    def candidates(self):
        return list(self._cands)


class _FakeGateway:
    """模拟 LLMGateway：get_free_router() 返回 fake router；
    我们把"直打 openai client"这一步替换成查表，避免真联网。"""
    def __init__(self, router, model_responses, judge_response):
        self._router = router
        self._model_responses = model_responses   # model_name -> str
        self._judge_response = judge_response     # 校验模型被打时返回的原文

    def get_free_router(self):
        return self._router


def _patch_call_candidate(monkeypatch, gateway):
    """把 RSConsensusK3M1._call_candidate 替换成查表，避免 import openai 真发请求。"""
    def _fake_call(cand, system_prompt, user_prompt,
                   temperature=0.7, max_tokens=600, timeout=45.0):
        # 如果 user_prompt 里含"用户问题"字样 → 是 judge 调用
        if "用户问题" in user_prompt or "候选" in user_prompt:
            return gateway._judge_response
        # 否则是 block 调用，按 cand.model 查表
        return gateway._model_responses.get(cand.model, f"[default:{cand.model}]")
    monkeypatch.setattr(RSConsensusK3M1, "_call_candidate", staticmethod(_fake_call))


def test_from_llm_gateway_end_to_end(monkeypatch):
    """工厂构造 4 callable，run() 端到端跑通，不真发网络请求。"""
    cands = [
        _FakeCandidate("pollinations", "https://x", "k", "openai-fast"),
        _FakeCandidate("pollinations", "https://x", "k", "llama"),
        _FakeCandidate("pollinations", "https://x", "k", "mistral"),
    ]
    router = _FakeRouter(cands)
    gw = _FakeGateway(
        router,
        model_responses={
            "openai-fast": "RS 码用 (n,k) 分组码，把 k 个数据块扩成 n 个校验块。",
            "llama": "里德所罗门是一种纠错码，通过冗余块恢复丢失数据。",
            "mistral": "RS 是线性分组码，多用于光盘/二维码/深空通信。",
        },
        judge_response="INDEX=1 REASON=最切题、解释了分组码本质。",
    )
    _patch_call_candidate(monkeypatch, gw)

    rs = RSConsensusK3M1.from_llm_gateway(
        gw,
        block_models=["openai-fast", "llama", "mistral"],
        judge_model="llama",  # 校验方也用 llama（mock 环境）
    )
    out = rs.run("什么是 RS 纠错码？")
    assert out["chosen_index"] == 1
    assert out["judge_reason"].startswith("最切题")
    assert len(out["blocks"]) == 3
    assert all(isinstance(b, str) for b in out["blocks"])
    assert out["agreed"] is False  # 三块措辞不同


def test_from_llm_gateway_missing_model_marks_error(monkeypatch):
    """block_models 里有一个不在候选池 → 该位被标 error dict，run() 不炸。"""
    cands = [
        _FakeCandidate("pollinations", "https://x", "k", "openai-fast"),
        _FakeCandidate("pollinations", "https://x", "k", "llama"),
    ]
    router = _FakeRouter(cands)
    gw = _FakeGateway(
        router,
        model_responses={"openai-fast": "A", "llama": "B"},
        judge_response="INDEX=0 REASON=另两块挂了。",
    )
    _patch_call_candidate(monkeypatch, gw)

    rs = RSConsensusK3M1.from_llm_gateway(
        gw,
        block_models=["openai-fast", "no-such-model", "llama"],
        judge_model="llama",
    )
    out = rs.run("p")
    # index=1 不在候选池 → error dict
    assert isinstance(out["blocks"][1], dict)
    assert out["blocks"][1].get("ok") is False
    assert "no-such-model" in out["blocks"][1].get("error", "")
    # 其余两块是 str
    assert out["blocks"][0] == "A"
    assert out["blocks"][2] == "B"
    # 校验方仍被调用（用 llama），返回 INDEX=0
    assert out["chosen_index"] == 0
