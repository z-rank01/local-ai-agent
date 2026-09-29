# Dot-sourced by start-daily.ps1. The listener stays in the launcher process so
# Docker CLI calls originate from the same process that successfully ran compose
# during startup; Docker Desktop denies calls from the BFF's process tree here.

function New-DailyServiceSupervisor([string]$ProjectRoot) {
    $privateDir = Join-Path $ProjectRoot 'data/private'
    New-Item -ItemType Directory -Force -Path $privateDir | Out-Null
    $tokenPath = Join-Path $privateDir 'service-supervisor.token'
    $token = ''
    if (Test-Path -LiteralPath $tokenPath) {
        try { $token = (Get-Content -LiteralPath $tokenPath -Raw -ErrorAction Stop).Trim() } catch {}
    }
    if ($token.Length -lt 32) {
        $randomBytes = New-Object byte[] 48
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($randomBytes) } finally { $rng.Dispose() }
        $token = [Convert]::ToBase64String($randomBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
        $encoding = New-Object System.Text.UTF8Encoding($false)
        $temporaryToken = $tokenPath + '.tmp'
        [System.IO.File]::WriteAllText($temporaryToken, $token, $encoding)
        Move-Item -LiteralPath $temporaryToken -Destination $tokenPath -Force | Out-Null
    }

    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 9511)
    $listener.Start()
    return @{
        Listener = $listener
        Token = $token
        Root = $ProjectRoot
        LogPath = Join-Path $ProjectRoot 'data/logs/service-controller.log'
        ErrorPath = Join-Path $ProjectRoot 'data/logs/service-controller.err.log'
        ShutdownRequested = $false
    }
}

function Write-DailySupervisorLog($Context, [string]$Message, [switch]$IsError) {
    $path = if ($IsError) { $Context.ErrorPath } else { $Context.LogPath }
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $Message
    Add-Content -LiteralPath $path -Value $line -Encoding UTF8
}

function Test-DailyToken([string]$Supplied, [string]$Expected) {
    if ($null -eq $Supplied -or $Supplied.Length -ne $Expected.Length) { return $false }
    $difference = 0
    for ($i = 0; $i -lt $Expected.Length; $i++) {
        $difference = $difference -bor ([int][char]$Supplied[$i] -bxor [int][char]$Expected[$i])
    }
    return $difference -eq 0
}

function Read-DailyHttpRequest([System.Net.Sockets.NetworkStream]$Stream) {
    $header = New-Object System.Text.StringBuilder
    while ($true) {
        $value = $Stream.ReadByte()
        if ($value -lt 0) { throw '客户端在 HTTP 请求头结束前断开连接' }
        [void]$header.Append([char]$value)
        if ($header.Length -gt 8192) { throw 'HTTP 请求头过大' }
        if ($header.Length -ge 4 -and $header.ToString().EndsWith("`r`n`r`n")) { break }
    }

    $lines = $header.ToString().TrimEnd("`r", "`n") -split "`r`n"
    $requestLine = $lines[0].Split(' ')
    if ($requestLine.Length -lt 2) { throw 'HTTP 请求行无效' }
    $headers = @{}
    foreach ($line in $lines | Select-Object -Skip 1) {
        $separator = $line.IndexOf(':')
        if ($separator -gt 0) {
            $name = $line.Substring(0, $separator).Trim().ToLowerInvariant()
            $headers[$name] = $line.Substring($separator + 1).Trim()
        }
    }

    $body = ''
    if ($headers.ContainsKey('content-length')) {
        $length = 0
        if (-not [int]::TryParse($headers['content-length'], [ref]$length) -or $length -lt 0 -or $length -gt 2048) {
            throw 'HTTP 请求体大小无效'
        }
        if ($length -gt 0) {
            $bytes = New-Object byte[] $length
            $offset = 0
            while ($offset -lt $length) {
                $count = $Stream.Read($bytes, $offset, $length - $offset)
                if ($count -le 0) { throw 'HTTP 请求体未完整接收' }
                $offset += $count
            }
            $body = [System.Text.Encoding]::UTF8.GetString($bytes)
        }
    }

    return @{
        Method = $requestLine[0].ToUpperInvariant()
        Path = ($requestLine[1] -split '\?', 2)[0]
        Headers = $headers
        Body = $body
    }
}

function Send-DailyHttpResponse([System.Net.Sockets.NetworkStream]$Stream, [int]$Status, $Payload) {
    $reason = switch ($Status) {
        200 { 'OK' }
        400 { 'Bad Request' }
        403 { 'Forbidden' }
        404 { 'Not Found' }
        default { 'Service Unavailable' }
    }
    $body = [System.Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Payload -Depth 8 -Compress))
    $headText = "HTTP/1.1 $Status $reason`r`nContent-Type: application/json; charset=utf-8`r`nContent-Length: $($body.Length)`r`nCache-Control: no-store`r`nConnection: close`r`n`r`n"
    $head = [System.Text.Encoding]::ASCII.GetBytes($headText)
    $Stream.Write($head, 0, $head.Length)
    if ($body.Length -gt 0) { $Stream.Write($body, 0, $body.Length) }
    $Stream.Flush()
}

function Invoke-DailyDockerOperation($Context, [string]$Operation) {
    $composeArgs = switch ($Operation) {
        'websearch_start' { @('compose', '--profile', 'websearch', 'up', '-d', 'searxng', 'skill-websearch') }
        'websearch_stop' { @('compose', '--profile', 'websearch', 'stop', 'searxng', 'skill-websearch') }
        'stack_shutdown' { @('compose', '--profile', 'websearch', 'stop') }
        default { throw '不支持的容器操作' }
    }
    $dockerCommand = Get-Command docker -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $dockerCommand) { throw '找不到 Docker CLI；请检查启动环境' }

    $processInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processInfo.FileName = $dockerCommand.Path
    $processInfo.Arguments = $composeArgs -join ' '
    $processInfo.WorkingDirectory = $Context.Root
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $processInfo
    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Stop'
    try {
        if (-not $process.Start()) { throw 'Docker CLI 无法启动' }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $timeout = if ($Operation -eq 'stack_shutdown') { 90000 } else { 150000 }
        if (-not $process.WaitForExit($timeout)) {
            try { $process.Kill() } catch {}
            throw "docker compose $Operation 超时"
        }
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) {
            $detail = if ($stderr.Trim()) { $stderr.Trim() } else { $stdout.Trim() }
            if (-not $detail) { $detail = "docker compose exited with $($process.ExitCode)" }
            throw $detail.Substring(0, [Math]::Min(1000, $detail.Length))
        }
        return $stdout.Trim().Substring(0, [Math]::Min(1000, $stdout.Trim().Length))
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
        $process.Dispose()
    }
}

function Invoke-DailyStockStartOperation($Context) {
    # Read the saved connection locally. The BFF can request this fixed operation,
    # but it cannot pass an executable, arguments, or arbitrary filesystem paths.
    $settingsPath = Join-Path $Context.Root 'data/private/daily-services.json'
    if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
        throw '股票服务尚未配置，请先在页面保存股票仓库连接'
    }
    try {
        $settings = Get-Content -LiteralPath $settingsPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw '股票服务配置无法读取或不是有效 JSON'
    }
    if ($settings.enabled -isnot [bool] -or -not $settings.enabled) {
        throw '股票技能开关未开启；请从页面重新开启'
    }
    if ($settings.root -isnot [string] -or -not $settings.root.Trim()) {
        throw '股票仓库路径未配置'
    }
    $stateDir = if ($settings.PSObject.Properties['state_dir']) { $settings.state_dir } else { 'simulation' }
    if ($stateDir -isnot [string] -or [System.IO.Path]::IsPathRooted($stateDir)) {
        throw '股票状态目录必须是仓库内的相对路径'
    }

    $port = 8765
    $portProperty = $settings.PSObject.Properties['port']
    if ($portProperty) {
        if ($portProperty.Value -is [bool] -or -not [int]::TryParse([string]$portProperty.Value, [ref]$port)) {
            throw '股票服务端口无效'
        }
    }
    if ($port -lt 1024 -or $port -gt 65535 -or $port -in @(9510, 9511, 5173)) {
        throw '股票服务端口无效或与 Web、BFF、本机服务控制器端口冲突'
    }

    try {
        $stockRoot = [System.IO.Path]::GetFullPath($settings.root)
        $statePath = [System.IO.Path]::GetFullPath((Join-Path $stockRoot $stateDir))
    } catch {
        throw '股票仓库或状态目录路径无效'
    }
    $rootPrefix = $stockRoot.TrimEnd('\') + '\'
    if (-not $statePath.Equals($stockRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
            -not $statePath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw '股票状态目录必须位于所选股票仓库内'
    }
    if (-not (Test-Path -LiteralPath $stockRoot -PathType Container)) {
        throw '股票仓库路径不存在'
    }

    $pythonPath = Join-Path $stockRoot '.venv-paper/Scripts/python.exe'
    $scriptPath = Join-Path $stockRoot 'scripts/run_paper.py'
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf) -or
            -not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        throw '股票仓库缺少 .venv-paper/Scripts/python.exe 或 scripts/run_paper.py'
    }

    $healthUrl = "http://127.0.0.1:$port/health"
    try {
        $null = Invoke-RestMethod $healthUrl -TimeoutSec 2
        return @{ output = "already running on 127.0.0.1:$port"; pid = $null }
    } catch {}
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        throw "端口 $port 已被其他进程占用；不会终止未知进程"
    }

    $logsDir = Join-Path $Context.Root 'data/logs'
    New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
    $stdoutPath = Join-Path $logsDir 'stock-service.log'
    $stderrPath = Join-Path $logsDir 'stock-service.err.log'
    # Quote path arguments because Start-Process flattens ArgumentList to a command line.
    $arguments = @('-X', 'utf8', ('"{0}"' -f $scriptPath), '--state-dir',
        ('"{0}"' -f $statePath), '--port', [string]$port, '--no-worker')
    $process = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $stockRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    if (-not $process) { throw '启动器无法创建股票后台进程' }

    $deadline = (Get-Date).AddSeconds(45)
    $ready = $false
    do {
        Start-Sleep -Milliseconds 500
        $process.Refresh()
        if ($process.HasExited) {
            throw "股票后台进程提前退出（exit code $($process.ExitCode)）；请查看 data/logs/stock-service.err.log"
        }
        try {
            $null = Invoke-RestMethod $healthUrl -TimeoutSec 2
            $ready = $true
        } catch {}
    } until ($ready -or (Get-Date) -gt $deadline)

    $stockPid = [int]$process.Id
    $process.Dispose()
    if (-not $ready) {
        throw '股票后台在 45 秒内未就绪，请查看 data/logs/stock-service.err.log'
    }
    return @{ output = "started on 127.0.0.1:$port"; pid = $stockPid }
}

function Invoke-DailyServiceSupervisorRequest($Context) {
    if (-not $Context -or -not $Context.Listener.Pending()) { return }
    $client = $null
    try {
        $client = $Context.Listener.AcceptTcpClient()
        $client.ReceiveTimeout = 10000
        $stream = $client.GetStream()
        $request = Read-DailyHttpRequest $stream
        if ($request.Method -eq 'GET' -and $request.Path -eq '/health') {
            Send-DailyHttpResponse $stream 200 @{ service = 'daily-launcher-service-supervisor'; status = 'ok' }
            return
        }
        if ($request.Method -ne 'POST' -or $request.Path -ne '/api/operation') {
            Send-DailyHttpResponse $stream 404 @{ detail = 'not found' }
            return
        }
        $authorization = [string]$request.Headers['authorization']
        if (-not (Test-DailyToken $authorization ('Bearer ' + $Context.Token))) {
            Send-DailyHttpResponse $stream 403 @{ detail = 'forbidden' }
            return
        }
        if (-not $request.Body) {
            Send-DailyHttpResponse $stream 400 @{ detail = 'invalid request size' }
            return
        }
        try { $body = $request.Body | ConvertFrom-Json -ErrorAction Stop } catch {
            Send-DailyHttpResponse $stream 400 @{ detail = 'invalid JSON' }
            return
        }
        $operation = [string]$body.operation
        if ($operation -notin @('websearch_start', 'websearch_stop', 'stock_start', 'stack_shutdown')) {
            Send-DailyHttpResponse $stream 400 @{ detail = 'unknown operation' }
            return
        }

        try {
            if ($operation -eq 'stock_start') {
                $result = Invoke-DailyStockStartOperation $Context
                Send-DailyHttpResponse $stream 200 @{ ok = $true; operation = $operation; output = $result.output; pid = $result.pid }
                Write-DailySupervisorLog $Context "completed $operation pid=$($result.pid)"
            } else {
                $output = Invoke-DailyDockerOperation $Context $operation
                Send-DailyHttpResponse $stream 200 @{ ok = $true; operation = $operation; output = $output }
                Write-DailySupervisorLog $Context "completed $operation"
            }
            if ($operation -eq 'stack_shutdown') { $Context.ShutdownRequested = $true }
        } catch {
            $detail = $_.Exception.Message
            Write-DailySupervisorLog $Context "failed $operation`: $detail" -IsError
            Send-DailyHttpResponse $stream 503 @{ detail = $detail.Substring(0, [Math]::Min(1000, $detail.Length)) }
        }
    } catch {
        if ($client) {
            try { Send-DailyHttpResponse $client.GetStream() 400 @{ detail = $_.Exception.Message } } catch {}
        }
        Write-DailySupervisorLog $Context "invalid request: $($_.Exception.Message)" -IsError
    } finally {
        if ($client) { $client.Close() }
    }
}

function Wait-DailyServiceSupervisor($Context) {
    while (-not $Context.ShutdownRequested) {
        Invoke-DailyServiceSupervisorRequest $Context
        if (-not $Context.ShutdownRequested) { Start-Sleep -Milliseconds 100 }
    }
    try { $Context.Listener.Stop() } catch {}
    Write-DailySupervisorLog $Context 'launcher supervisor stopped'
}
