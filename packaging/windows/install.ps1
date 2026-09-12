[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Install','Stop','Remove','Backup','ValidateBackup','Health','PrepareUpgrade','ConfigureMedia','ConfigureNetwork')][string]$Action,
    [Parameter(Mandatory)][string]$ProgramDir,
    [Parameter(Mandatory)][string]$DataDir,
    [ValidateSet('development','stable')][string]$Instance = 'development',
    [int]$Port = 0,
    [string]$BindAddress = '127.0.0.1',
    [string]$RootsFile,
    [string]$Archive,
    [switch]$Interactive,
    [switch]$RemoveData,
    [string]$ConfirmDataRemoval
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$TaskPrefix = if ($Instance -eq 'development') { 'BlueReelDevelopment' } else { 'BlueReel' }
$TaskDataName = if ($Instance -eq 'development') { 'BlueAshReel-Development' } else { 'BlueReel' }
$TaskRoles = @('API','Worker','Web','Proxy')
$TaskStopOrder = @('Proxy','Worker','API','Web')
$TaskDisplayName = 'Media server'
$taskProductFile = Join-Path $ProgramDir 'config\product.json'
if (Test-Path -LiteralPath $taskProductFile) {
    $TaskDisplayName = (Get-Content -LiteralPath $taskProductFile -Raw | ConvertFrom-Json).name
}

function Assert-Administrator {
    $taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not ([Security.Principal.WindowsPrincipal]::new($taskIdentity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Windows administrator elevation is required for native service and firewall maintenance.'
    }
}

function Assert-DedicatedPath([string]$Path) {
    $taskFull = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not [IO.Path]::IsPathRooted($Path) -or $taskFull.Length -lt 5 -or $taskFull -eq [IO.Path]::GetPathRoot($taskFull).TrimEnd('\')) {
        throw 'A dedicated absolute installation directory is required.'
    }
    $taskProbe = $taskFull
    while ($taskProbe) {
        if (Test-Path -LiteralPath $taskProbe) {
            if ((Get-Item -LiteralPath $taskProbe -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Installation directories must not contain junctions or reparse points.'
            }
        }
        $taskParent = [IO.Directory]::GetParent($taskProbe)
        $taskProbe = if ($null -eq $taskParent) { $null } else { $taskParent.FullName }
    }
    return $taskFull
}

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Native maintenance operation failed (exit $LASTEXITCODE)." }
}

function Invoke-Native([string]$Command, [string[]]$Extra = @()) {
    Invoke-Checked $TaskPython (@('-I','-B','-m','app.native_install',$Command,'--data-dir',$DataDir) + $Extra)
}

function Assert-ServiceIdentity([string]$Suffix) {
    $taskName = $TaskPrefix + $Suffix
    $taskService = Get-CimInstance Win32_Service -Filter "Name='$taskName'" -ErrorAction Stop
    if ($null -ne $taskService) {
        $taskExpected = Join-Path $ProgramDir "services\$taskName.exe"
        if ($taskService.PathName.Trim('"') -ine $taskExpected) { throw 'An existing service has a conflicting installation identity; nothing was removed.' }
        if ($taskService.StartName -notin @('NT AUTHORITY\LocalService','NT AUTHORITY\LOCAL SERVICE')) { throw 'The existing service identity differs from the expected least-privileged account.' }
    }
    return $taskService
}

function Get-OptionalRemoteService {
    $taskRemoteName = $TaskPrefix + 'Remote'
    $taskRemote = Get-CimInstance Win32_Service -Filter "Name='$taskRemoteName'" -ErrorAction Stop
    if ($taskRemote) {
        $taskExpected = Join-Path $ProgramDir "services\$taskRemoteName.exe"
        if ($taskRemote.PathName.Trim('"') -ine $taskExpected -or
            $taskRemote.StartName -ine "NT SERVICE\$taskRemoteName") {
            throw 'The optional remote connector service has a conflicting installation identity.'
        }
    }
    return $taskRemote
}

function Invoke-RemoteMaintenance([string]$RemoteAction) {
    # During pre-upgrade Inno extracts this script and its matching connector
    # helper together from the NEW verified payload, before replacing old files.
    $taskRemoteHelper = Join-Path $PSScriptRoot 'install-remote.ps1'
    if (-not (Test-Path -LiteralPath $taskRemoteHelper)) { throw 'The packaged connector provisioning helper is missing.' }
    & $taskRemoteHelper -Action $RemoteAction -ProgramDir $ProgramDir -DataDir $DataDir
}

function Stop-Instance {
    $taskRemote = Get-OptionalRemoteService
    if ($taskRemote -and $taskRemote.State -ne 'Stopped') {
        Stop-Service -Name $taskRemote.Name -ErrorAction Stop
        (Get-Service -Name $taskRemote.Name).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(30))
    }
    foreach ($taskRole in $TaskStopOrder) {
        $taskService = Assert-ServiceIdentity $taskRole
        if ($null -ne $taskService -and $taskService.State -ne 'Stopped') {
            Stop-Service -Name ($TaskPrefix + $taskRole) -NoWait -ErrorAction Stop
            (Get-Service -Name ($TaskPrefix + $taskRole)).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(120))
        }
    }
}

function Initialize-PrivateAclRuntime {
    if ('BlueReel.NativePrivateAcl' -as [type]) { return }
    # One in-process helper replaces two icacls processes per descendant. Open
    # handles (not a second path lookup) are used for metadata and ACL updates.
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using Microsoft.Win32.SafeHandles;

namespace BlueReel {
    public static class NativePrivateAcl {
        const uint ReadControl = 0x00020000, WriteDac = 0x00040000, WriteOwner = 0x00080000;
        const uint ReadAttributes = 0x80, OpenReparsePoint = 0x00200000, BackupSemantics = 0x02000000;
        const uint OwnerInfo = 1, DaclInfo = 4, UnprotectedDaclInfo = 0x20000000, ProtectedDaclInfo = 0x80000000;
        [StructLayout(LayoutKind.Sequential)]
        struct FileInfo {
            public uint Attributes;
            public System.Runtime.InteropServices.ComTypes.FILETIME Created, Accessed, Written;
            public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
        }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security, uint mode, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool GetFileInformationByHandle(SafeFileHandle handle, out FileInfo info);
        [DllImport("advapi32.dll", SetLastError=true)]
        static extern bool InitializeAcl(IntPtr acl, uint size, uint revision);
        [DllImport("advapi32.dll")]
        static extern uint SetSecurityInfo(SafeFileHandle handle, uint type, uint information, IntPtr owner, IntPtr group, IntPtr dacl, IntPtr sacl);
        [DllImport("advapi32.dll")]
        static extern uint GetSecurityInfo(SafeFileHandle handle, uint type, uint information, out IntPtr owner, out IntPtr group, out IntPtr dacl, out IntPtr sacl, out IntPtr descriptor);
        [DllImport("advapi32.dll")]
        static extern uint GetSecurityDescriptorLength(IntPtr descriptor);
        [DllImport("kernel32.dll")]
        static extern IntPtr LocalFree(IntPtr memory);

        static SafeFileHandle Open(string path, bool directory, bool writable) {
            // No FILE_SHARE_DELETE: neither this entry nor held ancestors may
            // be replaced/renamed while validation and mutation are in flight.
            // No FILE_SHARE_WRITE also rejects an existing mutable file handle.
            // FILE_READ_DATA / FILE_LIST_DIRECTORY activates Windows sharing
            // checks; metadata-only handles do not reliably hold this lock.
            uint rights = 1 | ReadControl | ReadAttributes | (writable ? WriteDac | WriteOwner : 0);
            SafeFileHandle handle = CreateFile(path, rights, 1, IntPtr.Zero, 3, OpenReparsePoint | BackupSemantics, IntPtr.Zero);
            if (handle.IsInvalid) { int error = Marshal.GetLastWin32Error(); handle.Dispose(); throw new Win32Exception(error, "Application data cannot be safely locked for permission hardening."); }
            try { Validate(handle, directory); return handle; }
            catch { handle.Dispose(); throw; }
        }

        static void Validate(SafeFileHandle handle, bool directory) {
            FileInfo info;
            if (!GetFileInformationByHandle(handle, out info)) throw new Win32Exception(Marshal.GetLastWin32Error());
            if ((info.Attributes & 0x400) != 0 || ((info.Attributes & 0x10) != 0) != directory || (!directory && info.Links != 1))
                throw new IOException("Application data changed or contains a reparse point/hard link; permission hardening stopped.");
        }

        static void Verify(SafeFileHandle handle, string ownerSid, IDictionary<string, int> allowed, bool inheritedOnly) {
            IntPtr owner, group, dacl, sacl, descriptor;
            uint error = GetSecurityInfo(handle, 1, OwnerInfo | DaclInfo, out owner, out group, out dacl, out sacl, out descriptor);
            if (error != 0) throw new Win32Exception((int)error);
            try {
                if (dacl == IntPtr.Zero) throw new IOException("Application data has an unsafe null DACL.");
                byte[] bytes = new byte[GetSecurityDescriptorLength(descriptor)];
                Marshal.Copy(descriptor, bytes, 0, bytes.Length);
                RawSecurityDescriptor security = new RawSecurityDescriptor(bytes, 0);
                if (security.Owner.Value != ownerSid || security.DiscretionaryAcl == null || security.DiscretionaryAcl.Count != allowed.Count)
                    throw new IOException("Application-data owner or permission allowlist is not the expected private policy.");
                if (inheritedOnly && (security.ControlFlags & ControlFlags.DiscretionaryAclProtected) != 0)
                    throw new IOException("Application-data child permissions did not inherit the protected parent.");
                if (!inheritedOnly && (security.ControlFlags & ControlFlags.DiscretionaryAclProtected) == 0)
                    throw new IOException("Application-data root permissions are not protected from their parent.");
                HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
                foreach (GenericAce raw in security.DiscretionaryAcl) {
                    CommonAce ace = raw as CommonAce;
                    int mask;
                    if (ace == null || ace.AceQualifier != AceQualifier.AccessAllowed ||
                        !allowed.TryGetValue(ace.SecurityIdentifier.Value, out mask) || ace.AccessMask != mask ||
                        !seen.Add(ace.SecurityIdentifier.Value) || (inheritedOnly && !ace.IsInherited))
                        throw new IOException("Application-data permissions contain an unexpected access rule.");
                }
            } finally { LocalFree(descriptor); }
        }

        public static void ResetEntry(string root, string path, bool directory, string ownerSid, IDictionary<string, int> allowed) {
            ApplyEntry(root, path, directory, ownerSid, allowed, allowed, false);
        }

        public static void ProtectEntry(string root, string path, string ownerSid, IDictionary<string, int> parentAllowed, IDictionary<string, int> allowed) {
            // Configuration is a read-only service boundary, not mutable state.
            ApplyEntry(root, path, true, ownerSid, parentAllowed, allowed, true);
        }

        static void ApplyEntry(string root, string path, bool directory, string ownerSid, IDictionary<string, int> parentAllowed, IDictionary<string, int> allowed, bool protect) {
            if (!Path.IsPathRooted(root) || !Path.IsPathRooted(path) ||
                Path.GetPathRoot(root).Length != 3 || Path.GetPathRoot(path).Length != 3)
                throw new IOException("Application-data ACL targets must be ordinary absolute drive paths.");
            root = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar);
            path = Path.GetFullPath(path);
            if (root.StartsWith(@"\\") || path.StartsWith(@"\\") || !Path.IsPathRooted(path) ||
                root.Length < 4 || !path.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) ||
                path.IndexOf(':', 2) >= 0)
                throw new IOException("Application-data ACL target escaped its dedicated local directory.");
            List<SafeFileHandle> ancestors = new List<SafeFileHandle>();
            Stack<string> chain = new Stack<string>();
            string parent = Path.GetDirectoryName(path);
            for (string current = parent; current != null; current = Path.GetDirectoryName(current)) chain.Push(current);
            try {
                while (chain.Count != 0) ancestors.Add(Open(chain.Pop(), true, false));
                // Breadth-first caller has already protected this exact parent.
                Verify(ancestors[ancestors.Count - 1], ownerSid, parentAllowed, !String.Equals(parent, root, StringComparison.OrdinalIgnoreCase));
                using (SafeFileHandle handle = Open(path, directory, true)) {
                    SecurityIdentifier owner = new SecurityIdentifier(ownerSid);
                    byte[] ownerBytes = new byte[owner.BinaryLength];
                    owner.GetBinaryForm(ownerBytes, 0);
                    GCHandle ownerPin = GCHandle.Alloc(ownerBytes, GCHandleType.Pinned);
                    RawAcl acl = new RawAcl(2, protect ? allowed.Count : 0);
                    if (protect) {
                        foreach (KeyValuePair<string, int> rule in allowed)
                            acl.InsertAce(acl.Count, new CommonAce(AceFlags.ObjectInherit | AceFlags.ContainerInherit,
                                AceQualifier.AccessAllowed, rule.Value, new SecurityIdentifier(rule.Key), false, null));
                    }
                    byte[] aclBytes = new byte[acl.BinaryLength];
                    acl.GetBinaryForm(aclBytes, 0);
                    IntPtr actualAcl = Marshal.AllocHGlobal(aclBytes.Length); // Non-null ACL, even when zero ACEs.
                    try {
                        Marshal.Copy(aclBytes, 0, actualAcl, aclBytes.Length);
                        Validate(handle, directory);
                        uint error = SetSecurityInfo(handle, 1, OwnerInfo | DaclInfo | (protect ? ProtectedDaclInfo : UnprotectedDaclInfo),
                            ownerPin.AddrOfPinnedObject(), IntPtr.Zero, actualAcl, IntPtr.Zero);
                        if (error != 0) throw new Win32Exception((int)error, "Application-data permission hardening failed.");
                        Validate(handle, directory);
                        Verify(handle, ownerSid, allowed, !protect);
                    } finally { Marshal.FreeHGlobal(actualAcl); ownerPin.Free(); }
                }
            } finally { for (int index = ancestors.Count - 1; index >= 0; index--) ancestors[index].Dispose(); }
        }
    }
}
'@
}

function Set-PrivateDataEntryAcl([string]$Root, [string]$Path, [bool]$Directory, [string]$OwnerSid, [Collections.Generic.Dictionary[string,int]]$Allowed) {
    Initialize-PrivateAclRuntime
    [BlueReel.NativePrivateAcl]::ResetEntry($Root, $Path, $Directory, $OwnerSid, $Allowed)
}

function Set-PrivateDataAcl {
    if (-not (Test-Path -LiteralPath $DataDir)) { New-Item -ItemType Directory -Path $DataDir | Out-Null }
    # Preflight the entire dedicated state tree without following a junction.
    # /inheritance:r + /grant:r alone would retain an attacker's explicit ACEs
    # and ownership when the predictable ProgramData directory was pre-created.
    $taskRoot = [IO.Path]::GetFullPath($DataDir).TrimEnd('\')
    $taskDirectories = [Collections.Generic.Queue[string]]::new()
    $taskEntries = [Collections.Generic.List[object]]::new()
    $taskDirectories.Enqueue($taskRoot)
    while ($taskDirectories.Count -gt 0) {
        $taskDirectory = $taskDirectories.Dequeue()
        $taskCurrent = Get-Item -LiteralPath $taskDirectory -Force
        if (-not $taskCurrent.PSIsContainer -or ($taskCurrent.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Protected application data cannot contain junctions or reparse points.'
        }
        foreach ($taskChild in @(Get-ChildItem -LiteralPath $taskDirectory -Force -ErrorAction Stop)) {
            $taskFull = [IO.Path]::GetFullPath($taskChild.FullName)
            if (-not $taskFull.StartsWith($taskRoot + '\',[StringComparison]::OrdinalIgnoreCase)) {
                throw 'An application-data ACL target escaped its dedicated instance directory.'
            }
            if ($taskChild.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Protected application data cannot contain junctions or reparse points.'
            }
            $taskEntries.Add([pscustomobject]@{Path=$taskFull; Directory=[bool]$taskChild.PSIsContainer})
            if ($taskChild.PSIsContainer) { $taskDirectories.Enqueue($taskFull) }
        }
    }

    # A predictable ProgramData name is not proof that its contents belong to
    # this instance. Complete this check before changing any owner or DACL.
    $taskMarker = Join-Path $taskRoot '.bluereel-native-instance'
    if (Test-Path -LiteralPath $taskMarker) {
        $taskMarkerItem = Get-Item -LiteralPath $taskMarker -Force
        if ($taskMarkerItem.PSIsContainer -or $taskMarkerItem.Length -gt 128) {
            throw 'The application-data instance marker is invalid; existing contents were preserved.'
        }
        $taskMarkerText = [IO.File]::ReadAllText($taskMarker).TrimEnd([char[]]"`r`n")
        if (-not [string]::Equals($taskMarkerText, $TaskPrefix, [StringComparison]::Ordinal)) {
            throw 'The application-data directory belongs to another instance; existing contents were preserved.'
        }
    } else {
        # Keep this allowlist aligned with native_install.STATE_DIRECTORIES.
        # Empty directories may have been created by an interrupted first setup.
        $taskKnownState = @('configuration','database','data','artwork','logs','temp','backups','state','upgrade')
        foreach ($taskChild in @(Get-ChildItem -LiteralPath $taskRoot -Force -ErrorAction Stop)) {
            if (-not $taskChild.PSIsContainer -or $taskChild.Name -cnotin $taskKnownState -or
                @(Get-ChildItem -LiteralPath $taskChild.FullName -Force -ErrorAction Stop).Count -gt 0) {
                throw 'The unmarked application-data directory is not empty; existing contents were preserved.'
            }
        }
    }

    # A hard-linked regular file shares its owner/DACL with every other name,
    # potentially including source media outside this directory. Never harden
    # such a file. Python is the already-pinned embedded runtime, isolated from
    # user/site environment settings; this check only reads filesystem metadata.
    $taskLinkCheck = @'
import os, pathlib, stat, sys
try:
    root = pathlib.Path(sys.argv[1])
    for directory, names, files in os.walk(root, followlinks=False, onerror=lambda error: (_ for _ in ()).throw(error)):
        for name in names + files:
            info = (pathlib.Path(directory) / name).lstat()
            if getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise SystemExit(65)
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise SystemExit(65)
except Exception:
    raise SystemExit(65)
'@
    $null = & $TaskPython -I -B -c $taskLinkCheck $taskRoot 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'Application data contains a hard link or cannot be safely inspected; existing permissions were preserved.'
    }

    $taskAdministrators = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    $taskSystem = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $taskAcl = [Security.AccessControl.DirectorySecurity]::new()
    $taskAcl.SetOwner($taskAdministrators)
    $taskAcl.SetAccessRuleProtection($true, $false)
    $taskInheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    foreach ($taskIdentity in @($taskSystem,$taskAdministrators)) {
        $taskAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $taskIdentity, [Security.AccessControl.FileSystemRights]::FullControl,
            $taskInheritance, [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow
        ))
    }
    # Retain this instance's registered service identities during repair. Fresh
    # installation grants them after registration in Set-ServiceDataAcl.
    foreach ($taskRole in $TaskRoles) {
        $taskName = $TaskPrefix + $taskRole
        if (Get-Service -Name $taskName -ErrorAction SilentlyContinue) {
            $taskServiceSid = ([Security.Principal.NTAccount]::new('NT SERVICE',$taskName)).Translate([Security.Principal.SecurityIdentifier])
            $taskAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
                $taskServiceSid, [Security.AccessControl.FileSystemRights]::Modify,
                $taskInheritance, [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow
            ))
        }
    }
    Set-Acl -LiteralPath $taskRoot -AclObject $taskAcl
    $taskAllowed = [Collections.Generic.Dictionary[string,int]]::new([StringComparer]::Ordinal)
    foreach ($taskRule in $taskAcl.GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier])) {
        $taskAllowed.Add($taskRule.IdentityReference.Value, [int]$taskRule.FileSystemRights)
    }
    # Reset each preflighted child to its newly protected parent's inherited
    # allowlist and replace the owner. No broad or unresolved /T operation and
    # no source-media ACL is touched. Breadth-first ordering handles parents first.
    foreach ($taskEntry in $taskEntries) {
        $taskEntryPath = [IO.Path]::GetFullPath($taskEntry.Path)
        if (-not $taskEntryPath.StartsWith($taskRoot + '\',[StringComparison]::OrdinalIgnoreCase) -or
            ((Get-Item -LiteralPath $taskEntryPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Application data changed during permission hardening; stop and inspect the instance.'
        }
        Set-PrivateDataEntryAcl $taskRoot $taskEntryPath $taskEntry.Directory $taskAdministrators.Value $taskAllowed
    }
}

function Set-ServiceDataAcl {
    $taskDataRoot = Assert-DedicatedPath $DataDir
    $taskConfigRoot = Assert-DedicatedPath (Join-Path $taskDataRoot 'configuration')
    if (-not [string]::Equals([IO.Path]::GetDirectoryName($taskConfigRoot),$taskDataRoot,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Private configuration escaped the dedicated application-data directory.'
    }
    $taskOwner = 'S-1-5-32-544'
    $taskParentAllowed = [Collections.Generic.Dictionary[string,int]]::new([StringComparer]::Ordinal)
    $taskConfigAllowed = [Collections.Generic.Dictionary[string,int]]::new([StringComparer]::Ordinal)
    foreach ($taskSid in @($taskOwner,'S-1-5-18')) {
        $taskParentAllowed.Add($taskSid,[int][Security.AccessControl.FileSystemRights]::FullControl)
        $taskConfigAllowed.Add($taskSid,[int][Security.AccessControl.FileSystemRights]::FullControl)
    }
    foreach ($taskRole in $TaskRoles) {
        $taskName = $TaskPrefix + $taskRole
        Invoke-Checked 'sc.exe' @('sidtype',$taskName,'unrestricted')
        Invoke-Checked 'icacls.exe' @($DataDir,'/grant',"NT SERVICE\${taskName}:(OI)(CI)(M)")
        $taskSid = ([Security.Principal.NTAccount]::new('NT SERVICE',$taskName)).Translate([Security.Principal.SecurityIdentifier]).Value
        # FileSystemAccessRule adds Synchronize to Allow rules. Match the actual
        # inherited NTFS masks, including that bit, for strict verification.
        $taskParentAllowed.Add($taskSid,[int]([Security.AccessControl.FileSystemRights]::Modify -bor [Security.AccessControl.FileSystemRights]::Synchronize))
        $taskConfigAllowed.Add($taskSid,[int]([Security.AccessControl.FileSystemRights]::ReadAndExecute -bor [Security.AccessControl.FileSystemRights]::Synchronize))
    }
    # The full tree was link-preflighted before any permission mutation. Lock
    # and revalidate every path while removing service writes from config.
    Initialize-PrivateAclRuntime
    [BlueReel.NativePrivateAcl]::ProtectEntry($taskDataRoot,$taskConfigRoot,$taskOwner,$taskParentAllowed,$taskConfigAllowed)
    $taskDirectories = [Collections.Generic.Queue[string]]::new()
    $taskDirectories.Enqueue($taskConfigRoot)
    while ($taskDirectories.Count -gt 0) {
        $taskDirectory = $taskDirectories.Dequeue()
        foreach ($taskChild in @(Get-ChildItem -LiteralPath $taskDirectory -Force -ErrorAction Stop)) {
            $taskFull = [IO.Path]::GetFullPath($taskChild.FullName)
            if (-not $taskFull.StartsWith($taskConfigRoot + '\',[StringComparison]::OrdinalIgnoreCase)) {
                throw 'Private configuration permission target escaped its dedicated directory.'
            }
            Set-PrivateDataEntryAcl $taskConfigRoot $taskFull ([bool]$taskChild.PSIsContainer) $taskOwner $taskConfigAllowed
            if ($taskChild.PSIsContainer) { $taskDirectories.Enqueue($taskFull) }
        }
    }
}

function Set-InstanceFirewall {
    # Never modify firewall defaults/profiles/global policy. Block only these
    # installed binaries and exclude loopback from both address families.
    $taskPrograms = [ordered]@{
        python = 'runtime\python\python.exe'; node = 'runtime\node\node.exe';
        ffmpeg = 'runtime\ffmpeg\ffmpeg.exe'; ffprobe = 'runtime\ffmpeg\ffprobe.exe';
        caddy = 'runtime\caddy\caddy.exe'
    }
    foreach ($taskKey in $taskPrograms.Keys) {
        $taskName = "$TaskPrefix-Outbound-$taskKey"
        $taskExisting = Get-NetFirewallRule -Name $taskName -ErrorAction SilentlyContinue
        if ($taskExisting) {
            if ($taskExisting.Group -ne $TaskPrefix) { throw 'A firewall rule identity conflicts with this installer.' }
            $taskExisting | Remove-NetFirewallRule
        }
        New-NetFirewallRule -Name $taskName -DisplayName "${TaskPrefix}: strict-local $taskKey" -Group $TaskPrefix `
            -Direction Outbound -Action Block -Enabled True -Profile Any -Protocol Any `
            -Program (Join-Path $ProgramDir $taskPrograms[$taskKey]) `
            -RemoteAddress @('0.0.0.0-126.255.255.255','128.0.0.0-255.255.255.255','::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff') | Out-Null
    }
    $taskInbound = Get-NetFirewallRule -Name "$TaskPrefix-Inbound-PrivateLAN" -ErrorAction SilentlyContinue
    if ($taskInbound) {
        if ($taskInbound.Group -ne $TaskPrefix) { throw 'An inbound firewall rule identity conflicts with this installer.' }
        $taskInbound | Remove-NetFirewallRule
    }
    $taskLocalRule = Get-NetFirewallRule -Name "$TaskPrefix-Inbound-LocalTransport" -ErrorAction SilentlyContinue
    if ($taskLocalRule) {
        if ($taskLocalRule.Group -ne $TaskPrefix) { throw 'A firewall rule identity conflicts with this installer.' }
        $taskLocalRule | Remove-NetFirewallRule
    }
    $taskEnvironment = Join-Path $DataDir 'configuration\.env'
    $taskLocalEnabled = [string](Select-String -LiteralPath $taskEnvironment -Pattern '^LOCAL_TRANSPORT_ENABLED=' | Select-Object -First 1)
    if ($taskLocalEnabled -match '=\s*"?true"?\s*$') {
        $taskLocalAddress = ([string](Select-String -LiteralPath $taskEnvironment -Pattern '^LOCAL_TRANSPORT_ADDRESS=' | Select-Object -First 1)).Split('=',2)[1].Trim('"')
        $taskLocalPort = [int](([string](Select-String -LiteralPath $taskEnvironment -Pattern '^LOCAL_TRANSPORT_PORT=' | Select-Object -First 1)).Split('=',2)[1].Trim('"'))
        New-NetFirewallRule -Name "$TaskPrefix-Inbound-LocalTransport" -DisplayName "$TaskPrefix authenticated local transport" -Group $TaskPrefix `
            -Direction Inbound -Action Allow -Enabled True -Profile Private -Protocol TCP -LocalPort $taskLocalPort `
            -LocalAddress $taskLocalAddress -RemoteAddress LocalSubnet -Program (Join-Path $ProgramDir 'runtime\python\python.exe') | Out-Null
    }
    $taskMetadata = Get-Content -LiteralPath (Join-Path $DataDir 'configuration\installation.json') -Raw | ConvertFrom-Json
    if ($taskMetadata.bind_address -ne '127.0.0.1') {
        New-NetFirewallRule -Name "$TaskPrefix-Inbound-PrivateLAN" -DisplayName "$TaskPrefix private-LAN access" -Group $TaskPrefix `
            -Direction Inbound -Action Allow -Enabled True -Profile Private -Protocol TCP -LocalPort $taskMetadata.port `
            -LocalAddress $taskMetadata.bind_address -RemoteAddress LocalSubnet -Program (Join-Path $ProgramDir 'runtime\caddy\caddy.exe') | Out-Null
    }
}

function Remove-InstanceFirewall {
    foreach ($taskName in @('Outbound-python','Outbound-node','Outbound-ffmpeg','Outbound-ffprobe','Outbound-caddy','Inbound-PrivateLAN','Inbound-LocalTransport')) {
        $taskRule = Get-NetFirewallRule -Name "$TaskPrefix-$taskName" -ErrorAction SilentlyContinue
        if ($taskRule) {
            if ($taskRule.Group -ne $TaskPrefix) { throw 'A firewall rule identity conflicts with this installer; it was preserved.' }
            $taskRule | Remove-NetFirewallRule
        }
    }
}

function Start-Instance([switch]$SkipRemote) {
    foreach ($taskRole in $TaskRoles) { Start-Service -Name ($TaskPrefix + $taskRole) -ErrorAction Stop }
    Invoke-Native 'health'
    $taskRemote = Get-OptionalRemoteService
    if (-not $SkipRemote -and $taskRemote -and $taskRemote.State -eq 'Stopped') { Start-Service -Name $taskRemote.Name }
    foreach ($taskRole in $TaskRoles) {
        if ((Get-Service -Name ($TaskPrefix + $taskRole)).Status -ne 'Running') { throw 'A media-server component did not remain running.' }
    }
}

function Select-ApprovedRoots {
    Add-Type -AssemblyName System.Windows.Forms
    $taskForm = [Windows.Forms.Form]::new()
    $taskForm.Text = "$TaskDisplayName approved media folders"
    $taskForm.Width = 650; $taskForm.Height = 420
    $taskForm.StartPosition = 'CenterScreen'
    $taskLabel = [Windows.Forms.Label]::new()
    $taskLabel.SetBounds(15, 15, 600, 65)
    $taskLabel.Text = 'One absolute Windows path per line. Only these approved roots can be browsed. This replaces the approved-root list, not the library database. Source files and their permissions are never changed. LocalService must have read access.'
    $taskText = [Windows.Forms.TextBox]::new()
    $taskText.Multiline = $true; $taskText.ScrollBars = 'Vertical'
    $taskText.SetBounds(15, 85, 600, 230)
    $taskRootLine = Get-Content -LiteralPath (Join-Path $DataDir 'configuration\.env') | Where-Object { $_.StartsWith('MEDIA_ROOT_DEFINITIONS=') } | Select-Object -First 1
    if ($taskRootLine) {
        $taskRootJson = $taskRootLine.Substring('MEDIA_ROOT_DEFINITIONS='.Length) | ConvertFrom-Json
        $taskCurrentRoots = $taskRootJson | ConvertFrom-Json
        $taskText.Lines = @($taskCurrentRoots | ForEach-Object { $_.path })
    }
    $taskBrowse = [Windows.Forms.Button]::new()
    $taskBrowse.Text = 'Add folder...'; $taskBrowse.SetBounds(15, 330, 130, 30)
    $taskBrowse.Add_Click({
        $taskDialog = [Windows.Forms.FolderBrowserDialog]::new()
        $taskDialog.ShowNewFolderButton = $false
        $taskDialog.Description = 'Select an approved media root. No source media is changed.'
        if ($taskDialog.ShowDialog() -eq 'OK') { $taskText.AppendText([Environment]::NewLine + $taskDialog.SelectedPath) }
        $taskDialog.Dispose()
    })
    $taskOkay = [Windows.Forms.Button]::new()
    $taskOkay.Text = 'Save and restart'; $taskOkay.SetBounds(350, 330, 140, 30)
    $taskOkay.DialogResult = 'OK'
    $taskCancel = [Windows.Forms.Button]::new()
    $taskCancel.Text = 'Cancel'; $taskCancel.SetBounds(500, 330, 110, 30)
    $taskCancel.DialogResult = 'Cancel'
    $taskForm.Controls.AddRange(@($taskLabel,$taskText,$taskBrowse,$taskOkay,$taskCancel))
    $taskForm.AcceptButton = $taskOkay; $taskForm.CancelButton = $taskCancel
    try {
        if ($taskForm.ShowDialog() -ne 'OK') { return $null }
        $taskSelectedFile = Join-Path $DataDir 'state\approved-roots-edit.txt'
        [IO.File]::WriteAllLines($taskSelectedFile, $taskText.Lines, [Text.UTF8Encoding]::new($false))
        return $taskSelectedFile
    } finally { $taskForm.Dispose() }
}

try {
    Assert-Administrator
    $ProgramDir = Assert-DedicatedPath $ProgramDir
    $DataDir = Assert-DedicatedPath $DataDir
    if ($ProgramDir -ieq $DataDir -or $DataDir.StartsWith($ProgramDir + '\',[StringComparison]::OrdinalIgnoreCase) -or $ProgramDir.StartsWith($DataDir + '\',[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Program and data directories must be separate.'
    }
    $TaskPython = Join-Path $ProgramDir 'runtime\python\python.exe'
    switch ($Action) {
        'Install' {
            $null = Get-OptionalRemoteService
            foreach ($taskRole in $TaskRoles) { $null = Assert-ServiceIdentity $taskRole }
            Stop-Instance
            Set-PrivateDataAcl
            $taskConfigure = @('--program-dir',$ProgramDir,'--instance',$Instance,'--bind-address',$BindAddress)
            if ($Port -ne 0) { $taskConfigure += @('--port',[string]$Port) }
            if ($RootsFile) { $taskConfigure += @('--roots-file',$RootsFile) }
            Invoke-Native 'configure' $taskConfigure
            foreach ($taskRole in $TaskRoles) {
                $taskName = $TaskPrefix + $taskRole
                if (-not (Get-Service -Name $taskName -ErrorAction SilentlyContinue)) {
                    Invoke-Checked (Join-Path $ProgramDir "services\$taskName.exe") @('install')
                }
            }
            Set-ServiceDataAcl
            Set-InstanceFirewall
            Invoke-Native 'migrate'
            # The one-EXE install includes a ready, isolated connector. It stays
            # disabled and creates no identity until the local Owner pairs it.
            # Repair replaces its code and policy while retaining its DPAPI key.
            Invoke-RemoteMaintenance 'Install'
            Start-Instance
        }
        'Stop' { Stop-Instance }
        'PrepareUpgrade' {
            $null = Get-OptionalRemoteService
            Invoke-Native 'prepare-upgrade'
            Stop-Instance
            if (Get-OptionalRemoteService) { Invoke-RemoteMaintenance 'PrepareUpgrade' }
        }
        'Health' { Invoke-Native 'health' }
        'Backup' { Invoke-Native 'backup' }
        'ValidateBackup' {
            if (-not $Archive) {
                Add-Type -AssemblyName System.Windows.Forms
                $taskDialog = New-Object System.Windows.Forms.OpenFileDialog
                $taskDialog.InitialDirectory = Join-Path $DataDir 'backups'
                $taskDialog.Filter = "$TaskDisplayName backups (*.zip)|*.zip"
                if ($taskDialog.ShowDialog() -ne 'OK') { exit 0 }
                $Archive = $taskDialog.FileName
            }
            Invoke-Native 'validate-backup' @('--archive',$Archive)
        }
        'ConfigureMedia' {
            if (-not $RootsFile) {
                if (-not $Interactive) { throw 'An explicit approved-root list is required.' }
                $RootsFile = Select-ApprovedRoots
                if (-not $RootsFile) { exit 0 }
            }
            Stop-Instance
            try {
                Invoke-Native 'change-media' @('--roots-file',$RootsFile)
                Invoke-RemoteMaintenance 'Install'
            } finally { Start-Instance -SkipRemote }
        }
        'ConfigureNetwork' {
            if ($Interactive) {
                Add-Type -AssemblyName Microsoft.VisualBasic
                $taskMetadata = Get-Content -LiteralPath (Join-Path $DataDir 'configuration\installation.json') -Raw | ConvertFrom-Json
                $BindAddress = [Microsoft.VisualBasic.Interaction]::InputBox('Owner/administrator choice: enter 127.0.0.1 to disable LAN access, or this PC''s exact private IPv4 address to enable it. Only the private firewall profile and local subnet are allowed. No router changes are made.', "$TaskDisplayName private-LAN access", $taskMetadata.bind_address)
                if (-not $BindAddress) { exit 0 }
            }
            Stop-Instance
            try {
                Invoke-Native 'change-network' @('--bind-address',$BindAddress)
                Set-InstanceFirewall
            } finally { Start-Instance }
        }
        'Remove' {
            Stop-Instance
            if (Get-OptionalRemoteService) {
                Invoke-RemoteMaintenance 'Remove'
            }
            foreach ($taskRole in $TaskStopOrder) {
                if ($null -ne (Assert-ServiceIdentity $taskRole)) {
                    Invoke-Checked (Join-Path $ProgramDir ('services\' + $TaskPrefix + $taskRole + '.exe')) @('uninstall')
                }
            }
            Remove-InstanceFirewall
            if ($RemoveData) {
                if ($Instance -eq 'development' -and
                    $DataDir -ieq (Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'BlueReel-Development')) {
                    $TaskDataName = 'BlueReel-Development'
                }
                $taskExpected = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) $TaskDataName
                if ($ConfirmDataRemoval -cne $TaskDataName -or $DataDir -ine $taskExpected) { throw 'Explicit data removal requires the exact instance name and default ProgramData target.' }
                if ((Get-Content -LiteralPath (Join-Path $DataDir '.bluereel-native-instance') -Raw).Trim() -cne $TaskPrefix) { throw 'Native data marker mismatch; no data was removed.' }
                if (Get-ChildItem -LiteralPath $DataDir -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } | Select-Object -First 1) { throw 'Data contains a reparse point; no recursive removal was attempted.' }
                Invoke-RemoteMaintenance 'PurgeState'
                # Exact canonical ProgramData instance validated above; never media or workspace.
                Remove-Item -LiteralPath $DataDir -Recurse -Force
                Write-Output 'Explicitly selected instance data was permanently removed; source media was not touched.'
            } else { Write-Output 'Instance data, configuration, history and backups were preserved.' }
        }
    }
    Write-Output "$TaskDisplayName native $Action completed."
    exit 0
} catch {
    Write-Error -Message ('Native maintenance failed. ' + $_.Exception.Message) -ErrorAction Continue
    exit 1
}
