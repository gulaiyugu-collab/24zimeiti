[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$EnablePaidContent,
    [switch]$DisablePaidContent
)

$ErrorActionPreference = "Stop"
$paidContentEnabled = -not $DisablePaidContent
if ($EnablePaidContent -and $DisablePaidContent) {
    throw "不能同时指定 -EnablePaidContent 和 -DisablePaidContent。"
}
$entry = Join-Path $PSScriptRoot "工作文件\app\run_v03_demo.ps1"
if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
    throw "找不到项目024 v0.3 演示入口：$entry"
}

if ($NoBrowser) {
    if ($paidContentEnabled) { & $entry -NoBrowser -EnablePaidContent }
    else { & $entry -NoBrowser -DisablePaidContent }
} else {
    if ($paidContentEnabled) { & $entry -EnablePaidContent }
    else { & $entry -DisablePaidContent }
}
