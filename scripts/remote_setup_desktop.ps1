#Requires -RunAsAdministrator
<#
ALEX 데스크톱 원격 준비 (ADR-052). 관리자 PowerShell 에서 실행한다.

  powershell -ExecutionPolicy Bypass -File scripts\remote_setup_desktop.ps1
  powershell -ExecutionPolicy Bypass -File scripts\remote_setup_desktop.ps1 -PublicKey "ssh-ed25519 AAAA... alex-laptop"

하는 일
  1. OpenSSH 서버 설치, 윈도우 시작 때 자동 실행
  2. 방화벽: SSH(22번)를 Tailscale 주소에서 오는 접속만 받는다
  3. 노트북 공개키 등록(-PublicKey 를 주면)
  4. 로그인할 때 ALEX 를 창 없이 자동 실행
  5. 노트북에 넣을 '데스크톱 이름' 과 '사용자 이름' 을 알려 준다

하지 않는 일
  - ALEX 를 네트워크에 열지 않는다. ALEX 는 계속 127.0.0.1 에서만 듣는다. 노트북은 SSH 터널로 들어온다.
  - 여기서 ALEX 를 켜지 않는다. 이 창은 관리자 권한이라, 여기서 켜면 ALEX 가 관리자로 돈다.

다시 실행해도 된다 — 이미 된 것은 건너뛰고, 키는 중복 없이 더한다.
이 파일은 UTF-8 BOM 으로 저장한다. PowerShell 5.1 은 BOM 이 없으면 한글을 ANSI 로 읽어 깨뜨린다.
#>
param(
    [string]$PublicKey = "",
    [string]$AlexRoot = ""
)
$ErrorActionPreference = "Stop"
if (-not $AlexRoot) { $AlexRoot = Split-Path -Parent $PSScriptRoot }
# Tailscale 이 기기에 주는 주소 범위. 집 공유기의 다른 기기나 인터넷에서는 SSH 에 닿지 못한다.
$TailscaleRanges = @("100.64.0.0/10", "fd7a:115c:a1e0::/48")

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "== $Text" -ForegroundColor Cyan
}

Write-Step "1/5 OpenSSH 서버"
$capability = Get-WindowsCapability -Online | Where-Object { $_.Name -like "OpenSSH.Server*" } | Select-Object -First 1
if (-not $capability) { throw "이 윈도우에서 OpenSSH 서버 기능을 찾지 못했습니다." }
if ($capability.State -ne "Installed") {
    Write-Host "설치합니다. 몇 분 걸릴 수 있습니다..."
    Add-WindowsCapability -Online -Name $capability.Name | Out-Null
}
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
Write-Host "sshd: $((Get-Service sshd).Status)"

Write-Step "2/5 방화벽 - Tailscale 안에서만"
$rule = Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue
if ($rule) {
    Set-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -RemoteAddress $TailscaleRanges -Profile Any
} else {
    New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (ALEX, Tailscale only)" `
        -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -Profile Any `
        -RemoteAddress $TailscaleRanges | Out-Null
}
Write-Host "SSH 는 이 주소에서 오는 접속만 받습니다: $($TailscaleRanges -join ', ')"

Write-Step "3/5 노트북 키"
if ($PublicKey) {
    $PublicKey = $PublicKey.Trim()
    if ($PublicKey -notmatch '^ssh-ed25519 [A-Za-z0-9+/]+=*( [ -~]*)?$') {
        throw "공개키 모양이 아닙니다. 노트북의 connect_desktop.cmd setup 이 알려 준 줄 전체를 따옴표 안에 넣으십시오."
    }
    # 관리자 계정이면 sshd 는 C:\ProgramData\ssh\administrators_authorized_keys 를 읽고(윈도우 기본값),
    # 일반 계정이면 %USERPROFILE%\.ssh\authorized_keys 를 읽는다. 둘 다에 넣는다 - 해롭지 않다.
    $adminKeys = Join-Path $env:ProgramData "ssh\administrators_authorized_keys"
    $userKeys = Join-Path $env:USERPROFILE ".ssh\authorized_keys"
    foreach ($file in @($adminKeys, $userKeys)) {
        $dir = Split-Path -Parent $file
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
        $existing = @()
        if (Test-Path $file) { $existing = @(Get-Content -Path $file) }
        # ASCII 로 쓴다. UTF-8 로 쓰면 PowerShell 5.1 이 BOM 을 붙이고, sshd 가 첫 줄을 못 읽는다.
        if ($existing -notcontains $PublicKey) { Add-Content -Path $file -Value $PublicKey -Encoding ASCII }
    }
    # administrators_authorized_keys 는 Administrators 와 SYSTEM 만 가져야 sshd 가 받아 준다.
    # 그룹 이름은 윈도우 언어마다 달라서 SID 로 준다.
    & icacls.exe $adminKeys /inheritance:r /grant "*S-1-5-32-544:F" /grant "*S-1-5-18:F" | Out-Null
    Write-Host "노트북 키를 등록했습니다."
} else {
    Write-Host "키를 받지 않았습니다. 노트북에서 connect_desktop.cmd setup 을 실행하면 등록 명령을 알려 줍니다."
    Write-Host "키가 없어도 데스크톱 윈도우 비밀번호로 접속할 수 있습니다."
}

Write-Step "4/5 로그인할 때 ALEX 자동 실행"
$vbs = Join-Path $AlexRoot "start_alex_background.vbs"
if (-not (Test-Path $vbs)) {
    throw "start_alex_background.vbs 를 찾지 못했습니다: $vbs  (-AlexRoot 로 ALEX 폴더를 지정하십시오)"
}
$startup = [Environment]::GetFolderPath("Startup")
$link = Join-Path $startup "ALEX (background).lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"
$shortcut.Arguments = '"' + $vbs + '"'
$shortcut.WorkingDirectory = $AlexRoot
$shortcut.Description = "ALEX background start for remote access (ADR-052)"
$shortcut.Save()
Write-Host "다음 로그인부터 ALEX 가 창 없이 자동으로 켜집니다: $link"
Write-Host "지금 켜려면 이 관리자 창이 아닌 곳에서 start_alex.vbs 를 두 번 클릭하십시오."
Write-Host "주의: 원격 켜기(WOL)로 부팅하면 로그인 화면에서 멈추므로 로그인 전에는 ALEX 가 켜지지 않습니다."

Write-Step "5/5 노트북에 넣을 값"
$user = $env:USERNAME
Write-Host "사용자 이름 (--user): $user"
$tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tailscale) {
    $candidate = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (Test-Path $candidate) { $tailscale = $candidate }
}
$hostValue = "<데스크톱 이름>"
if ($tailscale) {
    try {
        $ip = (& $tailscale ip -4 2>$null | Select-Object -First 1)
        $status = (& $tailscale status --json 2>$null | Out-String) | ConvertFrom-Json
        $name = ($status.Self.DNSName -split '\.')[0]
        if ($ip) { $hostValue = $ip.Trim() }
        Write-Host "데스크톱 주소 (--host): $hostValue   (Tailscale 이름: $name)"
    } catch {
        Write-Host "Tailscale 이 로그인되어 있지 않은 것 같습니다. Tailscale 앱에서 로그인한 뒤 다시 실행하십시오." -ForegroundColor Yellow
    }
} else {
    Write-Host "Tailscale 을 찾지 못했습니다. https://tailscale.com/download 에서 설치하고 로그인한 뒤 다시 실행하십시오." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "노트북에서:  connect_desktop.cmd setup --host $hostValue --user $user"
