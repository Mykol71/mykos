# Image-build provisioning only. Run elevated, reboot and verify before capture.
param([Parameter(Mandatory=$true)][ValidateRange(1024,65536)][int]$SizeMiB)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run elevated inside the Windows guest' }
$computer = Get-CimInstance Win32_ComputerSystem
$drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$env:SystemDrive'"
if ($drive.FreeSpace -lt ([int64]$SizeMiB * 1MB + 1GB)) { throw 'Not enough free disk space for the requested pagefile plus 1 GiB headroom' }
$backup = Join-Path $env:ProgramData 'MYKOS-pagefile-before.json'
if (-not (Test-Path $backup)) {
    @{AutomaticManagedPagefile=$computer.AutomaticManagedPagefile; Settings=@(Get-CimInstance Win32_PageFileSetting | Select-Object Name,InitialSize,MaximumSize)} | ConvertTo-Json -Depth 4 | Set-Content $backup
}
Set-CimInstance -InputObject $computer -Property @{AutomaticManagedPagefile=$false} | Out-Null
$path = "$env:SystemDrive\pagefile.sys"
$settings = @(Get-CimInstance Win32_PageFileSetting)
$target = $settings | Where-Object { $_.Name -eq $path }
if ($target) {
    $target | Set-CimInstance -Property @{InitialSize=[uint32]$SizeMiB; MaximumSize=[uint32]$SizeMiB} | Out-Null
} else {
    New-CimInstance -ClassName Win32_PageFileSetting -Property @{Name=$path; InitialSize=[uint32]$SizeMiB; MaximumSize=[uint32]$SizeMiB} | Out-Null
}
# Enforce one declared pagefile; retain all original settings in the recovery JSON.
$settings | Where-Object { $_.Name -ne $path } | Remove-CimInstance
Write-Output "Pagefile configured to $SizeMiB MiB. Reboot and verify Win32_PageFileUsage before live capture."
