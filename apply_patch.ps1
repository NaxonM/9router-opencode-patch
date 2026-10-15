<#
.SYNOPSIS
    9Router OpenCode Dual-Gate Patch Launcher for PowerShell

.DESCRIPTION
    Launches patch_9router.py with optional flags:
    -Status     Check patch status
    -Rollback   Restore .bak backups
    -Verify     Send a test probe to an OpenCode model
    -Docker     Target a running Docker container
    -Path       Target a custom directory
#>
param(
    [switch]$Status,
    [switch]$Rollback,
    [switch]$Verify,
    [string]$Docker,
    [string]$Path,
    [string]$Url,
    [string]$Key
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyScript = Join-Path $scriptDir "patch_9router.py"

$pyArgs = @()
if ($Status) { $pyArgs += "--status" }
if ($Rollback) { $pyArgs += "--rollback" }
if ($Verify) { $pyArgs += "--verify" }
if ($Docker) { $pyArgs += "--docker", $Docker }
if ($Path) { $pyArgs += "--path", $Path }
if ($Url) { $pyArgs += "--url", $Url }
if ($Key) { $pyArgs += "--key", $Key }

python $pyScript $pyArgs
