# Builds longshot_vk.dll (the Vulkan back end, with the Quest/OpenXR output) from this tree, using the toolchain the
# Longshot Builder already installed (llvm-mingw, Python, the Vulkan/SPIR-V/VMA headers, NVIDIA's DLSS SDK).
# The game itself is not rebuilt: the DLL is the only file that changes.
#   powershell -File quest\build_vk.ps1                 # build into ..\work\sa\build
#   powershell -File quest\build_vk.ps1 -Install        # and copy it into the game folder (the original kept as .orig)
#   powershell -File quest\build_vk.ps1 -Uninstall      # the original renderer back (longshot_vr.ini and the loader stay, unused)
param(
    [string]$Tools = "",   # (the Builder's tools folder, ...\LongshotBuilder\tools. Not given: the newest installed version's)
    [string]$Game = "$env:USERPROFILE\Games\Longshot",
    [string]$Work = "$env:LOCALAPPDATA\LongshotVR\work",   # (outside OneDrive and the like: the build objects made it sync and swell)
    [switch]$Install,
    [switch]$Uninstall,
    [int]$Jobs = [Environment]::ProcessorCount
)
$ErrorActionPreference = "Stop"
$bin = Join-Path $Game "bin"   # (a Builder's game folder: the launcher at its root, the game in bin; a make_game.py one: the game at its root)
if (-not (Test-Path (Join-Path $bin "longshot_vk.dll"))) { $bin = $Game }
if (Get-Process longshot -ErrorAction SilentlyContinue) { if ($Install -or $Uninstall) { throw "Longshot is running: close it first" } }
if ($Uninstall) {
    $orig = Join-Path $bin "longshot_vk.dll.orig"
    if (-not (Test-Path $orig)) { throw "no $orig to restore" }
    Copy-Item $orig (Join-Path $bin "longshot_vk.dll") -Force
    Write-Host "the original renderer is back in $bin"
    return
}
$repo = Split-Path -Parent $PSScriptRoot
$builder = Join-Path $env:LOCALAPPDATA "LongshotBuilder"
$tools = $Tools
if (-not $tools) {
    $tools = Get-ChildItem (Join-Path $builder "versions") -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending |
        ForEach-Object { Join-Path $_.FullName "LongshotBuilder\tools" } | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $tools -or -not (Test-Path $tools)) { throw "no Longshot Builder tools found (-Tools <folder>, or python -I scripts/tools.py install <folder>)" }

$env:OOT_WORK = $Work
$env:LONGSHOT_MINGW = Join-Path $tools "llvm-mingw"
$env:RX_CPM = Join-Path $tools "cpm"
$ngx = Join-Path $builder "ngx-sdk"
if (Test-Path (Join-Path $ngx "lib")) { $env:LONGSHOT_NGX_SDK = $ngx }
$env:RX_RELEASE = "1"
$env:RX_VK_ONLY = "1"
$env:LONGSHOT_MEM_GATE = "0"

$py = Get-ChildItem (Join-Path $tools "python") -Filter python.exe -Recurse | Select-Object -First 1
if (-not $py) { throw "no python.exe under $tools\python" }
Push-Location $repo
try {
    & $py.FullName -I -B nx2/build.py --standalone --jobs $Jobs
    if ($LASTEXITCODE) { throw "build failed ($LASTEXITCODE)" }
} finally { Pop-Location }

$dll = Join-Path $env:OOT_WORK "sa\build\longshot_vk.dll"
Write-Host "built $dll"
if ($Install) {
    $dst = Join-Path $bin "longshot_vk.dll"
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
    $ini = Join-Path $Game "longshot_vr.ini"
    if (-not (Test-Path $ini)) { Copy-Item (Join-Path $PSScriptRoot "longshot_vr.ini") $ini }
    Write-Host "installed into $bin (the original renderer is $orig); settings in $ini"
}

