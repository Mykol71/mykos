# A pinned regular Tiny11 builder, with a serviceable security-preserving policy.
# Upstream: https://github.com/ntdevlabs/tiny11builder (ntdevlabs)
# Core is intentionally unsupported. This runs only inside the Windows build VM.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$IsoPath,
    [Parameter(Mandatory=$true)][int]$Index,
    [Parameter(Mandatory=$true)][string]$Workspace
)
$ErrorActionPreference='Stop'
$ProgressPreference = 'SilentlyContinue'
$commit='8909da14b783e9625bfabc7ab2cf16e233b00a21'
$sourceHash='7a7baffa75742d9ae9512936d72887931c8bc2a91ac16ccd451a8869752b6f5e'
$answerHash='ed9837ab4a19c812d28e20f5278ca5bb25815a6a01d1caddbce157be5d519dba'
$principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator is required in the build VM'}
$Workspace=[IO.Path]::GetFullPath($Workspace)
if($Workspace.TrimEnd('\') -eq [IO.Path]::GetPathRoot($Workspace).TrimEnd('\') -or $Workspace.StartsWith($env:WINDIR,[StringComparison]::OrdinalIgnoreCase)){throw 'Use a dedicated build workspace'}
if(Test-Path $Workspace){if(Get-ChildItem $Workspace -Force){throw 'Use an empty workspace; interrupted builds are retained for diagnosis'}}
New-Item -ItemType Directory $Workspace -Force|Out-Null
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$upstream=Join-Path $Workspace 'tiny11-upstream.ps1'
Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/ntdevlabs/tiny11builder/$commit/tiny11maker.ps1" -OutFile $upstream
if((Get-FileHash $upstream -Algorithm SHA256).Hash.ToLower() -ne $sourceHash){throw 'Tiny11 source hash mismatch'}
$answer=Join-Path $Workspace 'autounattend.xml'
Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/ntdevlabs/tiny11builder/$commit/autounattend.xml" -OutFile $answer
if((Get-FileHash $answer -Algorithm SHA256).Hash.ToLower() -ne $answerHash){throw 'Tiny11 answer-file hash mismatch'}
# Use installed Microsoft deployment tools or the exact Microsoft-signed,
# hash-pinned symbol-server binary approved by the pinned upstream builder.
$oscdimg=Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\Assessment and Deployment Kit\Deployment Tools\amd64\Oscdimg\oscdimg.exe'
if(-not (Test-Path $oscdimg)){
    $oscdimg=Join-Path $Workspace 'oscdimg.exe'
    Invoke-WebRequest -UseBasicParsing 'https://msdl.microsoft.com/download/symbols/oscdimg.exe/688CABB065000/oscdimg.exe' -OutFile $oscdimg
    if((Get-FileHash $oscdimg -Algorithm SHA256).Hash -ne '2000160B2C5044691B2F9A0AC308E5207F273D4880A572457AF16D05886BA861'){throw 'Microsoft deployment tool hash mismatch'}
    if((Get-AuthenticodeSignature $oscdimg).Status -ne 'Valid'){throw 'Microsoft deployment tool signature validation failed'}
}
$source=Get-Content $upstream -Raw
function Replace-Once([string]$before,[string]$after){
    if(([regex]::Matches($script:source,[regex]::Escape($before))).Count -ne 1){throw "Pinned Tiny11 patch no longer matches: $before"}
    $script:source=$script:source.Replace($before,$after)
}
# Preserve shared Edge/WebView runtimes that the desktop application installer may need.
$start=$source.IndexOf('Write-Output "Removing Edge:"')
$end=$source.IndexOf('Write-Output "Removing OneDrive:"',$start)
if($start -lt 0 -or $end -lt $start){throw 'Pinned Edge-preservation patch mismatch'}
$source=$source.Substring(0,$start)+'Write-Output "MYKOS: keeping Edge and WebView runtimes"'+"`r`n"+$source.Substring($end)
# WIM media has no install.esd. Skip that optional upstream cleanup while
# retaining strict error handling for actual build operations.
$esdCleanup='Set-ItemProperty -Path "$ScratchDisk\tiny11\sources\install.esd" -Name IsReadOnly -Value $false > $null 2>&1'+"`n"+'Remove-Item "$ScratchDisk\tiny11\sources\install.esd" > $null 2>&1'
$source=$source.Replace("`r`n","`n")
Replace-Once '$Host.UI.RawUI.WindowTitle = "Tiny11 image creator"' '# MYKOS unattended builder has no interactive console window.'
$source=[regex]::Replace($source,'(?m)^Clear-Host[ \t]*$','# MYKOS unattended builder retains its diagnostic output.')
Replace-Once $esdCleanup ('if (Test-Path "$ScratchDisk\tiny11\sources\install.esd") {'+"`n"+$esdCleanup+"`n"+'}')
Replace-Once '/StartComponentCleanup /ResetBase' '/StartComponentCleanup'
Replace-Once "'DontOfferThroughWUAU' 'REG_DWORD' '1'" "'DontOfferThroughWUAU' 'REG_DWORD' '0'"
# Leave device encryption policy and original diagnostic tasks intact.
$start=$source.IndexOf('Write-Output "Disabling BitLocker Device Encryption"')
$end=$source.IndexOf('Write-Output "Disabling Chat icon:"',$start)
if($start -lt 0 -or $end -lt $start){throw 'Pinned encryption-policy patch mismatch'}
$source=$source.Remove($start,$end-$start)
$start=$source.IndexOf('Write-Host "Deleting scheduled task definition files..."')
$end=$source.IndexOf('Write-Host "Unmounting Registry..."',$start)
if($start -lt 0 -or $end -lt $start){throw 'Pinned diagnostics-preservation patch mismatch'}
$source=$source.Remove($start,$end-$start)
$patched=Join-Path $Workspace 'tiny11-mykos.ps1'
[IO.File]::WriteAllText($patched,$source,[Text.UTF8Encoding]::new($false))
$mounted=$null
try {
    $mounted=Mount-DiskImage -ImagePath ([IO.Path]::GetFullPath($IsoPath)) -Access ReadOnly -PassThru
    $volume=$mounted|Get-Volume
    if(-not $volume.DriveLetter){throw 'Installation ISO has no drive letter'}
    $letter=[string]$volume.DriveLetter
    $mediaImage=Join-Path ($letter+':\') 'sources\install.wim'
    if(-not(Test-Path $mediaImage)){$mediaImage=Join-Path ($letter+':\') 'sources\install.esd'}
    $info=Get-WindowsImage -ImagePath $mediaImage -Index $Index
    if($info.Version.Build -lt 26200){throw 'Use a supported Windows 11 25H2/26H2 source, not end-of-servicing Pro media'}
    # Original Tiny11 prompts are supplied deterministically; unexpected prompts stop.
    function Read-Host { param([string]$Prompt)
        if($Prompt -match 'image index'){return [string]$Index}
        if($Prompt -match 'Press Enter'){return ''}
        throw "Unexpected upstream prompt: $Prompt"
    }
    & $patched -ISO $letter
    if(-not(Test-Path (Join-Path $Workspace 'tiny11.iso'))){throw 'Tiny11 did not produce an ISO'}
} finally { if($mounted){Dismount-DiskImage -ImagePath $IsoPath -ErrorAction SilentlyContinue|Out-Null} }
$out=Join-Path $Workspace 'tiny11.iso'
$check=Mount-DiskImage -ImagePath $out -Access ReadOnly -PassThru
try {
    $drive=([string](($check|Get-Volume).DriveLetter))+':\'
    $wim=Join-Path $drive 'sources\install.wim'
    $info=Get-WindowsImage -ImagePath $wim -Index 1
    $report=@{kind='tiny11';core=$false;servicing_preserved=$true;upstream_commit=$commit;upstream_sha256=$sourceHash;patched_sha256=(Get-FileHash $patched -Algorithm SHA256).Hash.ToLower();source_iso_sha256=(Get-FileHash $IsoPath -Algorithm SHA256).Hash.ToLower();iso_sha256=(Get-FileHash $out -Algorithm SHA256).Hash.ToLower();install_wim_sha256=(Get-FileHash $wim -Algorithm SHA256).Hash.ToLower();windows_version=$info.Version.ToString();created=(Get-Date).ToUniversalTime().ToString('o');policy='Keep Defender/Update/recovery/printing/SMB/ODBC/WebView; no Core or ResetBase'}
    $report|ConvertTo-Json|Set-Content (Join-Path $Workspace 'MYKOS-Tiny11-build.json')
} finally {Dismount-DiskImage -ImagePath $out|Out-Null}
Write-Output 'Tiny11 ISO and matching build report are ready for MYKOS media import.'
