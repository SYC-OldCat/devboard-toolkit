param(
    [Parameter(Mandatory=$true)]
    [string]$TxtPath,

    [Parameter(Mandatory=$true)]
    [string]$OutputDir,

    [switch]$WriteBack,

    [string]$OutTxtPath = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $TxtPath)) {
    Write-Host "[错误] txt 文件不存在: $TxtPath" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $OutputDir)) {
    Write-Host "[错误] output 目录不存在: $OutputDir" -ForegroundColor Red
    exit 1
}

$allLines = Get-Content $TxtPath | Where-Object { $_.Trim() -ne "" }

$outSet = @{}
Get-ChildItem -Path $OutputDir -File -ErrorAction SilentlyContinue | ForEach-Object {
    $bn = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
    $outSet[$bn] = $true
}

$existing = @()
$missing  = @()
$missingLines = @()

foreach ($line in $allLines) {
    $bn = [System.IO.Path]::GetFileNameWithoutExtension($line.Trim())
    if ($outSet.ContainsKey($bn)) {
        $existing += $bn
    } else {
        $missing += $bn
        $missingLines += $line.Trim()
    }
}

Write-Host ""
Write-Host "========== 对比结果 ==========" -ForegroundColor Cyan
Write-Host "  Txt 路径:   $TxtPath"
Write-Host "  Output 目录: $OutputDir"
Write-Host "  txt 总行数:   $($allLines.Count)"
Write-Host "  output 文件数: $($outSet.Count)"
Write-Host "  已存在:       $($existing.Count)"
Write-Host "  缺失:         $($missing.Count)" -ForegroundColor Yellow
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

if ($missing.Count -gt 0) {
    Write-Host "[缺失文件名列表]" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
}

if ($WriteBack -or $OutTxtPath) {
    $target = if ($OutTxtPath) { $OutTxtPath } else { $TxtPath }
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllLines($target, $missingLines, $utf8NoBom)
    $newCount = (Get-Content $target).Count
    Write-Host "[写入完成] 已将 $newCount 条缺失路径写入:" -ForegroundColor Green
    Write-Host "  $target"
} else {
    Write-Host "[提示] 未执行写回。如需写回 txt:" -ForegroundColor DarkGray
    Write-Host "  覆盖原文件:  添加 -WriteBack"
    Write-Host "  另存新文件:  -OutTxtPath 'D:\path\to\new.txt'"
}
