[CmdletBinding()]
param(
    [string]$Name = 'mykos-vpn',
    [string]$Image = 'mykos-vpn:20261025'
)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command wslc.exe -ErrorAction SilentlyContinue)) {
    throw 'wslc.exe is missing. Install/update Microsoft WSL Containers, then rerun this script.'
}
& wslc.exe version
if ($LASTEXITCODE -ne 0) { throw 'Cannot query WSL Containers. Check its installation and nested virtualization support.' }
$helpText = (& wslc.exe run --help 2>&1 | Out-String)
if ($helpText -notmatch '--cap-add') {
    throw 'This wslc version cannot grant NET_ADMIN. Libreswan needs that capability; update to a release supporting --cap-add before deployment.'
}
$previousPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    & wslc.exe container inspect $Name 2>$null
    $alreadyExists = ($LASTEXITCODE -eq 0)
} finally {
    $ErrorActionPreference = $previousPreference
}
if ($alreadyExists) { throw "Container '$Name' already exists. It has been left intact." }
& wslc.exe build -t $Image $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'VPN tools image build failed.' }
& wslc.exe run --rm --cap-add NET_ADMIN --cap-add NET_RAW $Image check
if ($LASTEXITCODE -ne 0) { throw 'The WSL container kernel/capability check failed. Deployment stopped.' }
& wslc.exe run -d --name $Name --cap-add NET_ADMIN --cap-add NET_RAW --cap-add IPC_LOCK $Image
if ($LASTEXITCODE -ne 0) { throw 'VPN tools container did not start.' }
$ready = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    & wslc.exe exec $Name ipsec whack --status
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 1
}
& wslc.exe container logs $Name
if (-not $ready) { throw 'Libreswan did not become ready. Its container and logs have been retained for diagnosis.' }
Write-Host 'Libreswan and dhclient are installed. No VPN peers, secrets, DHCP request, Windows routes, or firewall changes have been configured.'
