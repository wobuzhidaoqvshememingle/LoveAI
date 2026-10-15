$ProgressPreference = 'SilentlyContinue'
$base = Join-Path $PSScriptRoot "LivePortrait\pretrained_weights"
$files = @(
  "liveportrait/base_models/appearance_feature_extractor.pth",
  "liveportrait/base_models/motion_extractor.pth",
  "liveportrait/base_models/spade_generator.pth",
  "liveportrait/base_models/warping_module.pth",
  "liveportrait/retargeting_models/stitching_retargeting_module.pth",
  "liveportrait/landmark.onnx",
  "insightface/models/buffalo_l/2d106det.onnx",
  "insightface/models/buffalo_l/det_10g.onnx"
)
foreach ($f in $files) {
  $url = "https://hf-mirror.com/KlingTeam/LivePortrait/resolve/main/$f"
  $dst = Join-Path $base ($f -replace '/', '\')
  $dstDir = Split-Path $dst -Parent
  if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
  if ((Test-Path $dst) -and (Get-Item $dst).Length -gt 1000000) { Write-Host "SKIP $f ($((Get-Item $dst).Length))"; continue }
  Write-Host "GET $f"
  try {
    Invoke-WebRequest -Uri $url -OutFile $dst -TimeoutSec 300 -UseBasicParsing
    Write-Host "  OK $((Get-Item $dst).Length) bytes"
  } catch {
    Write-Host "  FAIL: $($_.Exception.Message)"
  }
}
Write-Host "=== DONE ==="
Get-ChildItem $base -Recurse -File | ForEach-Object { "{0} {1}" -f $_.Length, $_.FullName.Replace($base,'') }
