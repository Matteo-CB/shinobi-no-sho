# Demarrage du serveur LLM local pour Shinobi no Sho
# Usage : .\scripts\start_llm_server.ps1

param(
    [string]$ModelPath = "models\llm\Qwen3-8B-UD-Q5_K_XL.gguf",
    [int]$GpuLayers = 99,
    [int]$ContextSize = 16384,
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"

# Verifier que le modele existe
if (-not (Test-Path $ModelPath)) {
    Write-Host "Modele introuvable a l'emplacement : $ModelPath" -ForegroundColor Red
    Write-Host ""
    Write-Host "Telecharge le depuis :" -ForegroundColor Yellow
    Write-Host "https://huggingface.co/unsloth/Qwen3-8B-GGUF"
    Write-Host ""
    Write-Host "Place le fichier Qwen3-8B-UD-Q5_K_XL.gguf dans : $ModelPath" -ForegroundColor Yellow
    exit 1
}

# Refresh PATH from registry (au cas ou le shell aurait ete lance avant la modification du PATH)
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
$env:Path = "$machinePath;$userPath"

# Verifier que llama-server est trouvable, soit dans le PATH soit aux emplacements connus
$llamaServer = Get-Command llama-server -ErrorAction SilentlyContinue
if (-not $llamaServer) {
    $candidates = @(
        "C:\Users\matte\llama.cpp\llama-server.exe",
        "C:\llama.cpp\llama-server.exe",
        ".\llama.cpp\llama-server.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            $env:Path = "$(Split-Path $candidate);$env:Path"
            $llamaServer = Get-Command llama-server -ErrorAction SilentlyContinue
            if ($llamaServer) {
                Write-Host "llama-server trouve dans : $(Split-Path $candidate)" -ForegroundColor DarkGray
                break
            }
        }
    }
}

if (-not $llamaServer) {
    Write-Host "llama-server introuvable dans le PATH." -ForegroundColor Red
    Write-Host ""
    Write-Host "Installation :" -ForegroundColor Yellow
    Write-Host "1. Telecharge llama-bXXXX-bin-win-cuda-12.4-x64.zip et cudart-llama-bin-win-cuda-12.4-x64.zip"
    Write-Host "   depuis https://github.com/ggml-org/llama.cpp/releases"
    Write-Host "2. Decompresse les deux dans le meme dossier (par exemple C:\Users\matte\llama.cpp\)"
    Write-Host "3. Ajoute ce dossier au PATH systeme"
    exit 1
}

Write-Host "Demarrage du serveur LLM Shinobi no Sho" -ForegroundColor Cyan
Write-Host "  Modele     : $ModelPath"
Write-Host "  GPU layers : $GpuLayers"
Write-Host "  Contexte   : $ContextSize tokens"
Write-Host "  Port       : $Port"
Write-Host ""
Write-Host "Le serveur va se lancer. Attends de voir 'all slots are idle' avant de jouer."
Write-Host "Pour arreter : Ctrl+C dans cette fenetre."
Write-Host ""

llama-server `
    -m $ModelPath `
    -ngl $GpuLayers `
    -c $ContextSize `
    --port $Port `
    --host 127.0.0.1 `
    --jinja
