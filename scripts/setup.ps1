# Bootstrap complet de Shinobi no Sho sur Windows.
# Idempotent : re-executable en toute securite, ne refait que ce qui manque.
#
# Usage : .\scripts\setup.ps1
#         .\scripts\setup.ps1 -SkipModel       # ne pas telecharger le modele Qwen3 (6 Go)
#         .\scripts\setup.ps1 -SkipLlama       # ne pas telecharger llama.cpp
#         .\scripts\setup.ps1 -Quiet           # moins de prompts interactifs
#         .\scripts\setup.ps1 -GitRemote https://github.com/<user>/shinobi-no-sho.git

param(
    [switch]$SkipModel,
    [switch]$SkipLlama,
    [switch]$Quiet,
    [string]$GitRemote = "",
    [string]$LlamaDir = "C:\Users\matte\llama.cpp",
    [string]$ModelUrl = "https://huggingface.co/unsloth/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-UD-Q5_K_XL.gguf",
    [string]$ModelFile = "Qwen3-8B-UD-Q5_K_XL.gguf"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}
function Write-Ok($msg) { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [!!] $msg" -ForegroundColor Yellow }
function Write-Skip($msg) { Write-Host "  [..] $msg" -ForegroundColor DarkGray }

function Ask($prompt, $default) {
    if ($Quiet) { return $default }
    $val = Read-Host "$prompt [$default]"
    if ([string]::IsNullOrWhiteSpace($val)) { return $default }
    return $val
}

# Etape 1 : Verifier Python ----------------------------------------------------
Write-Step "Verification de Python"
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Warn "Python introuvable dans le PATH."
    Write-Host "  Installe Python 3.11+ depuis https://www.python.org/downloads/windows/"
    Write-Host "  Coche 'Add python.exe to PATH' pendant l'installation."
    exit 1
}
$pyVersion = & python --version 2>&1
Write-Ok "$pyVersion"

# Etape 2 : Creer le venv -------------------------------------------------------
Write-Step "Environnement virtuel Python"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "  Creation de .venv..."
    & python -m venv .venv
    Write-Ok ".venv cree"
} else {
    Write-Skip ".venv deja present"
}
$venvPython = ".\.venv\Scripts\python.exe"

# Etape 3 : Installer les deps + projet editable -------------------------------
Write-Step "Installation des dependances et du projet"
& $venvPython -m pip install --upgrade pip --quiet
$pipDeps = "fastapi>=0.115","uvicorn[standard]>=0.30","pydantic>=2.8","pydantic-settings>=2.4",
           "sqlalchemy>=2.0","alembic>=1.13","chromadb>=0.5","sentence-transformers>=3.0",
           "httpx>=0.27","structlog>=24.0","rich>=13.7","typer>=0.12",
           "beautifulsoup4>=4.12","trafilatura>=1.12",
           "pytest>=8.3","pytest-asyncio>=0.24","pytest-cov>=5.0","ruff>=0.6","mypy>=1.11","hypothesis>=6.100"
& $venvPython -m pip install --quiet $pipDeps
& $venvPython -m pip install --quiet -e .
Write-Ok "Dependances installees + projet en mode editable (commande shinobi disponible)"

# Etape 4 : llama.cpp ----------------------------------------------------------
if (-not $SkipLlama) {
    Write-Step "llama.cpp (CUDA 12.4)"
    $llamaServerExe = Join-Path $LlamaDir "llama-server.exe"
    if (Test-Path $llamaServerExe) {
        Write-Skip "llama-server.exe deja present a $LlamaDir"
    } else {
        Write-Host "  Telechargement de la derniere release..."
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" `
                                 -Headers @{"User-Agent"="shinobi-setup"}
        $tag = $rel.tag_name
        $cudaUrl = ($rel.assets | Where-Object { $_.name -like "*cuda-12*" -and $_.name -notlike "*cudart*" }).browser_download_url | Select-Object -First 1
        $cudartUrl = ($rel.assets | Where-Object { $_.name -like "cudart-*cuda-12*" }).browser_download_url | Select-Object -First 1
        if (-not $cudaUrl -or -not $cudartUrl) {
            Write-Warn "Impossible de trouver les assets CUDA 12 dans la release $tag"
            exit 1
        }
        if (-not (Test-Path $LlamaDir)) { New-Item -ItemType Directory -Path $LlamaDir -Force | Out-Null }
        $tmp = Join-Path $env:TEMP "shinobi_llama"
        if (-not (Test-Path $tmp)) { New-Item -ItemType Directory -Path $tmp -Force | Out-Null }
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $cudaUrl -OutFile (Join-Path $tmp "llama.zip")
        Invoke-WebRequest -Uri $cudartUrl -OutFile (Join-Path $tmp "cudart.zip")
        Expand-Archive -Path (Join-Path $tmp "llama.zip") -DestinationPath $LlamaDir -Force
        Expand-Archive -Path (Join-Path $tmp "cudart.zip") -DestinationPath $LlamaDir -Force
        Write-Ok "llama.cpp $tag installe a $LlamaDir"
    }

    # Ajout au PATH utilisateur
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($userPath -split ";") -notcontains $LlamaDir) {
        $newPath = if ([string]::IsNullOrEmpty($userPath)) { $LlamaDir } else { "$userPath;$LlamaDir" }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-Ok "Ajoute au PATH utilisateur (effectif dans les nouveaux shells)"
    } else {
        Write-Skip "Deja dans le PATH utilisateur"
    }
    $env:Path = "$env:Path;$LlamaDir"
} else {
    Write-Skip "Installation llama.cpp ignoree (-SkipLlama)"
}

# Etape 5 : Modele Qwen3 -------------------------------------------------------
if (-not $SkipModel) {
    Write-Step "Modele LLM Qwen3-8B-UD-Q5_K_XL.gguf"
    $modelDir = Join-Path $ProjectRoot "models\llm"
    if (-not (Test-Path $modelDir)) { New-Item -ItemType Directory -Path $modelDir -Force | Out-Null }
    $modelPath = Join-Path $modelDir $ModelFile
    if (Test-Path $modelPath) {
        $sizeGb = [math]::Round((Get-Item $modelPath).Length / 1GB, 2)
        Write-Skip "Modele deja present ($sizeGb Go)"
    } else {
        Write-Host "  Telechargement (5.5 Go, peut prendre plusieurs minutes)..."
        & curl.exe -L --fail --retry 3 --retry-delay 5 -o $modelPath $ModelUrl
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Echec du telechargement du modele"
            exit 1
        }
        $sizeGb = [math]::Round((Get-Item $modelPath).Length / 1GB, 2)
        Write-Ok "Modele telecharge ($sizeGb Go)"
    }
} else {
    Write-Skip "Telechargement du modele ignore (-SkipModel)"
}

# Etape 6 : .env ----------------------------------------------------------------
Write-Step "Fichier de configuration .env"
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Ok ".env cree depuis .env.example"
    } else {
        Write-Warn ".env.example introuvable, .env non cree"
    }
} else {
    Write-Skip ".env deja present"
}

# Etape 7 : Git -----------------------------------------------------------------
Write-Step "Configuration Git"
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
    Write-Warn "git introuvable. Installe-le depuis https://git-scm.com/download/win"
    exit 1
}
if (-not (Test-Path ".git")) {
    & git init -b main 2>&1 | Out-Null
    Write-Ok "Repo git initialise (branche main)"
} else {
    Write-Skip "Repo git deja initialise"
}

$gitName = & git config --global user.name 2>$null
if ([string]::IsNullOrWhiteSpace($gitName)) {
    $name = Ask "Ton nom git" "Matteo"
    & git config --global user.name "$name"
    Write-Ok "git user.name = $name"
} else {
    Write-Skip "git user.name = $gitName"
}
$gitEmail = & git config --global user.email 2>$null
if ([string]::IsNullOrWhiteSpace($gitEmail)) {
    $email = Ask "Ton email git" "matteo.biyikli3224@gmail.com"
    & git config --global user.email "$email"
    Write-Ok "git user.email = $email"
} else {
    Write-Skip "git user.email = $gitEmail"
}

# Remote optionnel
if (-not [string]::IsNullOrWhiteSpace($GitRemote)) {
    $existing = & git remote get-url origin 2>$null
    if ($existing -ne $GitRemote) {
        if (-not [string]::IsNullOrWhiteSpace($existing)) {
            & git remote remove origin 2>$null
        }
        & git remote add origin $GitRemote
        Write-Ok "Remote origin = $GitRemote"
    } else {
        Write-Skip "Remote origin deja configure"
    }
}

# Premier commit si rien d'encore committe
$head = & git rev-parse --verify HEAD 2>$null
if ([string]::IsNullOrWhiteSpace($head)) {
    Write-Host "  Premier commit..."
    & git add CLAUDE.md README.md TUTORIAL.md .env.example .gitignore pyproject.toml ruff.toml mypy.ini docs/ scripts/ src/ tests/ data/canonical/ 2>&1 | Out-Null
    & git -c commit.gpgsign=false commit -m "initial bootstrap" 2>&1 | Out-Null
    Write-Ok "Commit initial cree"
} else {
    Write-Skip "Repo a deja un historique"
}

# Etape 8 : Smoke tests --------------------------------------------------------
Write-Step "Tests de fumee"
& $venvPython -m pytest tests/ -q 2>&1 | Select-Object -Last 3
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Tests en echec, mais setup termine. Inspecte la sortie ci-dessus."
} else {
    Write-Ok "Tests passent"
}

# Final ------------------------------------------------------------------------
Write-Host ""
Write-Host "===============================================" -ForegroundColor Magenta
Write-Host "  Setup termine. Pour jouer :" -ForegroundColor Magenta
Write-Host "===============================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "  1. Demarre le serveur LLM (dans un autre PowerShell) :" -ForegroundColor White
Write-Host "       .\scripts\start_llm_server.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "  2. Active le venv et lance le jeu :" -ForegroundColor White
Write-Host "       .\.venv\Scripts\activate" -ForegroundColor Cyan
Write-Host "       shinobi" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Re-execute ce script a tout moment, il est idempotent." -ForegroundColor DarkGray
