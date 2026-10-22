# -*- coding: utf-8 -*-
"""Reed-Solomon K=3 + M=1 多模型竞速共识（P0 卡11）。

设计要点
────────
- 不是简单 majority vote：同一条 prompt 并发打给 3 个"数据块模型"，
  再把 [block1, block2, block3] 整体交给第 4 个"校验模型"独立裁决，
  由校验方指出哪一块更靠谱 / 哪一块明显跑偏。
- 数据块抛异常不炸 run()：该位在 blocks 里以 {"ok": False, "error": ...} 标记。
- 校验模型抛异常降级为"取主块（首个非 error 块，否则 index=0）"，
  judge_reason 记为 "judge_failed"。
- 纯本地调度，不引入第三方依赖；并发用 concurrent.futures.ThreadPoolExecutor。

公开 API
────────
    rs = RSConsensusK3M1(block_fn_1, block_fn_2, block_fn_3, judge_fn)
    out = rs.run(prompt)
    out -> {
        "chosen_index": int,          # 0/1/2
        "blocks": [item, item, item], # item 为 str（成功）或 {"ok":False,"error":str}
        "judge_reason": str,          # 校验方给的理由；校验方失败时为 "judge_failed"
        "agreed": bool,               # 3 个成功块内容是否完全一致
    }

契约约定
────────
- block_fn(prompt) -> str
- judge_fn(prompt, blocks) -> (chosen_index:int, reason:str)
    blocks 元素可能是 str 或 error dict；judge 需自行跳过 error 块。
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, List, Optional, Union

logger = logging.getLogger(__name__)

BlockItem = Union[str, dict]
BlockFn = Callable[[str], str]
JudgeFn = Callable[[str, List[BlockItem]], Any]  # 返回 (idx, reason) 或 {"index":..,"reason":..}


class RSConsensusK3M1:
    """K=3 数据块 + M=1 校验方的 RS 式共识。"""

    _BLOCK_COUNT = 3

    def __init__(self,
                 block_fn_1: BlockFn,
                 block_fn_2: BlockFn,
                 block_fn_3: BlockFn,
                 judge_fn: JudgeFn,
                 *,
                 block_timeout: float = 30.0,
                 max_workers: int = 3) -> None:
        if block_fn_1 is None or block_fn_2 is None or block_fn_3 is None:
            raise ValueError("RSConsensusK3M1 需要 3 个非空的数据块 callable")
        if judge_fn is None:
            raise ValueError("RSConsensusK3M1 需要 1 个非空的校验 callable")
        self._blocks: List[BlockFn] = [block_fn_1, block_fn_2, block_fn_3]
        self._judge = judge_fn
        self._block_timeout = float(block_timeout)
        self._max_workers = int(max_workers)

    # ------------------------------------------------------------------ #
    # 工厂：从真实 LLMGateway 接线（P0 卡11 wire-up）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _find_candidate(router: Any, model_name: str) -> Optional[Any]:
        """在 FreeModelRouter.candidates() 里找一个 model 字段匹配的候选。

        匹配规则：精确相等优先；否则按子串包含匹配（忽略大小写）。
        找不到返回 None。
        """
        try:
            cands = router.candidates()
        except Exception:  # noqa: BLE001
            return None
        name = (model_name or "").strip().lower()
        if not name:
            return None
        for c in cands:
            if getattr(c, "model", "").lower() == name:
                return c
        for c in cands:
            if name in getattr(c, "model", "").lower():
                return c
        return None

    @staticmethod
    def _call_candidate(cand: Any, system_prompt: str, user_prompt: str,
                        temperature: float = 0.7,
                        max_tokens: int = 600,
                        timeout: float = 45.0) -> str:
        """用 openai 兼容 client 直打指定 Candidate（不复用 router.chat 的轮询/冷却）。"""
        import openai  # 延迟导入：测试环境 mock 时不强制要求
        client = openai.OpenAI(
            api_key=getattr(cand, "api_key", "zero-config") or "zero-config",
            base_url=getattr(cand, "base_url", None),
            timeout=timeout,
        )
        resp = client.chat.completions.create(
            model=getattr(cand, "model", ""),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()

    @classmethod
    def from_llm_gateway(cls,
                         gateway: Any,
                         block_models: List[str],
                         judge_model: str,
                         *,
                         block_temperature: float = 0.7,
                         judge_temperature: float = 0.0,
                         max_tokens: int = 600,
                         block_timeout: float = 45.0) -> "RSConsensusK3M1":
        """用真实 LLMGateway / FreeModelRouter 构造 RS 共识实例。

        Args:
            gateway: 一个 LLMGateway 实例（鸭子类型：需要 get_free_router()）。
            block_models: 3 个模型名（用于在 free_router.candidates() 里匹配候选）。
            judge_model: 校验模型名（同样在候选池里匹配）。

        每个块 callable 会：
          1. 调 gateway.get_free_router() 拿 router；
          2. 在 router.candidates() 里按 model 名找候选；
          3. 用 openai client 直打该候选（不走 router.chat 的轮询/冷却，
             保证 3 个块真的打在 3 个不同模型上，不被自动降级搅混）。

        校验 callable 会把 3 块打包成 user 消息，要求模型回
        ``INDEX=<0|1|2> REASON=<...>``，再用正则解析回 (idx, reason)。
        """
        if len(block_models) != 3:
            raise ValueError("block_models 必须是 3 个模型名")

        block_sys = (
            "你是 RS 共识的数据块生成器。请用简洁、准确、事实性的语言回答用户问题。"
            "不要客套、不要重复问题、不要加 markdown 格式。"
        )
        judge_sys = (
            "你是 RS 共识的独立校验方（cross-check judge）。"
            "用户问题会给出 3 个候选回答（来自不同模型）。"
            "请判断哪一个最准确、最完整、最切题。"
            "你必须只输出一行，格式严格为：\n"
            "INDEX=<0 或 1 或 2> REASON=<你的一句话理由>\n"
            "不要输出其它任何内容。"
        )

        def _make_block_fn(model_name: str) -> BlockFn:
            def _fn(prompt: str) -> str:
                router = gateway.get_free_router()
                cand = cls._find_candidate(router, model_name)
                if cand is None:
                    raise RuntimeError(
                        f"model {model_name!r} 不在 free_router 候选池里"
                    )
                return cls._call_candidate(
                    cand, block_sys, prompt,
                    temperature=block_temperature,
                    max_tokens=max_tokens,
                    timeout=block_timeout,
                )
            _fn.__name__ = f"rs_block[{model_name}]"
            return _fn

        def _judge_fn(prompt: str, blocks: List[BlockItem]) -> tuple:
            # 把 3 块拼成 user 消息；error 块明确标注
            lines = [f"用户问题：{prompt}", ""]
            for i, b in enumerate(blocks):
                if isinstance(b, str):
                    snippet = b if len(b) <= 800 else b[:800] + "…[截断]"
                    lines.append(f"[候选 {i}] (来自模型)：\n{snippet}\n")
                else:
                    err = (b or {}).get("error", "unknown")
                    lines.append(f"[候选 {i}] (生成失败: {err})\n")
            user_msg = "\n".join(lines)

            router = gateway.get_free_router()
            cand = cls._find_candidate(router, judge_model)
            if cand is None:
                raise RuntimeError(
                    f"judge_model {judge_model!r} 不在 free_router 候选池里"
                )
            raw = cls._call_candidate(
                cand, judge_sys, user_msg,
                temperature=judge_temperature,
                max_tokens=300,
                timeout=block_timeout,
            )
            # 解析 INDEX=n REASON=...
            m = re.search(r"INDEX\s*=\s*([012])", raw)
            idx = int(m.group(1)) if m else 0
            r = re.search(r"REASON\s*=\s*(.+?)(?:\n|$)", raw, re.DOTALL)
            reason = r.group(1).strip() if r else raw.strip()[:200]
            return idx, reason

        return cls(
            _make_block_fn(block_models[0]),
            _make_block_fn(block_models[1]),
            _make_block_fn(block_models[2]),
            _judge_fn,
            block_timeout=block_timeout,
        )

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _run_one_block(self, idx: int, fn: BlockFn, prompt: str) -> tuple:
        """跑单个数据块；异常吞掉并返回 error 占位 dict。"""
        try:
            out = fn(prompt)
            return idx, out if isinstance(out, str) else str(out)
        except Exception as e:  # noqa: BLE001 - 数据块失败不应炸 run()
            logger.warning("rs_consensus block[%d] failed: %s", idx, e)
            return idx, {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @staticmethod
    def _is_ok(item: BlockItem) -> bool:
        return isinstance(item, str)

    def _first_ok_index(self, blocks: List[BlockItem]) -> int:
        for i, b in enumerate(blocks):
            if self._is_ok(b):
                return i
        return 0

    def _agreed(self, blocks: List[BlockItem]) -> bool:
        oks = [b.strip() for b in blocks if self._is_ok(b)]
        if len(oks) < 2:
            # 少于 2 个成功块谈不上"一致"
            return False
        first = oks[0]
        return all(x == first for x in oks[1:])

    @staticmethod
    def _parse_judge_result(raw: Any) -> tuple:
        """把 judge 返回值归一化成 (index:int, reason:str)。"""
        if isinstance(raw, tuple) or isinstance(raw, list):
            if len(raw) >= 2:
                return int(raw[0]), str(raw[1])
            if len(raw) == 1:
                return int(raw[0]), ""
        if isinstance(raw, dict):
            idx = raw.get("index", raw.get("chosen_index", 0))
            reason = raw.get("reason", raw.get("judge_reason", ""))
            return int(idx), str(reason)
        # 其它形式：整体当 reason
        return 0, str(raw)

    # ------------------------------------------------------------------ #
    # 公开入口
    # ------------------------------------------------------------------ #
    def run(self, prompt: str) -> dict:
        # 1) 并发跑 3 个数据块
        blocks: List[BlockItem] = [None, None, None]  # type: ignore[list-item]
        with ThreadPoolExecutor(max_workers=self._max_workers) as ex:
            futs = {
                ex.submit(self._run_one_block, i, fn, prompt): i
                for i, fn in enumerate(self._blocks)
            }
            for fut in as_completed(futs, timeout=self._block_timeout + 5.0):
                idx, item = fut.result()
                blocks[idx] = item

        # 兜底：理论上 3 个 fut 都完成才退出 with；若仍有 None 视为 error
        for i in range(self._BLOCK_COUNT):
            if blocks[i] is None:
                blocks[i] = {"ok": False, "error": "block_returned_none"}

        # 2) 校验方裁决
        agreed = self._agreed(blocks)
        fallback_idx = self._first_ok_index(blocks)
        try:
            raw = self._judge(prompt, blocks)
            chosen_index, judge_reason = self._parse_judge_result(raw)
            # 校验方返回 error 块索引时回退到主块
            if not self._is_ok(blocks[chosen_index]):
                judge_reason = (judge_reason + " | " if judge_reason else "") + \
                    "judge_picked_error_block, fallback to first_ok"
                chosen_index = fallback_idx
        except Exception as e:  # noqa: BLE001 - 校验方失败降级
            logger.warning("rs_consensus judge failed: %s", e)
            chosen_index = fallback_idx
            judge_reason = "judge_failed"

        return {
            "chosen_index": int(chosen_index),
            "blocks": blocks,
            "judge_reason": str(judge_reason),
            "agreed": bool(agreed),
        }


__all__ = ["RSConsensusK3M1"]
