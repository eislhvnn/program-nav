# 程序导航 发布新版本：打包 → 在 GitHub 上创建 Release 并上传安装包
# 程序里「设置 → 检查更新」就是从这里下载的。双击同目录的「发布.bat」运行
# 发布前：改好 version.txt 里的版本号，提交并推送代码
Set-Location $PSScriptRoot

function Fail($msg) { Write-Host "`n✗ $msg" -ForegroundColor Red; exit 1 }
function Step($msg) { Write-Host "`n▶ $msg" -ForegroundColor Cyan }

$ver = (Get-Content version.txt -Raw).Trim()
$tag = "v$ver"
Step "准备发布 $tag"
if (git status --porcelain) { Fail '还有没提交的改动，先提交再发布' }
gh release view $tag *> $null
if ($LASTEXITCODE -eq 0) { Fail "$tag 已经发布过了，先把 version.txt 里的版本号改大" }
git push
if ($LASTEXITCODE) { Fail '推送代码失败' }

Step '打包（约 1 分钟）'
& cmd /c "`"$PSScriptRoot\build_installer.bat`""
if ($LASTEXITCODE) { Fail '打包失败，看上面的输出' }
$setup = "build\installer\程序导航-安装程序-$ver.exe"
if (-not (Test-Path $setup)) { Fail "找不到安装包 $setup" }

Step '上传到 GitHub'
# 更新说明：上次发布以来的提交标题；第一次发布就用最近一次提交
$prev = git describe --tags --abbrev=0 2>$null
$notes = if ($prev) { git log "$prev..HEAD" --format='- %s' } else { git log -1 --format='- %s' }
$notesFile = Join-Path $env:TEMP "program-nav-notes.txt"
Set-Content $notesFile ($notes -join "`n") -Encoding UTF8
gh release create $tag $setup --title "程序导航 $ver" --notes-file $notesFile --target (git rev-parse HEAD)
$ok = $LASTEXITCODE
Remove-Item $notesFile -ErrorAction SilentlyContinue
if ($ok) { Fail '创建 Release 失败' }
git fetch --tags -q
Write-Host "`n✓ 已发布 $tag。各电脑上的程序导航在「设置」里就能检查到并更新" -ForegroundColor Green
