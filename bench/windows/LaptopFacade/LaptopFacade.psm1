# The Windows commands the sandbox's PowerShell 7 lacks, answering with the
# laptop the product runs on (bench/laptop.py; facts in /tmp/.laptop/machine.json,
# written per task by bench/agent_runner.py). PowerShell loads this module the
# first time one of them is called. Only what PowerShell 7 has on Windows:
# no Get-WmiObject (gone since PowerShell 6), no Get-Counter (its counter
# names follow the Windows language).

$script:FactsPath = $env:LAPTOP_FACTS ?? '/tmp/.laptop/machine.json'

function Get-LaptopFacts {
    if (-not $script:Facts) { $script:Facts = Get-Content -Raw -LiteralPath $script:FactsPath | ConvertFrom-Json }
    $script:Facts
}

# Typed as Windows types them, so PowerShell's own type data for the class
# picks the columns a bare call shows.
function New-CimObject([string] $Class, [System.Collections.Specialized.OrderedDictionary] $Values) {
    $object = [pscustomobject] $Values
    $object.PSObject.TypeNames.Insert(0, "Microsoft.Management.Infrastructure.CimInstance#root/cimv2/$Class")
    $object
}

function Get-LaptopClass([string] $Class) {
    $m = Get-LaptopFacts
    $boot = (Get-Date).AddHours(-$m.os.uptime_hours)
    $driver = '32.0.15.' + ($m.gpu.driver -replace '^\d', '' -replace '\.', '')
    switch ($Class.ToLowerInvariant()) {
        'win32_processor' {
            New-CimObject 'Win32_Processor' ([ordered]@{
                DeviceID = 'CPU0'; Name = $m.cpu.name; Manufacturer = $m.cpu.vendor; Caption = $m.cpu.identifier
                NumberOfCores = $m.cpu.cores; NumberOfLogicalProcessors = $m.cpu.threads; MaxClockSpeed = $m.cpu.mhz
                CurrentClockSpeed = $m.cpu.mhz; LoadPercentage = $m.cpu.load; SocketDesignation = 'U3E1'
                Architecture = 9; Status = 'OK' })
        }
        'win32_videocontroller' {
            New-CimObject 'Win32_VideoController' ([ordered]@{
                DeviceID = 'VideoController1'; Name = $m.gpu.integrated; AdapterRAM = 1073741824
                DriverVersion = '31.0.101.5186'; VideoProcessor = $m.gpu.integrated; AdapterCompatibility = ($m.gpu.integrated -split ' ')[0]
                CurrentHorizontalResolution = 1920; CurrentVerticalResolution = 1080; CurrentRefreshRate = 144; Status = 'OK' })
            New-CimObject 'Win32_VideoController' ([ordered]@{
                DeviceID = 'VideoController2'; Name = $m.gpu.name; AdapterRAM = 4293918720
                DriverVersion = $driver; VideoProcessor = $m.gpu.name; AdapterCompatibility = 'NVIDIA'
                CurrentHorizontalResolution = $null; CurrentVerticalResolution = $null; CurrentRefreshRate = $null; Status = 'OK' })
        }
        'win32_operatingsystem' {
            New-CimObject 'Win32_OperatingSystem' ([ordered]@{
                Caption = $m.os.caption; Version = $m.os.version; BuildNumber = $m.os.build; OSArchitecture = $m.os.architecture
                CSName = $m.computer; TotalVisibleMemorySize = [uint64] $m.memory.total_kb; FreePhysicalMemory = [uint64] $m.memory.free_kb
                TotalVirtualMemorySize = [uint64] ($m.memory.total_kb + 4194304); FreeVirtualMemory = [uint64] ($m.memory.free_kb + 1800000)
                LastBootUpTime = $boot; InstallDate = $boot.AddDays(-212); NumberOfProcesses = @($m.processes).Count + 180
                SystemDrive = 'C:'; WindowsDirectory = 'C:\WINDOWS'; SystemDirectory = 'C:\WINDOWS\system32'; OSLanguage = 1028; Locale = '0404'
                Organization = ''; RegisteredUser = $m.user; SerialNumber = '00342-' + $m.computer.Substring(7, 5) + '-AAOEM' })
        }
        'win32_computersystem' {
            New-CimObject 'Win32_ComputerSystem' ([ordered]@{
                Name = $m.computer; Manufacturer = $m.system.maker; Model = $m.system.model
                TotalPhysicalMemory = [uint64] $m.memory.total_kb * 1024; NumberOfProcessors = 1
                NumberOfLogicalProcessors = $m.cpu.threads; SystemType = 'x64-based PC'; PCSystemType = 2
                UserName = "$($m.computer)\$($m.user)"; PrimaryOwnerName = $m.user; Domain = 'WORKGROUP' })
        }
        'win32_physicalmemory' {
            New-CimObject 'Win32_PhysicalMemory' ([ordered]@{
                DeviceLocator = 'DIMM 0'; BankLabel = 'BANK 0'; Capacity = [uint64] $m.memory.dimm_mb * 1MB; Speed = 3200
                ConfiguredClockSpeed = 3200; Manufacturer = 'Samsung'; PartNumber = 'M471A1K43EB1-CWE'; FormFactor = 12 })
        }
        'win32_bios' {
            New-CimObject 'Win32_BIOS' ([ordered]@{
                Manufacturer = 'American Megatrends International, LLC.'; SMBIOSBIOSVersion = $m.system.bios
                Name = $m.system.bios; SerialNumber = $m.computer.Substring(7) + 'X1'; ReleaseDate = $boot.AddDays(-400).Date; Version = 'ALASKA - 1072009' })
        }
        'win32_logicaldisk' {
            New-CimObject 'Win32_LogicalDisk' ([ordered]@{
                DeviceID = 'C:'; DriveType = 3; ProviderName = $null; FileSystem = 'NTFS'; VolumeName = 'Windows'
                Size = [uint64] $m.disk.c_bytes; FreeSpace = [uint64] $m.disk.c_free })
        }
        'win32_diskdrive' {
            New-CimObject 'Win32_DiskDrive' ([ordered]@{
                DeviceID = '\\.\PHYSICALDRIVE0'; Model = $m.disk.model; Caption = $m.disk.model; Size = [uint64] $m.disk.bytes
                InterfaceType = 'SCSI'; MediaType = 'Fixed hard disk media'; Partitions = 4; Status = 'OK' })
        }
        'win32_process' {
            foreach ($p in $m.processes) {
                New-CimObject 'Win32_Process' ([ordered]@{
                    ProcessId = $p.pid; Name = $p.name; WorkingSetSize = [uint64] $p.mb * 1MB; ThreadCount = 4 + $p.pid % 40
                    HandleCount = 100 + $p.pid % 900; CommandLine = $null })
            }
        }
        'win32_battery' {
            New-CimObject 'Win32_Battery' ([ordered]@{
                Name = 'Battery'; EstimatedChargeRemaining = $m.battery.charge; BatteryStatus = 2; Status = 'OK' })
        }
        'win32_perfformatteddata_perfos_processor' {
            New-CimObject 'Win32_PerfFormattedData_PerfOS_Processor' ([ordered]@{
                Name = '_Total'; PercentProcessorTime = $m.cpu.load })
        }
        'win32_perfformatteddata_perfos_memory' {
            New-CimObject 'Win32_PerfFormattedData_PerfOS_Memory' ([ordered]@{
                AvailableMBytes = [int] ($m.memory.free_kb / 1024); CommittedBytes = [uint64] ($m.memory.total_kb - $m.memory.free_kb) * 1KB })
        }
        default { $null }
    }
}

# A WQL condition as Windows takes it: comparisons joined by AND / OR.
function Test-WqlCondition($Object, [string] $Condition) {
    if (-not $Condition) { return $true }
    $alternatives = $Condition -split '(?i)\s+OR\s+'
    foreach ($alternative in $alternatives) {
        $all = $true
        foreach ($part in ($alternative -split '(?i)\s+AND\s+')) {
            if ($part -notmatch "^\s*\(?\s*(\w+)\s*(=|<>|!=|>=|<=|>|<|(?i:LIKE))\s*'?([^']*)'?\s*\)?\s*$") { $all = $false; break }
            $value, $operator, $wanted = $Object.($Matches[1]), $Matches[2].ToUpperInvariant(), $Matches[3]
            $number = 0.0
            $numeric = [double]::TryParse($wanted, [ref] $number) -and $value -is [ValueType]
            $ok = switch ($operator) {
                '=' { if ($numeric) { [double] $value -eq $number } else { "$value" -eq $wanted } }
                { $_ -in '<>', '!=' } { if ($numeric) { [double] $value -ne $number } else { "$value" -ne $wanted } }
                '>' { [double] $value -gt $number }
                '<' { [double] $value -lt $number }
                '>=' { [double] $value -ge $number }
                '<=' { [double] $value -le $number }
                'LIKE' { "$value" -like ($wanted -replace '%', '*' -replace '_', '?') }
            }
            if (-not $ok) { $all = $false; break }
        }
        if ($all) { return $true }
    }
    $false
}

function Get-CimInstance {
    [CmdletBinding(DefaultParameterSetName = 'ClassName')]
    param(
        [Parameter(Position = 0, ParameterSetName = 'ClassName', Mandatory)] [string] $ClassName,
        [Parameter(ParameterSetName = 'ClassName')] [string] $Filter,
        [Parameter(ParameterSetName = 'Query', Mandatory)] [string] $Query,
        [Parameter(ParameterSetName = 'Query')] [string] $QueryDialect = 'WQL',
        [string[]] $Property, [string] $Namespace = 'root/cimv2', [string[]] $ComputerName,
        [uint32] $OperationTimeoutSec, [switch] $Shallow, [object[]] $CimSession
    )
    if ($PSCmdlet.ParameterSetName -eq 'Query') {
        if ($Query -notmatch '(?i)^\s*SELECT\s+.+?\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+))?\s*$') {
            Write-Error -Message '無效的查詢 ' -Category InvalidArgument; return
        }
        $ClassName, $Filter = $Matches[1], $Matches[2]
    }
    if ($Namespace -notmatch '(?i)^root[\\/]cimv2$') { Write-Error -Message '無效的命名空間 ' -Category ObjectNotFound; return }
    $objects = @(Get-LaptopClass $ClassName)
    if (-not $objects -or $null -eq $objects[0]) { Write-Error -Message '無效的類別 ' -Category MetadataError; return }
    $objects | Where-Object { Test-WqlCondition $_ $Filter }
}

function Get-ComputerInfo {
    [CmdletBinding()] param([Parameter(Position = 0)] [string[]] $Property)
    $m = Get-LaptopFacts
    $info = [pscustomobject] [ordered]@{
        WindowsProductName = $m.os.caption -replace '^Microsoft ', ''; WindowsVersion = '2009'; WindowsBuildLabEx = "$($m.os.build).1.amd64fre.ge_release.240331-1435"
        BiosManufacturer = 'American Megatrends International, LLC.'; BiosSMBIOSBIOSVersion = $m.system.bios
        CsName = $m.computer; CsManufacturer = $m.system.maker; CsModel = $m.system.model; CsSystemType = 'x64-based PC'
        CsNumberOfLogicalProcessors = $m.cpu.threads; CsNumberOfProcessors = 1; CsProcessors = @($m.cpu.name)
        CsTotalPhysicalMemory = [uint64] $m.memory.total_kb * 1024; CsPhyicallyInstalledMemory = [uint64] $m.memory.dimm_mb * 1024
        OsName = $m.os.caption; OsVersion = $m.os.version; OsBuildNumber = $m.os.build; OsArchitecture = $m.os.architecture
        OsLanguage = 'zh-TW'; OsLastBootUpTime = (Get-Date).AddHours(-$m.os.uptime_hours)
        OsUptime = [timespan]::FromHours($m.os.uptime_hours); OsTotalVisibleMemorySize = [uint64] $m.memory.total_kb
        OsFreePhysicalMemory = [uint64] $m.memory.free_kb; TimeZone = '(UTC+08:00) 台北'
    }
    if ($Property) { $info | Select-Object -Property $Property } else { $info }
}

function Get-PhysicalDisk {
    [CmdletBinding()] param([Parameter(Position = 0)] [string] $FriendlyName, [string] $SerialNumber)
    $m = Get-LaptopFacts
    $disk = [pscustomobject] [ordered]@{
        DeviceId = '0'; FriendlyName = $m.disk.model; SerialNumber = '0025_38B1_21C0_' + $m.computer.Substring(7)
        MediaType = 'SSD'; BusType = 'NVMe'; CanPool = $false; OperationalStatus = 'OK'; HealthStatus = 'Healthy'
        Usage = 'Auto-Select'; Size = [uint64] $m.disk.bytes; SpindleSpeed = 0; Model = $m.disk.model; FirmwareVersion = '3L1QFXV7'
    }
    if (-not $FriendlyName -or $disk.FriendlyName -like $FriendlyName) { $disk }
}

function Get-Disk {
    [CmdletBinding()] param([Parameter(Position = 0)] [uint32[]] $Number)
    $m = Get-LaptopFacts
    if ($Number -and 0 -notin $Number) { return }
    [pscustomobject] [ordered]@{
        Number = 0; FriendlyName = $m.disk.model; SerialNumber = '0025_38B1_21C0_' + $m.computer.Substring(7)
        HealthStatus = 'Healthy'; OperationalStatus = 'Online'; BusType = 'NVMe'; PartitionStyle = 'GPT'
        Size = [uint64] $m.disk.bytes; AllocatedSize = [uint64] $m.disk.bytes; IsBoot = $true; IsSystem = $true
    }
}

function Get-Partition {
    [CmdletBinding()] param([uint32] $DiskNumber, [char] $DriveLetter)
    $m = Get-LaptopFacts
    $parts = @(
        [pscustomobject] [ordered]@{ DiskNumber = 0; PartitionNumber = 1; DriveLetter = [char] 0; Type = 'System'; Size = [uint64] 272629760; Offset = 1048576 }
        [pscustomobject] [ordered]@{ DiskNumber = 0; PartitionNumber = 2; DriveLetter = [char] 0; Type = 'Reserved'; Size = [uint64] 16777216; Offset = 273678336 }
        [pscustomobject] [ordered]@{ DiskNumber = 0; PartitionNumber = 3; DriveLetter = [char] 'C'; Type = 'Basic'; Size = [uint64] $m.disk.c_bytes; Offset = 290455552 }
        [pscustomobject] [ordered]@{ DiskNumber = 0; PartitionNumber = 4; DriveLetter = [char] 0; Type = 'Recovery'; Size = [uint64] 1073741824; Offset = 511123836928 }
    )
    $parts | Where-Object { (-not $PSBoundParameters.ContainsKey('DriveLetter') -or $_.DriveLetter -eq $DriveLetter) }
}

function Get-Volume {
    [CmdletBinding()] param([Parameter(Position = 0)] [char[]] $DriveLetter)
    $m = Get-LaptopFacts
    if ($DriveLetter -and 'C' -notin $DriveLetter) { Write-Error -Message "找不到磁碟機代號為 '$DriveLetter' 的 MSFT_Volume 物件。" -Category ObjectNotFound; return }
    [pscustomobject] [ordered]@{
        DriveLetter = 'C'; FriendlyName = 'Windows'; FileSystemType = 'NTFS'; DriveType = 'Fixed'
        HealthStatus = 'Healthy'; OperationalStatus = 'OK'; SizeRemaining = [uint64] $m.disk.c_free; Size = [uint64] $m.disk.c_bytes
    }
}

Export-ModuleMember -Function Get-CimInstance, Get-ComputerInfo, Get-PhysicalDisk, Get-Disk, Get-Partition, Get-Volume
