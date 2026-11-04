$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$cfg = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('@@CONFIG@@')) | ConvertFrom-Json
$headers = @{'X-MYKOS-PXE-Ticket'=$cfg.ticket}
$job = $cfg.origin + '/mykos/pxe/jobs/' + $cfg.job
$root = Join-Path $env:ProgramData 'MYKOS'
New-Item -ItemType Directory -Path $root -Force | Out-Null
# Restrict the lease-bearing script and downloaded installers to SYSTEM/admins.
& icacls.exe $root /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect staging directory' }
function Phase([string]$value) {
    Invoke-RestMethod -Uri ($job+'/status') -Method Post -Headers $headers -ContentType 'application/json' -Body (@{state=$value}|ConvertTo-Json) -TimeoutSec 30 | Out-Null
}
function Get-Asset([string]$name) {
    $asset=$cfg.assets.PSObject.Properties[$name].Value
    if (-not $asset) { throw "Missing approved asset: $name" }
    $path=Join-Path $root $name
    if (-not (Test-Path $path) -or (Get-FileHash $path -Algorithm SHA256).Hash.ToLower() -ne $asset.sha256) {
        Invoke-WebRequest -UseBasicParsing -Uri ($cfg.origin+'/mykos/pxe/assets/'+$asset.sha256+'/'+$name) -OutFile $path -TimeoutSec 1800
    }
    if ((Get-FileHash $path -Algorithm SHA256).Hash.ToLower() -ne $asset.sha256) { throw "Asset hash mismatch: $name" }
    return $path
}
function Run-Installer([string]$path,[string]$arguments) {
    $process=Start-Process -FilePath $path -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -notin @(0,3010)) { throw "Installer failed ($($process.ExitCode)): $([IO.Path]::GetFileName($path))" }
    if ($process.ExitCode -eq 3010) { $script:reboot=$true }
}
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    # Remove temporary automatic logon and cached answer files after provisioning.
    $logon='HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    Set-ItemProperty $logon AutoAdminLogon '0'
    Remove-ItemProperty $logon DefaultPassword -ErrorAction SilentlyContinue
    Remove-ItemProperty $logon AutoLogonCount -ErrorAction SilentlyContinue
    Remove-Item 'C:\Windows\Panther\unattend.xml','C:\Windows\Panther\Unattend\Unattend.xml' -Force -ErrorAction SilentlyContinue
    Phase 'firstboot'
    Set-Content (Join-Path $root 'stage-started') -Value (Get-Date).ToString('o')
    $chrome=Get-Asset 'chrome.msi'
    $sig=Get-AuthenticodeSignature $chrome
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'O=Google (LLC|Inc\.)') { throw 'Chrome installer publisher verification failed' }
    Run-Installer "$env:SystemRoot\System32\msiexec.exe" ('/i "'+$chrome+'" /qn /norestart /L*v "'+(Join-Path $root 'chrome-install.log')+'"')
    # Keep automatic Chrome updates; do not install a fixed-version update policy.
    foreach ($service in @('gupdate','gupdatem','GoogleUpdaterService','GoogleUpdaterInternalService')) {
        Get-Service ($service+'*') -ErrorAction SilentlyContinue | Set-Service -StartupType Automatic
    }
    Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True
    Set-MpPreference -DisableRealtimeMonitoring $false -DisableBehaviorMonitoring $false -DisableIOAVProtection $false -DisableScriptScanning $false
    Start-Service WinDefend
    Update-MpSignature
    $defender=Get-MpComputerStatus
    if (-not $defender.AntivirusEnabled -or -not $defender.RealTimeProtectionEnabled) { throw 'Defender real-time protection is not active' }
    # Printing and modern file sharing are retained; SMB1 is deliberately not enabled.
    Set-Service Spooler -StartupType Automatic
    Start-Service Spooler
    Start-Service LanmanWorkstation
    Start-Service LanmanServer
    # Preserve supported Windows servicing and schedule a security/update scan.
    foreach ($service in @('wuauserv','BITS','TrustedInstaller')) {
        if ((Get-Service $service).StartType -eq 'Disabled') { Set-Service $service -StartupType Manual }
    }
    $update=New-Object -ComObject Microsoft.Update.AutoUpdate
    $update.DetectNow()
    $report=@{customer=$cfg.customer;profile=$cfg.profile;hostname=$env:COMPUTERNAME;chrome=(Get-Item "$env:ProgramFiles\Google\Chrome\Application\chrome.exe").VersionInfo.ProductVersion;defender=$true;firewall=$true;completed=(Get-Date).ToString('o')}
    $report|ConvertTo-Json|Set-Content (Join-Path $root 'stage-result.json')
    Phase 'complete'
    Move-Item (Join-Path $root 'stage-result.json') (Join-Path $root 'stage-complete.json') -Force
    Remove-Item $PSCommandPath -Force
    if ($reboot) { Restart-Computer -Force }
} catch {
    # Keep a useful local error; never write the configuration, ticket or password.
    $_.Exception.Message|Set-Content (Join-Path $root 'stage-error.txt')
    try { Phase 'failed' } catch {}
    Remove-Item $PSCommandPath -Force -ErrorAction SilentlyContinue
    throw
}
