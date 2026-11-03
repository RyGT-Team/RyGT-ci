param(
    [Parameter(Mandatory=$true)][string]$SourceRoot,
    [Parameter(Mandatory=$true)][string]$SourceSha,
    [Parameter(Mandatory=$true)][string]$OutputRoot,
    [string]$UcrtRoot = 'C:\msys64\ucrt64'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($SourceSha -cnotmatch '^[0-9a-f]{40}$') { throw 'SourceSha must be a full lowercase Git SHA' }
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$workspace = [IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $workspace) { throw 'OutputRoot must not already exist' }
New-Item -ItemType Directory -Path $workspace | Out-Null
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
function Invoke-CmdFile([string]$Script, [string]$Argument) {
    # cmd /c must receive a single quoted command when paths contain spaces.
    $line = 'call "' + $Script + '" "' + $Argument + '"'
    & $env:ComSpec /d /c $line
    if ($LASTEXITCODE -ne 0) { throw "$Script failed with exit code $LASTEXITCODE" }
}
function Verify-Commit([string]$Directory, [string]$Expected) {
    $actual = (& git -C $Directory rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $actual -cne $Expected) { throw "Source commit mismatch: $Directory" }
}
Verify-Commit $source $SourceSha
if (!$env:LLVM_ROOT -or !$env:VCINSTALLDIR) { throw 'LLVM_ROOT and VS x64 developer environment are required' }
$llvm = (Resolve-Path -LiteralPath $env:LLVM_ROOT).Path
$clangVersion = (& "$llvm\bin\clang-cl.exe" --version | Out-String)
if ($LASTEXITCODE -ne 0 -or $clangVersion -notmatch '23\.1\.3') { throw 'LLVM 23.1.3 required' }
$builtins = Get-ChildItem "$llvm\lib" -Recurse -Filter clang_rt.builtins-x86_64.lib | Select-Object -First 1
if (!$builtins) { throw 'LLVM compiler-rt builtins are required' }
$ucrt = (Resolve-Path -LiteralPath $UcrtRoot).Path
$gcc = Join-Path $ucrt 'bin\gcc.exe'; $runtimeCmake = Join-Path $ucrt 'bin\cmake.exe'
$msysBin = Join-Path (Split-Path $ucrt -Parent) 'usr\bin'
if (!(Test-Path $gcc) -or !(Test-Path $runtimeCmake)) { throw 'MSYS2 UCRT64 GCC and CMake are required' }
$oldPath = $env:PATH; $oldLib = $env:LIB
try {
    $env:PATH = "$ucrt\bin;$msysBin;$oldPath"
    Invoke-Checked git @('-C',$source,'submodule','update','--init','--recursive')
    Invoke-CmdFile (Join-Path $source 'loader\test-gt7.cmd') (Join-Path $workspace 'launcher-tests')
    Invoke-Checked 'python' @((Join-Path $source 'build-support\test_package_release.py'))
    $runtimeBuild = Join-Path $workspace 'runtime-build'
    Invoke-Checked $runtimeCmake @('-S',$source,'-B',$runtimeBuild,'-G','Ninja','-DCMAKE_BUILD_TYPE=Release','-DRTY_BUILD_GUI=OFF','-DBUILD_TESTING=OFF','-DRTY_ENABLE_LTO=OFF',"-DCMAKE_C_COMPILER=$gcc", "-DCMAKE_CXX_COMPILER=$ucrt/bin/g++.exe")
    Invoke-Checked $runtimeCmake @('--build',$runtimeBuild,'--target','ryty','libs','ryty-wheel-probe','--parallel','4')
    $launcher = Join-Path $workspace 'launcher'; New-Item -ItemType Directory -Path $launcher | Out-Null
    Invoke-CmdFile (Join-Path $source 'loader\build.cmd') $launcher
    $stubDir = Join-Path $workspace 'gt7-stubs'
    $env:PATH = "$llvm\bin;$ucrt\bin;$msysBin;$oldPath"
    $env:LIB = "$($builtins.DirectoryName);$oldLib"
    Invoke-CmdFile (Join-Path $source 'runtime\gt7\build.cmd') $stubDir
    $pin = Get-Content (Join-Path $source 'build-support\renderer.json') -Raw | ConvertFrom-Json
    if ($pin.commit -cne '5bfec380dd02ecd56b482b1d8ed4e81131dc699d' -or
        $pin.repository -cne 'https://gitlab.com/Maxi_Razorback/shadgt7.git' -or
        $pin.branch -cne 'fix/amd-pipeline-policy') { throw 'Unexpected renderer pin; update and re-audit adapter explicitly' }
    $mirror = 'https://gitlab.com/rygt-team/shadgt7.git'
    $renderer = Join-Path $workspace 'renderer-source'
    Invoke-Checked git @('clone','--no-checkout',$mirror,$renderer)
    Invoke-Checked git @('-C',$renderer,'fetch','--no-tags','origin',$pin.commit)
    Invoke-Checked git @('-C',$renderer,'checkout','--detach','FETCH_HEAD')
    Verify-Commit $renderer $pin.commit
    Invoke-Checked git @('-C',$renderer,'submodule','update','--init','--recursive')
    $rendererBuild = Join-Path $workspace 'renderer-build'
    Invoke-Checked 'cmake' @('-S',$renderer,'-B',$rendererBuild,'-G','Ninja','-DCMAKE_BUILD_TYPE=Release','-DCMAKE_C_COMPILER=clang-cl','-DCMAKE_CXX_COMPILER=clang-cl','-DCMAKE_LINKER=lld-link','-DSHADGT_RYTY_GPU=ON','-DHAVE_ENDIAN_H=OFF','-DENABLE_DISCORD_RPC=ON')
    if (!(Select-String -LiteralPath (Join-Path $rendererBuild 'CMakeCache.txt') -Pattern '^ENABLE_DISCORD_RPC:BOOL=ON$' -Quiet)) { throw 'Renderer Discord RPC configuration was not enabled' }
    Invoke-Checked 'cmake' @('--build',$rendererBuild,'--target','ryty_gpu','ryty_audio3d','ryty_ajm','ryty_ngs2','ryty_zlib','ryty_discmap','ryty_screenshot','ryty_np','--parallel','4')
    $base = Join-Path $workspace 'runtime-base.zip'
    Invoke-WebRequest -Uri 'https://gitlab.com/api/v4/projects/88084032/packages/generic/rygt7/2026-10-31/RyGT7-2026-10-31-prerelease.zip' -OutFile $base
    $runtimeSubmodules = @(& git -C $source submodule status --recursive)
    if ($LASTEXITCODE -ne 0 -or @($runtimeSubmodules | Where-Object { $_ -match '^[-+U]' }).Count -ne 0) { throw 'Runtime submodule checkout mismatch' }
    $rendererSubmodules = @(& git -C $renderer submodule status --recursive)
    if ($LASTEXITCODE -ne 0 -or @($rendererSubmodules | Where-Object { $_ -match '^[-+U]' }).Count -ne 0) { throw 'Renderer submodule checkout mismatch' }
    $toolchainInfo = Join-Path $workspace 'toolchain-info.json'
    @{ gcc = ((& $gcc --version | Select-Object -First 1) -join ''); clang = $clangVersion.Trim();
       runtime_submodules = $runtimeSubmodules; renderer_submodules = $rendererSubmodules } |
        ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 $toolchainInfo
    $packageOut = Join-Path $workspace 'dist'; New-Item -ItemType Directory -Path $packageOut | Out-Null
    $name = "RyGT7-ci-$($SourceSha.Substring(0,8))"
    Invoke-Checked 'python' @((Join-Path $PSScriptRoot 'assemble.py'), '--source',$source,'--base',$base,'--runtime',(Join-Path $workspace 'assembled-runtime'), '--built-libs',(Join-Path $runtimeBuild 'core\libs\libs'), '--renderer-build',$rendererBuild,'--stubs',$stubDir,'--ucrt',$ucrt,'--info',(Join-Path $workspace 'BUILD_INFO.json'),'--launcher',(Join-Path $launcher 'RyGT7.exe'),'--converter',(Join-Path $runtimeBuild 'core\relinker\ryty.exe'),'--renderer-source',$renderer,'--wheel-probe',(Join-Path $runtimeBuild 'core\libs\prx\libScePad\ryty-wheel-probe.exe'),'--out',$packageOut,'--source-sha',$SourceSha,'--renderer-sha',$pin.commit,'--renderer-configured-url',$pin.repository,'--renderer-fetch-url',$mirror,'--toolchain-info',$toolchainInfo,'--name',$name)
    Write-Output "GT7 package: $packageOut\$name.zip"
} finally { $env:PATH=$oldPath; $env:LIB=$oldLib }
