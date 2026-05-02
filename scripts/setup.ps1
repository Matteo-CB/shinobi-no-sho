# Bootstrap complet et portable de Shinobi no Sho sur Windows.
# Idempotent : re-executable en toute securite, ne refait que ce qui manque.
# Marche pour n'importe quel utilisateur Windows (pas de paths hardcodes).
#
# Usage : .\scripts\setup.ps1
#         .\scripts\setup.ps1 -SkipModel       # ne pas telecharger le modele Qwen3 (5.5 Go)
#         .\scripts\setup.ps1 -SkipLlama       # ne pas telecharger llama.cpp
#         .\scripts\setup.ps1 -CpuOnly         # forcer le build CPU (pas de CUDA)
#         .\scripts\setup.ps1 -Quiet           # zero prompt interactif
#         .\scripts\setup.ps1 -GitRemote https://github.com/<user>/shinobi-no-sho.git

param(
    [switch]$SkipModel,
    [switch]$SkipLlama,
    [switch]$CpuOnly,
    [switch]$Quiet,
    [string]$GitRemote = "",
    [string]$LlamaDir = "",
    [string]$ModelUrl = "https://huggingface.co/unsloth/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-UD-Q5_K_XL.gguf",
    [string]$ModelFile = "Qwen3-8B-UD-Q5_K_XL.gguf"
)

if ([string]::IsNullOrWhiteSpace($LlamaDir)) {
    $LlamaDir = Join-Path $env:USERPROFILE "llama.cpp"
}

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

function ConfirmContinue($prompt) {
    if ($Quiet) { return $true }
    $ans = Read-Host "$prompt [y/N]"
    return ($ans -eq "y" -or $ans -eq "Y")
}

# Etape 0 : Pre-requis systeme -------------------------------------------------
Write-Step "Verification des pre-requis systeme"

# Python 3.11+
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Warn "Python introuvable dans le PATH."
    Write-Host "  Installer via : winget install Python.Python.3.13" -ForegroundColor Yellow
    Write-Host "  Ou manuellement : https://www.python.org/downloads/windows/"
    Write-Host "  IMPORTANT : coche 'Add python.exe to PATH' pendant l'installation."
    exit 1
}
$pyVerRaw = & python --version 2>&1
$pyVerMatch = [regex]::Match("$pyVerRaw", "(\d+)\.(\d+)")
if ($pyVerMatch.Success) {
    $major = [int]$pyVerMatch.Groups[1].Value
    $minor = [int]$pyVerMatch.Groups[2].Value
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
        Write-Warn "$pyVerRaw detecte, mais Python 3.11+ requis."
        exit 1
    }
}
Write-Ok "Python : $pyVerRaw"

# Git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Warn "git introuvable. Installer via : winget install Git.Git"
    exit 1
}
Write-Ok "git installe"

# GPU NVIDIA (recommande pour le LLM)
$cpuOnlyMode = $CpuOnly
if (-not $cpuOnlyMode) {
    $nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($nvidia) {
        $gpuInfo = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>&1
        Write-Ok "GPU NVIDIA detectee : $gpuInfo"
    } else {
        Write-Warn "Aucune GPU NVIDIA detectee. Le LLM tournera sur CPU (tres lent : 1-3 tok/s)."
        if (-not (ConfirmContinue "  Continuer en mode CPU ?")) { exit 0 }
        $cpuOnlyMode = $true
    }
} else {
    Write-Ok "Mode CPU force par l'utilisateur"
}

# Espace disque
$qualifier = (Split-Path -Qualifier $ProjectRoot).Replace(":", "")
$drive = (Get-PSDrive -Name $qualifier).Free / 1GB
$needed = if ($SkipModel) { 4 } else { 12 }
if ($drive -lt $needed) {
    Write-Warn "Seulement $([math]::Round($drive,1)) Go disponibles, $needed Go recommandes."
    if (-not (ConfirmContinue "  Continuer quand meme ?")) { exit 0 }
} else {
    Write-Ok "Espace disque OK ($([math]::Round($drive,1)) Go disponibles)"
}

# Etape 1 : Creer le venv -------------------------------------------------------
Write-Step "Environnement virtuel Python"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "  Creation de .venv..."
    & python -m venv .venv
    Write-Ok ".venv cree"
} else {
    Write-Skip ".venv deja present"
}
$venvPython = ".\.venv\Scripts\python.exe"

# Etape 2 : Installer les deps + projet editable -------------------------------
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

# Etape 3 : llama.cpp ----------------------------------------------------------
if (-not $SkipLlama) {
    if ($cpuOnlyMode) {
        Write-Step "llama.cpp (build CPU x64)"
        $llamaAssetPattern = "*-bin-win-cpu-x64.zip"
        $cudartNeeded = $false
    } else {
        Write-Step "llama.cpp (build CUDA 12.4)"
        $llamaAssetPattern = "*-bin-win-cuda-12*-x64.zip"
        $cudartNeeded = $true
    }
    $llamaServerExe = Join-Path $LlamaDir "llama-server.exe"
    if (Test-Path $llamaServerExe) {
        Write-Skip "llama-server.exe deja present a $LlamaDir"
    } else {
        Write-Host "  Telechargement de la derniere release..."
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" `
                                 -Headers @{"User-Agent"="shinobi-setup"}
        $tag = $rel.tag_name
        $mainAsset = $rel.assets | Where-Object {
            $_.name -like $llamaAssetPattern -and $_.name -notlike "*cudart*"
        } | Select-Object -First 1
        if (-not $mainAsset) {
            Write-Warn "Impossible de trouver l'asset llama.cpp ($llamaAssetPattern) dans la release $tag"
            Write-Host "  Telecharger manuellement depuis : https://github.com/ggml-org/llama.cpp/releases/$tag"
            exit 1
        }
        if (-not (Test-Path $LlamaDir)) { New-Item -ItemType Directory -Path $LlamaDir -Force | Out-Null }
        $tmp = Join-Path $env:TEMP "shinobi_llama"
        if (-not (Test-Path $tmp)) { New-Item -ItemType Directory -Path $tmp -Force | Out-Null }
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $mainAsset.browser_download_url -OutFile (Join-Path $tmp "llama.zip")
        Expand-Archive -Path (Join-Path $tmp "llama.zip") -DestinationPath $LlamaDir -Force
        if ($cudartNeeded) {
            $cudartAsset = $rel.assets | Where-Object {
                $_.name -like "cudart-*cuda-12*"
            } | Select-Object -First 1
            if ($cudartAsset) {
                Invoke-WebRequest -Uri $cudartAsset.browser_download_url -OutFile (Join-Path $tmp "cudart.zip")
                Expand-Archive -Path (Join-Path $tmp "cudart.zip") -DestinationPath $LlamaDir -Force
            }
        }
        Write-Ok "llama.cpp $tag installe a $LlamaDir"
    }

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

# Etape 4 : Modele Qwen3 -------------------------------------------------------
if (-not $SkipModel) {
    Write-Step "Modele LLM Qwen3-8B-UD-Q5_K_XL.gguf (5.5 Go)"
    $modelDir = Join-Path $ProjectRoot "models\llm"
    if (-not (Test-Path $modelDir)) { New-Item -ItemType Directory -Path $modelDir -Force | Out-Null }
    $modelPath = Join-Path $modelDir $ModelFile
    if (Test-Path $modelPath) {
        $sizeGb = [math]::Round((Get-Item $modelPath).Length / 1GB, 2)
        Write-Skip "Modele deja present ($sizeGb Go)"
    } else {
        Write-Host "  Telechargement (peut prendre 5 a 30 minutes selon la connexion)..."
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

# Etape 5 : Launchers globaux dans bin/ et ajout au PATH ----------------------
Write-Step "Launcher global shinobi (utilisable dans cmd, PowerShell, bash)"
$binDir = Join-Path $ProjectRoot "bin"
if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($userPath -split ";") -notcontains $binDir) {
    $newPath = if ([string]::IsNullOrEmpty($userPath)) { $binDir } else { "$userPath;$binDir" }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Ok "$binDir ajoute au PATH utilisateur"
    Write-Ok "Ouvre un NOUVEAU shell pour que la commande 'shinobi' soit disponible globalement"
} else {
    Write-Skip "$binDir deja dans le PATH utilisateur"
}
$env:Path = "$env:Path;$binDir"

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
if (-not (Test-Path ".git")) {
    & git init -b main 2>&1 | Out-Null
    Write-Ok "Repo git initialise (branche main)"
} else {
    Write-Skip "Repo git deja initialise"
}

$gitName = & git config --global user.name 2>$null
if ([string]::IsNullOrWhiteSpace($gitName)) {
    $name = Ask "  Ton nom git" $env:USERNAME
    & git config --global user.name "$name"
    Write-Ok "git user.name = $name"
} else {
    Write-Skip "git user.name = $gitName"
}
$gitEmail = & git config --global user.email 2>$null
if ([string]::IsNullOrWhiteSpace($gitEmail)) {
    $email = Ask "  Ton email git" "$env:USERNAME@example.com"
    & git config --global user.email "$email"
    Write-Ok "git user.email = $email"
} else {
    Write-Skip "git user.email = $gitEmail"
}

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

$head = & git rev-parse --verify HEAD 2>$null
if ([string]::IsNullOrWhiteSpace($head)) {
    Write-Host "  Premier commit..."
    & git add CLAUDE.md README.md TUTORIAL.md .env.example .gitignore pyproject.toml ruff.toml mypy.ini docs/ scripts/ src/ tests/ data/canonical/ bin/ 2>&1 | Out-Null
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
Write-Host "  1. Demarre le serveur LLM (autre PowerShell) :" -ForegroundColor White
Write-Host "       .\scripts\start_llm_server.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "  2. Ouvre un NOUVEAU PowerShell et lance :" -ForegroundColor White
Write-Host "       shinobi" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Re-execute ce script a tout moment, il est idempotent." -ForegroundColor DarkGray
