# -*- coding: utf-8 -*-
"""核对全需求补完报告里点名的 29 项落点是否真在代码里"""
import re, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

claims = [
    ("R3.3-14 voice_pipeline.LiveKitPipelineAdapter", "services/voice_pipeline.py", "LiveKitPipelineAdapter"),
    ("R3.3-35 smart_turn.py", "services/smart_turn.py", None),
    ("R3.3-36 chinese_eot.py", "services/chinese_eot.py", None),
    ("R3.3-30 fault_router.py", "services/fault_router.py", None),
    ("R3.3-31 startup_probe.py", "services/startup_probe.py", None),
    ("R3.3-32 free_model_router.CooldownTable", "services/free_model_router.py", "CooldownTable"),
    ("R3.3-33 scam_patterns.py", "services/scam_patterns.py", None),
    ("R3.3-34 memory_gate.py", "services/memory_gate.py", None),
    ("R3.3-34 memory_service.guarded_write_calibration", "services/memory_service.py", "guarded_write_calibration"),
    ("R3.3-41 llm_gateway.diagnose_model_gaps", "services/llm_gateway.py", "diagnose_model_gaps"),
    ("R3.3-42 hf_ssh_channel.py", "services/hf_ssh_channel.py", None),
    ("R3.3-43 free_channel_catalog.py", "services/free_channel_catalog.py", None),
    ("R3.3-21 app/dynamic_background.py", "app/dynamic_background.py", None),
    ("R3.3-22 app/widgets/analysis_output_card.py", "app/widgets/analysis_output_card.py", None),
    ("R3.3-23 confidence_cap.py", "services/confidence_cap.py", None),
    ("R3.3-26 relationship_graph_widget.py", "app/widgets/common/relationship_graph_widget.py", None),
    ("R3.3-44 psych_catalog.py", "services/psych_catalog.py", None),
    ("R3.3-45 six_layer_persona.to_six_layer", "services/six_layer_persona.py", "to_six_layer"),
    ("R3.3-46 superstition_playalong.py", "services/superstition_playalong.py", None),
    ("R3.3-47 auto_strategy_pipeline.py", "services/auto_strategy_pipeline.py", None),
    ("R3.3-49 immersive_voice_window.py", "app/widgets/immersive_voice_window.py", None),
    ("R3.3-50 portrait_generator.py", "services/portrait_generator.py", None),
    ("R3.3-51 pet_animator.py", "services/pet_animator.py", None),
    ("R3.3-56 realtime_digital_human.py", "services/realtime_digital_human.py", None),
    ("R3.3-12 attribution_registry.py", "services/attribution_registry.py", None),
    ("R3.3-57 data_pool package", "services/data_pool/__init__.py", None),
    ("R3.3-28 training_engine.py", "services/training_engine.py", None),
    ("R3.3-28 training_data_collector.py", "services/training_data_collector.py", None),
    ("R3.3-17 local_ai_scanner.py", "services/local_ai_scanner.py", None),
    ("R3.3-17 browser_ai_hub.py", "services/browser_ai_hub.py", None),
    ("R3.3-09 relationship_graph.py", "services/relationship_graph.py", None),
    ("R3.3-xx script_library.py", "services/script_library.py", None),
    ("R3.3-xx output_normalizer.py", "services/output_normalizer.py", None),
    ("R3.3-xx quality_check", "services/output_normalizer.py", "quality_check"),
]

results = []
for label, rel, sym in claims:
    p = ROOT / rel
    exists = p.exists()
    sym_found = None
    if exists and sym:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
            sym_found = bool(re.search(rf"\b{re.escape(sym)}\b", txt))
        except Exception:
            sym_found = False
    results.append({"claim": label, "file": rel, "exists": exists, "symbol": sym, "symbol_found": sym_found})

missing_files = [r for r in results if not r["exists"]]
missing_sym = [r for r in results if r["exists"] and r["symbol"] and not r["symbol_found"]]
print(json.dumps({"total": len(results), "missing_files": missing_files, "missing_symbol": missing_sym}, ensure_ascii=False, indent=2))
