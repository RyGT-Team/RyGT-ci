$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# This script runs only on the disposable hosted Windows VM.
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ($LASTEXITCODE -or !$vs) { throw 'Visual Studio C++ x64 tools are required' }
& (Join-Path $vs 'Common7/Tools/Launch-VsDevShell.ps1') -Arch amd64 -HostArch amd64 -SkipAutomaticLocation
if (!$env:VCINSTALLDIR) { throw 'Visual Studio developer environment was not initialized' }

$tools = 'C:/rygt7-ci/tools'
New-Item -ItemType Directory -Path $tools -Force | Out-Null
$archive = Join-Path $tools 'llvm-23.1.3.tar.xz'
Invoke-WebRequest -Uri 'https://github.com/llvm/llvm-project/releases/download/llvmorg-23.1.3/clang%2Bllvm-23.1.3-x86_64-pc-windows-msvc.tar.xz' -OutFile $archive
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne '9ee6a2192fbb30c776415af529927f76ca6b5d1d55699bb1315f9878e610b5b8') {
    throw 'LLVM archive SHA-256 mismatch'
}
$sevenZip = Join-Path $env:ProgramFiles '7-Zip/7z.exe'
& $sevenZip x $archive "-o$tools" -y -bso0 -bsp0
if ($LASTEXITCODE) { throw 'LLVM XZ extraction failed' }
& $sevenZip x (Join-Path $tools 'llvm-23.1.3.tar') "-o$tools" -y -bso0 -bsp0
if ($LASTEXITCODE) { throw 'LLVM TAR extraction failed' }
$env:LLVM_ROOT = Join-Path $tools 'clang+llvm-23.1.3-x86_64-pc-windows-msvc'
if (!(Test-Path -LiteralPath (Join-Path $env:LLVM_ROOT 'bin/clang-cl.exe'))) { throw 'LLVM compiler missing' }
python -m pip install --disable-pip-version-check cmake==3.31.6 ninja==1.11.1.4
if ($LASTEXITCODE) { throw 'CMake/Ninja installation failed' }
$scripts = & python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
if ($LASTEXITCODE) { throw 'Python scripts path lookup failed' }
$env:PATH = "$env:LLVM_ROOT/bin;$scripts;$env:PATH"
