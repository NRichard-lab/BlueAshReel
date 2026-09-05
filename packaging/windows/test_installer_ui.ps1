<#
Opt-in GUI acceptance for the exact unsigned development installer. Launch this
helper with RunAs + WindowStyle Hidden only after the disposable instance was
explicitly purged. The installer is also launched Hidden; no windows are forced
visible. A missing/offscreen native provider is a gap. Browser content/control
is outside this helper; use the browser skill.

No coordinate clicks, SendKeys, global focus changes, or unrelated UI searches.
An ambiguous/missing UI Automation pattern is a reported gap, never a guess.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$InstallerPath,
    [Parameter(Mandatory)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$InstallerSha256,
    [Parameter(Mandatory)][string]$MediaRoot,
    [Parameter(Mandatory)][string]$ReportDirectory,
    [switch]$AllowDisposableInstallerUI,
    [switch]$ConfirmOwnedTestMedia,
    [ValidateRange(60,1200)][int]$TimeoutSeconds = 600
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$TaskPrefix = 'BlueReelDevelopment'
$TaskProgram = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'BlueAshReel Development'
$TaskData = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'BlueAshReel-Development'
$TaskExpectedMedia = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'BlueAshReel-Development-TestMedia'
$TaskRepository = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$TaskArtifactRoot = [IO.Path]::GetFullPath((Join-Path $TaskRepository 'artifacts\development')).TrimEnd('\')
$TaskStage = 'input_validation'
$TaskReportPath = $null
$TaskStarted = [DateTime]::UtcNow
$TaskKnownProcesses = @{}
$TaskWizardProcessId = $null
$TaskInvocations = [Collections.Generic.List[object]]::new()
$TaskEvidence = [ordered]@{
    schema_version=1; started_utc=$TaskStarted.ToString('o'); native_gui_passed=$false
    typed_fallback_verified=$false; native_picker_verified=$false; finish_invoked=$false
    localhost_defaults_verified=$false
    browser_launch_requested=$false; browser_load_verified=$false; browser_verification='requires_browser_skill'
    source_media_unchanged=$false; transitions=@()
}

function Assert-SafePath([string]$Path) {
    if (-not [IO.Path]::IsPathRooted($Path) -or $Path -match '(^|[\\/])\.\.([\\/]|$)') { throw 'Unsafe path' }
    $taskCurrent = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    while ($taskCurrent) {
        if (Test-Path -LiteralPath $taskCurrent) {
            if ((Get-Item -LiteralPath $taskCurrent -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse path' }
        }
        $taskParent = [IO.Directory]::GetParent($taskCurrent)
        $taskCurrent = if ($null -eq $taskParent) { $null } else { $taskParent.FullName }
    }
}

function Get-SafeFiles([string]$Root) {
    Assert-SafePath $Root
    if (-not (Test-Path -LiteralPath $Root)) { return @() }
    $taskPending = [Collections.Generic.Queue[string]]::new()
    $taskPending.Enqueue($Root)
    $taskFiles = [Collections.Generic.List[object]]::new()
    while ($taskPending.Count) {
        $taskItem = Get-Item -LiteralPath $taskPending.Dequeue() -Force
        if ($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse entry' }
        if ($taskItem.PSIsContainer) {
            foreach ($taskChild in @(Get-ChildItem -LiteralPath $taskItem.FullName -Force)) { $taskPending.Enqueue($taskChild.FullName) }
        } else { $taskFiles.Add($taskItem) }
    }
    return $taskFiles.ToArray()
}

function Assert-GuiInputs {
    if (-not $AllowDisposableInstallerUI -or -not $ConfirmOwnedTestMedia) { throw 'Explicit disposable GUI and owned-media opt-ins required' }
    if (-not [Environment]::Is64BitProcess) { throw 'Use x64 Windows PowerShell' }
    $taskPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Explicit RunAs elevation required' }
    foreach ($taskPath in @($InstallerPath,$MediaRoot,$ReportDirectory,$TaskProgram,$TaskData)) { Assert-SafePath $taskPath }
    $taskInstaller = [IO.Path]::GetFullPath($InstallerPath)
    if (-not $taskInstaller.StartsWith($TaskArtifactRoot + '\',[StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($taskInstaller) -cne 'BlueAshReel-Setup-Development-x64.exe') { throw 'Wrong installer target' }
    if ([IO.Path]::GetFullPath($MediaRoot).TrimEnd('\') -ine $TaskExpectedMedia -or -not (Test-Path -LiteralPath $MediaRoot -PathType Container)) { throw 'Only the owned TestMedia root is allowed' }
    if (-not [IO.Path]::GetFullPath($ReportDirectory).TrimEnd('\').StartsWith($TaskArtifactRoot + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Reports must be in an ignored native artifact subdirectory' }
    if ((Get-FileHash -LiteralPath $taskInstaller -Algorithm SHA256).Hash -ine $InstallerSha256) { throw 'Installer checksum mismatch' }
    if ((Get-AuthenticodeSignature -LiteralPath $taskInstaller).Status -ne 'NotSigned') { throw 'This test expects the verified unsigned development binary' }
    if (Test-Path -LiteralPath $TaskData) { throw 'Existing disposable data must be explicitly purged before this clean GUI test' }
    if (@(Get-SafeFiles $TaskProgram).Count) { throw 'An old program payload remains' }
    if (Test-Path -LiteralPath 'HKLM:\Software\BlueReel\development') { throw 'An old product registration remains' }
    foreach ($taskRole in @('API','Worker','Web','Proxy','Remote')) {
        if (Test-Path -LiteralPath ('HKLM:\SYSTEM\CurrentControlSet\Services\' + $TaskPrefix + $taskRole)) { throw 'An old service remains' }
    }
    $null = @(Get-SafeFiles $MediaRoot)
}

function Get-MediaFingerprint {
    $taskRows = @()
    foreach ($taskFile in @(Get-SafeFiles $MediaRoot)) {
        $taskRows += $taskFile.FullName.Substring($MediaRoot.Length) + ':' + $taskFile.LastWriteTimeUtc.Ticks + ':' + (Get-FileHash -LiteralPath $taskFile.FullName -Algorithm SHA256).Hash
    }
    $taskHash = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($taskHash.ComputeHash([Text.Encoding]::UTF8.GetBytes((($taskRows | Sort-Object) -join "`n")))).Replace('-','').ToLowerInvariant() }
    finally { $taskHash.Dispose() }
}

function Write-ProgressEvidence([string]$Stage) {
    $script:TaskStage = $Stage
    $TaskEvidence.transitions += [ordered]@{stage=$Stage; utc=[DateTime]::UtcNow.ToString('o')}
    if ($TaskReportPath) { [IO.File]::WriteAllText($TaskReportPath,($TaskEvidence | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false)) }
}

function Update-InstallerLineage {
    $taskProcesses = @(Get-CimInstance Win32_Process)
    foreach ($taskProcess in $taskProcesses) {
        $taskKey = [int]$taskProcess.ProcessId
        if ($TaskKnownProcesses.ContainsKey($taskKey) -and $taskProcess.CreationDate.ToUniversalTime().Ticks -ne $TaskKnownProcesses[$taskKey]) {
            $TaskKnownProcesses.Remove($taskKey) # Never adopt a reused process ID.
        }
    }
    for ($taskPass = 0; $taskPass -lt 8; $taskPass++) {
        foreach ($taskProcess in $taskProcesses) {
            $taskKey = [int]$taskProcess.ProcessId
            if ($TaskKnownProcesses.ContainsKey([int]$taskProcess.ParentProcessId) -and $taskProcess.CreationDate.ToUniversalTime() -ge $TaskStarted) {
                $TaskKnownProcesses[$taskKey] = $taskProcess.CreationDate.ToUniversalTime().Ticks
            }
        }
    }
}

function Get-OwnedNativeWindows {
    Update-InstallerLineage
    $taskWindows = [Collections.Generic.List[object]]::new()
    foreach ($taskKey in @($TaskKnownProcesses.Keys)) {
        $taskCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,[int]$taskKey)
        $taskMatches = [System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,$taskCondition)
        foreach ($taskWindow in $taskMatches) {
            # Browser windows, even when launched by Finish, are never inspected.
            if ($taskWindow.Current.ClassName -in @('TWizardForm','#32770') -and -not $taskWindow.Current.IsOffscreen) { $taskWindows.Add($taskWindow) }
        }
    }
    return $taskWindows.ToArray()
}

function Assert-OwnedElement($Element) {
    if ($null -eq $Element -or -not $TaskKnownProcesses.ContainsKey([int]$Element.Current.ProcessId)) { throw 'Foreign UI element' }
    if ($null -ne $TaskWizardProcessId -and [int]$Element.Current.ProcessId -ne $TaskWizardProcessId) { throw 'Foreign wizard process' }
    if (-not $Element.Current.IsEnabled -or $Element.Current.IsOffscreen) { throw 'Unavailable UI element' }
}

function Select-UniqueElement([object[]]$Elements) {
    if ($Elements.Count -ne 1) { throw 'Ambiguous or missing UI control' }
    Assert-OwnedElement $Elements[0]
    return $Elements[0]
}

function Find-Controls($Window, [string]$Type, [string]$NamePattern) {
    Assert-OwnedElement $Window
    $taskCondition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::$Type)
    $taskMatches = $Window.FindAll([System.Windows.Automation.TreeScope]::Descendants,$taskCondition)
    return @($taskMatches | Where-Object {
        $_.Current.IsEnabled -and -not $_.Current.IsOffscreen -and ($_.Current.Name.Replace('&','').Trim() -match $NamePattern)
    })
}

function Invoke-Owned($Element) {
    Assert-OwnedElement $Element
    $TaskInvocations.Add([BlueReelGuiAutomation]::InvokeAsync($Element,[int]$Element.Current.ProcessId))
}

function Set-OwnedValue($Element,[string]$Value) {
    Assert-OwnedElement $Element
    $taskPattern = $Element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    if ($taskPattern.Current.IsReadOnly) { throw 'Read-only field' }
    $taskPattern.SetValue($Value)
    if ($taskPattern.Current.Value -cne $Value) { throw 'Field value was not accepted' }
}

function Get-Wizard {
    $taskWindows = @(Get-OwnedNativeWindows | Where-Object {
        $_.Current.ClassName -eq 'TWizardForm' -and $_.Current.Name -match 'Blue Ash Reel Development'
    })
    if ($taskWindows.Count -eq 0) { return $null }
    $taskWizard = Select-UniqueElement $taskWindows
    if ($null -eq $TaskWizardProcessId) { $script:TaskWizardProcessId = [int]$taskWizard.Current.ProcessId }
    return $taskWizard
}

function Wait-FolderDialog {
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        $taskCandidates = @(Get-OwnedNativeWindows | Where-Object {
            $_.Current.ProcessId -eq $TaskWizardProcessId -and $_.Current.ClassName -eq '#32770' -and
            $_.Current.Name -ceq 'Choose one approved media root (source files are never changed)'
        })
        if ($taskCandidates.Count) { return (Select-UniqueElement $taskCandidates) }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $taskDeadline)
    throw 'Native folder dialog unavailable'
}

function Test-MediaPage($Wizard) {
    Write-ProgressEvidence 'typed_media_fallback'
    $taskMemo = Select-UniqueElement @(Find-Controls $Wizard 'Edit' '.*')
    Set-OwnedValue $taskMemo $MediaRoot
    Set-OwnedValue $taskMemo ''
    $TaskEvidence.typed_fallback_verified = $true
    $taskBrowse = Select-UniqueElement @(Find-Controls $Wizard 'Button' '^Add folder\.\.\.$')
    Invoke-Owned $taskBrowse
    Write-ProgressEvidence 'native_folder_dialog'
    $taskDialog = Wait-FolderDialog
    $taskEdits = @(Find-Controls $taskDialog 'Edit' '.*' | Where-Object {
        $_.Current.Name -match '^(Folder( name)?|File name):?$' -or $_.Current.AutomationId -in @('1152','1148','1001')
    })
    $taskPath = Select-UniqueElement $taskEdits
    Set-OwnedValue $taskPath $MediaRoot
    $taskSelect = Select-UniqueElement @(Find-Controls $taskDialog 'Button' '^(Select [Ff]older|OK)$')
    $taskDialogHandle = [int]$taskDialog.Current.NativeWindowHandle
    Invoke-Owned $taskSelect
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 200
        $taskVisibleDialog = @(Get-OwnedNativeWindows | Where-Object { $_.Current.NativeWindowHandle -eq $taskDialogHandle })
        if ($taskVisibleDialog.Count -eq 0) { break }
    } while ([DateTime]::UtcNow -lt $taskDeadline)
    if ($taskVisibleDialog.Count) { throw 'Folder selection did not close the native dialog' }
    $taskWizard = Get-Wizard
    $taskMemo = Select-UniqueElement @(Find-Controls $taskWizard 'Edit' '.*')
    $taskSelected = $taskMemo.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value.Trim()
    if ($taskSelected -cne $MediaRoot) { throw 'Native picker did not return the owned media root' }
    $TaskEvidence.native_picker_verified = $true
    $TaskEvidence.native_picker_method = 'native_dialog_path_entry_and_select_folder'
    Write-ProgressEvidence 'native_folder_round_trip_verified'
    Invoke-Owned (Select-UniqueElement @(Find-Controls $taskWizard 'Button' '^Next\s*>?$'))
}

function Assert-LocalDefaults($Wizard) {
    $taskFields = @(Find-Controls $Wizard 'Edit' '.*')
    if ($taskFields.Count -ne 2) { throw 'Unexpected local access fields' }
    $taskValues = @($taskFields | ForEach-Object {
        Assert-OwnedElement $_
        $_.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value
    })
    if (@($taskValues | Where-Object { $_ -ceq '18080' }).Count -ne 1 -or
        @($taskValues | Where-Object { $_ -ceq '127.0.0.1' }).Count -ne 1) { throw 'Non-default local access settings' }
    $TaskEvidence.localhost_defaults_verified = $true
}

function Enable-BrowserLaunch($Wizard) {
    $taskCandidates = @()
    foreach ($taskType in @('CheckBox','ListItem')) {
        $taskCandidates += @(Find-Controls $Wizard $taskType '^Open Blue Ash Reel Development and set up the Owner account$')
    }
    $taskOption = Select-UniqueElement $taskCandidates
    $taskPattern = $null
    if ($taskOption.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$taskPattern)) {
        if ($taskPattern.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::On) { $taskPattern.Toggle() }
        if ($taskPattern.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::On) { throw 'Browser launch option could not be verified' }
    } else {
        $taskPattern = $taskOption.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
        if (-not ($taskPattern.Current.State -band 16)) { $taskPattern.DoDefaultAction() }
        if (-not ($taskPattern.Current.State -band 16)) { throw 'Browser launch check state is unavailable' }
    }
    $TaskEvidence.browser_launch_option_checked = $true
}

function Get-NativeFailureDiagnostics {
    if ($null -eq $TaskWizardProcessId) { return @() }
    $taskRows = [Collections.Generic.List[object]]::new()
    foreach ($taskWindow in @(Get-OwnedNativeWindows | Where-Object { $_.Current.ProcessId -eq $TaskWizardProcessId })) {
        foreach ($taskControl in $taskWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)) {
            if ($taskRows.Count -ge 120) { break }
            if ($taskControl.Current.ProcessId -ne $TaskWizardProcessId) { continue }
            $taskName = $taskControl.Current.Name.Replace('&','').Trim()
            # Never emit path text, folder/file names, field values or arbitrary
            # labels. Only known installer captions are safe for the report.
            if ($taskName -notmatch '^(Welcome to the Blue Ash Reel Development Setup Wizard|Completing the Blue Ash Reel Development Setup Wizard|Information|Local access|Approved media folders|Ready to Install|Installing|Next\s*>?|<\s*Back|Cancel|Finish|Install|Add folder\.\.\.|Select [Ff]older|OK|Folder( name)?:?|File name:?|Open Blue Ash Reel Development and set up the Owner account)$') { $taskName = '[redacted]' }
            $taskId = $taskControl.Current.AutomationId
            if ($taskId -notmatch '^[0-9]{0,16}$') { $taskId = '[redacted]' }
            $taskRows.Add([ordered]@{
                control_type=$taskControl.Current.ControlType.ProgrammaticName
                automation_id=$taskId; name=$taskName
                enabled=$taskControl.Current.IsEnabled; offscreen=$taskControl.Current.IsOffscreen
            })
        }
    }
    return $taskRows.ToArray()
}

try {
    Assert-GuiInputs
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Automation;
public static class BlueReelGuiAutomation {
    static CancellationTokenSource deadline = new CancellationTokenSource();
    public static Task InvokeAsync(AutomationElement element, int expectedProcess) {
        return Task.Factory.StartNew(() => {
            if (element.Current.ProcessId != expectedProcess) throw new InvalidOperationException("UI scope changed");
            ((InvokePattern)element.GetCurrentPattern(InvokePattern.Pattern)).Invoke();
        });
    }
    public static void ArmDeadline(int seconds, string report) {
        Task.Delay(TimeSpan.FromSeconds(seconds), deadline.Token).ContinueWith(task => {
            if (task.IsCanceled) return;
            File.WriteAllText(report,"{\"schema_version\":1,\"native_gui_passed\":false,\"timed_out\":true,\"browser_load_verified\":false,\"installer_left_untouched\":true}");
            Environment.Exit(124);
        });
    }
    public static void CancelDeadline() { deadline.Cancel(); }
}
'@ -ReferencedAssemblies @('UIAutomationClient','UIAutomationTypes','WindowsBase','System.Core','System')
    if (-not (Test-Path -LiteralPath $ReportDirectory)) { New-Item -ItemType Directory -Path $ReportDirectory | Out-Null }
    $TaskReportPath = Join-Path $ReportDirectory ('installer-ui-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' + [guid]::NewGuid().ToString('N') + '.json')
    $TaskEvidence.installer_sha256 = $InstallerSha256.ToLowerInvariant()
    $TaskEvidence.source_fingerprint_before = Get-MediaFingerprint
    [BlueReelGuiAutomation]::ArmDeadline($TimeoutSeconds,$TaskReportPath)
    Write-ProgressEvidence 'launching_verified_installer'
    # Never force/show/focus any window. If Hidden suppresses the native provider,
    # report the GUI gap instead of changing the user's desktop state.
    $taskInstallerProcess = Start-Process -FilePath $InstallerPath -ArgumentList '/NORESTART' -WindowStyle Hidden -PassThru
    $taskRootProcess = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $taskInstallerProcess.Id)
    if ($null -eq $taskRootProcess -or $taskRootProcess.ExecutablePath -ine $InstallerPath) { throw 'Launched installer identity could not be verified' }
    $TaskKnownProcesses[[int]$taskRootProcess.ProcessId] = $taskRootProcess.CreationDate.ToUniversalTime().Ticks
    $TaskEvidence.installer_pid = [int]$taskRootProcess.ProcessId
    $taskInstallInvoked = $false
    $taskNextClicks = 0
    $taskLastSetupPage = $null
    $taskPageChangeDeadline = [DateTime]::UtcNow.AddSeconds(20)
    $taskDeadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds - 5)
    while ([DateTime]::UtcNow -lt $taskDeadline) {
        foreach ($taskInvocation in $TaskInvocations) { if ($taskInvocation.IsFaulted) { throw 'A scoped UI invocation failed' } }
        $taskWizard = Get-Wizard
        if ($null -eq $taskWizard) { Start-Sleep -Milliseconds 250; continue }
        $taskFinish = @(Find-Controls $taskWizard 'Button' '^Finish$')
        if ($taskFinish.Count) {
            if (-not $taskInstallInvoked -or -not $TaskEvidence.native_picker_verified) { throw 'Unexpected completion page' }
            Write-ProgressEvidence 'completion_page'
            Enable-BrowserLaunch $taskWizard
            Invoke-Owned (Select-UniqueElement $taskFinish)
            $TaskEvidence.finish_invoked = $true
            $TaskEvidence.browser_launch_requested = $true
            break
        }
        if ($taskInstallInvoked) { Start-Sleep -Milliseconds 500; continue }
        $taskBrowse = @(Find-Controls $taskWizard 'Button' '^Add folder\.\.\.$')
        if ($taskBrowse.Count) {
            if ($TaskEvidence.native_picker_verified) {
                if ([DateTime]::UtcNow -ge $taskPageChangeDeadline) { throw 'Media page did not advance' }
                Start-Sleep -Milliseconds 200
                continue
            }
            Test-MediaPage $taskWizard
            $taskPageChangeDeadline = [DateTime]::UtcNow.AddSeconds(20)
            Start-Sleep -Milliseconds 300
            continue
        }
        $taskInstall = @(Find-Controls $taskWizard 'Button' '^Install$')
        if ($taskInstall.Count) {
            if (-not $TaskEvidence.native_picker_verified -or -not $TaskEvidence.typed_fallback_verified -or
                -not $TaskEvidence.localhost_defaults_verified) { throw 'Media or localhost GUI checks were not completed' }
            Write-ProgressEvidence 'installing'
            Invoke-Owned (Select-UniqueElement $taskInstall)
            $taskInstallInvoked = $true
            continue
        }
        $taskTexts = @(Find-Controls $taskWizard 'Text' '.*' | ForEach-Object { $_.Current.Name })
        $taskPageNames = @($taskTexts | Where-Object { $_ -match '^(Welcome to the Blue Ash Reel Development Setup Wizard|Information|Local access)$' })
        if ($taskPageNames.Count -ne 1) { throw 'Unrecognized or ambiguous installer page' }
        if ($taskPageNames[0] -ceq $taskLastSetupPage) {
            if ([DateTime]::UtcNow -ge $taskPageChangeDeadline) { throw 'Installer page did not advance' }
            Start-Sleep -Milliseconds 200
            continue
        }
        $taskLastSetupPage = $taskPageNames[0]
        $taskPageChangeDeadline = [DateTime]::UtcNow.AddSeconds(20)
        if ($taskLastSetupPage -ceq 'Local access') { Assert-LocalDefaults $taskWizard }
        $taskNextClicks++
        if ($taskNextClicks -gt 4) { throw 'Unexpected repeated installer page' }
        Write-ProgressEvidence 'recognized_setup_page'
        Invoke-Owned (Select-UniqueElement @(Find-Controls $taskWizard 'Button' '^Next\s*>?$'))
        Start-Sleep -Milliseconds 500
    }
    if (-not $TaskEvidence.finish_invoked) { throw 'Installer GUI deadline reached' }
    if (-not $taskInstallerProcess.WaitForExit(20000)) { throw 'Installer did not exit after Finish' }
    $TaskEvidence.installer_exit_code = $taskInstallerProcess.ExitCode
    if ($taskInstallerProcess.ExitCode -ne 0) { throw 'Installer reported failure' }
    $TaskEvidence.source_fingerprint_after = Get-MediaFingerprint
    $TaskEvidence.source_media_unchanged = $TaskEvidence.source_fingerprint_before -ceq $TaskEvidence.source_fingerprint_after
    if (-not $TaskEvidence.source_media_unchanged) { throw 'Owned source fixture changed during GUI test' }
    $TaskEvidence.native_gui_passed = $true
    Write-ProgressEvidence 'native_gui_complete_browser_verification_pending'
} catch {
    $TaskEvidence.failed_stage = $TaskStage
    $TaskEvidence.error_type = $_.Exception.GetType().Name
    $TaskEvidence.gui_gap = $true
    $TaskEvidence.installer_left_untouched_on_failure = $true
    try { $TaskEvidence.native_control_diagnostics = @(Get-NativeFailureDiagnostics) } catch { $TaskEvidence.native_diagnostics_unavailable = $true }
    # Do not dismiss unknown dialogs, terminate the installer, or roll back.
} finally {
    if ('BlueReelGuiAutomation' -as [type]) { [BlueReelGuiAutomation]::CancelDeadline() }
    $TaskEvidence.finished_utc = [DateTime]::UtcNow.ToString('o')
    if ($TaskReportPath) { [IO.File]::WriteAllText($TaskReportPath,($TaskEvidence | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false)) }
    [ordered]@{native_gui_passed=$TaskEvidence.native_gui_passed; report_written=($null -ne $TaskReportPath); browser_load_verified=$false} | ConvertTo-Json -Compress
}
if (-not $TaskEvidence.native_gui_passed) { exit 1 }
