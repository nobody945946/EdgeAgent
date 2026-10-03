param(
    [ValidateSet('Inspect','Apply','Restore','RestartWifi','DisableWifiExtension','PlanLight','ApplyLight','RestoreLight','BeginAI','EndAI')][string]$Action = 'Inspect',
    [string]$StateDirectory = (Join-Path $env:USERPROFILE '.codex/windows-ai-maintenance')
)
$ErrorActionPreference = 'Stop'
$maintenanceRoot = [IO.Path]::GetFullPath($StateDirectory)
$snapshotPath = Join-Path $maintenanceRoot 'original-state.json'
$registry = @(
    @{ Path='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'; Name='Discord'; Remove=$true },
    @{ Path='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'; Name='Steam'; Remove=$true },
    @{ Path='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'; Name='MicrosoftEdgeAutoLaunch_2C1E6D67CE6525CFA2B2D8FB1E73CEA3'; Remove=$true },
    @{ Path='HKCU:\Software\Policies\Microsoft\Edge'; Name='StartupBoostEnabled'; Value=0 },
    @{ Path='HKCU:\Software\Policies\Microsoft\Edge'; Name='BackgroundModeEnabled'; Value=0 }
)
$taskNames = @('AsusSystemAnalysis_754F3273-0563-4F20-B12F-826510B07474','AsusSystemDiagnosis_DriverQuality')
$serviceNames = @('ASUSSystemAnalysis','ASUSSystemDiagnosis')
$wifiExtension = @{Path='HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\0002\Ndi\IHVExtensions';Name='ExtensibilityDLL'}
function Measure-System {
    $os = Get-CimInstance Win32_OperatingSystem
    $wlan = @(Get-Process -Name WLANExt -ErrorAction SilentlyContinue)
    [pscustomobject]@{
        Time=(Get-Date).ToUniversalTime().ToString('o')
        AvailableRAMMiB=[math]::Round($os.FreePhysicalMemory/1024)
        AvailableCommitMiB=[math]::Round($os.FreeVirtualMemory/1024)
        WLAN=@($wlan | ForEach-Object { [pscustomobject]@{PID=$_.Id;PrivateMiB=[math]::Round($_.PrivateMemorySize64/1MB);WorkingMiB=[math]::Round($_.WorkingSet64/1MB)} })
        ConnectedAdapters=@(Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | Select-Object -ExpandProperty Name)
    }
}
function Read-Registry($item) {
    $key = Get-Item -LiteralPath $item.Path -ErrorAction SilentlyContinue
    $exists = $key -and ($key.GetValueNames() -contains $item.Name)
    [pscustomobject]@{Path=$item.Path;Name=$item.Name;Exists=[bool]$exists;Kind=if($exists){$key.GetValueKind($item.Name).ToString()}else{$null};Data=if($exists){$key.GetValue($item.Name,$null,[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)}else{$null}}
}
function Set-Registry($item) {
    if ($item['Remove']) {Remove-ItemProperty -LiteralPath $item.Path -Name $item.Name -ErrorAction SilentlyContinue}
    else {
        if (!(Test-Path -LiteralPath $item.Path)) {New-Item -Path $item.Path -Force | Out-Null}
        $kind=if($item.Kind){$item.Kind}else{'DWord'}
        New-ItemProperty -LiteralPath $item.Path -Name $item.Name -PropertyType $kind -Value $item.Value -Force | Out-Null
    }
}
function Restore-Registry($items) {
    foreach ($item in $items) {
        Set-Registry @{Path=$item.Path;Name=$item.Name;Remove=(!$item.Exists);Kind=$item.Kind;Value=$item.Data}
    }
}
function Read-ServiceState($names) {
    @(Get-CimInstance Win32_Service | Where-Object {$names -contains $_.Name} | ForEach-Object {
        [pscustomobject]@{Name=$_.Name;StartMode=$_.StartMode;State=$_.State;DelayedAutoStart=(Read-Registry @{Path=('HKLM:\SYSTEM\CurrentControlSet\Services\'+$_.Name);Name='DelayedAutoStart'})}
    })
}
function Restore-Services($services) {
    foreach ($service in $services) {
        $startType=switch($service.StartMode){'Auto'{'Automatic'};'Manual'{'Manual'};'Disabled'{'Disabled'};default{throw 'Unsupported original service start mode'}}
        Set-Service -Name $service.Name -StartupType $startType
        Restore-Registry @($service.DelayedAutoStart)
        if ($service.State -eq 'Running') {Start-Service -Name $service.Name}
        else {Stop-Service -Name $service.Name}
    }
}
function Save-State($state) {
    $stateDirectory=Split-Path -Parent $snapshotPath
    if (!(Test-Path -LiteralPath $stateDirectory)) {New-Item -ItemType Directory -Path $stateDirectory | Out-Null}
    $pending=$snapshotPath+'.pending'
    [IO.File]::WriteAllText($pending,($state | ConvertTo-Json -Depth 8),(New-Object Text.UTF8Encoding($true)))
    if (Test-Path -LiteralPath $snapshotPath) {[IO.File]::Replace($pending,$snapshotPath,[NullString]::Value)}
    else {[IO.File]::Move($pending,$snapshotPath)}
}
function Read-State {
    if (!(Test-Path -LiteralPath $snapshotPath)) {return [pscustomobject]@{Version=1}}
    $state=Get-Content -LiteralPath $snapshotPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($state.Version -ne 1) {throw 'Unsupported snapshot version'}
    return $state
}
function Assert-Administrator {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    if (!(New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script as administrator using the same Windows user account.'
    }
    $ownerProfile=Split-Path -Parent (Split-Path -Parent $maintenanceRoot)
    if ([IO.Path]::GetFullPath($env:USERPROFILE) -ne [IO.Path]::GetFullPath($ownerProfile)) {
        throw 'Do not elevate with a different account: this script changes the original user profile.'
    }
}
function Initialize-CoreState($state) {
    if ($state.PSObject.Properties.Name -notcontains 'Registry') {
        $state | Add-Member -NotePropertyName Registry -NotePropertyValue @($registry | ForEach-Object {Read-Registry $_})
        $state | Add-Member -NotePropertyName Tasks -NotePropertyValue @(Get-ScheduledTask | Where-Object {$taskNames -contains $_.TaskName} | Select-Object TaskPath,TaskName,@{n='Enabled';e={$_.Settings.Enabled}})
        $state | Add-Member -NotePropertyName System -NotePropertyValue (Measure-System)
        Save-State $state
    }
}
# Supported Win32 setters: preserve each original value, including mixed/custom settings.
function Initialize-VisualApi {
    if ('WindowsAiVisual' -as [type]) {return}
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class WindowsAiVisual {
    [StructLayout(LayoutKind.Sequential)] public struct AnimationInfo { public uint Size; public int Enabled; }
    [DllImport("user32.dll", EntryPoint="SystemParametersInfoW", SetLastError=true)]
    static extern bool GetBool(uint action, uint param, ref int value, uint flags);
    [DllImport("user32.dll", EntryPoint="SystemParametersInfoW", SetLastError=true)]
    static extern bool SetBool(uint action, uint param, IntPtr value, uint flags);
    [DllImport("user32.dll", EntryPoint="SystemParametersInfoW", SetLastError=true)]
    static extern bool Animation(uint action, uint param, ref AnimationInfo value, uint flags);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr SendMessageTimeout(IntPtr h, uint message, IntPtr w, string text, uint flags, uint timeout, out IntPtr result);
    public static int Read(uint action) {
        int value=0;
        if (!GetBool(action,0,ref value,0)) throw new Win32Exception();
        return value;
    }
    public static void Write(uint action, int value) {
        if (!SetBool(action,0,new IntPtr(value),3)) throw new Win32Exception();
    }
    public static int ReadAnimation() {
        var value=new AnimationInfo {Size=8};
        if (!Animation(0x48,8,ref value,0)) throw new Win32Exception();
        return value.Enabled;
    }
    public static void WriteAnimation(int enabled) {
        var value=new AnimationInfo {Size=8,Enabled=enabled};
        if (!Animation(0x49,8,ref value,3)) throw new Win32Exception();
    }
    public static void Refresh() {
        IntPtr result;
        SendMessageTimeout(new IntPtr(0xffff),0x1a,IntPtr.Zero,"ImmersiveColorSet",2,2000,out result);
    }
}
'@
}
$visualOptions=@(
    @{Name='ClientAreaAnimation';Get=0x1042;Set=0x1043},
    @{Name='MenuAnimation';Get=0x1002;Set=0x1003},
    @{Name='ComboBoxAnimation';Get=0x1004;Set=0x1005},
    @{Name='ListBoxSmoothScrolling';Get=0x1006;Set=0x1007},
    @{Name='TooltipAnimation';Get=0x1016;Set=0x1017}
)
function Read-Visual {
    Initialize-VisualApi
    @([pscustomobject]@{Name='WindowAnimation';Value=[WindowsAiVisual]::ReadAnimation()}) + @($visualOptions | ForEach-Object {
        [pscustomobject]@{Name=$_.Name;Value=[WindowsAiVisual]::Read($_.Get)}
    })
}
function Write-Visual($values) {
    Initialize-VisualApi
    foreach ($item in $values) {
        if ($item.Name -eq 'WindowAnimation') {[WindowsAiVisual]::WriteAnimation([int]$item.Value)}
        else {
            $option=$visualOptions | Where-Object Name -eq $item.Name
            if (!$option) {throw 'Unknown visual setting in snapshot'}
            [WindowsAiVisual]::Write($option.Set,[int]$item.Value)
        }
    }
    [WindowsAiVisual]::Refresh()
}
$lightRegistry=@($registry | Where-Object {$_.Path -eq 'HKCU:\Software\Policies\Microsoft\Edge'}) + @(
    @{Path='HKCU:\Software\Policies\Google\Chrome';Name='BackgroundModeEnabled';Value=0},
    @{Path='HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize';Name='EnableTransparency';Value=0}
)
function Invoke-LightProfile($mode) {
    if ($mode -eq 'PlanLight') {
        [pscustomobject]@{System=(Measure-System);Registry=@($lightRegistry | ForEach-Object {Read-Registry $_});Visual=(Read-Visual);Services=(Read-ServiceState @('SysMain','WSearch','DiagTrack','wuauserv'));RelevantPackages=@(Get-AppxPackage | Where-Object {$_.Name -match '^(Microsoft\.Copilot|Microsoft\.549981C3F5F10|MicrosoftWindows\.Client\.WebExperience)$'} | Select-Object -ExpandProperty Name);ApplyChanges=@('Browser background/startup boost policies','DiagTrack Disabled/Stopped','Animations off, font smoothing unchanged','Transparency off');Excluded=@('SysMain','Windows Update','Mem Reduct','Package uninstall');SearchSession='BeginAI/EndAI is optional and restores the original Search service configuration'} | ConvertTo-Json -Depth 7
        return
    }
    Assert-Administrator
    $state=Read-State
    if ($mode -eq 'ApplyLight') {
        Initialize-CoreState $state
        if (!$state.Light) {
            $light=[pscustomobject]@{Registry=@($lightRegistry | ForEach-Object {Read-Registry $_});Visual=(Read-Visual);Services=(Read-ServiceState @('DiagTrack'))}
            $state | Add-Member -NotePropertyName Light -NotePropertyValue $light
            Save-State $state
        }
        try {
            $dependent=@((Get-Service -Name DiagTrack).DependentServices | Where-Object Status -eq 'Running')
            if ($dependent.Count) {throw 'DiagTrack has running dependent services; refusing to force-stop them'}
            Set-Service -Name DiagTrack -StartupType Disabled
            Stop-Service -Name DiagTrack
            foreach ($item in $lightRegistry) {Set-Registry $item}
            Write-Visual @($state.Light.Visual | ForEach-Object {[pscustomobject]@{Name=$_.Name;Value=0}})
        } catch {
            $failure=$_
            Invoke-LightProfile 'RestoreLight' | Out-Null
            throw $failure
        }
        Invoke-LightProfile 'PlanLight'
    } elseif ($mode -eq 'RestoreLight') {
        if (!$state.Light) {throw 'No Light profile snapshot exists; no change made'}
        if ($state.SearchSession) {Invoke-LightProfile 'EndAI' | Out-Null}
        Restore-Registry $state.Light.Registry
        Write-Visual $state.Light.Visual
        Restore-Services $state.Light.Services
        Invoke-LightProfile 'PlanLight'
    } elseif ($mode -eq 'BeginAI') {
        if ($state.SearchSession) {throw 'A Search session already exists; run EndAI first'}
        $search=(Read-ServiceState @('WSearch'))
        if (@($search).Count -ne 1) {throw 'Windows Search service not found'}
        $state | Add-Member -NotePropertyName SearchSession -NotePropertyValue $search
        Save-State $state
        try {
            Set-Service -Name WSearch -StartupType Manual
            Stop-Service -Name WSearch
        } catch {
            $failure=$_
            Invoke-LightProfile 'EndAI' | Out-Null
            throw $failure
        }
        Write-Output 'Search paused. Use EndAI to restore it after the AI task.'
    } elseif ($mode -eq 'EndAI') {
        if (!$state.SearchSession) {throw 'No Search session exists; no change made'}
        Restore-Services $state.SearchSession
        $state.PSObject.Properties.Remove('SearchSession')
        Save-State $state
        Write-Output 'Original Search configuration and running state restored.'
    } else {throw 'Unknown Light profile action'}
}
switch ($Action) {
    {$_ -in @('PlanLight','ApplyLight','RestoreLight','BeginAI','EndAI')} {Invoke-LightProfile $Action}
    'Inspect' {
        [pscustomobject]@{System=(Measure-System);Registry=@($registry | ForEach-Object {Read-Registry $_});WifiExtension=(Read-Registry $wifiExtension);Services=@(Get-CimInstance Win32_Service | Where-Object {$serviceNames -contains $_.Name} | Select-Object Name,StartMode,State);Tasks=@(Get-ScheduledTask | Where-Object {$taskNames -contains $_.TaskName} | Select-Object TaskPath,TaskName,@{n='Enabled';e={$_.Settings.Enabled}})} | ConvertTo-Json -Depth 6
    }
    'Apply' {
        $original=Read-State
        Initialize-CoreState $original
        if (!$original.Services) {
            $serviceState=@(Get-CimInstance Win32_Service | Where-Object {$serviceNames -contains $_.Name} | ForEach-Object {
                [pscustomobject]@{Name=$_.Name;StartMode=$_.StartMode;State=$_.State;DelayedAutoStart=(Read-Registry @{Path=('HKLM:\SYSTEM\CurrentControlSet\Services\'+$_.Name);Name='DelayedAutoStart'})}
            })
            $original | Add-Member -NotePropertyName Services -NotePropertyValue $serviceState
            Save-State $original
        }
        foreach ($service in (Get-CimInstance Win32_Service | Where-Object {$serviceNames -contains $_.Name})) {
            if ($service.PathName -notlike '*\DriverStore\FileRepository\asussci2.inf_*\ASUSSystem*\AsusSystem*.exe') {throw 'Unexpected diagnostic service path'}
            Set-Service -Name $service.Name -StartupType Manual
            Stop-Service -Name $service.Name
        }
        foreach ($item in $registry) {Set-Registry $item}
        foreach ($task in (Get-ScheduledTask | Where-Object {$taskNames -contains $_.TaskName})) {
            Disable-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName | Out-Null
            Stop-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName
        }
        & $PSCommandPath -StateDirectory $maintenanceRoot -Action Inspect
    }
    'Restore' {
        $state=Read-State
        if ($state.SearchSession) {Invoke-LightProfile 'EndAI' | Out-Null}
        if ($state.Light) {Invoke-LightProfile 'RestoreLight' | Out-Null}
        $original = Get-Content -LiteralPath $snapshotPath -Raw | ConvertFrom-Json
        if ($original.Version -ne 1) {throw 'Unsupported snapshot version'}
        Restore-Registry $original.Registry
        foreach ($task in $original.Tasks) {
            if ($task.Enabled) {Enable-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName | Out-Null}
            else {Disable-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName | Out-Null}
        }
        Restore-Services $original.Services
        if ($original.WifiExtension) {
            $item=$original.WifiExtension
            if ($item.Exists) {New-ItemProperty -LiteralPath $item.Path -Name $item.Name -PropertyType $item.Kind -Value $item.Data -Force | Out-Null}
            else {Remove-ItemProperty -LiteralPath $item.Path -Name $item.Name -ErrorAction SilentlyContinue}
            & $PSCommandPath -StateDirectory $maintenanceRoot -Action RestartWifi | Out-Null
        }
        & $PSCommandPath -StateDirectory $maintenanceRoot -Action Inspect
    }
    'DisableWifiExtension' {
        $adapter=Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\0002'
        if ($adapter.NetCfgInstanceId -ne '{5FD242FF-B4FD-4728-9C46-7A4F39926073}' -or $adapter.DriverDesc -notmatch 'AX201') {throw 'Adapter identity changed; no modification made'}
        $status=(& netsh wlan show interfaces) -join "`n"
        if ($status -notmatch 'Authentication\s*:\s*WPA[23]-Personal') {throw 'A verified Personal Wi-Fi connection is required; no modification made'}
        $original=Get-Content -LiteralPath $snapshotPath -Raw | ConvertFrom-Json
        if (!$original.WifiExtension) {
            $value=Read-Registry $wifiExtension
            if (!$value.Exists -or $value.Data -notlike '*IntelIHVRouter10.dll') {throw 'Unexpected extension registration; no modification made'}
            $original | Add-Member -NotePropertyName WifiExtension -NotePropertyValue $value
            Save-State $original
        }
        try {
            Remove-ItemProperty -LiteralPath $wifiExtension.Path -Name $wifiExtension.Name -ErrorAction SilentlyContinue
            & $PSCommandPath -StateDirectory $maintenanceRoot -Action RestartWifi | Out-Null
            $deadline=(Get-Date).AddSeconds(30)
            while ((Get-Date) -lt $deadline -and (Get-NetAdapter -Name 'Wi-Fi').Status -ne 'Up') {Start-Sleep -Milliseconds 250}
            if ((Get-NetAdapter -Name 'Wi-Fi').Status -ne 'Up') {throw 'Wi-Fi did not reconnect'}
            $probe=Invoke-WebRequest -Uri 'https://github.com' -UseBasicParsing -TimeoutSec 15
            if ($probe.StatusCode -ne 200) {throw 'Connectivity probe failed'}
        } catch {
            $item=$original.WifiExtension
            New-ItemProperty -LiteralPath $item.Path -Name $item.Name -PropertyType $item.Kind -Value $item.Data -Force | Out-Null
            & $PSCommandPath -StateDirectory $maintenanceRoot -Action RestartWifi | Out-Null
            throw
        }
        & $PSCommandPath -StateDirectory $maintenanceRoot -Action Inspect
    }
    'RestartWifi' {
        $before = Measure-System
        $oldIDs = @(Get-Process -Name WLANExt -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
        try {
            Stop-Service -Name WlanSvc
            foreach ($processID in $oldIDs) {
                $remaining = Get-CimInstance Win32_Process -Filter "ProcessId=$processID"
                if ($remaining -and $remaining.Name -eq 'WLANExt.exe') {
                    Stop-Process -Id $processID -Force -ErrorAction SilentlyContinue
                    $remaining = Get-CimInstance Win32_Process -Filter "ProcessId=$processID"
                    if ($remaining -and $remaining.Name -eq 'WLANExt.exe') {throw 'WLAN extension could not be stopped'}
                }
            }
        } finally {Start-Service -Name WlanSvc}
        [pscustomobject]@{Before=$before;After=(Measure-System)} | ConvertTo-Json -Depth 5
    }
}
