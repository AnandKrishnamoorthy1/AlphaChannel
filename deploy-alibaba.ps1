#!/usr/bin/env powershell
# 
# Alibaba Cloud Serverless Deployment Script
# 
# Purpose: Safely inject .env variables into PowerShell process memory, 
#          then deploy to Alibaba Cloud Function Compute using Serverless Devs CLI
#
# Usage:
#   PowerShell -ExecutionPolicy Bypass -File deploy-alibaba.ps1
#
# Security: 
#   - .env variables are injected ONLY into this process's memory
#   - Secrets are NOT written to disk or shell history
#   - After deployment completes, PowerShell window closes (secrets vanish)

param(
    [string]$Environment = "production",
    [string]$Region = "ap-southeast-1"
)

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "🚀 Alibaba Cloud Serverless Deployment" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================================
# Step 1: Verify prerequisites
# ============================================================================
Write-Host "1️⃣  Verifying prerequisites..." -ForegroundColor Yellow

# Check if .env file exists
if (-not (Test-Path ".env")) {
    Write-Host "❌ ERROR: .env file not found!" -ForegroundColor Red
    Write-Host "   Create .env by copying .env.example:" -ForegroundColor Gray
    Write-Host "   cp .env.example .env" -ForegroundColor Gray
    exit 1
}
Write-Host "   ✅ .env file found" -ForegroundColor Green

# Check if s.yaml exists
if (-not (Test-Path "s.yaml")) {
    Write-Host "❌ ERROR: s.yaml not found!" -ForegroundColor Red
    exit 1
}
Write-Host "   ✅ s.yaml found" -ForegroundColor Green

# Check if Serverless Devs CLI is installed
$sDevs = (npm list -g @serverless-devs/core 2>$null)
if (-not $?) {
    Write-Host "❌ ERROR: Serverless Devs not installed!" -ForegroundColor Red
    Write-Host "   Install with: npm install -g @serverless-devs/core" -ForegroundColor Gray
    exit 1
}
Write-Host "   ✅ Serverless Devs CLI installed" -ForegroundColor Green

Write-Host ""

# ============================================================================
# Step 2: Load .env variables into process environment
# ============================================================================
Write-Host "2️⃣  Loading .env variables into process memory..." -ForegroundColor Yellow

$envVars = @()
$secretVars = @("DASHSCOPE_API_KEY", "ALPHA_VANTAGE_API_KEY", "PROD_SERVERLESS_DB_URL", "ROBINHOOD_CLIENT_SECRET")

Get-Content .env | ForEach-Object {
    # Skip comments and empty lines
    if ($_ -match '^\s*([^#\s=]+)\s*=\s*(.*)\s*$') {
        $varName = $Matches[1]
        $varValue = $Matches[2].Trim("'`"")
        
        # Set in process environment
        [System.Environment]::SetEnvironmentVariable($varName, $varValue, "Process")
        $envVars += $varName
        
        # Show which ones were loaded (mask sensitive values)
        if ($secretVars -contains $varName) {
            $display = if ($varValue.Length -gt 10) { 
                $varValue.Substring(0, 10) + "..." 
            } else { 
                "***" 
            }
            Write-Host "   ✅ $varName = $display" -ForegroundColor Green
        } else {
            Write-Host "   ✅ $varName = $varValue" -ForegroundColor Green
        }
    }
}

Write-Host ""

# ============================================================================
# Step 3: Verify critical variables are set
# ============================================================================
Write-Host "3️⃣  Validating critical environment variables..." -ForegroundColor Yellow

$criticalVars = @("DASHSCOPE_API_KEY", "ALPHA_VANTAGE_API_KEY", "QWEN_MODEL")
$missingVars = @()

foreach ($varName in $criticalVars) {
    $value = [System.Environment]::GetEnvironmentVariable($varName, "Process")
    if ([string]::IsNullOrEmpty($value)) {
        $missingVars += $varName
        Write-Host "   ❌ $varName is NOT set!" -ForegroundColor Red
    } else {
        Write-Host "   ✅ $varName is set" -ForegroundColor Green
    }
}

if ($missingVars.Count -gt 0) {
    Write-Host ""
    Write-Host "❌ Missing critical variables: $($missingVars -join ', ')" -ForegroundColor Red
    exit 1
}

Write-Host ""

# ============================================================================
# Step 4: Run deployment
# ============================================================================
Write-Host "4️⃣  Deploying to Alibaba Cloud Function Compute..." -ForegroundColor Yellow
Write-Host "   Region: $Region" -ForegroundColor Gray
Write-Host "   Environment: $Environment" -ForegroundColor Gray
Write-Host ""

try {
    # Run Serverless Devs deployment
    # The s.yaml file references ${env.VARIABLE_NAME} which reads from process environment
    & s deploy
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "================================================" -ForegroundColor Green
        Write-Host "✅ DEPLOYMENT SUCCESSFUL!" -ForegroundColor Green
        Write-Host "================================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "📌 Next steps:" -ForegroundColor Cyan
        Write-Host "   1. Open Alibaba Cloud Console" -ForegroundColor Gray
        Write-Host "   2. Go to Function Compute → corporate-intelligence-fastapi" -ForegroundColor Gray
        Write-Host "   3. Add environment variables via console UI:" -ForegroundColor Gray
        Write-Host "      - DASHSCOPE_API_KEY" -ForegroundColor Gray
        Write-Host "      - ALPHA_VANTAGE_API_KEY" -ForegroundColor Gray
        Write-Host "      - PROD_SERVERLESS_DB_URL" -ForegroundColor Gray
        Write-Host ""
        Write-Host "💡 Security reminder:" -ForegroundColor Cyan
        Write-Host "   - Close this PowerShell window to clear .env from memory" -ForegroundColor Gray
        Write-Host "   - Never commit .env to git (add to .gitignore)" -ForegroundColor Gray
        Write-Host ""
    } else {
        Write-Host ""
        Write-Host "❌ DEPLOYMENT FAILED" -ForegroundColor Red
        Write-Host "   Exit code: $LASTEXITCODE" -ForegroundColor Red
        exit 1
    }
}
catch {
    Write-Host ""
    Write-Host "❌ DEPLOYMENT ERROR: $_" -ForegroundColor Red
    exit 1
}

# ============================================================================
# Step 5: View logs
# ============================================================================
Write-Host ""
$response = Read-Host "View deployment logs? (y/n)"
if ($response -eq "y" -or $response -eq "Y") {
    Write-Host ""
    Write-Host "📋 Fetching logs..." -ForegroundColor Yellow
    & s logs -t 50
}

Write-Host ""
Write-Host "🎉 Deployment pipeline complete!" -ForegroundColor Green
Write-Host ""
Write-Host "💨 This PowerShell window will close in 10 seconds..." -ForegroundColor Gray
Write-Host "   (This clears your .env variables from memory)" -ForegroundColor Gray
Write-Host ""

Start-Sleep -Seconds 10
