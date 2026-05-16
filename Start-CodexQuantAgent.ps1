#Requires -Version 5.1
<#
.SYNOPSIS
Codex CLI supervisor for the Nikkei 225 ETF quant project.

.DESCRIPTION
This script uses repeated `codex exec` calls as specialist subagents:
developer, QA/backtest runner, and strategy optimizer. The PowerShell
script is the supervisor. It keeps logs and state under
`.codex_quant_agent/`.

The workflow is intentionally limited to historical backtests and paper
trading. It tells every Codex subagent to keep live trading disabled and
to record every simulated trade with a reason. It cannot guarantee
positive returns; it can only iterate on backtest evidence.

.EXAMPLE
.\Start-CodexQuantAgent.ps1 -UseSearch -ResetState

.EXAMPLE
.\Start-CodexQuantAgent.ps1 -UseSearch -DangerouslyBypassApprovalsAndSandbox -ResetState

.EXAMPLE
.\Start-CodexQuantAgent.ps1 -DryRun -MaxCycles 1
#>

[CmdletBinding()]
param(
    [string]$Workspace = (Get-Location).Path,
    [string]$SpecPath = "nikkei225_etf_quant_codex_implementation_spec.md",
    [string]$CodexExe = "codex",
    [string]$Model = "gpt-5.5",
    [ValidateSet("low", "medium", "high", "xhigh")]
    [string]$ReasoningEffort = "xhigh",
    [string]$ServiceTier = "",
    [int]$MaxCycles = 0,
    [int]$MaxDevelopmentPasses = 0,
    [int]$RepairAfterUnreadyPasses = 2,
    [int]$SleepSeconds = 10,
    [int]$HeartbeatMinutes = 5,
    [int]$SubagentTimeoutMinutes = 120,
    [int]$SubagentStatusSeconds = 60,
    [int]$StalledSubagentMinutes = 30,
    [int]$MaxConsecutiveFailures = 20,
    [double]$TargetTotalReturnPct = 3.0,
    [double]$TargetReturnIncrementPct = 2.0,
    [double]$MaxTargetTotalReturnPct = 0,
    [ValidateSet("average", "median", "minimum", "latest")]
    [string]$MonthlyReturnTargetMode = "average",
    [int]$MinTrades = 50,
    [double]$MinTotalReturnPct = 0.0,
    [double]$MinProfitFactor = 1.2,
    [double]$MaxDrawdownPct = 15.0,
    [double]$MinPositiveMonthRatio = 0.55,
    [double]$MinMonthlyReturnFloorPct = -8.0,
    [int]$MinWalkForwardWindows = 6,
    [int]$MinRegressionCycles = 20,
    [int]$RequiredConsecutiveSuccesses = 5,
    [double]$MinRuntimeHours = 0,
    [int]$DataExpansionEveryCycles = 25,
    [int]$DataStaleCyclesBeforeExpansion = 25,
    [int]$MaxDataExpansionRuns = 0,
    [string]$StopFile = "",
    [switch]$UseSearch,
    [switch]$DangerouslyBypassApprovalsAndSandbox,
    [switch]$AllowAutonomousConfigApply,
    [switch]$SkipInitialDataExpansion,
    [switch]$StopWhenStableTargetReached,
    [switch]$DisableAutoRepair,
    [switch]$StopOnCodexFailure,
    [switch]$DryRun,
    [switch]$ResetState
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:State = $null
$script:StatePath = $null
$script:StateDir = $null
$script:StopFilePath = $null
$script:SupervisorLog = $null
$script:PythonExe = $null
$script:CodexCommandPath = $null

function Resolve-AbsolutePath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }

    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $Path))
}

function New-Directory {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }
}

function Write-AgentMessage {
    param([Parameter(Mandatory = $true)][string]$Message)

    $line = "[{0}] {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    if ($script:SupervisorLog) {
        Add-Content -LiteralPath $script:SupervisorLog -Value $line -Encoding UTF8
    }
}

function New-State {
    [pscustomobject]@{
        version = 1
        created_at = (Get-Date).ToString("o")
        updated_at = (Get-Date).ToString("o")
        development_pass = 0
        cycle = 0
        regression_cycles = 0
        successful_cycles = 0
        consecutive_successes = 0
        data_expansion_runs = 0
        repeated_data_signature_cycles = 0
        last_data_signature = $null
        current_target_total_return_pct = $TargetTotalReturnPct
        target_return_increment_pct = $TargetReturnIncrementPct
        target_milestones = @()
        consecutive_failures = 0
        last_heartbeat = $null
        last_error = $null
        stop_file = $null
        target = $null
        status = "new"
        success = $false
        last_metrics = $null
        history = @()
    }
}

function Ensure-StateShape {
    param([Parameter(Mandatory = $true)]$State)

    $defaults = @{
        consecutive_failures = 0
        regression_cycles = 0
        successful_cycles = 0
        consecutive_successes = 0
        data_expansion_runs = 0
        repeated_data_signature_cycles = 0
        last_data_signature = $null
        current_target_total_return_pct = $TargetTotalReturnPct
        target_return_increment_pct = $TargetReturnIncrementPct
        target_milestones = @()
        last_heartbeat = $null
        last_error = $null
        stop_file = $null
        target = $null
    }

    foreach ($key in $defaults.Keys) {
        if (-not ($State.PSObject.Properties.Name -contains $key)) {
            $State | Add-Member -NotePropertyName $key -NotePropertyValue $defaults[$key]
        }
    }

    return $State
}

function Read-State {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($ResetState -and (Test-Path -LiteralPath $Path)) {
        Remove-Item -LiteralPath $Path -Force
    }

    if (-not (Test-Path -LiteralPath $Path)) {
        return New-State
    }

    try {
        return Ensure-StateShape (Get-Content -Raw -LiteralPath $Path -Encoding UTF8 | ConvertFrom-Json)
    }
    catch {
        $backup = "$Path.broken.$((Get-Date).ToString("yyyyMMdd_HHmmss"))"
        Copy-Item -LiteralPath $Path -Destination $backup -Force
        Write-AgentMessage "State file was not valid JSON; backed it up to $backup"
        return New-State
    }
}

function Save-State {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $State.updated_at = (Get-Date).ToString("o")
    $State | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Add-StateHistory {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][string]$Event,
        [Parameter(Mandatory = $false)]$Data = $null
    )

    $entry = [pscustomobject]@{
        time = (Get-Date).ToString("o")
        event = $Event
        data = $Data
    }
    $State.history = @($State.history) + @($entry)
}

function Set-SupervisorHeartbeat {
    param([Parameter(Mandatory = $true)][string]$Context)

    if ($null -eq $script:State -or -not $script:StatePath) {
        return
    }

    $now = Get-Date
    $writeHeartbeat = $false
    if ($null -eq $script:State.last_heartbeat) {
        $writeHeartbeat = $true
    }
    elseif ($HeartbeatMinutes -le 0) {
        $writeHeartbeat = $false
    }
    else {
        try {
            $last = [datetime]$script:State.last_heartbeat
            $writeHeartbeat = (($now - $last).TotalMinutes -ge $HeartbeatMinutes)
        }
        catch {
            $writeHeartbeat = $true
        }
    }

    if ($writeHeartbeat) {
        $script:State.last_heartbeat = $now.ToString("o")
        Save-State -State $script:State -Path $script:StatePath
        Write-AgentMessage "Heartbeat: $Context"
    }
}

function Register-SupervisorFailure {
    param([Parameter(Mandatory = $true)][string]$Reason)

    $script:State.consecutive_failures = [int]$script:State.consecutive_failures + 1
    $script:State.last_error = $Reason
    Add-StateHistory -State $script:State -Event "failure" -Data ([pscustomobject]@{
        reason = $Reason
        consecutive_failures = $script:State.consecutive_failures
    })
    Save-State -State $script:State -Path $script:StatePath

    if ($MaxConsecutiveFailures -gt 0 -and [int]$script:State.consecutive_failures -ge $MaxConsecutiveFailures) {
        throw "Reached MaxConsecutiveFailures=$MaxConsecutiveFailures. Last failure: $Reason"
    }
}

function Stop-ProcessTree {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
    }

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Write-AgentLogStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$LogFile
    )

    if (-not (Test-Path -LiteralPath $LogFile)) {
        Write-AgentMessage "Subagent '$Role' is still running; log file has not been created yet."
        return
    }

    $item = Get-Item -LiteralPath $LogFile
    $tail = ""
    try {
        $tail = (Get-Content -Tail 1 -LiteralPath $LogFile -ErrorAction SilentlyContinue) -join " "
    }
    catch {
        $tail = ""
    }

    if ($tail.Length -gt 180) {
        $tail = $tail.Substring(0, 180) + "..."
    }

    Write-AgentMessage ("Subagent '{0}' still running; log_size={1} bytes; log_updated={2}; tail={3}" -f $Role, $item.Length, $item.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss"), $tail)
}

function Clear-SupervisorFailures {
    param([string]$Reason = "progress")

    if ($null -eq $script:State) {
        return
    }

    if ([int]$script:State.consecutive_failures -ne 0 -or $null -ne $script:State.last_error) {
        $script:State.consecutive_failures = 0
        $script:State.last_error = $null
        Add-StateHistory -State $script:State -Event "failure-counter-reset" -Data ([pscustomobject]@{ reason = $Reason })
        Save-State -State $script:State -Path $script:StatePath
    }
}

function Test-StopRequested {
    if (-not $script:StopFilePath) {
        return $false
    }

    if (Test-Path -LiteralPath $script:StopFilePath) {
        Write-AgentMessage "Stop requested by file: $script:StopFilePath"
        $script:State.status = "stopped_by_stop_file"
        Save-State -State $script:State -Path $script:StatePath
        return $true
    }

    return $false
}

function Test-CodexCli {
    param([Parameter(Mandatory = $true)][string]$CommandName)

    $cmd = Get-Command $CommandName -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "Cannot find Codex CLI executable '$CommandName'. Install/login to Codex CLI first."
    }

    $script:CodexCommandPath = $cmd.Source
    $version = & $CommandName --version 2>$null
    Write-AgentMessage "Using Codex CLI: $version"
    Write-AgentMessage "Codex command path: $script:CodexCommandPath"
}

function Resolve-PythonExe {
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:QUANT_PYTHON_EXE)) {
        $candidates += $env:QUANT_PYTHON_EXE
    }
    $candidates += @(
        (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"),
        "python",
        "py"
    )

    foreach ($candidate in $candidates) {
        try {
            if ([System.IO.Path]::IsPathRooted($candidate)) {
                if (Test-Path -LiteralPath $candidate) {
                    return (Resolve-Path -LiteralPath $candidate).Path
                }
            }
            else {
                $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
                if ($cmd) {
                    return $cmd.Source
                }
            }
        }
        catch {
            continue
        }
    }

    return ""
}

function Get-CodexArgs {
    param([Parameter(Mandatory = $true)][string]$LastMessageFile)

    $codexArgs = @()

    if ($UseSearch) {
        $codexArgs += "--search"
    }

    $codexArgs += @(
        "exec",
        "--skip-git-repo-check",
        "-C", $script:WorkspaceRoot,
        "-m", $Model,
        "-c", "model_reasoning_effort=`"$ReasoningEffort`"",
        "--output-last-message", $LastMessageFile
    )

    if (-not [string]::IsNullOrWhiteSpace($ServiceTier)) {
        $codexArgs += @("-c", "service_tier=`"$ServiceTier`"")
    }

    if ($DangerouslyBypassApprovalsAndSandbox) {
        $codexArgs += "--dangerously-bypass-approvals-and-sandbox"
    }
    else {
        $codexArgs += @("-s", "danger-full-access", "-a", "never")
    }

    $codexArgs += "-"
    return $codexArgs
}

function Get-CommonPromptHeader {
    @"
You are running as a Codex CLI subagent inside this local workspace:
$script:WorkspaceRoot

Implementation spec:
$script:SpecFile

Current supervisor monthly return target:
$(Get-CurrentReturnTargetPct)% monthly return using MonthlyReturnTargetMode=$MonthlyReturnTargetMode. When this target is stably achieved, the supervisor raises it by $TargetReturnIncrementPct percentage points and continues.

Python runtime:
$script:PythonExe
Use this exact interpreter for project commands:
& `$env:QUANT_PYTHON_EXE -m pytest
& `$env:QUANT_PYTHON_EXE -m src.main walk-forward --model random_forest
& `$env:QUANT_PYTHON_EXE -m src.main backtest --model latest
& `$env:QUANT_PYTHON_EXE -m src.main batch-search --candidates 24 --target-monthly-return-pct $(Get-CurrentReturnTargetPct) --min-trades $MinTrades --min-profit-factor $MinProfitFactor --max-drawdown-pct $MaxDrawdownPct --min-positive-month-ratio $MinPositiveMonthRatio --min-monthly-return-floor-pct $MinMonthlyReturnFloorPct --min-walk-forward-windows $MinWalkForwardWindows
Do not download Python, uv, package managers, or create a new .venv unless this interpreter is missing and commands fail.

/goal Build, repair, expand data, backtest, audit, and iteratively improve this local project. The current monthly return target starts at $TargetTotalReturnPct percent and increases by $TargetReturnIncrementPct percentage points after each stable win, for example 3%, 5%, 7%. A stable win requires real non-synthetic data, walk_forward_windows >= $MinWalkForwardWindows without fallback single-split validation, total_return_pct > $MinTotalReturnPct, total_trades >= $MinTrades, profit_factor >= $MinProfitFactor, max_drawdown_pct no worse than -$MaxDrawdownPct, positive_active_month_ratio >= $MinPositiveMonthRatio, min_monthly_return_pct >= $MinMonthlyReturnFloorPct, monthly return metric >= current target, at least $MinRegressionCycles fresh regression cycles, and at least $RequiredConsecutiveSuccesses consecutive successful cycles at the current monthly return target. Every run must record monthly returns by year/month regardless of sign, with CSV and HTML table outputs. If data is synthetic, stale, exhausted, or too narrow, acquire/implement more historical data from configured APIs or safe public sources before trusting positive results. Keep iterating on evidence until the user manually stops the supervisor process or an explicit maximum target/stop option is reached.

Supervisor requirements:
- Build and improve a Python 3.11 Nikkei 225 ETF historical backtest and paper-trading project according to the spec.
- Use the spec as the source of truth for project structure, CLI commands, config files, trade logs, reports, and tests.
- This project must remain historical_backtest / paper_trading / alert_only only. Keep live_trading.enabled=false and live_order_enabled=false.
- Do not implement real-money order placement. Do not connect any broker order API except as disabled/stubbed paper-trading interfaces.
- Do not promise guaranteed profit. Backtest profit is evidence to investigate, not a guarantee.
- Profitability milestones are valid only when latest metrics have data_is_synthetic=false and real J-Quants/approved provider data. Synthetic data is allowed only for plumbing tests.
- Avoid future leakage. Use walk-forward validation and next-bar execution where applicable.
- Every simulated buy/sell must be logged with timestamp, action, symbol, price, quantity, reason, exit reason when relevant, and PnL when known.
- MinTrades is a sample-size validation gate, not a quota. Do not force trades when market conditions are unsuitable just to reach the trade count.
- If external API keys or paid data are unavailable, create a clean offline path: provider abstractions, symbol probes, CSV replay, sample/synthetic data generation, and clear TODOs for real providers.
- Keep all symbols, thresholds, costs, dates, risk limits, and model settings in YAML/config files rather than hard-coding strategy constants.
- Preserve user files. Do not delete the spec or unrelated files.
- After changes, run the relevant local validation when practical and record what passed or failed in your final message.

"@
}

function Invoke-CodexAgent {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$PromptBody
    )

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $safeRole = $Role -replace "[^A-Za-z0-9_.-]", "_"
    $promptFile = Join-Path $script:PromptDir "$stamp.$safeRole.prompt.md"
    $logFile = Join-Path $script:LogDir "$stamp.$safeRole.log.txt"
    $lastMessageFile = Join-Path $script:OutputDir "$stamp.$safeRole.last.md"

    $fullPrompt = (Get-CommonPromptHeader) + "`nRole: $Role`n`n" + $PromptBody
    $fullPrompt | Set-Content -LiteralPath $promptFile -Encoding UTF8

    Write-AgentMessage "Starting Codex subagent '$Role'"
    Write-AgentMessage "Prompt: $promptFile"

    if ($DryRun) {
        Write-AgentMessage "DryRun enabled; not invoking Codex for '$Role'"
        return [pscustomobject]@{
            role = $Role
            exit_code = 0
            prompt = $promptFile
            log = $logFile
            last_message = $lastMessageFile
        }
    }

    $codexArgs = Get-CodexArgs -LastMessageFile $lastMessageFile
    $runnerFile = Join-Path $script:StateDir "codex_agent_runner.ps1"
    $runnerConfigFile = Join-Path $script:StateDir "$stamp.$safeRole.runner.json"

    $exitCodeFile = Join-Path $script:StateDir "$stamp.$safeRole.exitcode.txt"
    $runnerErrorFile = Join-Path $script:LogDir "$stamp.$safeRole.runner.error.txt"

    @'
param([Parameter(Mandatory = $true)][string]$ConfigPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$config = Get-Content -Raw -LiteralPath $ConfigPath -Encoding UTF8 | ConvertFrom-Json
$codexArgs = @($config.codex_args)
$codexCommandPath = [string]$config.codex_command_path
if ([string]::IsNullOrWhiteSpace($codexCommandPath)) {
    $codexCommandPath = [string]$config.codex_exe
}

try {
    $logDirectory = Split-Path -Parent $config.log_file
    if (-not (Test-Path -LiteralPath $logDirectory)) {
        New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    }
    "runner started $(Get-Date -Format o)" | Set-Content -LiteralPath $config.runner_error_file -Encoding UTF8
    $promptText = Get-Content -Raw -LiteralPath $config.prompt_file -Encoding UTF8
    Push-Location -LiteralPath $config.workspace
    try {
        if (-not [string]::IsNullOrWhiteSpace([string]$config.python_exe)) {
            $env:QUANT_PYTHON_EXE = [string]$config.python_exe
        }
        $promptText | & $codexCommandPath @codexArgs *>&1 | Tee-Object -FilePath $config.log_file
        $code = $LASTEXITCODE
        if ($null -eq $code) {
            $code = 0
        }
        Set-Content -LiteralPath $config.exit_code_file -Value ([string]$code) -Encoding ASCII
        "runner completed $(Get-Date -Format o) exit_code=$code" | Add-Content -LiteralPath $config.runner_error_file -Encoding UTF8
        exit $code
    }
    finally {
        Pop-Location
    }
}
catch {
    $message = "runner exception $(Get-Date -Format o): $($_.Exception.Message)"
    $message | Add-Content -LiteralPath $config.runner_error_file -Encoding UTF8
    $message | Add-Content -LiteralPath $config.log_file -Encoding UTF8
    Set-Content -LiteralPath $config.exit_code_file -Value "997" -Encoding ASCII
    exit 997
}
'@ | Set-Content -LiteralPath $runnerFile -Encoding UTF8

    [pscustomobject]@{
        workspace = $script:WorkspaceRoot
        codex_exe = $CodexExe
        codex_command_path = $script:CodexCommandPath
        python_exe = $script:PythonExe
        codex_args = $codexArgs
        prompt_file = $promptFile
        log_file = $logFile
        exit_code_file = $exitCodeFile
        runner_error_file = $runnerErrorFile
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $runnerConfigFile -Encoding UTF8

    $runnerStdoutFile = Join-Path $script:LogDir "$stamp.$safeRole.runner.stdout.txt"
    $runnerStderrFile = Join-Path $script:LogDir "$stamp.$safeRole.runner.stderr.txt"
    $process = Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $runnerFile,
        "-ConfigPath",
        $runnerConfigFile
    ) -WorkingDirectory $script:WorkspaceRoot -WindowStyle Hidden -RedirectStandardOutput $runnerStdoutFile -RedirectStandardError $runnerStderrFile -PassThru

    $exitCode = $null
    $startedAt = Get-Date
    $lastProgressAt = $startedAt
    $lastLogLength = -1L
    $lastLogWriteTicks = 0L
    $deadline = $null
    if ($SubagentTimeoutMinutes -gt 0) {
        $deadline = $startedAt.AddMinutes($SubagentTimeoutMinutes)
    }

    while (-not $process.HasExited) {
        if (Test-StopRequested) {
            Write-AgentMessage "Stop requested while subagent '$Role' is running; terminating its process tree."
            Stop-ProcessTree -ProcessId $process.Id
            $exitCode = 130
            break
        }

        if ($null -ne $deadline -and (Get-Date) -ge $deadline) {
            Write-AgentMessage "Subagent '$Role' exceeded timeout of $SubagentTimeoutMinutes minutes; terminating its process tree."
            Stop-ProcessTree -ProcessId $process.Id
            $exitCode = 124
            break
        }

        $waitSeconds = [Math]::Max(5, $SubagentStatusSeconds)
        Start-Sleep -Seconds $waitSeconds
        Write-AgentLogStatus -Role $Role -LogFile $logFile
        if (Test-Path -LiteralPath $logFile) {
            $logItem = Get-Item -LiteralPath $logFile
            $currentLength = [int64]$logItem.Length
            $currentWriteTicks = [int64]$logItem.LastWriteTimeUtc.Ticks
            if ($currentLength -ne $lastLogLength -or $currentWriteTicks -ne $lastLogWriteTicks) {
                $lastProgressAt = Get-Date
                $lastLogLength = $currentLength
                $lastLogWriteTicks = $currentWriteTicks
            }
        }

        if ($StalledSubagentMinutes -gt 0 -and ((Get-Date) - $lastProgressAt).TotalMinutes -ge $StalledSubagentMinutes) {
            $stallMessage = "Subagent '$Role' appears stalled: no log progress since $($lastProgressAt.ToString("yyyy-MM-dd HH:mm:ss")) for at least $StalledSubagentMinutes minutes. Terminating process tree and handing to repair."
            Write-AgentMessage $stallMessage
            Add-Content -LiteralPath $logFile -Value "`n=== supervisor stall detection ===`n$stallMessage" -Encoding UTF8
            Add-StateHistory -State $script:State -Event "subagent-stalled" -Data ([pscustomobject]@{
                role = $Role
                started_at = $startedAt.ToString("o")
                last_progress_at = $lastProgressAt.ToString("o")
                stalled_minutes = $StalledSubagentMinutes
                log = $logFile
            })
            Save-State -State $script:State -Path $script:StatePath
            Stop-ProcessTree -ProcessId $process.Id
            $exitCode = 125
            break
        }
        Set-SupervisorHeartbeat -Context "waiting for subagent '$Role'"
    }

    $process.Refresh()
    if ($null -eq $exitCode) {
        if (Test-Path -LiteralPath $exitCodeFile) {
            try {
                $exitCode = [int](Get-Content -Raw -LiteralPath $exitCodeFile)
            }
            catch {
                $exitCode = $null
            }
        }
    }
    if ($null -eq $exitCode) {
        try {
            if ($process.HasExited) {
                $exitCode = $process.ExitCode
            }
        }
        catch {
            $exitCode = $null
        }
    }
    if ($null -eq $exitCode) {
        $exitCode = 999
        Write-AgentMessage "Subagent '$Role' wrapper exited without an exit code; treating it as failure."
    }

    foreach ($diagnosticFile in @($runnerStdoutFile, $runnerStderrFile)) {
        if ((Test-Path -LiteralPath $diagnosticFile) -and ((Get-Item -LiteralPath $diagnosticFile).Length -gt 0)) {
            Add-Content -LiteralPath $logFile -Value "`n=== runner diagnostic: $diagnosticFile ===" -Encoding UTF8
            Get-Content -LiteralPath $diagnosticFile -Encoding UTF8 | Add-Content -LiteralPath $logFile -Encoding UTF8
        }
    }
    if ((Test-Path -LiteralPath $runnerErrorFile) -and ((Get-Item -LiteralPath $runnerErrorFile).Length -gt 0)) {
        Add-Content -LiteralPath $logFile -Value "`n=== runner status: $runnerErrorFile ===" -Encoding UTF8
        Get-Content -LiteralPath $runnerErrorFile -Encoding UTF8 | Add-Content -LiteralPath $logFile -Encoding UTF8
    }

    Write-AgentMessage "Codex subagent '$Role' exited with code $exitCode"
    if ($StopOnCodexFailure -and $exitCode -ne 0) {
        throw "Codex subagent '$Role' failed with exit code $exitCode. See $logFile"
    }

    return [pscustomobject]@{
        role = $Role
        exit_code = $exitCode
        prompt = $promptFile
        log = $logFile
        last_message = $lastMessageFile
    }
}

function Invoke-RepairAgent {
    param(
        [Parameter(Mandatory = $true)][string]$Context,
        [Parameter(Mandatory = $false)]$FailedResult = $null,
        [Parameter(Mandatory = $false)]$Metrics = $null
    )

    if ($DisableAutoRepair) {
        Write-AgentMessage "Auto-repair disabled; skipping repair for: $Context"
        return $null
    }

    $failedText = "No failed subagent result was provided."
    if ($null -ne $FailedResult) {
        $failedText = ($FailedResult | ConvertTo-Json -Depth 8)
    }

    $metricsText = "No readable metrics JSON was found."
    if ($null -ne $Metrics) {
        $metricsText = ($Metrics | ConvertTo-Json -Depth 8)
    }

    $prompt = @"
Repair context:
$Context

Failed or suspicious subagent result:
$failedText

Latest supervisor-readable metrics:
$metricsText

You are the repair subagent. Inspect the repository, .codex_quant_agent logs, latest prompt files, latest last-message files, test output, and data/reports outputs. Fix the concrete blocker that prevents the supervisor from reaching its /goal.

Repair priorities:
1. If the project is missing, create or finish the minimal runnable implementation required by the spec.
2. If a command failed, reproduce the failure, inspect the traceback/log, and patch the root cause.
3. If the previous subagent stalled, timed out, produced a zero-byte log, or exited 124/125/999, inspect the runner files under .codex_quant_agent/state and .codex_quant_agent/logs, then fix the concrete launch, command, dependency, or long-running workflow issue.
4. If no metrics JSON exists, make the backtest/report path generate data/reports/backtest metrics JSON with total_return_pct, monthly return fields, and total_trades.
5. If trades are missing, make the simulation produce trade_log.csv with every simulated buy/sell reason and PnL fields.
6. If the backtest is negative, do not fake results. Fix real issues or make one bounded strategy/model/risk improvement based on the logged losses.
7. Keep live_trading.enabled=false and live_order_enabled=false. Do not implement real-money order placement.

After repair, run the smallest relevant validation command and summarize what was fixed.
"@

    $repairResult = Invoke-CodexAgent -Role "repair-$((Get-Date).ToString("yyyyMMdd_HHmmss"))" -PromptBody $prompt
    return $repairResult
}

function Repair-IfSubagentFailed {
    param(
        [Parameter(Mandatory = $true)][string]$Context,
        [Parameter(Mandatory = $false)]$Result = $null,
        [Parameter(Mandatory = $false)]$Metrics = $null
    )

    if ($null -eq $Result) {
        return $false
    }

    if ([int]$Result.exit_code -eq 0) {
        return $false
    }

    Write-AgentMessage "Subagent '$($Result.role)' failed; starting repair agent."
    Register-SupervisorFailure -Reason "$Context; subagent '$($Result.role)' exited with code $($Result.exit_code)"
    $repairResult = Invoke-RepairAgent -Context $Context -FailedResult $Result -Metrics $Metrics
    if ($null -ne $repairResult) {
        Add-StateHistory -State $script:State -Event "repair" -Data $repairResult
        Save-State -State $script:State -Path $script:StatePath
        if ([int]$repairResult.exit_code -eq 0) {
            Clear-SupervisorFailures -Reason "repair agent completed"
        }
    }

    return $true
}

function Test-ProjectReady {
    $mainPath = Join-Path $script:WorkspaceRoot "src\main.py"
    $pyprojectPath = Join-Path $script:WorkspaceRoot "pyproject.toml"
    $symbolsPath = Join-Path $script:WorkspaceRoot "config\symbols.yaml"
    $backtestPath = Join-Path $script:WorkspaceRoot "config\backtest.yaml"

    return (
        (Test-Path -LiteralPath $mainPath) -and
        ((Test-Path -LiteralPath $pyprojectPath) -or (Test-Path -LiteralPath (Join-Path $script:WorkspaceRoot "requirements.txt"))) -and
        (Test-Path -LiteralPath $symbolsPath) -and
        (Test-Path -LiteralPath $backtestPath)
    )
}

function Find-MetricValue {
    param(
        [Parameter(Mandatory = $false)]$Node,
        [Parameter(Mandatory = $true)][string[]]$Keys,
        [int]$Depth = 0
    )

    if ($null -eq $Node -or $Depth -gt 8) {
        return $null
    }

    if ($Node -is [string]) {
        return $null
    }

    if ($Node -is [System.Collections.IEnumerable] -and -not ($Node -is [pscustomobject])) {
        foreach ($item in $Node) {
            $value = Find-MetricValue -Node $item -Keys $Keys -Depth ($Depth + 1)
            if ($null -ne $value) {
                return $value
            }
        }
        return $null
    }

    $properties = $Node.PSObject.Properties
    foreach ($property in $properties) {
        if ($Keys -contains $property.Name) {
            try {
                return [double]$property.Value
            }
            catch {
                return $null
            }
        }
    }

    foreach ($property in $properties) {
        $value = Find-MetricValue -Node $property.Value -Keys $Keys -Depth ($Depth + 1)
        if ($null -ne $value) {
            return $value
        }
    }

    return $null
}

function Get-LatestBacktestMetrics {
    param([datetime]$NotBefore = [datetime]::MinValue)

    $reportRoot = Join-Path $script:WorkspaceRoot "data\reports"
    if (-not (Test-Path -LiteralPath $reportRoot)) {
        return $null
    }

    $preferredMetrics = Join-Path $reportRoot "backtest\metrics.json"
    if (Test-Path -LiteralPath $preferredMetrics) {
        $files = @(Get-Item -LiteralPath $preferredMetrics)
    }
    else {
        $files = @()
    }

    $files += Get-ChildItem -LiteralPath $reportRoot -Recurse -File -Filter "*.json" |
        Where-Object { $_.FullName -ne $preferredMetrics } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 50

    foreach ($file in $files) {
        try {
            if ($file.Length -gt 10MB) {
                continue
            }
            if ($file.LastWriteTime -lt $NotBefore) {
                continue
            }

            $json = Get-Content -Raw -LiteralPath $file.FullName -Encoding UTF8 | ConvertFrom-Json
            $properties = $json.PSObject.Properties
            $returnPct = $null
            foreach ($name in @("total_return_pct", "cumulative_return_pct", "return_pct", "net_return_pct")) {
                $property = $properties[$name]
                if ($null -ne $property) {
                    $returnPct = [double]$property.Value
                    break
                }
            }

            $tradeCount = $null
            foreach ($name in @("total_trades", "trade_count", "trades_count", "num_trades")) {
                $property = $properties[$name]
                if ($null -ne $property) {
                    $tradeCount = [double]$property.Value
                    break
                }
            }

            $maxDrawdownPct = $null
            foreach ($name in @("max_drawdown_pct", "maximum_drawdown_pct", "max_dd_pct")) {
                $property = $properties[$name]
                if ($null -ne $property) {
                    $maxDrawdownPct = [double]$property.Value
                    break
                }
            }

            $averageMonthlyReturnPct = $null
            $property = $properties["average_monthly_return_pct"]
            if ($null -ne $property) {
                $averageMonthlyReturnPct = [double]$property.Value
            }

            $medianMonthlyReturnPct = $null
            $property = $properties["median_monthly_return_pct"]
            if ($null -ne $property) {
                $medianMonthlyReturnPct = [double]$property.Value
            }

            $minMonthlyReturnPct = $null
            $property = $properties["min_monthly_return_pct"]
            if ($null -ne $property) {
                $minMonthlyReturnPct = [double]$property.Value
            }

            $maxMonthlyReturnPct = $null
            $property = $properties["max_monthly_return_pct"]
            if ($null -ne $property) {
                $maxMonthlyReturnPct = [double]$property.Value
            }

            $profitFactor = $null
            $property = $properties["profit_factor"]
            if ($null -ne $property) {
                $profitFactor = [double]$property.Value
            }

            $positiveMonthRatio = $null
            $property = $properties["positive_active_month_ratio"]
            if ($null -eq $property) {
                $property = $properties["positive_month_ratio"]
            }
            if ($null -ne $property) {
                $positiveMonthRatio = [double]$property.Value
            }

            $walkForwardWindows = $null
            $property = $properties["walk_forward_windows"]
            if ($null -ne $property) {
                $walkForwardWindows = [double]$property.Value
            }

            $walkForwardFallbackUsed = $null
            $property = $properties["walk_forward_fallback_used"]
            if ($null -ne $property) {
                $walkForwardFallbackUsed = [bool]$property.Value
            }

            $latestMonthlyReturnPct = $null
            $property = $properties["monthly_returns"]
            if ($null -ne $property -and $null -ne $property.Value) {
                $monthlyRows = @($property.Value)
                if ($monthlyRows.Count -gt 0 -and ($monthlyRows[-1].PSObject.Properties.Name -contains "return_pct")) {
                    $latestMonthlyReturnPct = [double]$monthlyRows[-1].return_pct
                }
            }

            $dataIsSynthetic = $null
            $property = $properties["data_is_synthetic"]
            if ($null -ne $property) {
                $dataIsSynthetic = [bool]$property.Value
            }

            $dataProviders = @()
            $property = $properties["data_providers"]
            if ($null -ne $property -and $null -ne $property.Value) {
                $dataProviders = @($property.Value)
            }

            if ($null -ne $returnPct -or $null -ne $tradeCount) {
                return [pscustomobject]@{
                    file = $file.FullName
                    total_return_pct = $returnPct
                    total_trades = $tradeCount
                    max_drawdown_pct = $maxDrawdownPct
                    average_monthly_return_pct = $averageMonthlyReturnPct
                    median_monthly_return_pct = $medianMonthlyReturnPct
                    min_monthly_return_pct = $minMonthlyReturnPct
                    max_monthly_return_pct = $maxMonthlyReturnPct
                    profit_factor = $profitFactor
                    positive_active_month_ratio = $positiveMonthRatio
                    walk_forward_windows = $walkForwardWindows
                    walk_forward_fallback_used = $walkForwardFallbackUsed
                    latest_monthly_return_pct = $latestMonthlyReturnPct
                    data_is_synthetic = $dataIsSynthetic
                    data_providers = $dataProviders
                    last_write_time = $file.LastWriteTime.ToString("o")
                }
            }
        }
        catch {
            continue
        }
    }

    return $null
}

function Get-MetricDouble {
    param(
        [Parameter(Mandatory = $false)]$Metrics,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if ($null -eq $Metrics -or -not ($Metrics.PSObject.Properties.Name -contains $Name) -or $null -eq $Metrics.$Name) {
        return $null
    }

    try {
        return [double]$Metrics.$Name
    }
    catch {
        return $null
    }
}

function Test-BacktestSuccess {
    param([Parameter(Mandatory = $false)]$Metrics)

    if ($null -eq $Metrics) {
        return $false
    }

    $monthlyMetric = Get-MonthlyReturnMetricPct -Metrics $Metrics
    if ($null -eq $monthlyMetric) {
        return $false
    }

    $totalReturn = Get-MetricDouble -Metrics $Metrics -Name "total_return_pct"
    if ($null -eq $totalReturn -or $totalReturn -le $MinTotalReturnPct) {
        return $false
    }

    $profitFactor = Get-MetricDouble -Metrics $Metrics -Name "profit_factor"
    if ($null -eq $profitFactor -or $profitFactor -lt $MinProfitFactor) {
        return $false
    }

    $maxDrawdown = Get-MetricDouble -Metrics $Metrics -Name "max_drawdown_pct"
    if ($null -eq $maxDrawdown -or $maxDrawdown -lt (-1.0 * [math]::Abs($MaxDrawdownPct))) {
        return $false
    }

    $positiveMonthRatio = Get-MetricDouble -Metrics $Metrics -Name "positive_active_month_ratio"
    if ($null -eq $positiveMonthRatio -or $positiveMonthRatio -lt $MinPositiveMonthRatio) {
        return $false
    }

    $minMonthlyReturn = Get-MetricDouble -Metrics $Metrics -Name "min_monthly_return_pct"
    if ($null -eq $minMonthlyReturn -or $minMonthlyReturn -lt $MinMonthlyReturnFloorPct) {
        return $false
    }

    $walkForwardWindows = Get-MetricDouble -Metrics $Metrics -Name "walk_forward_windows"
    if ($null -eq $walkForwardWindows -or $walkForwardWindows -lt $MinWalkForwardWindows) {
        return $false
    }

    if (($Metrics.PSObject.Properties.Name -contains "walk_forward_fallback_used") -and $Metrics.walk_forward_fallback_used -eq $true) {
        return $false
    }

    $tradesOk = $true
    if ($MinTrades -gt 0) {
        $tradesOk = ($null -ne $Metrics.total_trades -and $Metrics.total_trades -ge $MinTrades)
    }

    $realDataOk = $true
    if ($Metrics.PSObject.Properties.Name -contains "data_is_synthetic") {
        $realDataOk = ($Metrics.data_is_synthetic -ne $true)
    }

    return ($monthlyMetric -ge (Get-CurrentReturnTargetPct) -and $tradesOk -and $realDataOk)
}

function Get-MonthlyReturnMetricPct {
    param([Parameter(Mandatory = $false)]$Metrics)

    if ($null -eq $Metrics) {
        return $null
    }

    $map = @{
        average = "average_monthly_return_pct"
        median = "median_monthly_return_pct"
        minimum = "min_monthly_return_pct"
        latest = "latest_monthly_return_pct"
    }
    $propertyName = $map[$MonthlyReturnTargetMode]
    if (-not ($Metrics.PSObject.Properties.Name -contains $propertyName)) {
        return $null
    }
    $value = $Metrics.$propertyName
    if ($null -eq $value) {
        return $null
    }
    return [double]$value
}

function Get-CurrentReturnTargetPct {
    if ($null -ne $script:State -and ($script:State.PSObject.Properties.Name -contains "current_target_total_return_pct") -and $null -ne $script:State.current_target_total_return_pct) {
        return [double]$script:State.current_target_total_return_pct
    }

    return [double]$TargetTotalReturnPct
}

function Set-CurrentReturnTargetPct {
    param([Parameter(Mandatory = $true)][double]$Value)

    if (-not ($script:State.PSObject.Properties.Name -contains "current_target_total_return_pct")) {
        $script:State | Add-Member -NotePropertyName "current_target_total_return_pct" -NotePropertyValue $Value
    }
    else {
        $script:State.current_target_total_return_pct = $Value
    }
}

function Complete-CurrentReturnTarget {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $false)]$Metrics
    )

    $currentTarget = Get-CurrentReturnTargetPct
    $milestone = [pscustomobject]@{
        time = (Get-Date).ToString("o")
        achieved_target_monthly_return_pct = $currentTarget
        monthly_return_target_mode = $MonthlyReturnTargetMode
        achieved_monthly_return_metric_pct = Get-MonthlyReturnMetricPct -Metrics $Metrics
        regression_cycles = $State.regression_cycles
        successful_cycles = $State.successful_cycles
        consecutive_successes = $State.consecutive_successes
        metrics = $Metrics
    }
    $State.target_milestones = @($State.target_milestones) + @($milestone)

    if ($StopWhenStableTargetReached) {
        $State.success = $true
        $State.status = "stable_target_reached"
        return $false
    }

    if ($MaxTargetTotalReturnPct -gt 0 -and $currentTarget -ge $MaxTargetTotalReturnPct) {
        $State.success = $true
        $State.status = "max_return_target_reached"
        return $false
    }

    $nextTarget = $currentTarget + $TargetReturnIncrementPct
    if ($MaxTargetTotalReturnPct -gt 0 -and $nextTarget -gt $MaxTargetTotalReturnPct) {
        $nextTarget = $MaxTargetTotalReturnPct
    }

    Set-CurrentReturnTargetPct -Value $nextTarget
    $State.consecutive_successes = 0
    $State.successful_cycles = 0
    Add-StateHistory -State $State -Event "return-target-increased" -Data ([pscustomobject]@{
        previous_target_monthly_return_pct = $currentTarget
        next_target_monthly_return_pct = $nextTarget
        increment_pct = $TargetReturnIncrementPct
        monthly_return_target_mode = $MonthlyReturnTargetMode
    })
    $State.status = "running"
    return $true
}

function Get-DataSignature {
    $dataRoot = Join-Path $script:WorkspaceRoot "data"
    if (-not (Test-Path -LiteralPath $dataRoot)) {
        return [pscustomobject]@{
            signature = "no-data"
            file_count = 0
            total_bytes = 0
            newest_write_time = $null
        }
    }

    $files = Get-ChildItem -LiteralPath $dataRoot -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch "\\data\\reports\\" } |
        Sort-Object FullName

    $totalBytes = 0L
    $newest = $null
    $parts = foreach ($file in $files) {
        $totalBytes += [int64]$file.Length
        if ($null -eq $newest -or $file.LastWriteTimeUtc -gt $newest) {
            $newest = $file.LastWriteTimeUtc
        }
        "{0}|{1}|{2}" -f $file.FullName.Substring($script:WorkspaceRoot.Length), $file.Length, $file.LastWriteTimeUtc.Ticks
    }

    $text = ($parts -join "`n")
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
        $hashBytes = $sha.ComputeHash($bytes)
        $signature = -join ($hashBytes | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $sha.Dispose()
    }

    return [pscustomobject]@{
        signature = $signature
        file_count = @($files).Count
        total_bytes = $totalBytes
        newest_write_time = if ($null -ne $newest) { $newest.ToString("o") } else { $null }
    }
}

function Update-RegressionState {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $false)]$Metrics
    )

    $State.regression_cycles = [int]$State.regression_cycles + 1
    $isSuccess = Test-BacktestSuccess -Metrics $Metrics
    if ($isSuccess) {
        $State.successful_cycles = [int]$State.successful_cycles + 1
        $State.consecutive_successes = [int]$State.consecutive_successes + 1
    }
    else {
        $State.consecutive_successes = 0
    }

    $signature = Get-DataSignature
    if ($null -ne $State.last_data_signature -and $State.last_data_signature.signature -eq $signature.signature) {
        $State.repeated_data_signature_cycles = [int]$State.repeated_data_signature_cycles + 1
    }
    else {
        $State.repeated_data_signature_cycles = 0
    }
    $State.last_data_signature = $signature

    Add-StateHistory -State $State -Event "regression-result" -Data ([pscustomobject]@{
        regression_cycles = $State.regression_cycles
        successful_cycles = $State.successful_cycles
        consecutive_successes = $State.consecutive_successes
        current_target_total_return_pct = Get-CurrentReturnTargetPct
        success = $isSuccess
        data_signature = $signature
        metrics = $Metrics
    })

    return $isSuccess
}

function Test-StableTargetReached {
    param([Parameter(Mandatory = $true)]$State)

    if ([int]$State.regression_cycles -lt $MinRegressionCycles) {
        return $false
    }

    if ([int]$State.consecutive_successes -lt $RequiredConsecutiveSuccesses) {
        return $false
    }

    if ($MinRuntimeHours -gt 0) {
        $createdAt = [datetime]$State.created_at
        if (((Get-Date) - $createdAt).TotalHours -lt $MinRuntimeHours) {
            return $false
        }
    }

    return $true
}

function Test-ShouldExpandData {
    param(
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][int]$NextCycle,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    if ($MaxDataExpansionRuns -gt 0 -and [int]$State.data_expansion_runs -ge $MaxDataExpansionRuns) {
        return $false
    }

    if ($Reason -eq "initial" -and -not $SkipInitialDataExpansion -and [int]$State.data_expansion_runs -eq 0) {
        return $true
    }

    if ($DataExpansionEveryCycles -gt 0 -and $NextCycle -gt 0 -and ($NextCycle % $DataExpansionEveryCycles -eq 0)) {
        return $true
    }

    if ($DataStaleCyclesBeforeExpansion -gt 0 -and [int]$State.repeated_data_signature_cycles -ge $DataStaleCyclesBeforeExpansion) {
        return $true
    }

    return $false
}

function Get-DevelopmentPrompt {
    param([int]$PassNumber)

    @"
Development pass $PassNumber.

Turn the spec into a runnable repository in the current directory. If a partial implementation exists, continue from it instead of rewriting unrelated work.

Priorities:
1. Create the required Python project structure, config YAML files, README, requirements/pyproject, src package, and tests.
2. Implement the CLI commands listed in the spec:
   probe-symbols, download-history, normalize-data, build-features, build-labels, walk-forward, backtest, report, replay, paper-trade.
3. Build a runnable vertical slice even without paid API keys:
   config loading, symbol mapping, provider abstractions, sample/offline data generation, feature building, labels, simple models, walk-forward, backtest, report.
4. Backtest output must include metrics JSON/Markdown, equity curve CSV, signal log CSV, and trade_log.csv with a reason for each simulated buy/sell.
5. Implement safety defaults: live_trading.enabled=false, kabustation live_order_enabled=false, no real order sending.
6. Add focused tests for config loading, symbol mapping, resampling/features, labeling, costs, and backtest accounting.

If the whole spec is too large for one pass, create the smallest complete end-to-end version first, then leave structured TODOs. Run tests or at least syntax/import checks before finishing.
"@
}

function Get-QaPrompt {
    param([int]$PassNumber)

    @"
QA and integration pass $PassNumber.

Inspect the implementation against the spec. Fix concrete blockers that prevent the project from running. Then run the most relevant validation you can:
- python -m pytest
- python -m src.main probe-symbols --config config/data_sources.yaml
- python -m src.main backtest --model latest
- python -m src.main report --type backtest

Focus on:
- CLI commands exist and fail gracefully when API keys/data are missing.
- Offline sample or synthetic data path can run a simulation.
- No live trading is enabled.
- Trade logs include reasons and PnL fields.
- Reports expose total_return_pct and total_trades in JSON so the supervisor can read them.
- Tests cover the risky accounting and leakage-related areas.

Make small fixes if needed and record remaining gaps in your final response.
"@
}

function Get-BacktestRunnerPrompt {
    param([int]$CycleNumber)

    @"
Backtest cycle $CycleNumber.

Run the implemented local workflow and make it produce a fresh backtest report. If necessary, fix small execution blockers.

Preferred commands:
1. python -m pytest
2. python -m src.main probe-symbols --config config/data_sources.yaml
3. python -m src.main import-market-data --provider jquants --interval 1m
4. python -m src.main normalize-data
5. python -m src.main validate-data
6. python -m src.main build-features
7. python -m src.main build-labels
8. python -m src.main walk-forward --model random_forest
9. python -m src.main backtest --model latest
10. python -m src.main batch-search --candidates 24 --target-monthly-return-pct $(Get-CurrentReturnTargetPct) --min-trades $MinTrades --min-profit-factor $MinProfitFactor --max-drawdown-pct $MaxDrawdownPct --min-positive-month-ratio $MinPositiveMonthRatio --min-monthly-return-floor-pct $MinMonthlyReturnFloorPct --min-walk-forward-windows $MinWalkForwardWindows
11. python -m src.main report --type backtest

Use market_data_collector J-Quants parquet data when available. If minute data is missing, run the collector download commands or ask the data-expander path to do it; do not use synthetic data to satisfy profitability milestones. Keep outputs under data/reports/backtest.
Use batch-search for local candidate sweeps. It reuses real walk-forward predictions and runs many backtest-only parameter candidates under data/reports/experiments so the AI does not spend tokens supervising each candidate one by one.

Required output artifacts:
- metrics JSON containing total_return_pct and total_trades
- monthly_returns.csv and monthly_returns.html containing every year/month return_pct, including positive and negative months
- trade_log.csv with buy/sell reasons
- trade_log.html with searchable/sortable rows
- equity curve CSV
- markdown report
- metrics JSON must include data_is_synthetic=false for target success.

Do not tune just to make a fake number. Keep the simulation accounting coherent and costs/slippage enabled.
"@
}

function Get-DataExpansionPrompt {
    param(
        [int]$CycleNumber,
        [string]$Reason,
        [Parameter(Mandatory = $false)]$Metrics = $null,
        [Parameter(Mandatory = $false)]$DataSignature = $null
    )

    $metricsText = "No readable metrics JSON was provided."
    if ($null -ne $Metrics) {
        $metricsText = ($Metrics | ConvertTo-Json -Depth 8)
    }

    $signatureText = "No data signature was provided."
    if ($null -ne $DataSignature) {
        $signatureText = ($DataSignature | ConvertTo-Json -Depth 8)
    }

    @"
Data expansion cycle $CycleNumber.

Trigger reason:
$Reason

Current data signature:
$signatureText

Latest supervisor-readable metrics:
$metricsText

Task:
1. Inspect current data coverage, data lineage, and whether the backtest is using synthetic/offline data. Write or update data/reports/data_status.md with:
   - source type: real provider / public source / synthetic
   - date range by symbol
   - row counts by symbol
   - whether results are valid for profitability claims
2. If existing data is synthetic, too short, stale, or exhausted, expand historical data before the next regression:
   - Prefer the local market_data_collector package and configured authenticated J-Quants API key:
     python -m market_data_collector.cli download --provider jquants --symbols 1357,1570,1321,1571 --interval 1m --from-date 2024-05-16 --to-date 2026-05-15 --format parquet --incremental
     python -m market_data_collector.cli validate --provider jquants --symbols 1357,1570,1321,1571 --interval 1m --from-date 2024-05-16 --to-date 2026-05-15
     python -m src.main import-market-data --provider jquants --interval 1m --from-date 2024-05-16 --to-date 2026-05-15
   - Download 10-year daily J-Quants data for long-term regime/context research when useful, but do not treat daily-only data as minute-level execution evidence.
   - If paid/API data is unavailable, public providers may be used only for research diagnostics, not for profitability target success. Respect robots.txt, provider terms, rate limits, and avoid bypassing paywalls or private APIs.
   - Use config/symbols.yaml provider_symbol mappings; do not hard-code ETF symbols in strategy logic.
   - Try to extend coverage backward before config/historical.yaml start_date and forward to the current configured end date when real data is available.
3. After adding or changing raw/normalized data, invalidate stale derived artifacts that depend on old data: features, labels, model predictions, and backtest reports. Then rebuild enough of the pipeline so the next backtest can produce a fresh metrics.json.
4. Ensure the next backtest writes monthly_returns.csv and monthly_returns.html. The monthly table must include year_month, year, month, start equity, end equity, PnL, and return_pct for every month, whether positive or negative.
5. Keep data provenance explicit. If only synthetic data is possible, make that obvious in data_status.md and do not present positive returns as real-market evidence.
6. Keep live trading disabled and do not send real orders.

Run focused validation after data changes. If you cannot acquire more real data, improve the provider abstraction and leave exact instructions/API-key requirements in data_status.md.
"@
}

function Get-RegressionAuditPrompt {
    param(
        [int]$CycleNumber,
        [Parameter(Mandatory = $false)]$Metrics,
        [bool]$CycleSuccess
    )

    $metricsText = "No readable metrics JSON was provided."
    if ($null -ne $Metrics) {
        $metricsText = ($Metrics | ConvertTo-Json -Depth 8)
    }

    @"
Regression audit cycle $CycleNumber.

This cycle success flag from the supervisor: $CycleSuccess

Latest supervisor-readable metrics:
$metricsText

Task:
1. Treat any very large positive return as suspicious until proven otherwise.
2. Audit the latest run for future leakage, synthetic-data dependence, unrealistic execution, stale report reuse, training/test contamination, and costs/slippage being disabled.
2a. Confirm metrics.json has data_is_synthetic=false and data_providers does not contain synthetic providers before counting success.
3. Verify monthly_returns.csv and monthly_returns.html exist and include every month with return_pct whether positive or negative.
4. Add or run tests/checks that make the next regression harder to game.
5. If a concrete issue is found, fix it and force a fresh backtest on the next cycle by invalidating stale downstream artifacts as needed.
6. Write findings to data/reports/agent_reviews/regression_audit_$CycleNumber.md.

Do not optimize by disabling costs, reducing slippage unrealistically, or hard-coding dates/symbols. Keep live trading disabled.
"@
}

function Get-OptimizerPrompt {
    param(
        [int]$CycleNumber,
        [Parameter(Mandatory = $false)]$Metrics
    )

    $metricsText = "No readable metrics JSON was found."
    if ($null -ne $Metrics) {
        $metricsText = ($Metrics | ConvertTo-Json -Depth 8)
    }

    $configPolicy = "Do not overwrite production strategy/model/backtest YAML directly. Generate candidate or experiment config files and wire the CLI so they can be tested."
    if ($AllowAutonomousConfigApply) {
        $configPolicy = "You may update project config for the next backtest, but only for historical/paper mode. Record each changed parameter and the reason."
    }

    @"
Strategy optimizer cycle $CycleNumber.

Latest supervisor-readable metrics:
$metricsText

Task:
1. Inspect the latest data/reports/backtest outputs, trade_log.csv, signal logs, and tests.
2. First run a local batch sweep before spending effort on code changes:
   python -m src.main batch-search --candidates 48 --target-monthly-return-pct $(Get-CurrentReturnTargetPct) --min-trades $MinTrades --min-profit-factor $MinProfitFactor --max-drawdown-pct $MaxDrawdownPct --min-positive-month-ratio $MinPositiveMonthRatio --min-monthly-return-floor-pct $MinMonthlyReturnFloorPct --min-walk-forward-windows $MinWalkForwardWindows
   Inspect the newest data/reports/experiments/batch_search_*/ranking.csv and summary.json. If a candidate is materially better, explain which parameters improved the result and whether it passed all gates.
3. Write a concise loss/weakness review under data/reports/agent_reviews/iteration_$CycleNumber.md. Include reasons for negative trades grouped by signal/action, ETF, market regime, time of day, and execution/risk issue when those fields are available.
4. Make one bounded improvement to the strategy/model/backtest implementation or candidate config only after reading the batch ranking. Examples: confidence threshold, risk sizing, stop/take-profit behavior, feature bug, label threshold, execution-cost realism, leakage prevention, regime filter.
   Candidate edit surfaces include src/features/feature_pipeline.py, src/models/rule_based_model.py, src/models/sklearn_model.py, config/model.yaml, config/strategy.yaml, config/labeling.yaml, and config/backtest.yaml.
5. $configPolicy
6. Run the relevant tests/backtest again if practical.

Rules:
- Do not use future data in features.
- Do not disable costs/slippage just to make returns positive.
- Do not overfit by repeatedly hard-coding dates or symbols.
- Keep live trading disabled.
- No guarantee language.
"@
}

function Get-FinalReviewPrompt {
    param([Parameter(Mandatory = $false)]$Metrics)

    $metricsText = "No readable metrics JSON was found."
    if ($null -ne $Metrics) {
        $metricsText = ($Metrics | ConvertTo-Json -Depth 8)
    }

    @"
Final review.

Inspect the current project and latest reports. Produce or update a short summary under data/reports/agent_reviews/final_supervisor_review.md covering:
- what is implemented
- how to run it
- latest backtest metrics
- latest monthly return table location and target mode
- where trade logs and reasons are stored
- remaining risks/gaps
- whether the result is positive in this backtest, without implying future profit

Latest supervisor-readable metrics:
$metricsText
"@
}

$script:WorkspaceRoot = Resolve-AbsolutePath $Workspace
if (-not (Test-Path -LiteralPath $script:WorkspaceRoot)) {
    throw "Workspace does not exist: $script:WorkspaceRoot"
}

$script:SpecFile = $SpecPath
if (-not [System.IO.Path]::IsPathRooted($script:SpecFile)) {
    $script:SpecFile = Join-Path $script:WorkspaceRoot $script:SpecFile
}
$script:SpecFile = [System.IO.Path]::GetFullPath($script:SpecFile)
if (-not (Test-Path -LiteralPath $script:SpecFile)) {
    throw "Spec file does not exist: $script:SpecFile"
}

$script:PythonExe = Resolve-PythonExe
if ([string]::IsNullOrWhiteSpace($script:PythonExe)) {
    Write-AgentMessage "WARNING: No Python runtime was resolved. Subagents may need to install dependencies."
}
else {
    $env:QUANT_PYTHON_EXE = $script:PythonExe
}

$agentRoot = Join-Path $script:WorkspaceRoot ".codex_quant_agent"
$script:LogDir = Join-Path $agentRoot "logs"
$script:PromptDir = Join-Path $agentRoot "prompts"
$script:OutputDir = Join-Path $agentRoot "outputs"
$stateDir = Join-Path $agentRoot "state"
$script:StateDir = $stateDir
New-Directory $agentRoot
New-Directory $script:LogDir
New-Directory $script:PromptDir
New-Directory $script:OutputDir
New-Directory $stateDir

$script:SupervisorLog = Join-Path $script:LogDir "supervisor.log"
$statePath = Join-Path $stateDir "state.json"
$script:StatePath = $statePath
if ([string]::IsNullOrWhiteSpace($StopFile)) {
    $script:StopFilePath = Join-Path $agentRoot "STOP"
}
elseif ([System.IO.Path]::IsPathRooted($StopFile)) {
    $script:StopFilePath = [System.IO.Path]::GetFullPath($StopFile)
}
else {
    $script:StopFilePath = [System.IO.Path]::GetFullPath((Join-Path $script:WorkspaceRoot $StopFile))
}

$supervisorConfigPath = Join-Path $stateDir "supervisor_config.json"
$serviceTierForDisplay = if ([string]::IsNullOrWhiteSpace($ServiceTier)) { "default" } else { $ServiceTier }
[pscustomobject]@{
    workspace = $script:WorkspaceRoot
    spec = $script:SpecFile
    codex_exe = $CodexExe
    model = $Model
    reasoning_effort = $ReasoningEffort
    service_tier = $serviceTierForDisplay
    target_total_return_pct = $TargetTotalReturnPct
    target_return_increment_pct = $TargetReturnIncrementPct
    max_target_total_return_pct = $MaxTargetTotalReturnPct
    monthly_return_target_mode = $MonthlyReturnTargetMode
    min_trades = $MinTrades
    min_total_return_pct = $MinTotalReturnPct
    min_profit_factor = $MinProfitFactor
    max_drawdown_pct = $MaxDrawdownPct
    min_positive_month_ratio = $MinPositiveMonthRatio
    min_monthly_return_floor_pct = $MinMonthlyReturnFloorPct
    min_walk_forward_windows = $MinWalkForwardWindows
    min_regression_cycles = $MinRegressionCycles
    required_consecutive_successes = $RequiredConsecutiveSuccesses
    min_runtime_hours = $MinRuntimeHours
    data_expansion_every_cycles = $DataExpansionEveryCycles
    data_stale_cycles_before_expansion = $DataStaleCyclesBeforeExpansion
    max_data_expansion_runs = $MaxDataExpansionRuns
    max_cycles = $MaxCycles
    max_development_passes = $MaxDevelopmentPasses
    repair_after_unready_passes = $RepairAfterUnreadyPasses
    sleep_seconds = $SleepSeconds
    heartbeat_minutes = $HeartbeatMinutes
    subagent_timeout_minutes = $SubagentTimeoutMinutes
    subagent_status_seconds = $SubagentStatusSeconds
    stalled_subagent_minutes = $StalledSubagentMinutes
    max_consecutive_failures = $MaxConsecutiveFailures
    stop_file = $script:StopFilePath
    use_search = [bool]$UseSearch
    dangerously_bypass_approvals_and_sandbox = [bool]$DangerouslyBypassApprovalsAndSandbox
    allow_autonomous_config_apply = [bool]$AllowAutonomousConfigApply
    skip_initial_data_expansion = [bool]$SkipInitialDataExpansion
    stop_when_stable_target_reached = [bool]$StopWhenStableTargetReached
    disable_auto_repair = [bool]$DisableAutoRepair
    dry_run = [bool]$DryRun
    started_at = (Get-Date).ToString("o")
} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $supervisorConfigPath -Encoding UTF8

Write-AgentMessage "Workspace: $script:WorkspaceRoot"
Write-AgentMessage "Spec: $script:SpecFile"
Write-AgentMessage "Model: $Model, reasoning effort: $ReasoningEffort, service tier: $serviceTierForDisplay"
Write-AgentMessage "Python: $script:PythonExe"
Write-AgentMessage "Goal: stable fresh regressions with dynamic monthly return target starting at $TargetTotalReturnPct%, incrementing by $TargetReturnIncrementPct percentage points"
Write-AgentMessage "MonthlyReturnTargetMode: $MonthlyReturnTargetMode"
Write-AgentMessage "Risk/quality gates: MinTrades=$MinTrades, MinTotalReturnPct>$MinTotalReturnPct, MinProfitFactor=$MinProfitFactor, MaxDrawdownPct=$MaxDrawdownPct, MinPositiveActiveMonthRatio=$MinPositiveMonthRatio, MinMonthlyReturnFloorPct=$MinMonthlyReturnFloorPct, MinWalkForwardWindows=$MinWalkForwardWindows"
Write-AgentMessage "Current target stop cap: MaxTargetTotalReturnPct=$MaxTargetTotalReturnPct (0 means no cap), StopWhenStableTargetReached=$StopWhenStableTargetReached"
Write-AgentMessage "Stable target requires: MinRegressionCycles=$MinRegressionCycles, RequiredConsecutiveSuccesses=$RequiredConsecutiveSuccesses, MinRuntimeHours=$MinRuntimeHours"
Write-AgentMessage "Data expansion: every $DataExpansionEveryCycles cycles, stale after $DataStaleCyclesBeforeExpansion repeated data signatures, MaxDataExpansionRuns=$MaxDataExpansionRuns (0 unlimited)"
Write-AgentMessage "MaxCycles: $MaxCycles (0 means unlimited until the goal is reached)"
Write-AgentMessage "MaxDevelopmentPasses: $MaxDevelopmentPasses (0 means unlimited; dry-run treats unlimited as one pass)"
Write-AgentMessage "RepairAfterUnreadyPasses: $RepairAfterUnreadyPasses"
Write-AgentMessage "SubagentTimeoutMinutes: $SubagentTimeoutMinutes (0 means no timeout)"
Write-AgentMessage "StalledSubagentMinutes: $StalledSubagentMinutes (0 means no stall detection)"
Write-AgentMessage "Auto-repair: $(-not $DisableAutoRepair)"
Write-AgentMessage "Stop file: $script:StopFilePath"
Write-AgentMessage "Live trading guardrail: always disabled by prompt"

if (-not $DryRun) {
    Test-CodexCli -CommandName $CodexExe
}

$script:State = Read-State -Path $statePath
$state = $script:State
$state.status = "running"
$state.stop_file = $script:StopFilePath
$state.target = [pscustomobject]@{
    initial_total_return_pct_gt = $TargetTotalReturnPct
    current_total_return_pct_gt = Get-CurrentReturnTargetPct
    increment_pct = $TargetReturnIncrementPct
    max_total_return_pct = $MaxTargetTotalReturnPct
    monthly_return_target_mode = $MonthlyReturnTargetMode
    min_trades = $MinTrades
    min_total_return_pct = $MinTotalReturnPct
    min_profit_factor = $MinProfitFactor
    max_drawdown_pct = $MaxDrawdownPct
    min_positive_month_ratio = $MinPositiveMonthRatio
    min_monthly_return_floor_pct = $MinMonthlyReturnFloorPct
    min_walk_forward_windows = $MinWalkForwardWindows
    min_regression_cycles = $MinRegressionCycles
    required_consecutive_successes = $RequiredConsecutiveSuccesses
    min_runtime_hours = $MinRuntimeHours
}
Save-State -State $state -Path $statePath
$stopRequested = $false

try {
    if (-not (Test-ProjectReady)) {
        Write-AgentMessage "Project is not ready yet; starting development passes."
        $developmentLimit = $MaxDevelopmentPasses
        if ($DryRun -and $developmentLimit -le 0) {
            $developmentLimit = 1
        }

        while ((($developmentLimit -le 0) -or ([int]$state.development_pass -lt $developmentLimit)) -and -not (Test-ProjectReady)) {
            Set-SupervisorHeartbeat -Context "development pass $([int]$state.development_pass + 1)"
            if (Test-StopRequested) {
                $stopRequested = $true
                break
            }

            $state.development_pass = [int]$state.development_pass + 1
            Save-State -State $state -Path $statePath

            $devResult = Invoke-CodexAgent -Role "developer-pass-$($state.development_pass)" -PromptBody (Get-DevelopmentPrompt -PassNumber $state.development_pass)
            Add-StateHistory -State $state -Event "developer-pass" -Data $devResult
            Save-State -State $state -Path $statePath
            Repair-IfSubagentFailed -Context "developer pass $($state.development_pass) failed while creating the project" -Result $devResult | Out-Null

            $qaResult = Invoke-CodexAgent -Role "qa-pass-$($state.development_pass)" -PromptBody (Get-QaPrompt -PassNumber $state.development_pass)
            Add-StateHistory -State $state -Event "qa-pass" -Data $qaResult
            Save-State -State $state -Path $statePath
            Repair-IfSubagentFailed -Context "QA pass $($state.development_pass) failed while validating project readiness" -Result $qaResult | Out-Null

            if ((-not (Test-ProjectReady)) -and $RepairAfterUnreadyPasses -gt 0 -and ([int]$state.development_pass % $RepairAfterUnreadyPasses -eq 0)) {
                Write-AgentMessage "Project is still not ready after $($state.development_pass) development passes; starting repair agent."
                Register-SupervisorFailure -Reason "Project not ready after $($state.development_pass) development passes"
                $repairResult = Invoke-RepairAgent -Context "Project is still missing readiness files after $($state.development_pass) development/QA passes."
                Add-StateHistory -State $state -Event "repair-project-unready-loop" -Data $repairResult
                Save-State -State $state -Path $statePath
                if ($null -ne $repairResult -and [int]$repairResult.exit_code -eq 0) {
                    Clear-SupervisorFailures -Reason "project readiness repair completed"
                }
            }
        }
    }

    while ((-not $stopRequested) -and -not (Test-ProjectReady)) {
        Write-AgentMessage "Project still does not satisfy basic readiness checks after development passes."
        if ($DryRun) {
            $state.status = "dry_run_completed_project_not_ready"
            Save-State -State $state -Path $statePath
            Write-AgentMessage "DryRun completed. Real Codex runs would create or improve the project before backtest cycles."
            exit 0
        }
        if (Test-StopRequested) {
            $stopRequested = $true
            break
        }
        if ($DisableAutoRepair) {
            $state.status = "blocked_project_not_ready"
            Save-State -State $state -Path $statePath
            throw "Project is not ready and auto-repair is disabled."
        }

        Register-SupervisorFailure -Reason "Project readiness check failed"
        $repairResult = Invoke-RepairAgent -Context "Project readiness failed: src/main.py, pyproject/requirements, config/symbols.yaml, or config/backtest.yaml is missing."
        Add-StateHistory -State $state -Event "repair-project-readiness" -Data $repairResult
        Save-State -State $state -Path $statePath
        if ($null -ne $repairResult -and [int]$repairResult.exit_code -eq 0) {
            Clear-SupervisorFailures -Reason "project readiness repair completed"
        }

        if ($SleepSeconds -gt 0) {
            Write-AgentMessage "Sleeping $SleepSeconds seconds before rechecking project readiness."
            Start-Sleep -Seconds $SleepSeconds
        }
    }

    if ($stopRequested) {
        Write-AgentMessage "Supervisor stopped before project readiness completed."
    }
    else {
    Write-AgentMessage "Project readiness checks passed."

    if (Test-ShouldExpandData -State $state -NextCycle 1 -Reason "initial") {
        $dataSignature = Get-DataSignature
        Write-AgentMessage "Starting initial data expansion before regression cycles."
        $state.data_expansion_runs = [int]$state.data_expansion_runs + 1
        Save-State -State $state -Path $statePath
        $dataResult = Invoke-CodexAgent -Role "data-expander-initial" -PromptBody (Get-DataExpansionPrompt -CycleNumber 0 -Reason "initial long-run startup" -Metrics $state.last_metrics -DataSignature $dataSignature)
        Add-StateHistory -State $state -Event "data-expansion" -Data $dataResult
        Save-State -State $state -Path $statePath
        Repair-IfSubagentFailed -Context "initial data expansion failed" -Result $dataResult -Metrics $state.last_metrics | Out-Null
    }

    while ($true) {
        Set-SupervisorHeartbeat -Context "cycle $([int]$state.cycle + 1)"
        if (Test-StopRequested) {
            $stopRequested = $true
            break
        }

        if ($MaxCycles -gt 0 -and [int]$state.cycle -ge $MaxCycles) {
            Write-AgentMessage "Reached MaxCycles=$MaxCycles."
            break
        }

        $nextCycle = [int]$state.cycle + 1
        if (Test-ShouldExpandData -State $state -NextCycle $nextCycle -Reason "periodic") {
            $dataSignature = Get-DataSignature
            Write-AgentMessage "Starting data expansion before cycle $nextCycle."
            $state.data_expansion_runs = [int]$state.data_expansion_runs + 1
            Save-State -State $state -Path $statePath
            $dataResult = Invoke-CodexAgent -Role "data-expander-cycle-$nextCycle" -PromptBody (Get-DataExpansionPrompt -CycleNumber $nextCycle -Reason "periodic/stale data expansion trigger" -Metrics $state.last_metrics -DataSignature $dataSignature)
            Add-StateHistory -State $state -Event "data-expansion" -Data $dataResult
            Save-State -State $state -Path $statePath
            Repair-IfSubagentFailed -Context "data expansion failed before cycle $nextCycle" -Result $dataResult -Metrics $state.last_metrics | Out-Null
        }

        $state.cycle = [int]$state.cycle + 1
        Save-State -State $state -Path $statePath

        $cycleStartedAt = Get-Date
        $runnerResult = Invoke-CodexAgent -Role "backtest-runner-cycle-$($state.cycle)" -PromptBody (Get-BacktestRunnerPrompt -CycleNumber $state.cycle)
        Add-StateHistory -State $state -Event "backtest-runner" -Data $runnerResult
        if (Repair-IfSubagentFailed -Context "backtest runner failed in cycle $($state.cycle)" -Result $runnerResult) {
            if ($SleepSeconds -gt 0) {
                Write-AgentMessage "Sleeping $SleepSeconds seconds after repair before next cycle."
                Start-Sleep -Seconds $SleepSeconds
            }
            continue
        }

        $metrics = Get-LatestBacktestMetrics -NotBefore $cycleStartedAt.AddSeconds(-2)
        $state.last_metrics = $metrics
        Save-State -State $state -Path $statePath

        if ($null -ne $metrics) {
            Write-AgentMessage ("Latest metrics: total_return_pct={0}, monthly_{1}_return_pct={2}, trades={3}, file={4}" -f $metrics.total_return_pct, $MonthlyReturnTargetMode, (Get-MonthlyReturnMetricPct -Metrics $metrics), $metrics.total_trades, $metrics.file)
        }
        else {
            Write-AgentMessage "No fresh readable metrics were found after cycle $($state.cycle). Existing reports before $($cycleStartedAt.ToString("yyyy-MM-dd HH:mm:ss")) are ignored."
            Register-SupervisorFailure -Reason "No readable metrics JSON after cycle $($state.cycle)"
            $repairResult = Invoke-RepairAgent -Context "No readable backtest metrics JSON was found after cycle $($state.cycle)." -FailedResult $runnerResult -Metrics $metrics
            Add-StateHistory -State $state -Event "repair-missing-metrics" -Data $repairResult
            Save-State -State $state -Path $statePath
            if ($null -ne $repairResult -and [int]$repairResult.exit_code -eq 0) {
                Clear-SupervisorFailures -Reason "missing metrics repair completed"
            }

            if ($SleepSeconds -gt 0) {
                Write-AgentMessage "Sleeping $SleepSeconds seconds after metrics repair before next cycle."
                Start-Sleep -Seconds $SleepSeconds
            }
            continue
        }

        $cycleSuccess = Update-RegressionState -State $state -Metrics $metrics
        Save-State -State $state -Path $statePath
        Write-AgentMessage ("Regression progress: target_monthly_{0}_return_pct>={1}, cycles={2}/{3}, successful_at_current_target={4}, consecutive_successes={5}/{6}, current_success={7}" -f $MonthlyReturnTargetMode, (Get-CurrentReturnTargetPct), $state.regression_cycles, $MinRegressionCycles, $state.successful_cycles, $state.consecutive_successes, $RequiredConsecutiveSuccesses, $cycleSuccess)

        if (Test-StableTargetReached -State $state) {
            $achievedTarget = Get-CurrentReturnTargetPct
            Write-AgentMessage "Stable monthly return target $achievedTarget% reached after $($state.regression_cycles) fresh regression cycles and $($state.consecutive_successes) consecutive successful cycles."
            Clear-SupervisorFailures -Reason "stable target reached"
            $shouldContinue = Complete-CurrentReturnTarget -State $state -Metrics $metrics
            $state.target.current_total_return_pct_gt = Get-CurrentReturnTargetPct
            Save-State -State $state -Path $statePath
            if ($shouldContinue) {
                Write-AgentMessage "Increasing return target to $(Get-CurrentReturnTargetPct)% and continuing long-run regressions."
            }
            else {
                Write-AgentMessage "Stopping after stable target because stop/cap option was reached."
                break
            }
        }

        Clear-SupervisorFailures -Reason "readable metrics produced"
        if ($cycleSuccess) {
            $auditResult = Invoke-CodexAgent -Role "regression-audit-cycle-$($state.cycle)" -PromptBody (Get-RegressionAuditPrompt -CycleNumber $state.cycle -Metrics $metrics -CycleSuccess $cycleSuccess)
            Add-StateHistory -State $state -Event "regression-audit" -Data $auditResult
            Save-State -State $state -Path $statePath
            Repair-IfSubagentFailed -Context "regression audit failed in cycle $($state.cycle)" -Result $auditResult -Metrics $metrics | Out-Null
        }
        else {
            $optimizerResult = Invoke-CodexAgent -Role "strategy-optimizer-cycle-$($state.cycle)" -PromptBody (Get-OptimizerPrompt -CycleNumber $state.cycle -Metrics $metrics)
            Add-StateHistory -State $state -Event "strategy-optimizer" -Data $optimizerResult
            Save-State -State $state -Path $statePath
            Repair-IfSubagentFailed -Context "strategy optimizer failed in cycle $($state.cycle)" -Result $optimizerResult -Metrics $metrics | Out-Null
        }

        if ($SleepSeconds -gt 0) {
            Write-AgentMessage "Sleeping $SleepSeconds seconds before next cycle."
            Start-Sleep -Seconds $SleepSeconds
        }
    }
    }

    $finalMetrics = Get-LatestBacktestMetrics
    $state.last_metrics = $finalMetrics
    Save-State -State $state -Path $statePath

    if (-not $stopRequested) {
        $finalResult = Invoke-CodexAgent -Role "final-review" -PromptBody (Get-FinalReviewPrompt -Metrics $finalMetrics)
        Add-StateHistory -State $state -Event "final-review" -Data $finalResult
    }

    if ($stopRequested) {
        $state.status = "stopped_by_stop_file"
    }
    elseif (-not $state.success -and $state.status -eq "running") {
        $state.status = "stopped_without_target"
    }
    Save-State -State $state -Path $statePath
}
catch {
    $state.status = "failed"
    Add-StateHistory -State $state -Event "error" -Data ([pscustomobject]@{ message = $_.Exception.Message })
    Save-State -State $state -Path $statePath
    Write-AgentMessage "FAILED: $($_.Exception.Message)"
    throw
}

Write-AgentMessage "Supervisor finished with status: $($state.status)"
