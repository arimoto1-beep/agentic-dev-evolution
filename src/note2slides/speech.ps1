# Windows の音声合成を呼び出し、WAV ファイルを書き出す。
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File speech.ps1 -ListVoices sapi
#   powershell -NoProfile -ExecutionPolicy Bypass -File speech.ps1 -JobFile job.json
#
# 結果は 1 行 1 件の JSON で標準出力に出す(note2slides.tts が読む)。
#   {"kind":"voice","name":...,"language":...,"gender":...}
#   {"kind":"engine","engine":...,"voice":...}
#   {"kind":"done","index":1,"ok":true}
#   {"kind":"done","index":2,"ok":false,"error":"..."}
#
# 1 件ごとに成否を出すので、途中で失敗しても残りは合成され、どれが失敗したかが
# 分かる。job.json の形式は note2slides/tts.py を参照。

param(
    [string]$JobFile = "",
    [string]$ListVoices = ""
)

$ErrorActionPreference = "Stop"
# 日本語の音声名やエラーメッセージが文字化けしないようにする。
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

function Write-Line($obj) {
    Write-Output ($obj | ConvertTo-Json -Compress -Depth 4)
}

function Read-Utf8($path) {
    [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
}

# --- SAPI(System.Speech)--------------------------------------------------

function New-SapiSynth {
    Add-Type -AssemblyName System.Speech
    New-Object System.Speech.Synthesis.SpeechSynthesizer
}

function Get-SapiVoices {
    $synth = New-SapiSynth
    foreach ($voice in $synth.GetInstalledVoices()) {
        if (-not $voice.Enabled) { continue }
        $info = $voice.VoiceInfo
        Write-Line @{ kind = "voice"; name = $info.Name; language = $info.Culture.Name; gender = [string]$info.Gender }
    }
    $synth.Dispose()
}

function Invoke-Sapi($job) {
    $synth = New-SapiSynth
    if ($job.voice) { $synth.SelectVoice($job.voice) }
    $synth.Rate = [int]$job.rate
    $synth.Volume = [int]$job.volume
    $bits = [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen
    $channel = [System.Speech.AudioFormat.AudioChannel]::Mono
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo([int]$job.sample_rate, $bits, $channel)
    Write-Line @{ kind = "engine"; engine = "sapi"; voice = $synth.Voice.Name }

    foreach ($item in $job.items) {
        try {
            $text = Read-Utf8 $item.text_file
            $synth.SetOutputToWaveFile($item.out_file, $format)
            $synth.Speak($text)
            $synth.SetOutputToNull()
            Write-Line @{ kind = "done"; index = $item.index; ok = $true }
        } catch {
            # 出力先を開いたままだと次の 1 件も巻き込んで失敗する。
            try { $synth.SetOutputToNull() } catch { }
            Write-Line @{ kind = "done"; index = $item.index; ok = $false; error = $_.Exception.Message }
        }
    }
    $synth.Dispose()
}

# --- OneCore(Windows.Media.SpeechSynthesis)--------------------------------

function Initialize-OneCore {
    if ($script:oneCoreReady) { return }
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media, ContentType = WindowsRuntime] | Out-Null
    [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
    # WinRT の非同期処理を待つには .NET の Task に変換する。PowerShell 5.1 には
    # await が無いため、AsTask(IAsyncOperation<T>) をリフレクションで取り出す。
    $script:asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]
    $script:oneCoreReady = $true
}

function Wait-WinRt($operation, $resultType, [int]$timeoutMs = 300000) {
    $task = $script:asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    if (-not $task.Wait($timeoutMs)) { throw "音声合成が $timeoutMs ミリ秒以内に終わりませんでした。" }
    $task.Result
}

function Get-OneCoreVoiceList {
    Initialize-OneCore
    [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices
}

function Get-OneCoreVoices {
    foreach ($voice in Get-OneCoreVoiceList) {
        Write-Line @{ kind = "voice"; name = $voice.DisplayName; language = $voice.Language; gender = [string]$voice.Gender }
    }
}

function Invoke-OneCore($job) {
    Initialize-OneCore
    $synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
    if ($job.voice) {
        $selected = Get-OneCoreVoiceList | Where-Object { $_.DisplayName -eq $job.voice } | Select-Object -First 1
        if (-not $selected) { throw "音声が見つかりません: $($job.voice)" }
        $synth.Voice = $selected
    }
    # 読み上げ速度・音量は環境によって未対応のことがあるため、失敗しても続ける。
    try { $synth.Options.SpeakingRate = [double]$job.speed } catch { }
    try { $synth.Options.AudioVolume = [double]$job.volume / 100.0 } catch { }
    Write-Line @{ kind = "engine"; engine = "onecore"; voice = $synth.Voice.DisplayName }

    $streamType = [Windows.Media.SpeechSynthesis.SpeechSynthesisStream]
    foreach ($item in $job.items) {
        try {
            $text = Read-Utf8 $item.text_file
            $stream = Wait-WinRt $synth.SynthesizeTextToStreamAsync($text) $streamType
            $reader = New-Object Windows.Storage.Streams.DataReader($stream)
            Wait-WinRt $reader.LoadAsync([uint32]$stream.Size) ([uint32]) | Out-Null
            $bytes = New-Object byte[] $stream.Size
            $reader.ReadBytes($bytes)
            [System.IO.File]::WriteAllBytes($item.out_file, $bytes)
            $reader.Dispose()
            $stream.Dispose()
            Write-Line @{ kind = "done"; index = $item.index; ok = $true }
        } catch {
            Write-Line @{ kind = "done"; index = $item.index; ok = $false; error = $_.Exception.Message }
        }
    }
    $synth.Dispose()
}

# --- 実行 -------------------------------------------------------------------

if ($ListVoices) {
    switch ($ListVoices) {
        "sapi" { Get-SapiVoices }
        "onecore" { Get-OneCoreVoices }
        default { throw "未知のエンジンです: $ListVoices" }
    }
    exit 0
}

if (-not $JobFile) { throw "-JobFile か -ListVoices を指定してください。" }
$job = Read-Utf8 $JobFile | ConvertFrom-Json
switch ($job.engine) {
    "sapi" { Invoke-Sapi $job }
    "onecore" { Invoke-OneCore $job }
    default { throw "未知のエンジンです: $($job.engine)" }
}
exit 0
