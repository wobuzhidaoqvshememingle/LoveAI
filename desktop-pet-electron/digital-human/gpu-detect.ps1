# gpu-detect.ps1
# 检测本机 NVIDIA 显卡显存，输出**一行 JSON** 到 stdout。
# 规则（与任务契约逐字一致）：
#   显存 >= 6144 MiB -> tier=high
#   0 < 显存 < 6144  -> tier=low，  hint="本地帧率低、建议云端"
#   无 NVIDIA        -> tier=none， hint="未检测到NVIDIA显卡，建议使用云端数字人或保持2D"
# 调用方：Electron 主进程启动时 spawn 一次，读 stdout 末行 JSON。

$ErrorActionPreference = 'SilentlyContinue'

$gpuName = ''
$vramMb  = 0

try {
    $raw = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null
    if ($raw) {
        $first = ($raw | Where-Object { $_ -and $_.Trim() -ne '' } | Select-Object -First 1)
        if ($first) {
            $parts = $first -split ','
            $gpuName = $parts[0].Trim()
            $vramMb  = [int]($parts[1].Trim())
        }
    }
} catch {
    $gpuName = ''
    $vramMb  = 0
}

if (-not $gpuName -or $vramMb -le 0) {
    $obj = [ordered]@{
        gpu      = 'none'
        vram_mb  = 0
        tier     = 'none'
        hint     = '未检测到NVIDIA显卡，建议使用云端数字人或保持2D'
    }
} elseif ($vramMb -ge 6144) {
    $obj = [ordered]@{
        gpu      = $gpuName
        vram_mb  = $vramMb
        tier     = 'high'
        hint     = ''
    }
} else {
    $obj = [ordered]@{
        gpu      = $gpuName
        vram_mb  = $vramMb
        tier     = 'low'
        hint     = '本地帧率低、建议云端'
    }
}

$json = $obj | ConvertTo-Json -Compress
Write-Output $json