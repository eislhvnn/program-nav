# 程序导航 一键更新：拉取最新代码 → 打包 → 静默安装 → 重新启动
# 双击同目录的「更新.bat」运行。加 -Force 可以在没有新代码时也重新打包安装
# 新电脑上：git clone 下来后直接运行，会自动创建 venv、安装依赖（需要先装好 Python 3 和 Inno Setup 6）
# 个人数据（程序列表等）在安装目录里，安装时不会被覆盖
param([switch]$Force)
Set-Location $PSScriptRoot
$stamp = 'build\installed-commit.txt'  # 上次装的是哪个提交（build 不进仓库）
$appKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F1C2B7E-4D3A-4B8E-9C61-2E7A5D0F9B13}_is1'

function Fail($msg) { Write-Host "`n✗ $msg" -ForegroundColor Red; exit 1 }
function Step($msg) { Write-Host "`n▶ $msg" -ForegroundColor Cyan }

Step '拉取最新代码'
git pull --ff-only
if ($LASTEXITCODE) { Fail '拉取失败：检查网络和 GitHub 登录；如果在这台电脑上改过代码，先提交或撤销改动' }
$head = (git rev-parse HEAD).Trim()
$last = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { '' }
if ($head -eq $last -and -not $Force) {
  Write-Host "`n✓ 已经是最新版本（$($head.Substring(0, 7))），不用更新" -ForegroundColor Green
  exit 0
}
git log -1 --format='  要安装的版本：%h %s（%cr）'

if (-not (Test-Path '.venv\Scripts\python.exe')) {
  Step '第一次运行：创建 Python 虚拟环境'
  $py = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
  & $py -m venv .venv
  if (-not (Test-Path '.venv\Scripts\python.exe')) { Fail '创建虚拟环境失败：请先安装 Python 3（python.org）' }
}
Step '检查依赖'
& .\.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE) { Fail '安装依赖失败，检查网络后重试' }

Step '打包（约 1 分钟）'
& cmd /c "`"$PSScriptRoot\build_installer.bat`""
if ($LASTEXITCODE) { Fail '打包失败，看上面的输出' }
$ver = (Get-Content version.txt -Raw).Trim()
$setup = "build\installer\程序导航-安装程序-$ver.exe"
if (-not (Test-Path $setup)) { Fail "找不到安装包 $setup" }

Step "安装 $ver（会先关掉正在运行的程序导航）"
$p = Start-Process $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/CLOSEAPPLICATIONS', '/NORESTART' -Wait -PassThru
if ($p.ExitCode) { Fail "安装失败（退出码 $($p.ExitCode)）" }
Set-Content $stamp $head

$dir = (Get-ItemProperty $appKey -ErrorAction SilentlyContinue).InstallLocation
if ($dir -and (Test-Path "$dir\程序导航.exe")) { Start-Process "$dir\程序导航.exe" -WorkingDirectory $dir }
Write-Host "`n✓ 已更新到 $ver（$($head.Substring(0, 7))），程序导航已重新启动" -ForegroundColor Green
