param(
  [ValidateSet('prepare', 'finish')]
  [string]$Stage = 'prepare',
  [Parameter(Mandatory = $true)]
  [string]$Ref
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'

$PluginRoot = Split-Path -Parent $PSScriptRoot
$Root = Join-Path $env:USERPROFILE 'vk-mcp'
$ConfigDir = Join-Path $Root 'config'
$TokenDir = Join-Path $ConfigDir 'tokens'
$TokenFile = Join-Path $TokenDir 'pending.txt'
$PendingFile = Join-Path $ConfigDir 'pending.json'
$Utf8NoBom = New-Object Text.UTF8Encoding($false)

function Say([string]$Text) { Write-Host $Text }
function Stop-Install([string]$Text) {
  Write-Host ''
  Write-Host ('ОШИБКА: ' + $Text) -ForegroundColor Red
  Write-Host 'Сделайте снимок этого окна и пришлите только текст ошибки, без ключа.'
  throw 'VKSTOP'
}

function Test-Python([string]$Exe) {
  if (-not $Exe -or $Exe -match 'WindowsApps' -or -not (Test-Path $Exe)) { return $false }
  try { return ((& $Exe -c 'import sys;print(sys.version_info >= (3,9))' 2>$null) -eq 'True') } catch { return $false }
}

function Find-Python {
  $candidates = @()
  foreach ($command in @('py', 'python')) {
    try {
      $value = & $command -3 -c 'import sys;print(sys.executable)' 2>$null
      if ($value) { $candidates += ($value | Select-Object -First 1) }
    } catch {}
    try {
      $value = & $command -c 'import sys;print(sys.executable)' 2>$null
      if ($value) { $candidates += ($value | Select-Object -First 1) }
    } catch {}
  }
  foreach ($base in @($env:LOCALAPPDATA, $env:ProgramFiles)) {
    if (-not $base) { continue }
    Get-ChildItem $base -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
      Sort-Object Name -Descending |
      ForEach-Object { $candidates += (Join-Path $_.FullName 'python.exe') }
  }
  foreach ($candidate in $candidates) { if (Test-Python $candidate) { return $candidate } }
  return $null
}

function Ensure-Python {
  $py = Find-Python
  if ($py) { return $py }
  Say 'Python не найден. Устанавливаю его (1–3 минуты)...'
  $winget = Get-Command winget -ErrorAction SilentlyContinue
  if ($winget) {
    & winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
    $py = Find-Python
  }
  if (-not $py) {
    $installer = Join-Path $env:TEMP 'python-setup.exe'
    try {
      [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
      Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $installer -UseBasicParsing
      Start-Process -FilePath $installer -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_test=0','Include_launcher=1' -Wait
      $py = Find-Python
    } catch { Stop-Install 'не удалось установить Python' }
  }
  if (-not $py) { Stop-Install 'Python не установился' }
  return $py
}

try {
  if ([string]::IsNullOrWhiteSpace($Ref)) { Stop-Install 'не указана ссылка или ID сообщества' }
  $Ref = $Ref.Trim()
  $py = Ensure-Python
  Say ('Python: ' + $py)

  if ($Stage -eq 'prepare') {
    foreach ($dir in @((Join-Path $Root 'src'), (Join-Path $Root 'schema'), $ConfigDir, $TokenDir)) {
      New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    Copy-Item (Join-Path (Join-Path $PluginRoot 'server') 'vk_mcp_server.py') (Join-Path $Root 'src') -Force
    Copy-Item (Join-Path (Join-Path $PluginRoot 'schema') 'catalog.json') (Join-Path $Root 'schema') -Force
    Copy-Item (Join-Path (Join-Path $PluginRoot 'setup') 'setup_helper.py') $Root -Force
    $connections = Join-Path $ConfigDir 'connections.json'
    if (-not (Test-Path $connections)) { [IO.File]::WriteAllText($connections, '{"connections":[]}', $Utf8NoBom) }
    if (-not (Test-Path $TokenFile)) { [IO.File]::WriteAllText($TokenFile, '', $Utf8NoBom) }
    [IO.File]::WriteAllText($PendingFile, (@{ ref = $Ref; python = $py } | ConvertTo-Json), $Utf8NoBom)
    if (-not $env:VK_INSTALL_NO_EDITOR) { Start-Process -FilePath 'notepad.exe' -ArgumentList $TokenFile }
    Say ''
    Say 'Открылся файл ключа в Блокноте.'
    Say 'Вставьте ключ, сохраните файл и напишите в Codex: «готово».'
    Say 'После этого Codex запустит этап finish.'
  } else {
    if (-not (Test-Path $PendingFile)) { Stop-Install 'сначала нужно выполнить этап prepare' }
    $pending = Get-Content $PendingFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $helper = Join-Path $Root 'setup_helper.py'
    if (-not (Test-Path $helper)) { Stop-Install 'файлы установки не найдены: повторите этап prepare' }
    if (-not (Test-Path $TokenFile)) { Stop-Install 'файл ключа не найден' }
    if (-not ('' + (Get-Content $TokenFile -Raw -Encoding UTF8)).Trim()) { Stop-Install 'файл ключа пуст: вставьте ключ и сохраните его' }
    & $py $helper $Root ([string]$pending.ref) $TokenFile
    if ($LASTEXITCODE -ne 0) { Stop-Install 'проверка VK или настройка Codex не завершилась' }
    try {
      # Сначала обнуляем файл, чтобы даже при невозможности удаления в нём
      # не осталось второго экземпляра ключа.
      [IO.File]::WriteAllText($TokenFile, '', $Utf8NoBom)
      Remove-Item $TokenFile -Force -ErrorAction Stop
    } catch {
      Say ('Временный файл не удалился, ключа в нём уже нет. Его можно удалить вручную: ' + $TokenFile)
    }
    Remove-Item $PendingFile -Force -ErrorAction SilentlyContinue
    Say ''
    Write-Host 'ГОТОВО.' -ForegroundColor Green
    Say 'Полностью закройте Codex и откройте его снова.'
    Say 'После перезапуска напишите: Проверь подключение к ВК.'
  }
} catch {
  if ($_.Exception.Message -ne 'VKSTOP') {
    Write-Host ''
    Write-Host ('Непредвиденная ошибка: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Пришлите только текст ошибки, без ключа.'
  }
}
