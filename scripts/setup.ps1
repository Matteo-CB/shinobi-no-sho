# Bootstrap COMPLET et PORTABLE de Shinobi no Sho sur Windows.
# Idempotent : re-executable en toute securite, ne refait que ce qui manque.
# Detecte automatiquement la GPU (NVIDIA / AMD / Intel) et choisit le meilleur
# backend pour llama.cpp (CUDA, Vulkan, CPU) + PyTorch (cu128 nightly, cu124,
# cu121, ou CPU). Auto-tune le nombre de layers GPU selon la VRAM disponible.
#
# Usage : .\scripts\setup.ps1
#         .\scripts\setup.ps1 -SkipModel       # ne pas telecharger le modele Qwen3 (5.5 Go)
#         .\scripts\setup.ps1 -SkipLlama       # ne pas telecharger llama.cpp
#         .\scripts\setup.ps1 -CpuOnly         # forcer le mode CPU (pas de GPU)
#         .\scripts\setup.ps1 -Backend vulkan  # forcer Vulkan (au lieu de CUDA pour NVIDIA)
#         .\scripts\setup.ps1 -Quiet           # zero prompt interactif
#         .\scripts\setup.ps1 -GitRemote https://github.com/<user>/shinobi-no-sho.git

param(
    [switch]$SkipModel,
    [switch]$SkipLlama,
    [switch]$CpuOnly,
    [ValidateSet("auto", "cuda", "vulkan", "cpu")]
    [string]$Backend = "auto",
    [ValidateSet("auto", "tiny", "small", "medium", "large", "xlarge")]
    [string]$ModelSize = "auto",
    [switch]$Quiet,
    [string]$GitRemote = "",
    [string]$LlamaDir = ""
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
function Write-Info($msg) { Write-Host "  $msg" -ForegroundColor White }

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

# ----------------------------------------------------------------------------
# Detection materielle
# ----------------------------------------------------------------------------

function Detect-Gpu {
    # Retourne un hashtable : vendor, name, vram_mib, compute_cap
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        try {
            $raw = & nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader,nounits 2>$null
            if ($raw) {
                $line = ($raw -split "`n")[0].Trim()
                $parts = $line -split "," | ForEach-Object { $_.Trim() }
                return @{
                    vendor = "nvidia"
                    name = $parts[0]
                    vram_mib = [int]$parts[1]
                    compute_cap = $parts[2]
                }
            }
        } catch {}
    }
    # AMD / Intel via WMI
    try {
        $gpus = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue
        foreach ($gpu in $gpus) {
            $name = $gpu.Name
            $ramBytes = [int64]$gpu.AdapterRAM
            $vramMib = if ($ramBytes -gt 0) { [int]($ramBytes / 1MB) } else { 0 }
            if ($name -match 'AMD|Radeon|\bRX\b') {
                return @{ vendor = "amd"; name = $name; vram_mib = $vramMib; compute_cap = "" }
            }
            if ($name -match 'Intel.*Arc|Intel\(R\) Arc') {
                return @{ vendor = "intel"; name = $name; vram_mib = $vramMib; compute_cap = "" }
            }
        }
    } catch {}
    return @{ vendor = "none"; name = "(aucune GPU dediee)"; vram_mib = 0; compute_cap = "" }
}

function Get-OptimalGpuLayers([int]$vramMib) {
    # Pour Qwen3-4B Q4_K_XL (~2.5 Go base + ~1 Go KV cache 8k) : tient en 4-5 Go.
    # Si Qwen3-8B Q5 est utilise (~5.5 Go) : il faut 8 Go+ pour full offload.
    if ($vramMib -ge 6000) { return 99 }   # full GPU avec marge confortable
    if ($vramMib -ge 4000) { return 99 }   # full GPU (ok pour 4B)
    if ($vramMib -ge 2500) { return 32 }   # offload partiel
    if ($vramMib -ge 1500) { return 16 }
    return 0  # full CPU
}

function Resolve-LlamaBackend($gpu) {
    if ($CpuOnly) { return "cpu" }
    if ($Backend -ne "auto") { return $Backend }
    if ($gpu.vendor -eq "none") { return "cpu" }
    if ($gpu.vendor -eq "nvidia") { return "cuda" }
    # AMD, Intel Arc, ou autre : Vulkan marche partout
    return "vulkan"
}

function Resolve-EmbeddingsDevice($gpu, $cudaWorks) {
    if ($CpuOnly) { return "cpu" }
    if ($gpu.vendor -eq "nvidia" -and $cudaWorks) { return "cuda" }
    return "cpu"
}

function Resolve-ModelChoice([int]$vramMib, [bool]$forceCpu, [string]$override) {
    # Retourne hashtable : name, file, url, size_gb, ctx_default, max_tokens
    # Le defaut est equilibre vitesse/qualite. Le user peut forcer via -ModelSize.
    $catalog = @{
        tiny = @{
            name = "Qwen3 1.7B Q4 (CPU ou 2 Go VRAM, ultra-rapide ~80 tok/s)"
            file = "Qwen3-1.7B-Q4_K_M.gguf"
            url = "https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf"
            size_gb = 1.1
            ctx_default = 32768
            max_tokens = 600
        }
        small = @{
            name = "Qwen3 4B UD-Q4_K_XL (4-8 Go VRAM, equilibre ~50 tok/s)"
            file = "Qwen3-4B-UD-Q4_K_XL.gguf"
            url = "https://huggingface.co/unsloth/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-UD-Q4_K_XL.gguf"
            size_gb = 2.5
            ctx_default = 32768
            max_tokens = 800
        }
        medium = @{
            name = "Qwen3 8B UD-Q5_K_XL (10+ Go VRAM, qualite max ~15 tok/s)"
            file = "Qwen3-8B-UD-Q5_K_XL.gguf"
            url = "https://huggingface.co/unsloth/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-UD-Q5_K_XL.gguf"
            size_gb = 5.5
            ctx_default = 16384
            max_tokens = 2048
        }
        large = @{
            name = "Qwen3 14B Q5_K_M (16+ Go VRAM, premium)"
            file = "Qwen3-14B-Q5_K_M.gguf"
            url = "https://huggingface.co/Qwen/Qwen3-14B-GGUF/resolve/main/Qwen3-14B-Q5_K_M.gguf"
            size_gb = 10
            ctx_default = 16384
            max_tokens = 2048
        }
        xlarge = @{
            name = "Qwen3 32B Q4_K_M (24+ Go VRAM, RP profond)"
            file = "Qwen3-32B-Q4_K_M.gguf"
            url = "https://huggingface.co/Qwen/Qwen3-32B-GGUF/resolve/main/Qwen3-32B-Q4_K_M.gguf"
            size_gb = 19
            ctx_default = 16384
            max_tokens = 2048
        }
    }
    if ($override -ne "auto") { return $catalog[$override] }
    # Defauts equilibres :
    # - <3 Go VRAM ou CPU only : 1.7B (acceptable, ultra-rapide)
    # - 3-9 Go VRAM (cas RTX 3060/3070/4060/5060 Ti) : 4B (equilibre, sweet spot)
    # - 10-15 Go VRAM : 8B (qualite max sans deborder)
    # - 16-23 Go VRAM : 14B
    # - 24+ Go VRAM : 32B
    if ($forceCpu -or $vramMib -lt 3000) { return $catalog["tiny"] }
    if ($vramMib -lt 10000) { return $catalog["small"] }
    if ($vramMib -lt 16000) { return $catalog["medium"] }
    if ($vramMib -lt 24000) { return $catalog["large"] }
    return $catalog["xlarge"]
}

# ----------------------------------------------------------------------------
# Etape 0 : Pre-requis systeme
# ----------------------------------------------------------------------------

Write-Step "Verification des pre-requis systeme"

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Warn "Python introuvable dans le PATH."
    Write-Info "Installer via : winget install Python.Python.3.13"
    Write-Info "Ou : https://www.python.org/downloads/windows/  (cocher 'Add to PATH')"
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

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Warn "git introuvable. Installer via : winget install Git.Git"
    exit 1
}
Write-Ok "git installe"

# Detection GPU
$gpu = Detect-Gpu
$llamaBackend = Resolve-LlamaBackend $gpu
$gpuLayers = Get-OptimalGpuLayers $gpu.vram_mib
$model = Resolve-ModelChoice $gpu.vram_mib $CpuOnly $ModelSize

Write-Info "GPU detectee : $($gpu.name) ($($gpu.vram_mib) MiB VRAM, vendor=$($gpu.vendor))"
Write-Info "Backend llama.cpp choisi : $llamaBackend"
Write-Info "Layers GPU recommandes : $gpuLayers / 99"
Write-Info "Modele choisi : $($model.name) (~$($model.size_gb) Go)"

if ($gpu.vendor -eq "none" -and -not $CpuOnly) {
    Write-Warn "Aucune GPU dediee detectee. Le LLM tournera sur CPU (1-3 tok/s)."
    if (-not (ConfirmContinue "  Continuer en mode CPU ?")) { exit 0 }
}

# Espace disque
$qualifier = (Split-Path -Qualifier $ProjectRoot).Replace(":", "")
$drive = (Get-PSDrive -Name $qualifier).Free / 1GB
$needed = if ($SkipModel) { 4 } else { 12 }
if ($drive -lt $needed) {
    Write-Warn "Seulement $([math]::Round($drive,1)) Go disponibles, $needed Go recommandes."
    if (-not (ConfirmContinue "  Continuer quand meme ?")) { exit 0 }
} else {
    Write-Ok "Espace disque : $([math]::Round($drive,1)) Go disponibles"
}

# ----------------------------------------------------------------------------
# Etape 1 : venv
# ----------------------------------------------------------------------------

Write-Step "Environnement virtuel Python"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Info "Creation de .venv..."
    & python -m venv .venv
    Write-Ok ".venv cree"
} else {
    Write-Skip ".venv deja present"
}
$venvPython = ".\.venv\Scripts\python.exe"

# ----------------------------------------------------------------------------
# Etape 2 : Dependances + projet editable
# ----------------------------------------------------------------------------

Write-Step "Installation des dependances et du projet"
& $venvPython -m pip install --upgrade pip --quiet
$pipDeps = "fastapi>=0.115","uvicorn[standard]>=0.30","pydantic>=2.8","pydantic-settings>=2.4",
           "sqlalchemy>=2.0","alembic>=1.13","chromadb>=0.5","sentence-transformers>=3.0",
           "httpx>=0.27","structlog>=24.0","rich>=13.7","typer>=0.12",
           "beautifulsoup4>=4.12","trafilatura>=1.12",
           "pytest>=8.3","pytest-asyncio>=0.24","pytest-cov>=5.0","ruff>=0.6","mypy>=1.11","hypothesis>=6.100"
& $venvPython -m pip install --quiet $pipDeps
& $venvPython -m pip install --quiet -e .
Write-Ok "Dependances installees + projet en mode editable"

# ----------------------------------------------------------------------------
# Etape 2b : PyTorch avec backend GPU adapte (chaine de fallback)
# ----------------------------------------------------------------------------

$cudaWorks = $false
if ($gpu.vendor -eq "nvidia" -and -not $CpuOnly) {
    Write-Step "PyTorch avec support CUDA (chaine de fallback selon la GPU)"

    $sm = 0
    if ($gpu.compute_cap) {
        $smMatch = [regex]::Match($gpu.compute_cap, "(\d+)")
        if ($smMatch.Success) { $sm = [int]$smMatch.Groups[1].Value }
    }

    # Determine la chaine de wheels a essayer selon compute capability
    $wheels = @()
    if ($sm -ge 12) {
        # Blackwell (RTX 50xx) : nightly cu128 obligatoire
        $wheels += @{ name = "nightly cu128 (Blackwell)"; index = "https://download.pytorch.org/whl/nightly/cu128"; pre = $true }
    }
    # Toujours essayer les stables comme fallback
    $wheels += @{ name = "stable cu124"; index = "https://download.pytorch.org/whl/cu124"; pre = $false }
    $wheels += @{ name = "stable cu121"; index = "https://download.pytorch.org/whl/cu121"; pre = $false }

    # Verifier l'etat actuel
    $existing = & $venvPython -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_capability(0)[0] if torch.cuda.is_available() else 0)" 2>$null
    $existingLines = "$existing" -split "`n" | ForEach-Object { $_.Trim() }
    $currentVer = if ($existingLines.Count -gt 0) { $existingLines[0] } else { "" }
    $currentCuda = if ($existingLines.Count -gt 1) { $existingLines[1] -eq "True" } else { $false }
    $currentSm = if ($existingLines.Count -gt 2) { try { [int]$existingLines[2] } catch { 0 } } else { 0 }

    if ($currentCuda -and $currentSm -ge $sm) {
        Write-Skip "PyTorch CUDA deja compatible : $currentVer (sm_$($currentSm)0+)"
        $cudaWorks = $true
    } else {
        foreach ($wheel in $wheels) {
            Write-Info "Tentative : $($wheel.name)"
            & $venvPython -m pip uninstall torch -y --quiet 2>&1 | Out-Null
            $preFlag = if ($wheel.pre) { "--pre" } else { "" }
            if ($preFlag) {
                & $venvPython -m pip install $preFlag torch --index-url $wheel.index --quiet 2>&1 | Out-Null
            } else {
                & $venvPython -m pip install torch --index-url $wheel.index --upgrade --quiet 2>&1 | Out-Null
            }
            # Test reel
            $testOut = & $venvPython -c "import torch; t = torch.tensor([1.0]).cuda() if torch.cuda.is_available() else None; print('OK' if t is not None else 'NO_CUDA')" 2>&1
            if ("$testOut".Contains("OK")) {
                Write-Ok "PyTorch GPU operationnel : $($wheel.name)"
                $cudaWorks = $true
                break
            } else {
                Write-Warn "  $($wheel.name) ne supporte pas cette GPU, tentative suivante..."
            }
        }
        if (-not $cudaWorks) {
            Write-Warn "Aucun wheel PyTorch CUDA ne marche, fallback sur CPU pour les embeddings."
            & $venvPython -m pip install torch --quiet 2>&1 | Out-Null
        }
    }
} elseif ($gpu.vendor -eq "amd" -and -not $CpuOnly) {
    Write-Step "PyTorch sur AMD : ROCm n'est pas dispo sur Windows -> CPU"
    Write-Skip "Embeddings sur CPU (le LLM utilisera Vulkan via llama.cpp)"
} elseif ($gpu.vendor -eq "intel" -and -not $CpuOnly) {
    Write-Step "PyTorch sur Intel Arc : XPU complexe -> CPU"
    Write-Skip "Embeddings sur CPU (le LLM utilisera Vulkan via llama.cpp)"
}

# ----------------------------------------------------------------------------
# Etape 3 : llama.cpp (selon backend)
# ----------------------------------------------------------------------------

if (-not $SkipLlama) {
    $assetPattern = switch ($llamaBackend) {
        "cuda"   { "*-bin-win-cuda-12*-x64.zip" }
        "vulkan" { "*-bin-win-vulkan-x64.zip" }
        "cpu"    { "*-bin-win-cpu-x64.zip" }
    }
    $cudartNeeded = ($llamaBackend -eq "cuda")

    Write-Step "llama.cpp (build $llamaBackend)"
    $llamaServerExe = Join-Path $LlamaDir "llama-server.exe"
    if (Test-Path $llamaServerExe) {
        Write-Skip "llama-server.exe deja present a $LlamaDir"
    } else {
        Write-Info "Telechargement de la derniere release..."
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" `
                                 -Headers @{"User-Agent"="shinobi-setup"}
        $tag = $rel.tag_name
        $mainAsset = $rel.assets | Where-Object {
            $_.name -like $assetPattern -and $_.name -notlike "*cudart*"
        } | Select-Object -First 1
        if (-not $mainAsset) {
            Write-Warn "Pas d'asset $assetPattern dans la release $tag"
            Write-Info "Telecharger manuellement : https://github.com/ggml-org/llama.cpp/releases/$tag"
            exit 1
        }
        if (-not (Test-Path $LlamaDir)) { New-Item -ItemType Directory -Path $LlamaDir -Force | Out-Null }
        $tmp = Join-Path $env:TEMP "shinobi_llama"
        if (-not (Test-Path $tmp)) { New-Item -ItemType Directory -Path $tmp -Force | Out-Null }
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $mainAsset.browser_download_url -OutFile (Join-Path $tmp "llama.zip")
        Expand-Archive -Path (Join-Path $tmp "llama.zip") -DestinationPath $LlamaDir -Force
        if ($cudartNeeded) {
            $cudartAsset = $rel.assets | Where-Object { $_.name -like "cudart-*cuda-12*" } | Select-Object -First 1
            if ($cudartAsset) {
                Invoke-WebRequest -Uri $cudartAsset.browser_download_url -OutFile (Join-Path $tmp "cudart.zip")
                Expand-Archive -Path (Join-Path $tmp "cudart.zip") -DestinationPath $LlamaDir -Force
            }
        }
        Write-Ok "llama.cpp $tag ($llamaBackend) installe a $LlamaDir"
    }

    # PATH user
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($userPath -split ";") -notcontains $LlamaDir) {
        $newPath = if ([string]::IsNullOrEmpty($userPath)) { $LlamaDir } else { "$userPath;$LlamaDir" }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-Ok "$LlamaDir ajoute au PATH utilisateur"
    } else {
        Write-Skip "Deja dans le PATH utilisateur"
    }
    $env:Path = "$env:Path;$LlamaDir"
} else {
    Write-Skip "Installation llama.cpp ignoree (-SkipLlama)"
}

# ----------------------------------------------------------------------------
# Etape 4 : Modele
# ----------------------------------------------------------------------------

if (-not $SkipModel) {
    Write-Step "Modele LLM : $($model.name)"
    $modelDir = Join-Path $ProjectRoot "models\llm"
    if (-not (Test-Path $modelDir)) { New-Item -ItemType Directory -Path $modelDir -Force | Out-Null }
    $modelPath = Join-Path $modelDir $model.file
    if (Test-Path $modelPath) {
        $sizeGb = [math]::Round((Get-Item $modelPath).Length / 1GB, 2)
        Write-Skip "Modele deja present ($sizeGb Go)"
    } else {
        Write-Info "Telechargement (~$($model.size_gb) Go, 2 a 30 min selon la connexion)..."
        & curl.exe -L --fail --retry 3 --retry-delay 5 -o $modelPath $model.url
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

# ----------------------------------------------------------------------------
# Etape 5 : Launchers globaux
# ----------------------------------------------------------------------------

Write-Step "Launcher global shinobi (cmd, PowerShell, bash)"
$binDir = Join-Path $ProjectRoot "bin"
if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($userPath -split ";") -notcontains $binDir) {
    $newPath = if ([string]::IsNullOrEmpty($userPath)) { $binDir } else { "$userPath;$binDir" }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Ok "$binDir ajoute au PATH utilisateur"
    Write-Info "(ouvre un NOUVEAU shell pour que la commande 'shinobi' soit globale)"
} else {
    Write-Skip "$binDir deja dans le PATH utilisateur"
}
$env:Path = "$env:Path;$binDir"

# ----------------------------------------------------------------------------
# Etape 6 : .env auto-configure
# ----------------------------------------------------------------------------

Write-Step "Fichier .env (auto-configure selon ton hardware)"
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Ok ".env cree depuis .env.example"
    }
}
if (Test-Path ".env") {
    $embDevice = Resolve-EmbeddingsDevice $gpu $cudaWorks
    $modelPath = "models/llm/$($model.file)"
    $modelName = ($model.file -replace '\.gguf$','').ToLower()
    $envContent = Get-Content ".env" -Raw
    $envContent = [regex]::Replace($envContent, 'EMBEDDINGS_DEVICE=\S+', "EMBEDDINGS_DEVICE=$embDevice")
    $envContent = [regex]::Replace($envContent, 'LLM_GPU_LAYERS=\d+', "LLM_GPU_LAYERS=$gpuLayers")
    $envContent = [regex]::Replace($envContent, 'LLM_MODEL_PATH=\S+', "LLM_MODEL_PATH=$modelPath")
    $envContent = [regex]::Replace($envContent, 'LLM_MODEL_NAME=\S+', "LLM_MODEL_NAME=$modelName")
    $envContent = [regex]::Replace($envContent, 'LLM_CONTEXT_SIZE=\d+', "LLM_CONTEXT_SIZE=$($model.ctx_default)")
    $envContent = [regex]::Replace($envContent, 'LLM_MAX_TOKENS=\d+', "LLM_MAX_TOKENS=$($model.max_tokens)")
    Set-Content ".env" $envContent -NoNewline
    Write-Ok ".env mis a jour : modele=$($model.file), embeddings=$embDevice, layers=$gpuLayers, ctx=$($model.ctx_default), max_tokens=$($model.max_tokens)"
}

# ----------------------------------------------------------------------------
# Etape 7 : Git
# ----------------------------------------------------------------------------

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
    Write-Info "Premier commit..."
    & git add CLAUDE.md README.md TUTORIAL.md .env.example .gitignore pyproject.toml ruff.toml mypy.ini docs/ scripts/ src/ tests/ data/canonical/ bin/ 2>&1 | Out-Null
    & git -c commit.gpgsign=false commit -m "initial bootstrap" 2>&1 | Out-Null
    Write-Ok "Commit initial cree"
} else {
    Write-Skip "Repo a deja un historique"
}

# ----------------------------------------------------------------------------
# Etape 8 : Tests
# ----------------------------------------------------------------------------

Write-Step "Tests de fumee"
& $venvPython -m pytest tests/ -q 2>&1 | Select-Object -Last 3
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Tests en echec, mais setup termine. Inspecte la sortie ci-dessus."
} else {
    Write-Ok "Tests passent"
}

# ----------------------------------------------------------------------------
# Final
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "===============================================" -ForegroundColor Magenta
Write-Host "  Setup termine. Configuration detectee :" -ForegroundColor Magenta
Write-Host "===============================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "  GPU       : $($gpu.name)" -ForegroundColor White
Write-Host "  VRAM      : $($gpu.vram_mib) MiB" -ForegroundColor White
Write-Host "  Modele    : $($model.name)" -ForegroundColor White
Write-Host "  llama.cpp : $llamaBackend" -ForegroundColor White
Write-Host "  GPU layers: $gpuLayers / 99" -ForegroundColor White
Write-Host "  Embeddings: $(if ($cudaWorks) { 'cuda' } else { 'cpu' })" -ForegroundColor White
Write-Host ""
Write-Host "  Pour jouer (NOUVEAU PowerShell apres setup) :" -ForegroundColor Cyan
Write-Host "    .\scripts\start_llm_server.ps1   # terminal 1" -ForegroundColor Cyan
Write-Host "    shinobi                          # terminal 2" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Re-execute ce script a tout moment, il est idempotent." -ForegroundColor DarkGray
