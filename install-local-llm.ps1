param(
    [string]$PythonPath = "",
    [ValidateSet("all", "gguf", "transformers")][string]$Profile = "all",
    [ValidateSet("auto", "cuda", "cpu")][string]$Device = "auto",
    [switch]$CheckOnly
)
$ErrorActionPreference = "Stop"
$nodeRoot = $PSScriptRoot
$comfyRoot = Split-Path (Split-Path $nodeRoot -Parent) -Parent
$bundleRoot = Split-Path $comfyRoot -Parent
if (-not $PythonPath) {
    $candidates = @(@(
        (Join-Path $bundleRoot "python\python.exe"),
        (Join-Path $bundleRoot "python_embeded\python.exe"),
        (Join-Path $bundleRoot "python_embedded\python.exe"),
        (Join-Path $comfyRoot ".venv\Scripts\python.exe"),
        (Join-Path $comfyRoot "venv\Scripts\python.exe"),
        (Join-Path $bundleRoot ".venv\Scripts\python.exe"),
        (Join-Path $bundleRoot "venv\Scripts\python.exe")
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -Unique)
    if (@($candidates).Count -eq 1) {
        $PythonPath = $candidates[0]
    } else {
        Write-Host "Enter the full path of the Python executable used to START ComfyUI."
        if (@($candidates).Count -gt 1) { $candidates | ForEach-Object { Write-Host $_ } }
        $PythonPath = (Read-Host "ComfyUI Python path").Trim().Trim('"')
    }
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python executable not found. Use the Python that starts ComfyUI."
}
$resolvedPython = (Resolve-Path -LiteralPath $PythonPath).Path
Write-Host "Easy H3 dependency installer - Python: $resolvedPython"
$installerArguments = @((Join-Path $nodeRoot "install.py"), "--profile", $Profile, "--device", $Device)
if ($CheckOnly) { $installerArguments += "--check" }
& $resolvedPython @installerArguments
exit $LASTEXITCODE
