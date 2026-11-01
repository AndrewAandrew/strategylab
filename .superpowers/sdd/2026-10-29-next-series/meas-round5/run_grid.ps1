# Round 5 grid runner: 27 isolated harness calls (one process = one cell).
# Commands transcribed from round5-grid-commands.md and verified against the frozen spec.
$ErrorActionPreference = 'Continue'
$env:PYTHONUTF8 = '1'
$PY = 'C:\Users\egoro\Documents\GitHub\strategylab\.venv\Scripts\python.exe'
$REPO = 'C:\Users\egoro\Documents\GitHub\strategylab'
$TMP = 'C:\Users\egoro\AppData\Local\Temp\opencode\meas-r5'
$log = "$TMP\runlog.txt"

$folds = @{ f1 = @('1761523200','1771113600'); f2 = @('1771113600','1780704000'); f3 = @('1780704000','1790294400') }
$syms  = @{ btc = @('data/btcusdt.db','BTCUSDT'); eth = @('data/ethusdt.db','ETHUSDT') }

$cells = @()
foreach ($fk in @('f1','f2','f3')) {
  foreach ($sk in @('btc','eth')) {
    $cells += @{ id = "j_p_${sk}_${fk}"; args = @($syms[$sk][0], $syms[$sk][1], $folds[$fk][0], $folds[$fk][1], 'jaguar_ema', '--interval','5m','--leverage','20','--capital','10000','--param','risk_frac=0.005','--param','fund_floor=0.00025','--out',"data/calib/r5_j_p_${sk}_${fk}") }
  }
}
foreach ($fk in @('f1','f2','f3')) {
  foreach ($sk in @('btc','eth')) {
    $cells += @{ id = "s_p_${sk}_${fk}"; args = @($syms[$sk][0], $syms[$sk][1], $folds[$fk][0], $folds[$fk][1], 'smc_choch_fvg', '--interval','1m','--leverage','20','--capital','10000','--param','risk_frac=0.005','--param','fund_floor=0.00025','--out',"data/calib/r5_s_p_${sk}_${fk}") }
  }
}
foreach ($fk in @('f1','f2','f3')) {
  foreach ($sk in @('btc','eth')) {
    $cells += @{ id = "j_w_${sk}_${fk}"; args = @($syms[$sk][0], $syms[$sk][1], $folds[$fk][0], $folds[$fk][1], 'jaguar_ema', '--interval','5m','--leverage','20','--capital','10000','--param','risk_frac=0.005','--param','fund_floor=0.001','--out',"data/calib/r5_j_w_${sk}_${fk}") }
  }
}
foreach ($fk in @('f1','f2','f3')) {
  foreach ($sk in @('btc','eth')) {
    $cells += @{ id = "s_w_${sk}_${fk}"; args = @($syms[$sk][0], $syms[$sk][1], $folds[$fk][0], $folds[$fk][1], 'smc_choch_fvg', '--interval','1m','--leverage','20','--capital','10000','--param','risk_frac=0.005','--param','fund_floor=0.0015','--out',"data/calib/r5_s_w_${sk}_${fk}") }
  }
}
foreach ($fk in @('f1','f2','f3')) {
  $cells += @{ id = "j_t2_btc_${fk}"; args = @($syms['btc'][0], $syms['btc'][1], $folds[$fk][0], $folds[$fk][1], 'jaguar_ema', '--interval','5m','--leverage','20','--capital','10000','--param','risk_frac=0.005','--param','fund_floor=0.00025','--param','tp_mult=2.0','--out',"data/calib/r5_j_t2_btc_${fk}") }
}

Write-Output ("total cells: " + $cells.Count)
Add-Content -Path $log -Value "=== round5 grid start $(Get-Date -Format o) HEAD=262b7263a7d638251208c33394a91c5ef8b5ef8d cells=$($cells.Count) ==="

foreach ($cell in $cells) {
  $so = "$TMP\stdout_$($cell.id).txt"
  $se = "$TMP\stderr_$($cell.id).txt"
  $t0 = Get-Date
  $p = Start-Process -FilePath $PY -ArgumentList (@('scripts/calib/harness.py') + $cell.args) `
       -WorkingDirectory $REPO -RedirectStandardOutput $so -RedirectStandardError $se `
       -Wait -PassThru -NoNewWindow
  $ec = $p.ExitCode
  $dt = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
  $res = ''
  if (Test-Path $so) { $res = ((Get-Content $so -Encoding UTF8 | Where-Object { $_ -match '\S' } | Select-Object -Last 4) -join ' | ') }
  $err = ''
  if (Test-Path $se) { $err = ((Get-Content $se -Encoding UTF8 | Select-Object -Last 8) -join ' ~ ') }
  $line = "CELL $($cell.id) EXIT=$ec TIME=${dt}s`n  CMD: harness.py $($cell.args -join ' ')`n  RESULT: $res`n  STDERR: $err"
  Add-Content -Path $log -Value $line
  Write-Output ("{0} exit={1} {2}s" -f $cell.id, $ec, $dt)
}

Add-Content -Path $log -Value "=== round5 grid end $(Get-Date -Format o) ==="
Write-Output 'GRID DONE'
