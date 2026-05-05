param(
    [string]$HostName = "208.85.18.176",
    [string]$User = "root",
    [string]$HostKey = "SHA256:AQ88n3zIVTV5OlSn3vtCrAblBsUnpM/fqwELt712agc",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\fintechtrading_vps_ed25519",
    [string]$Password = $env:VPS_PASSWORD
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

$remoteCommand = @'
cd /root/fintechtrading_real
pkill -f "[p]aper_polymarket_5m_live.py.*paper_bollinger_rsi_reversal_trades.csv" || true
pkill -f "[p]aper_polymarket_5m_live.py.*paper_momentum_confirmed_value_trades.csv" || true
rm -f paper_bollinger_rsi_reversal_live.pid paper_momentum_confirmed_value_live.pid
echo "Removidas/paradas: Reversao DOWN, Momentum confirmado valor"
pgrep -af "[p]aper_polymarket_5m_live.py.*(paper_bollinger_rsi_reversal_trades.csv|paper_momentum_confirmed_value_trades.csv)" || true
'@

if ($useKey) {
    & $ssh -i $KeyPath -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$User@$HostName" $remoteCommand
}
else {
    & $plink -batch -hostkey $HostKey -pw $Password -ssh "$User@$HostName" $remoteCommand
}
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao parar estrategias removidas no VPS"
}
