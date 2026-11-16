$targetDir = "$env:USERPROFILE\.cache\huggingface\hub\models--mobiuslabsgmbh--faster-whisper-large-v3-turbo\snapshots\0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
if (-not (Test-Path $targetDir)) {
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
}
$targetFile = "$targetDir\model.bin"
$url = "https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo/resolve/main/model.bin"

Write-Host "Starting resilient background download of model.bin to $targetFile..." -ForegroundColor Cyan
curl.exe -L -C - --retry 10 --retry-delay 3 -o $targetFile $url
Write-Host "Download finished." -ForegroundColor Green
