# 校准闭环演示 — 任务状态

- 任务目标：把 review_loop 从"只过单测"推进到"真实可复核跑一轮"，产出命中率/ECE 产物，标注合成回填。
- 已完成：
  1. 新增 services/calibration_runner.py（自有包装模块，未改 review_loop.py / twin_sim_pipeline.py / digital_twin.py）
  2. 新增 tools/run_calibration_demo.py 入口
  3. 新增 tests/test_calibration_runner.py（12 个断言，全部通过）
  4. 跑通 .venv\Scripts\python.exe tools\run_calibration_demo.py，落盘 data/calibration/calibration_result.{json,md}
- 关键数值（seed=20261009，合成/模拟回填）：
  - n=16（t_healthy/t_npd/t_bpd/p_slowwarm 各 4 条）
  - hit_rate=0.3125
  - ECE=0.0677
  - calib_factor=0.9604
- 闭环状态：分批（4/8/12/16）后 hit_rate/ECE/calib_factor 与 ReviewLoop rolling_validity 均随批次更新。
- 下一步：待真人真实数据接入后，把 _synthesize_truth 替换为真实回填通道；当前合成结果不得对外当作真人命中率。
