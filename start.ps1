# Legend Startup Script for Windows (PowerShell)
# Equivalent to start.sh for Unix systems

$ErrorActionPreference = "Stop"
$ROOT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$VENV_DIR = Join-Path $ROOT_DIR "backend\.venv"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  Legend - Architecture Mapping Tool" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

# --- Check Python 3.10+ ---
try {
    $pythonVersion = python --version 2>&1
    if ($pythonVersion -match "Python (\d+)\.(\d+)") {
        $pyMajor = [int]$Matches[1]
        $pyMinor = [int]$Matches[2]
        if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 10)) {
            Write-Host "Error: Python 3.10+ required (found $pythonVersion)" -ForegroundColor Red
            Write-Host "Install a newer Python and make sure it's on PATH."
            exit 1
        }
        Write-Host "Using Python $pyMajor.$pyMinor" -ForegroundColor Green
    }
} catch {
    Write-Host "Error: Python not found. Install Python 3.10+ and add to PATH." -ForegroundColor Red
    exit 1
}

# --- Check Node.js 18+ ---
try {
    $nodeVersion = node --version 2>&1
    if ($nodeVersion -match "v(\d+)") {
        $nodeMajor = [int]$Matches[1]
        if ($nodeMajor -lt 18) {
            Write-Host "Error: Node.js 18+ required (found $nodeVersion)" -ForegroundColor Red
            Write-Host "Update Node.js: https://nodejs.org/"
            exit 1
        }
        Write-Host "Using Node.js $nodeVersion" -ForegroundColor Green
    }
} catch {
    Write-Host "Error: Node.js not found." -ForegroundColor Red
    Write-Host "Install Node.js 18+: https://nodejs.org/"
    exit 1
}

# --- Check Rust/Cargo 1.77.2+ ---
try {
    $cargoVersion = cargo --version 2>&1
    if ($cargoVersion -match "cargo (\d+)\.(\d+)\.(\d+)") {
        $rustMajor = [int]$Matches[1]
        $rustMinor = [int]$Matches[2]
        $rustPatch = [int]$Matches[3]

        $rustOK = $false
        if ($rustMajor -gt 1) { $rustOK = $true }
        elseif ($rustMajor -eq 1) {
            if ($rustMinor -gt 77) { $rustOK = $true }
            elseif ($rustMinor -eq 77 -and $rustPatch -ge 2) { $rustOK = $true }
        }

        if (-not $rustOK) {
            Write-Host "Error: Rust 1.77.2+ required (found $rustMajor.$rustMinor.$rustPatch)" -ForegroundColor Red
            Write-Host "Update Rust: rustup update stable"
            exit 1
        }
        Write-Host "Using Cargo $rustMajor.$rustMinor.$rustPatch" -ForegroundColor Green
    }
} catch {
    Write-Host "Error: Rust/Cargo not found." -ForegroundColor Red
    Write-Host "Install Rust via rustup: https://rustup.rs/"
    exit 1
}

# --- Install opencode CLI if not present ---
try {
    $opencodeVersion = opencode --version 2>&1
    Write-Host "Using opencode $opencodeVersion" -ForegroundColor Green
} catch {
    Write-Host "Installing opencode CLI..." -ForegroundColor Yellow
    npm i -g opencode-ai@latest
    Write-Host "opencode installed." -ForegroundColor Green
}

# --- Create venv if it doesn't exist ---
if (-not (Test-Path $VENV_DIR)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv $VENV_DIR
    Write-Host "Virtual environment created." -ForegroundColor Green
} else {
    Write-Host "Virtual environment exists." -ForegroundColor Green
}

# --- Install backend dependencies ---
Write-Host "Installing backend dependencies..." -ForegroundColor Yellow
& "$VENV_DIR\Scripts\pip.exe" install -q -r "$ROOT_DIR\backend\requirements.txt"
Write-Host "Backend dependencies installed." -ForegroundColor Green

# --- Install frontend dependencies ---
if (-not (Test-Path "$ROOT_DIR\frontend\node_modules")) {
    Write-Host "Installing frontend dependencies..." -ForegroundColor Yellow
    Push-Location "$ROOT_DIR\frontend"
    npm install
    Pop-Location
    Write-Host "Frontend dependencies installed." -ForegroundColor Green
} else {
    Write-Host "Frontend dependencies exist." -ForegroundColor Green
}

# --- Check Docker for SCIP (optional) ---
$dockerAvailable = $false
try {
    $dockerInfo = docker info 2>&1
    if ($LASTEXITCODE -eq 0) {
        $dockerAvailable = $true

        # Check if SCIP image exists
        $scipImage = docker image inspect "scip-engine" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "SCIP engine image not found. Attempting to pull..." -ForegroundColor Yellow
            docker pull "ghcr.io/williamekberg/scip-engine:latest" 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                docker tag "ghcr.io/williamekberg/scip-engine:latest" "scip-engine"
                Write-Host "SCIP engine image pulled and tagged." -ForegroundColor Green
            } else {
                Write-Host "Could not pull SCIP image. Will use local indexers." -ForegroundColor Yellow
            }
        } else {
            Write-Host "SCIP engine image found." -ForegroundColor Green
        }
    }
} catch {
    Write-Host "Docker not available. SCIP indexing will use local binaries." -ForegroundColor Yellow
}

# --- Build Tauri Rust backend (first run takes a while) ---
Write-Host "Building Tauri Rust backend (first run may take a few minutes)..." -ForegroundColor Yellow
Push-Location "$ROOT_DIR\frontend\src-tauri"
cargo build 2>&1 | Out-Host
Pop-Location
Write-Host "Tauri Rust build complete." -ForegroundColor Green

Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  Starting Legend..." -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

# --- Start backend server ---
Write-Host "Starting backend (FastAPI) on http://localhost:8000..." -ForegroundColor Yellow
$backendJob = Start-Job -ScriptBlock {
    param($venvDir, $rootDir)
    & "$venvDir\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
} -ArgumentList $VENV_DIR, $ROOT_DIR -WorkingDirectory "$ROOT_DIR\backend"

# --- Start frontend/Tauri app ---
Write-Host "Starting Legend desktop app..." -ForegroundColor Yellow
$frontendJob = Start-Job -ScriptBlock {
    param($rootDir)
    npm run tauri:dev
} -ArgumentList $ROOT_DIR -WorkingDirectory "$ROOT_DIR\frontend"

Write-Host ""
Write-Host "=========================================" -ForegroundColor Green
Write-Host "  Legend is starting!" -ForegroundColor Green
Write-Host "  Backend:  http://localhost:8000" -ForegroundColor Green
Write-Host "  Desktop app launching..." -ForegroundColor Green
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Green
Write-Host ""

# --- Wait and show output ---
try {
    while ($true) {
        # Check if jobs are still running
        $backendState = (Get-Job -Id $backendJob.Id).State
        $frontendState = (Get-Job -Id $frontendJob.Id).State

        # Receive any output
        Receive-Job -Job $backendJob -ErrorAction SilentlyContinue
        Receive-Job -Job $frontendJob -ErrorAction SilentlyContinue

        if ($backendState -eq "Failed" -or $frontendState -eq "Failed") {
            Write-Host "A process has failed." -ForegroundColor Red
            break
        }

        Start-Sleep -Milliseconds 500
    }
} finally {
    Write-Host ""
    Write-Host "Shutting down..." -ForegroundColor Yellow
    Stop-Job -Job $backendJob -ErrorAction SilentlyContinue
    Stop-Job -Job $frontendJob -ErrorAction SilentlyContinue
    Remove-Job -Job $backendJob -Force -ErrorAction SilentlyContinue
    Remove-Job -Job $frontendJob -Force -ErrorAction SilentlyContinue
    Write-Host "Done." -ForegroundColor Green
}
