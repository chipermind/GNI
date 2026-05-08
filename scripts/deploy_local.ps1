$ErrorActionPreference = "Stop"
Write-Host "START" -ForegroundColor Cyan

$TargetDir = "C:\Users\lucas\GNI\gni-bot-creator"
Set-Location $TargetDir
if (-not (Test-Path ".\scripts\deploy_vm.ps1")) {
    throw "deploy_vm.ps1 not found in $TargetDir\scripts"
}
Write-Host "PATH_OK" -ForegroundColor Green

.\scripts\deploy_vm.ps1 -Full
Write-Host "DEPLOY_OK" -ForegroundColor Green

Write-Host "DONE" -ForegroundColor Cyan
