param(
    [string]$HostName = "208.85.18.176",
    [string]$User = "root",
    [string]$RemoteDir = "/root/fintechtrading_real",
    [string]$HostKey = "SHA256:AQ88n3zIVTV5OlSn3vtCrAblBsUnpM/fqwELt712agc",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\fintechtrading_vps_ed25519",
    [string]$Password = $env:VPS_PASSWORD,
    [int]$IntervalSeconds = 300,
    [switch]$Once
)

$ErrorActionPreference = "Stop"

$plink = "C:\Program Files\PuTTY\plink.exe"
$ssh = "C:\Program Files\Git\usr\bin\ssh.exe"

$useKey = $KeyPath -and (Test-Path $KeyPath)
if ($useKey) {
    if (-not (Test-Path $ssh)) {
        throw "ssh nao encontrado em $ssh"
    }
}
elseif (-not (Test-Path $plink)) {
    throw "plink nao encontrado em $plink"
}

if (-not $useKey -and -not $Password) {
    $secure = Read-Host "Senha SSH do VPS" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $Password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

$localRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Invoke-RemoteCommand {
    param([string]$Command)

    if ($useKey) {
        return & $ssh -i $KeyPath -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$User@$HostName" $Command
    }

    return & $plink -batch -hostkey $HostKey -pw $Password -ssh "$User@$HostName" $Command
}

function Get-Timestamp {
    return (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
}

function Invoke-DashboardSync {
    Write-Host "[$(Get-Timestamp)] Iniciando sincronizacao..."

    $fileList = & python -c "from streamlit_dashboard import SOURCES; print('\n'.join(sorted({name for s in SOURCES for name in (s.csv_name, s.log_name) if name})))"
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao ler SOURCES do streamlit_dashboard.py"
    }

    $wanted = @{}
    foreach ($file in $fileList) {
        if ($file) {
            $wanted[$file] = $true
        }
    }

    $remoteCommand = "cd '$RemoteDir' && ls -1 *.csv *.log 2>/dev/null || true"
    $remoteFiles = Invoke-RemoteCommand $remoteCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao listar arquivos no VPS"
    }

    $existingFiles = $remoteFiles | Where-Object { $wanted.ContainsKey($_) }

    $synced = 0
    foreach ($file in $existingFiles) {
        if (-not $file) {
            continue
        }
        $remoteFile = "$RemoteDir/$file"
        $localPath = Join-Path $localRoot $file
        $tmpPath = "$localPath.tmp"
        Invoke-RemoteCommand "cat '$remoteFile'" | Set-Content -Encoding UTF8 $tmpPath
        if ($LASTEXITCODE -ne 0) {
            throw "Falha ao baixar $file"
        }
        Move-Item -Force $tmpPath $localPath
        $synced += 1
    }

    Write-Host "[$(Get-Timestamp)] Sincronizacao concluida: $synced arquivos baixados para $localRoot"
}

if ($Once) {
    Invoke-DashboardSync
    return
}

Write-Host "[$(Get-Timestamp)] Sync continuo iniciado. Intervalo: $IntervalSeconds segundos. Use Ctrl+C para parar."
while ($true) {
    try {
        Invoke-DashboardSync
    }
    catch {
        Write-Host "[$(Get-Timestamp)] ERRO na sincronizacao: $($_.Exception.Message)"
    }
    Write-Host "[$(Get-Timestamp)] Proxima sincronizacao em $IntervalSeconds segundos."
    Start-Sleep -Seconds $IntervalSeconds
}
