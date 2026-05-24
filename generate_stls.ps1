$openscad = "C:\Program Files\OpenSCAD\openscad.exe"
$scad     = "D:\2026\dev\o&o\ampersand_lap_joint.scad"
$outDir   = "D:\2026\dev\o&o\stl"

if (-not (Test-Path $outDir)) { New-Item -ItemType Directory $outDir | Out-Null }

$jobs = @()
for ($i = 0; $i -le 11; $i++) {
    $out = "$outDir\piece_$i.stl"
    Write-Host "Queuing piece $i → $out"
    $idx = $i
    $jobs += Start-Job -ScriptBlock {
        param($exe, $scad, $out, $i)
        $cmdArgs = "`"$exe`" -D `"RENDER_MODE=\`"piece\`";PIECE_IDX=$i`" -o `"$out`" `"$scad`""
        $result  = cmd /c $cmdArgs 2>&1
        [PSCustomObject]@{ Piece = $i; Exit = $LASTEXITCODE; Output = ($result -join "`n") }
    } -ArgumentList $openscad, $scad, $out, $idx
}

Write-Host "`nWaiting for $($jobs.Count) render jobs…"
$results = $jobs | Wait-Job | Receive-Job
$jobs | Remove-Job

$ok   = ($results | Where-Object { $_.Exit -eq 0 })
$errs = ($results | Where-Object { $_.Exit -ne 0 })

Write-Host "`nDone: $($ok.Count) / 12 pieces rendered successfully."
foreach ($r in $ok) {
    $stl  = "$outDir\piece_$($r.Piece).stl"
    $size = [math]::Round((Get-Item $stl).Length / 1KB, 1)
    Write-Host "  [OK] piece_$($r.Piece).stl  ($size KB)"
}
if ($errs) {
    Write-Host "`nErrors:"
    foreach ($r in $errs) { Write-Host "  [ERR] piece_$($r.Piece): $($r.Output)" }
}
