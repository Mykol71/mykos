@echo off
setlocal
wpeinit
if errorlevel 1 goto failed
set "CURL=X:\Windows\System32\curl.exe"
set "CURL_CA_BUNDLE=X:\Windows\System32\curl-ca-bundle.crt"
set "JOB=@@ORIGIN@@/mykos/pxe/jobs/@@ID@@"
set "TICKET=@@TICKET@@"
X:\Windows\System32\mykos-pe-guard.exe @@MAC@@ 68719476736 @@EMPTY@@
if errorlevel 1 goto failed
REM Claim the destructive phase before cleaning disk0. PXE reboot cannot rearm this job.
"%CURL%" --silent --show-error --fail --connect-timeout 15 --max-time 30 -H "X-MYKOS-PXE-Ticket: %TICKET%" -H "Content-Type: application/json" --data "{\"state\":\"applying\"}" "%JOB%/status"
if errorlevel 1 goto failed
> X:\mykos-partitions.txt echo select disk 0
>>X:\mykos-partitions.txt echo clean
>>X:\mykos-partitions.txt echo convert gpt
>>X:\mykos-partitions.txt echo create partition efi size=260
>>X:\mykos-partitions.txt echo format quick fs=fat32 label=System
>>X:\mykos-partitions.txt echo assign letter=S
>>X:\mykos-partitions.txt echo create partition msr size=16
>>X:\mykos-partitions.txt echo create partition primary
>>X:\mykos-partitions.txt echo shrink minimum=1024
>>X:\mykos-partitions.txt echo format quick fs=ntfs label=Windows
>>X:\mykos-partitions.txt echo assign letter=W
>>X:\mykos-partitions.txt echo create partition primary
>>X:\mykos-partitions.txt echo format quick fs=ntfs label=Recovery
>>X:\mykos-partitions.txt echo assign letter=R
>>X:\mykos-partitions.txt echo set id=de94bba4-06d1-4d40-a16a-bfd50179d6ac
>>X:\mykos-partitions.txt echo gpt attributes=0x8000000000000001
diskpart /s X:\mykos-partitions.txt
if errorlevel 1 goto failed
if not exist W:\ goto failed
set "MYKOS_DISK_PREPARED=1"
mkdir W:\.mykos
"%CURL%" --silent --show-error --fail --retry 3 --connect-timeout 15 --max-time 14400 -H "X-MYKOS-PXE-Ticket: %TICKET%" --output W:\.mykos\install.wim "%JOB%/install.wim"
if errorlevel 1 goto failed
dism /Apply-Image /ImageFile:W:\.mykos\install.wim /Index:@@INDEX@@ /ApplyDir:W:\ /CheckIntegrity /Verify
if errorlevel 1 goto failed
mkdir W:\Windows\Panther
"%CURL%" --silent --show-error --fail --retry 2 --max-time 120 -H "X-MYKOS-PXE-Ticket: %TICKET%" --output W:\Windows\Panther\unattend.xml "%JOB%/answer.xml"
if errorlevel 1 goto failed
mkdir W:\ProgramData\MYKOS
"%CURL%" --silent --show-error --fail --retry 2 --max-time 120 -H "X-MYKOS-PXE-Ticket: %TICKET%" --output W:\ProgramData\MYKOS\Stage.ps1 "%JOB%/stage.ps1"
if errorlevel 1 goto failed
bcdboot W:\Windows /s S: /f UEFI
if errorlevel 1 goto failed
if exist W:\Windows\System32\Recovery\Winre.wim (
 mkdir R:\Recovery\WindowsRE
 copy /y W:\Windows\System32\Recovery\Winre.wim R:\Recovery\WindowsRE\Winre.wim
 W:\Windows\System32\reagentc.exe /setreimage /path R:\Recovery\WindowsRE /target W:\Windows
 if errorlevel 1 goto failed
)
del /f W:\.mykos\install.wim
rmdir W:\.mykos
wpeutil reboot
exit /b 0
:failed
if defined MYKOS_DISK_PREPARED del /f W:\Windows\Panther\unattend.xml W:\ProgramData\MYKOS\Stage.ps1 2>nul
echo MYKOS staging stopped. The job will not automatically reinstall this desktop.
if defined JOB "%CURL%" --silent --show-error --fail --max-time 20 -H "X-MYKOS-PXE-Ticket: %TICKET%" -H "Content-Type: application/json" --data "{\"state\":\"failed\"}" "%JOB%/status"
echo Inspect this console before explicitly rearming the job.
exit /b 1
