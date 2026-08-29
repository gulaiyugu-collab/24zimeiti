[CmdletBinding()]
param(
    [int]$Port = 8787,
    [string]$BindAddress = "127.0.0.1",
    [string]$PythonExe = "",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$normalizedBindAddress = $BindAddress.Trim().ToLowerInvariant()
$allowedBindAddresses = @("127.0.0.1", "localhost", "::1")
if ($normalizedBindAddress -notin $allowedBindAddresses) {
    throw "P3-05 包含本地账号数据和付费 Agent，当前只允许 127.0.0.1、localhost 或 ::1 回环监听。局域网或公网访问必须先实现访问控制并完成物理设备验收。"
}
$BindAddress = $BindAddress.Trim()
$ProjectRoot = $PSScriptRoot
$VenvDir = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements.txt"
$RequirementsStamp = Join-Path $VenvDir ".requirements.sha256"

$env:PIP_CACHE_DIR = Join-Path $ProjectRoot ".cache\pip"
$env:TEMP = Join-Path $ProjectRoot ".temp"
$env:TMP = $env:TEMP
[System.IO.Directory]::CreateDirectory($env:PIP_CACHE_DIR) | Out-Null
[System.IO.Directory]::CreateDirectory($env:TEMP) | Out-Null

function Resolve-SystemPython {
    param([string]$ExplicitPath)

    if ($ExplicitPath) {
        if (-not (Test-Path -LiteralPath $ExplicitPath -PathType Leaf)) {
            throw "指定的 Python 解释器不存在：$ExplicitPath"
        }
        return (Resolve-Path -LiteralPath $ExplicitPath).Path
    }

    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "未找到 Python。请安装 Python 3.11 及以上版本，或使用 -PythonExe 指定真实解释器。"
    }
    if ($command.Source -like "*\WindowsApps\*") {
        throw "不能使用 WindowsApps Python 占位入口，请使用 -PythonExe 指向真实的 python.exe。"
    }
    return $command.Source
}

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    $systemPython = Resolve-SystemPython -ExplicitPath $PythonExe
    Write-Host "首次运行：正在创建项目虚拟环境……"
    & $systemPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "虚拟环境创建失败，退出码：$LASTEXITCODE。"
    }
}

$requirementsHash = (Get-FileHash -LiteralPath $Requirements -Algorithm SHA256).Hash
$currentHash = if (Test-Path -LiteralPath $RequirementsStamp) {
    (Get-Content -Raw -LiteralPath $RequirementsStamp).Trim()
} else {
    ""
}

if ($requirementsHash -ne $currentHash) {
    Write-Host "正在安装或更新项目依赖……"
    & $VenvPython -m pip install --disable-pip-version-check -r $Requirements
    if ($LASTEXITCODE -ne 0) {
        throw "项目依赖安装失败，退出码：$LASTEXITCODE。"
    }
    [System.IO.File]::WriteAllText(
        $RequirementsStamp,
        $requirementsHash,
        [System.Text.UTF8Encoding]::new($false)
    )
}

$healthHost = if ($normalizedBindAddress -eq "::1") { "[::1]" } else { $BindAddress }
$url = "http://${healthHost}:$Port"
$arguments = @(
    "-m",
    "uvicorn",
    "app.main:app",
    "--host",
    $BindAddress,
    "--port",
    "$Port"
)
$processParameters = @{
    FilePath = $VenvPython
    ArgumentList = $arguments
    WorkingDirectory = $ProjectRoot
    NoNewWindow = $true
    PassThru = $true
}
$server = Start-Process @processParameters

try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ($server.HasExited) {
            throw "服务启动过程中退出，退出码：$($server.ExitCode)。"
        }
        try {
            $health = Invoke-WebRequest -UseBasicParsing -Uri "$url/api/health" -TimeoutSec 1
            if ($health.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }

    if (-not $ready) {
        throw "服务在 10 秒内未通过健康检查：$url/api/health"
    }

    Write-Host "电脑访问地址：$url"
    Write-Host "按 Ctrl+C 停止服务。"
    if (-not $NoBrowser) {
        Start-Process $url
    }

    Wait-Process -Id $server.Id
    if ($server.ExitCode -ne 0) {
        throw "服务已退出，退出码：$($server.ExitCode)。"
    }
}
finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
