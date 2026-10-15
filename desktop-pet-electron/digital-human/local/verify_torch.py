import torch, sys, json, platform

info = {
    "torch_version": torch.__version__,
    "cuda_available": torch.cuda.is_available(),
    "cuda_version_compiled": torch.version.cuda,
    "device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
}
if torch.cuda.is_available():
    info["device_name"] = torch.cuda.get_device_name(0)
    info["device_capability"] = torch.cuda.get_device_capability(0)
    # tiny CUDA op sanity test
    x = torch.randn(1024, 1024, device="cuda")
    y = x @ x
    torch.cuda.synchronize()
    info["matmul_on_gpu_ok"] = True
    info["vram_allocated_mb"] = round(torch.cuda.memory_allocated(0) / 1024 / 1024, 1)

print(json.dumps(info, indent=2))
ok = info["cuda_available"] and "GTX 1050" in info.get("device_name", "")
print("VERIFY_RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 2)
