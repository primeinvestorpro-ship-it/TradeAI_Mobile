$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$sdkDir = "$env:LOCALAPPDATA\Android\Sdk"
$buildTools = "$sdkDir\build-tools\34.0.0"
$androidJar = "$sdkDir\platforms\android-34\android.jar"

Write-Host "=== Building TradeAI Android APK ===" -ForegroundColor Cyan
Write-Host "SDK: $sdkDir"
Write-Host "Build Tools: $buildTools"

# 1. Clean build directory
if (Test-Path "bin") {
    Remove-Item -Path "bin" -Recurse -Force
}
New-Item -ItemType Directory -Path "bin\classes", "bin\dex" -Force | Out-Null

# 2. Compile resources with AAPT2
Write-Host "Compiling resources..." -ForegroundColor Yellow
& "$buildTools\aapt2.exe" compile --dir "res" -o "bin\res.zip"

# 3. Link resources and generate R.java
Write-Host "Linking resources and generating R.java..." -ForegroundColor Yellow
& "$buildTools\aapt2.exe" link -I "$androidJar" --manifest "AndroidManifest.xml" -o "bin\app-unaligned.apk" "bin\res.zip" --java "src"

# 4. Compile Java sources
Write-Host "Compiling Java sources..." -ForegroundColor Yellow
$javaFiles = Get-ChildItem -Path "src" -Recurse -Filter "*.java" | Select-Object -ExpandProperty FullName
& javac -encoding UTF-8 -cp "$androidJar" -d "bin\classes" $javaFiles

# 5. Compile bytecode to DEX using D8
Write-Host "DEX-ing classes with D8..." -ForegroundColor Yellow
$classFiles = Get-ChildItem -Path "bin\classes" -Recurse -Filter "*.class" | Select-Object -ExpandProperty FullName
& "$buildTools\d8.bat" --min-api 26 --output "bin\dex" $classFiles

# 6. Add classes.dex to APK
Write-Host "Adding classes.dex into APK..." -ForegroundColor Yellow
Push-Location "bin\dex"
& jar uf "..\app-unaligned.apk" "classes.dex"
Pop-Location

# 7. ZipAlign APK
Write-Host "Zipaligning APK..." -ForegroundColor Yellow
& "$buildTools\zipalign.exe" -p -f 4 "bin\app-unaligned.apk" "bin\TradeAI-aligned.apk"

# 8. Generate release signing key if needed
$keystorePath = "bin\tradeai.keystore"
if (-not (Test-Path $keystorePath)) {
    Write-Host "Generating signing keystore..." -ForegroundColor Yellow
    & keytool -genkeypair -keystore "$keystorePath" -storepass "tradeai123" -keypass "tradeai123" -alias "tradeai" -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=TradeAI, OU=Trading, O=TradeAI, L=Bangalore, ST=KA, C=IN"
}

# 9. Sign APK with apksigner (v1 + v2 + v3 scheme)
$outApk = "$scriptDir\..\TradeAI.apk"
$staticApk = "$scriptDir\..\static\TradeAI.apk"

Write-Host "Signing APK with apksigner..." -ForegroundColor Yellow
& "$buildTools\apksigner.bat" sign --ks "$keystorePath" --ks-pass "pass:tradeai123" --key-pass "pass:tradeai123" --ks-key-alias "tradeai" --out "$outApk" "bin\TradeAI-aligned.apk"

# Also copy to static folder for direct in-browser download
Copy-Item "$outApk" "$staticApk" -Force

Write-Host "Verifying signed APK..." -ForegroundColor Yellow
& "$buildTools\apksigner.bat" verify --verbose "$outApk"

$apkItem = Get-Item "$outApk"
Write-Host "`nSUCCESS! TradeAI Android APK generated:" -ForegroundColor Green
Write-Host "Path: $($apkItem.FullName)" -ForegroundColor Green
Write-Host "Size: $([math]::Round($apkItem.Length / 1KB, 2)) KB" -ForegroundColor Green
Write-Host "Direct Browser Download URL: http://127.0.0.1:5050/static/TradeAI.apk (or LAN: http://192.168.1.43:5050/static/TradeAI.apk)" -ForegroundColor Cyan
