# Builds hookshot_vk.dll (the Vulkan back end, with the Quest/OpenXR output) from this tree, using the toolchain the
# Hookshot Builder already installed (llvm-mingw, Python, the Vulkan/SPIR-V/VMA headers, NVIDIA's DLSS SDK).
# The game itself is not rebuilt: the DLL is the only file that changes.
#   powershell -File quest\build_vk.ps1                 # build into ..\work\sa\build
#   powershell -File quest\build_vk.ps1 -Install        # and copy it into the game folder (the original kept as .orig)
#   powershell -File quest\build_vk.ps1 -Uninstall      # the original renderer back (hookshot_vr.ini and the loader stay, unused)
param(
    [string]$BuilderVersion = "v2.1.0",
    [string]$Game = "$env:USERPROFILE\Games\Hookshot",
    [string]$Work = "$env:LOCALAPPDATA\HookshotVR\work",   # (outside OneDrive and the like: the build objects made it sync and swell)
    [switch]$Install,
    [switch]$Uninstall,
    [int]$Jobs = [Environment]::ProcessorCount
)
$ErrorActionPreference = "Stop"
$bin = Join-Path $Game "bin"
if (Get-Process hookshot -ErrorAction SilentlyContinue) { if ($Install -or $Uninstall) { throw "Hookshot is running: close it first" } }
if ($Uninstall) {
    $orig = Join-Path $bin "hookshot_vk.dll.orig"
    if (-not (Test-Path $orig)) { throw "no $orig to restore" }
    Copy-Item $orig (Join-Path $bin "hookshot_vk.dll") -Force
    Write-Host "the original renderer is back in $bin"
    return
}
$repo = Split-Path -Parent $PSScriptRoot
$builder = Join-Path $env:LOCALAPPDATA "HookshotBuilder"
$tools = Join-Path $builder "versions\$BuilderVersion\HookshotBuilder\tools"
if (-not (Test-Path $tools)) { throw "no Hookshot Builder $BuilderVersion at $tools" }

$env:OOT_WORK = $Work
$env:HOOKSHOT_MINGW = Join-Path $tools "llvm-mingw"
$env:RX_CPM = Join-Path $tools "cpm"
$ngx = Join-Path $builder "ngx-sdk"
if (Test-Path (Join-Path $ngx "lib")) { $env:HOOKSHOT_NGX_SDK = $ngx }
$env:RX_RELEASE = "1"
$env:RX_VK_ONLY = "1"
$env:HOOKSHOT_MEM_GATE = "0"

$py = Get-ChildItem (Join-Path $tools "python") -Filter python.exe -Recurse | Select-Object -First 1
if (-not $py) { throw "no python.exe under $tools\python" }
Push-Location $repo
try {
    & $py.FullName -I -B nx2/build.py --standalone --jobs $Jobs
    if ($LASTEXITCODE) { throw "build failed ($LASTEXITCODE)" }
} finally { Pop-Location }

$dll = Join-Path $env:OOT_WORK "sa\build\hookshot_vk.dll"
Write-Host "built $dll"
if ($Install) {
    $dst = Join-Path $bin "hookshot_vk.dll"
    $orig = "$dst.orig"
    if (-not (Test-Path $orig)) { Copy-Item $dst $orig }
    Copy-Item $dll $dst -Force
    # (Khronos' loader is not in the repository: its release's own package, checked by its SHA-256, kept in questin)
    $loader = Join-Path $PSScriptRoot "bin\openxr_loader.dll"
    if (-not (Test-Path $loader)) {
        $zip = Join-Path $env:TEMP "openxr_loader_windows-1.1.65.zip"
        Invoke-WebRequest "https://github.com/KhronosGroup/OpenXR-SDK-Source/releases/download/release-1.1.65/openxr_loader_windows-1.1.65.zip" -OutFile $zip
        if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne "859D7DE35B7A968112DB770B09ED8BDDEED3CB16FD8AE070643A857676BEA6FD") { throw "openxr_loader_windows-1.1.65.zip is not Khronos' release (SHA-256)" }
        $out = Join-Path $env:TEMP "openxr_loader_windows-1.1.65"
        Expand-Archive $zip $out -Force
        New-Item -ItemType Directory -Force (Split-Path $loader) | Out-Null
        Copy-Item (Join-Path $out "x64in\openxr_loader.dll") $loader
    }
    Copy-Item $loader (Join-Path $bin "openxr_loader.dll") -Force
    $ini = Join-Path $Game "hookshot_vr.ini"
    if (-not (Test-Path $ini)) { Copy-Item (Join-Path $PSScriptRoot "hookshot_vr.ini") $ini }
    Write-Host "installed into $bin (the original renderer is $orig); settings in $ini"
}

