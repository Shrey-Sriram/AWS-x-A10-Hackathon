# run_local.ps1 — runs the local logic tests (no AWS, no cost).
# Usage:  powershell -ExecutionPolicy Bypass -File run_local.ps1

Write-Host "=== Text-to-3D : local test run ===" -ForegroundColor Cyan

Write-Host "`n[1/2] Installing local test dependencies (trimesh, numpy)..." -ForegroundColor Yellow
python -m pip install --quiet trimesh numpy
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed. Is Python on your PATH? Try: python --version" -ForegroundColor Red
    exit 1
}

Write-Host "`n[2/2] Running inference logic tests..." -ForegroundColor Yellow
python tests/test_inference_local.py
$code = $LASTEXITCODE

Write-Host "`n=== Done ===" -ForegroundColor Cyan
if ($code -eq 0) {
    Write-Host "All local tests passed. The request-parsing and mesh-export logic works." -ForegroundColor Green
    Write-Host "To run REAL 3D generation you must deploy to SageMaker (needs AWS). See README.md / TESTING.md." -ForegroundColor Green
} else {
    Write-Host "Some tests failed (exit $code). Scroll up for the FAIL lines." -ForegroundColor Red
}
exit $code
