[CmdletBinding()]
param(
    [string]$WorkerId = "worker-home-pc",
    [string]$ControlPlane = "http://106.55.25.127:8787",
    [string]$AcquisitionRoot = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "未找到项目虚拟环境：$PythonExe"
}

if (-not $AcquisitionRoot) {
    $AcquisitionRoot = Join-Path $ProjectRoot "var\acquisition"
}
[System.IO.Directory]::CreateDirectory($AcquisitionRoot) | Out-Null

$env:PROJECT024_CLOUD_CONTROL_BASE_URL = $ControlPlane.TrimEnd('/')
$env:PROJECT024_CLOUD_WORKER_ID = $WorkerId
$env:PROJECT024_ACQUISITION_ROOT = $AcquisitionRoot

$secure = Read-Host "请输入 Worker token（不会回显）" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $env:PROJECT024_CLOUD_WORKER_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}

Write-Host "云端 Worker 即将启动：worker_id=$WorkerId，控制面=$($env:PROJECT024_CLOUD_CONTROL_BASE_URL)"
& $PythonExe -m app.services.cloud_worker_runner
exit $LASTEXITCODE
