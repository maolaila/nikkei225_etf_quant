#Requires -Version 5.1
<#
.SYNOPSIS
Prepare real J-Quants data and optionally hand the project to the Codex CLI supervisor.

.DESCRIPTION
This script keeps the real-data workflow explicit:
1. Download/validate J-Quants daily context data.
2. Download/validate J-Quants 1m data for the paid minute coverage period.
3. Import the real 1m parquet data into the main backtest data lake.
4. Run one smoke training/backtest pass.
5. Optionally start Start-CodexQuantAgent.ps1 for long-running model search.

It never prints API keys. The collector reads keys from market_data_collector\.env.
#>

[CmdletBinding()]
param(
    [string]$Workspace = (Get-Location).Path,
    [string]$PythonExe = "",
    [string]$Symbols = "1357,1570,1321,1571",
    [string]$MinuteStartDate = "2024-05-16",
    [string]$MinuteEndDate = "2026-05-15",
    [string]$DailyStartDate = "2016-05-16",
    [string]$DailyEndDate = "2026-05-15",
    [switch]$SkipDailyDownload,
    [switch]$SkipMinuteDownload,
    [switch]$SkipSmokeBacktest,
    [switch]$StartCodexSupervisor,
    [switch]$DangerouslyBypassApprovalsAndSandbox,
    [switch]$UseSearch,
    [int]$SubagentTimeoutMinutes = 120,
    [int]$StalledSubagentMinutes = 30,
    [int]$MinRegressionCycles = 100,
    [int]$RequiredConsecutiveSuccesses = 10,
    [double]$TargetTotalReturnPct = 3.0,
    [double]$TargetReturnIncrementPct = 2.0,
    [int]$MinTrades = 50,
    [double]$MinTotalReturnPct = 0.0,
    [double]$MinProfitFactor = 1.2,
    [double]$MaxDrawdownPct = 15.0,
    [double]$MinPositiveMonthRatio = 0.55,
    [double]$MinMonthlyReturnFloorPct = -8.0,
    [int]$MinWalkForwardWindows = 6,
    [int]$DataExpansionEveryCycles = 25,
    [int]$DataStaleCyclesBeforeExpansion = 25
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-WorkspacePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $Path))
}

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ("[{0}] {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"), $Message)
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string[]]$Command
    )
    Write-Step ("Running: {0}" -f ($Command -join " "))
    Push-Location $WorkingDirectory
    try {
        & $Command[0] @($Command | Select-Object -Skip 1)
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code $LASTEXITCODE`: $($Command -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

$Workspace = Resolve-WorkspacePath $Workspace
$CollectorDir = Join-Path $Workspace "market_data_collector"
if (-not (Test-Path -LiteralPath $CollectorDir)) {
    throw "market_data_collector directory does not exist: $CollectorDir"
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $BundledPython) {
        $PythonExe = $BundledPython
    }
    else {
        $PythonExe = "python"
    }
}

$CollectorEnv = Join-Path $CollectorDir ".env"
if (-not (Test-Path -LiteralPath $CollectorEnv)) {
    throw "Missing collector .env: $CollectorEnv"
}

$envText = Get-Content -Raw -LiteralPath $CollectorEnv
if ($envText -notmatch "(?m)^JQUANTS_API_KEY\s*=\s*\S+") {
    throw "JQUANTS_API_KEY is not configured in market_data_collector\.env"
}
if ($envText -notmatch "(?m)^JQUANTS_ENABLE_MINUTE\s*=\s*true\s*$") {
    throw "JQUANTS_ENABLE_MINUTE must be true in market_data_collector\.env before downloading paid minute data"
}

Write-Step "Workspace: $Workspace"
Write-Step "Collector: $CollectorDir"
Write-Step "Minute range: $MinuteStartDate to $MinuteEndDate"
Write-Step "Daily context range: $DailyStartDate to $DailyEndDate"

if (-not $SkipDailyDownload) {
    Invoke-Checked -WorkingDirectory $CollectorDir -Command @(
        $PythonExe, "-m", "market_data_collector.cli", "download",
        "--provider", "jquants",
        "--symbols", $Symbols,
        "--interval", "1d",
        "--from-date", $DailyStartDate,
        "--to-date", $DailyEndDate,
        "--format", "parquet",
        "--incremental"
    )
    Invoke-Checked -WorkingDirectory $CollectorDir -Command @(
        $PythonExe, "-m", "market_data_collector.cli", "validate",
        "--provider", "jquants",
        "--symbols", $Symbols,
        "--interval", "1d",
        "--from-date", $DailyStartDate,
        "--to-date", $DailyEndDate
    )
}

if (-not $SkipMinuteDownload) {
    Invoke-Checked -WorkingDirectory $CollectorDir -Command @(
        $PythonExe, "-m", "market_data_collector.cli", "download",
        "--provider", "jquants",
        "--symbols", $Symbols,
        "--interval", "1m",
        "--from-date", $MinuteStartDate,
        "--to-date", $MinuteEndDate,
        "--format", "parquet",
        "--incremental"
    )
    Invoke-Checked -WorkingDirectory $CollectorDir -Command @(
        $PythonExe, "-m", "market_data_collector.cli", "validate",
        "--provider", "jquants",
        "--symbols", $Symbols,
        "--interval", "1m",
        "--from-date", $MinuteStartDate,
        "--to-date", $MinuteEndDate
    )
}

Invoke-Checked -WorkingDirectory $Workspace -Command @(
    $PythonExe, "-m", "src.main", "import-market-data",
    "--provider", "jquants",
    "--interval", "1m",
    "--from-date", $MinuteStartDate,
    "--to-date", $MinuteEndDate
)

if (-not $SkipSmokeBacktest) {
    Invoke-Checked -WorkingDirectory $Workspace -Command @($PythonExe, "-m", "src.main", "normalize-data")
    Invoke-Checked -WorkingDirectory $Workspace -Command @($PythonExe, "-m", "src.main", "validate-data")
    Invoke-Checked -WorkingDirectory $Workspace -Command @($PythonExe, "-m", "src.main", "build-features")
    Invoke-Checked -WorkingDirectory $Workspace -Command @($PythonExe, "-m", "src.main", "build-labels")
    Invoke-Checked -WorkingDirectory $Workspace -Command @($PythonExe, "-m", "src.main", "walk-forward", "--model", "random_forest")
    Invoke-Checked -WorkingDirectory $Workspace -Command @($PythonExe, "-m", "src.main", "backtest", "--model", "latest")
    Invoke-Checked -WorkingDirectory $Workspace -Command @($PythonExe, "-m", "src.main", "report", "--type", "backtest")
}

if ($StartCodexSupervisor) {
    $Supervisor = Join-Path $Workspace "Start-CodexQuantAgent.ps1"
    $supervisorArgs = @{
        Workspace = $Workspace
        ResetState = $true
        ReasoningEffort = "xhigh"
        SubagentTimeoutMinutes = $SubagentTimeoutMinutes
        StalledSubagentMinutes = $StalledSubagentMinutes
        MinRegressionCycles = $MinRegressionCycles
        RequiredConsecutiveSuccesses = $RequiredConsecutiveSuccesses
        TargetTotalReturnPct = $TargetTotalReturnPct
        TargetReturnIncrementPct = $TargetReturnIncrementPct
        MinTrades = $MinTrades
        MinTotalReturnPct = $MinTotalReturnPct
        MinProfitFactor = $MinProfitFactor
        MaxDrawdownPct = $MaxDrawdownPct
        MinPositiveMonthRatio = $MinPositiveMonthRatio
        MinMonthlyReturnFloorPct = $MinMonthlyReturnFloorPct
        MinWalkForwardWindows = $MinWalkForwardWindows
        MonthlyReturnTargetMode = "average"
        DataExpansionEveryCycles = $DataExpansionEveryCycles
        DataStaleCyclesBeforeExpansion = $DataStaleCyclesBeforeExpansion
        SkipInitialDataExpansion = $true
    }
    if ($UseSearch) {
        $supervisorArgs["UseSearch"] = $true
    }
    if ($DangerouslyBypassApprovalsAndSandbox) {
        $supervisorArgs["DangerouslyBypassApprovalsAndSandbox"] = $true
    }
    Write-Step "Starting Codex supervisor"
    & $Supervisor @supervisorArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Codex supervisor exited with code $LASTEXITCODE"
    }
}

Write-Step "Real-data model search preparation complete"
