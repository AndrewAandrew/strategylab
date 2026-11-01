# Round 6 grid runner: 24 isolated harness calls (one process = one cell).
# Commands transcribed from round6-grid-commands.md and verified against the frozen spec.
$ErrorActionPreference = 'Continue'
$env:PYTHONUTF8 = '1'
$PY = 'C:\Users\egoro\Documents\GitHub\strategylab\.venv\Scripts\python.exe'
$REPO = 'C:\Users\egoro\Documents\GitHub\strategylab'
$TMP = 'C:\Users\egoro\AppData\Local\Temp\opencode\meas-r6'
$log = "$TMP\runlog.txt"

$folds = @{ f1 = @('1761523200','1771113600'); f2 = @('1771113600','1780704000'); f3 = @('1780704000','1790294400') }
$syms  = @{ btc = @('data/btcusdt.db','BTCUSDT'); eth = @('data/ethusdt.db','ETHUSDT') }

$cells = @()
# PRIMARY 12: J 1h (j_h) x6, S 15m (s_15) x6
foreach ($fk in @('f1','f2','f3')) {
  foreach ($sk in @('btc','eth')) {
    $cells += @{ id = "j_h_${sk}_${fk}"; args = @($syms[$sk][0], $syms[$sk][1], $folds[$fk][0], $folds[$fk][1], 'jaguar_ema', '--interval','hour','--leverage','20','--capital','10000','--param','risk_frac=0.005','--param','fund_floor=0.00025','--out',"data/calib/r6_j_h_${sk}_${fk}") }
  }
}
foreach ($fk in @('f1','f2','f3')) {
  foreach ($sk in @('btc','eth')) {
    $cells += @{ id = "s_15_${sk}_${fk}"; args = @($syms[$sk][0], $syms[$sk][1], $folds[$fk][0], $folds[$fk][1], 'smc_choch_fvg', '--interval','15m','--leverage','20','--capital','10000','--param','risk_frac=0.005','--param','fund_floor=0.00025','--param','ttl_bars=4','--param','max_hold=16','--out',"data/calib/r6_s_15_${sk}_${fk}") }
  }
}
# DIAGNOSTICS 12 (not for spec): J 15m (j_15) x6, S 5m (s_5) x6
foreach ($fk in @('f1','f2','f3')) {
  foreach ($sk in @('btc','eth')) {
    $cells += @{ id = "j_15_${sk}_${fk}"; args = @($syms[$sk][0], $syms[$sk][1], $folds[$fk][0], $folds[$fk][1], 'jaguar_ema', '--interval','15m','--leverage','20','--capital','10000','--param','risk_frac=0.005','--param','fund_floor=0.00025','--out',"data/calib/r6_j_15_${sk}_${fk}") }
  }
}
foreach ($fk in @('f1','f2','f3')) {
  foreach ($sk in @('btc','eth')) {
    $cells += @{ id = "s_5_${sk}_${fk}"; args = @($syms[$sk][0], $syms[$sk][1], $folds[$fk][0], $folds[$fk][1], 'smc_choch_fvg', '--interval','5m','--leverage','20','--capital','10000','--param','risk_frac=0.005','--param','fund_floor=0.00025','--param','ttl_bars=12','--param','max_hold=48','--out',"data/calib/r6_s_5_${sk}_${fk}") }
  }
}

Write-Output ("total cells: " + $cells.Count)
Add-Content -Path $log -Value "=== round6 grid start $(Get-Date -Format o) HEAD=692e2e7e75fa3129b0144e4092fd0f69b12db766 cells=$($cells.Count) ==="

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

Add-Content -Path $log -Value "=== round6 grid end $(Get-Date -Format o) ==="
Write-Output 'GRID DONE'
