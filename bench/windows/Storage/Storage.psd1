@{
    # The laptop's Windows commands for the bench sandbox (LaptopFacade/Laptop.ps1).
    RootModule = 'Storage.psm1'
    ModuleVersion = '1.0.0'
    GUID = 'f2442294-8324-5748-8d83-516c4a02965f'
    PowerShellVersion = '7.0'
    FunctionsToExport = @('Get-PhysicalDisk', 'Get-Disk', 'Get-Partition', 'Get-Volume')
    CmdletsToExport = @()
    AliasesToExport = @()
    VariablesToExport = @()
}
