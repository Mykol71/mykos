# Run elevated during unattended image provisioning, before completion is marked.
$ErrorActionPreference = 'Stop'
$msi = Join-Path $PSScriptRoot 'qemu-ga-x86_64.msi'
if (-not (Get-Service qemu-ga -ErrorAction SilentlyContinue)) {
    if (-not (Test-Path $msi)) { throw 'QEMU Guest Agent MSI is missing' }
    # The installer is extracted only after the host verifies Red Hat's RPM
    # signature against its trusted RPM key database. Bind this staged copy to
    # that authenticated payload; do not accept the standalone unsigned download.
    $record = Get-Content (Join-Path $PSScriptRoot 'qemu-ga-x86_64.json') -Raw | ConvertFrom-Json
    if ($record.authentication -ne 'trusted-rhel-rpm-signature' -or
        $record.rpm -notmatch '^virtio-win-.*\.rpm$' -or
        $record.rpm_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $record.sha256 -notmatch '^[0-9a-f]{64}$' -or
        (Get-FileHash $msi -Algorithm SHA256).Hash.ToLowerInvariant() -ne $record.sha256) {
        throw 'Guest agent does not match the payload extracted from the trusted signed Red Hat RPM'
    }
    $signature = Get-AuthenticodeSignature $msi
    if ($signature.Status -notin @('Valid','NotSigned')) {
        throw 'QEMU Guest Agent package has an invalid Authenticode signature'
    }
    if ($signature.Status -eq 'Valid' -and $signature.SignerCertificate.Subject -notmatch 'Red Hat|Fedora|QEMU') {
        throw 'Unexpected QEMU Guest Agent publisher'
    }
    $arguments = '/i "' + $msi + '" /qn /norestart /l*v "' + (Join-Path $PSScriptRoot 'mykos-agent-msi.log') + '"'
    $process = Start-Process msiexec.exe -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -notin @(0,3010)) { throw "QEMU Guest Agent installation failed: $($process.ExitCode)" }
}
Set-Service qemu-ga -StartupType Automatic
Start-Service qemu-ga
if ((Get-Service qemu-ga).Status -ne 'Running') { throw 'QEMU Guest Agent did not start' }
Write-Output 'QEMU Guest Agent installed and running.'
