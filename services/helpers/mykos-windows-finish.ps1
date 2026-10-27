param([Parameter(Mandatory=$true)][string]$UserName)
$ErrorActionPreference = 'Stop'
# A valid non-expired local account prevents Server's mandatory password-change
# screen from interrupting unattended first logon.
foreach ($name in @('Administrator', $UserName)) {
    $account = [ADSI]("WinNT://./" + $name + ",user")
    $account.PasswordExpired = 0
    $account.SetInfo()
    $flags = [int]$account.UserFlags.Value
    $account.UserFlags = ($flags -bor 0x10000)
    $account.SetInfo()
}
$folder = Join-Path $env:ProgramData 'MYKOS'
New-Item -ItemType Directory -Path $folder -Force | Out-Null
# Installed Windows and OEM provisioning, not Windows PE, must reach this point.
if ($env:SystemDrive -ne 'C:') { throw 'Installed Windows is required' }
Set-Content -Path (Join-Path $folder 'image-install-complete') -Value 'Installed and provisioned'
