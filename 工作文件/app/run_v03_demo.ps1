[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [switch]$NoBrowser,
    [switch]$EnablePaidContent,
    [switch]$DisablePaidContent
)

$ErrorActionPreference = "Stop"
$paidContentEnabled = -not $DisablePaidContent
if ($EnablePaidContent -and $DisablePaidContent) {
    throw "不能同时指定 -EnablePaidContent 和 -DisablePaidContent。"
}

if (-not $paidContentEnabled) {
    Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:PROJECT024_CONTENT_API_KEY -ErrorAction SilentlyContinue
}

$runScript = Join-Path $PSScriptRoot "run.ps1"
if (-not (Test-Path -LiteralPath $runScript -PathType Leaf)) {
    throw "找不到开发版启动脚本：$runScript"
}
$nodePackage = Join-Path $PSScriptRoot "node_modules\playwright-core\package.json"
if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) {
    throw "找不到 Node.js；抖音公开采集回退需要 Node.js 和隔离浏览器。"
}
if (-not (Test-Path -LiteralPath $nodePackage -PathType Leaf)) {
    throw "缺少抖音公开采集回退依赖，请在工作文件\app 运行 npm install。"
}

function Enable-ProjectLocalVision {
    $visionHost = "http://127.0.0.1:11435"
    $visionModel = "qwen2.5vl:3b"
    $modelRoot = Join-Path $PSScriptRoot ".cache\ollama-vision"
    $ollama = Get-Command ollama.exe -ErrorAction SilentlyContinue

    $env:PROJECT024_VISION_BASE_URL = $visionHost
    $env:PROJECT024_VISION_MODEL = $visionModel
    if (-not $ollama -or -not (Test-Path -LiteralPath $modelRoot -PathType Container)) {
        $env:PROJECT024_VISION_ENABLED = "0"
        Write-Warning "未找到项目024本地视觉模型环境；画面语义将如实显示为不可用。"
        return
    }

    $listener = Get-NetTCPConnection -State Listen -LocalPort 11435 -ErrorAction SilentlyContinue
    if (-not $listener) {
        $savedEnvironment = @{}
        $temporaryNames = @(
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy",
            "OLLAMA_HOST", "OLLAMA_MODELS"
        )
        foreach ($name in $temporaryNames) {
            $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
        try {
            $env:OLLAMA_HOST = "127.0.0.1:11435"
            $env:OLLAMA_MODELS = $modelRoot
            Start-Process `
                -FilePath $ollama.Source `
                -ArgumentList "serve" `
                -WindowStyle Hidden `
                -RedirectStandardOutput (Join-Path $modelRoot "server.stdout.log") `
                -RedirectStandardError (Join-Path $modelRoot "server.stderr.log") | Out-Null
        } finally {
            foreach ($name in $temporaryNames) {
                $value = $savedEnvironment[$name]
                if ($null -eq $value) {
                    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
                } else {
                    Set-Item -LiteralPath "Env:$name" -Value $value
                }
            }
        }
    }

    $tags = $null
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        try {
            $tags = Invoke-RestMethod -Uri "$visionHost/api/tags" -TimeoutSec 1
            break
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    $installedModels = @(
        $tags.models |
            ForEach-Object { $_.name }
    )
    if ($tags -and $installedModels -contains $visionModel) {
        $env:PROJECT024_VISION_ENABLED = "1"
        Write-Host "本地画面语义已启用：$visionModel"
    } else {
        $env:PROJECT024_VISION_ENABLED = "0"
        Write-Warning "项目024本地视觉服务未就绪或模型缺失；画面语义将如实显示为不可用。"
    }
}

Enable-ProjectLocalVision

$port = 8792
$computerUrl = "http://127.0.0.1:$port"
$healthUrl = "$computerUrl/api/health"

try {
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    $healthSupportsMode = $health.PSObject.Properties.Name -contains "paid_content_enabled"
    $modeMatches = $healthSupportsMode -and ([bool]$health.paid_content_enabled -eq [bool]$paidContentEnabled)
    if ($health.status -eq "ok" -and $health.version -eq "0.3.0" -and $modeMatches) {
        Write-Host "项目024 v0.3 已在运行：$computerUrl"
        if (-not $NoBrowser) {
            Start-Process $computerUrl
        }
        return
    }
} catch {
    # No matching service is running; continue with the normal cold start.
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
if ($listener) {
    throw "端口 $port 已被其他模式或其他程序占用，请先关闭占用程序后重试。"
}

if ($paidContentEnabled) {
    Write-Host "项目024 v0.3 电脑端演示入口正在启动（默认启用付费内容生成）。"
} else {
    Write-Host "项目024 v0.3 电脑端演示入口正在启动（已显式关闭付费内容生成）。"
}
if ($PythonExe -and $NoBrowser) {
    & $runScript -Port $port -BindAddress "127.0.0.1" -PythonExe $PythonExe -NoBrowser
} elseif ($PythonExe) {
    & $runScript -Port $port -BindAddress "127.0.0.1" -PythonExe $PythonExe
} elseif ($NoBrowser) {
    & $runScript -Port $port -BindAddress "127.0.0.1" -NoBrowser
} else {
    & $runScript -Port $port -BindAddress "127.0.0.1"
}
