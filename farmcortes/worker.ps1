$ErrorActionPreference = "Stop"

$BaseUrl = "__BASE_URL__"
$Token = "__WORKER_TOKEN__"
$WorkerName = "$env:COMPUTERNAME-$env:USERNAME"
$Root = Join-Path $env:LOCALAPPDATA "FarmcortesWorker"
$YtDlp = Join-Path $Root "yt-dlp.exe"
$Deno = Join-Path $Root "deno.exe"
$Headers = @{ Authorization = "Bearer $Token"; "X-Worker-Name" = $WorkerName }

New-Item -ItemType Directory -Force -Path $Root | Out-Null

function Test-YtDlp {
    if (-not (Test-Path $YtDlp)) { return $false }
    try {
        & $YtDlp --version *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Test-Deno {
    if (-not (Test-Path $Deno)) { return $false }
    try {
        & $Deno --version *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

if (-not (Test-YtDlp)) {
    Remove-Item -Force -ErrorAction SilentlyContinue $YtDlp
    $temporary = "$YtDlp.download"
    Remove-Item -Force -ErrorAction SilentlyContinue $temporary
    Write-Host "Baixando e validando o yt-dlp oficial..."
    & curl.exe -fL --retry 3 --connect-timeout 30 -o $temporary "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
    if ($LASTEXITCODE -ne 0) { throw "Falha ao baixar o yt-dlp oficial" }
    $file = Get-Item $temporary
    $stream = [System.IO.File]::OpenRead($temporary)
    try {
        $first = $stream.ReadByte()
        $second = $stream.ReadByte()
    } finally {
        $stream.Dispose()
    }
    if ($file.Length -lt 5MB -or $first -ne 77 -or $second -ne 90) {
        Remove-Item -Force $temporary
        throw "O download do yt-dlp nao passou na verificacao"
    }
    Move-Item -Force $temporary $YtDlp
    if (-not (Test-YtDlp)) {
        Remove-Item -Force $YtDlp
        throw "O yt-dlp baixado nao conseguiu iniciar"
    }
}

if (-not (Test-Deno)) {
    Remove-Item -Force -ErrorAction SilentlyContinue $Deno
    $archive = Join-Path $Root "deno.download.zip"
    $extractDir = Join-Path $Root "deno-download"
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $Root "deno.zip.download")
    Remove-Item -Force -ErrorAction SilentlyContinue $archive
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $extractDir
    Write-Host "Baixando e validando o Deno para o YouTube..."
    & curl.exe -fL --retry 3 --connect-timeout 30 -o $archive "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
    if ($LASTEXITCODE -ne 0) { throw "Falha ao baixar o Deno oficial" }
    Expand-Archive -Path $archive -DestinationPath $extractDir -Force
    $downloadedDeno = Join-Path $extractDir "deno.exe"
    if (-not (Test-Path $downloadedDeno) -or (Get-Item $downloadedDeno).Length -lt 10MB) {
        throw "O download do Deno nao passou na verificacao"
    }
    Move-Item -Force $downloadedDeno $Deno
    Remove-Item -Force -ErrorAction SilentlyContinue $archive
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $extractDir
    if (-not (Test-Deno)) {
        Remove-Item -Force $Deno
        throw "O Deno baixado nao conseguiu iniciar"
    }
}

Write-Host "Farmcortes Worker ativo em $WorkerName. Pressione Ctrl+C para encerrar."

while ($true) {
    try {
        $response = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/worker/jobs/next" -Headers $Headers -TimeoutSec 30
        if (-not $response.job) {
            Start-Sleep -Seconds 8
            continue
        }

        $job = $response.job
        $jobDir = Join-Path $Root $job.id
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $jobDir
        New-Item -ItemType Directory -Force -Path $jobDir | Out-Null
        Write-Host "Obtendo video $($job.id) pelo IP deste PC..."

        & $YtDlp --js-runtimes "deno:$Deno" --no-playlist --max-filesize 4G --write-subs --write-auto-subs --sub-langs "pt.*,en.*" --sub-format "srt/vtt/best" -f "b[height<=1080]/b" -o "$jobDir/source.%(ext)s" -- $job.url
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "O formato preferido nao estava disponivel; tentando a selecao automatica..."
            Get-ChildItem $jobDir -File -ErrorAction SilentlyContinue | Remove-Item -Force
            & $YtDlp --js-runtimes "deno:$Deno" --no-playlist --max-filesize 4G -o "$jobDir/source.%(ext)s" -- $job.url
        }
        if ($LASTEXITCODE -ne 0) { throw "yt-dlp encerrou com codigo $LASTEXITCODE" }

        $info = $null
        try {
            $infoJson = & $YtDlp --js-runtimes "deno:$Deno" --no-playlist --dump-single-json --skip-download -- $job.url
            if ($LASTEXITCODE -eq 0 -and $infoJson) { $info = $infoJson | ConvertFrom-Json }
        } catch {}

        $video = Get-ChildItem $jobDir -File | Where-Object { $_.Extension -in ".mp4", ".webm", ".mkv", ".mov", ".m4v" } | Select-Object -First 1
        if (-not $video) { throw "Nenhum arquivo de video foi criado" }
        $subtitle = Get-ChildItem $jobDir -File | Where-Object { $_.Extension -in ".srt", ".vtt" } | Select-Object -First 1

        $curlArgs = @("-fS", "-H", "Authorization: Bearer $Token", "-H", "X-Worker-Name: $WorkerName", "-F", "video=@$($video.FullName)")
        if ($subtitle) { $curlArgs += @("-F", "subtitle=@$($subtitle.FullName)") }
        if ($info.title) { $curlArgs += @("-F", "title=$($info.title)") }
        if ($info.uploader) { $curlArgs += @("-F", "source_author=$($info.uploader)") }
        if ($info.license) { $curlArgs += @("-F", "source_license=$($info.license)") }
        $curlArgs += "$BaseUrl/api/worker/jobs/$($job.id)/upload"
        & curl.exe @curlArgs
        if ($LASTEXITCODE -ne 0) { throw "Falha ao enviar o video para a VPS" }

        Write-Host "Video enviado. A VPS iniciou os cortes."
        Remove-Item -Recurse -Force $jobDir
    }
    catch {
        Write-Warning $_.Exception.Message
        if ($job -and $job.id) {
            try {
                Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/worker/jobs/$($job.id)/fail" -Headers $Headers -Body @{ error = $_.Exception.Message } -TimeoutSec 30 | Out-Null
            } catch {}
        }
        Start-Sleep -Seconds 10
    }
}
