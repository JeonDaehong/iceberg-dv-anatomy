# Windows 용 JMH 러너 (Maven/Gradle 불필요).
#
#   .\run.ps1                    # 전체
#   .\run.ps1 -Filter c_forAll   # 특정 벤치만
#   .\run.ps1 -Quick             # 빠른 확인 (fork 1)
#
# 주의: javac 에 인자를 넘길 때 반드시 @argfile 을 쓴다.
#   - PowerShell 네이티브 인자 파싱이 classpath 의 ';' 를 망가뜨린다.
#   - argfile 은 BOM 없이 써야 한다. PS 5.1 의 `Set-Content -Encoding utf8` 은
#     BOM 을 붙여서 javac 가 "invalid flag: ?-classpath" 로 죽는다.
param(
  [string]$Filter = "",
  [switch]$Quick,
  [string]$LibDir = "D:/dv-tools/bench-libs",
  [string]$Out    = ""
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$jars = Get-ChildItem "$LibDir/*.jar" -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName -replace '\\','/' }
if (-not $jars -or $jars.Count -eq 0) { throw "no jars in $LibDir" }
$CP = ($jars -join ';')

Remove-Item -Recurse -Force "$here/build" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$here/build/classes", "$here/build/generated" | Out-Null

Write-Host "[1/2] compiling (JMH annotation processor)..." -ForegroundColor Cyan
$sources = Get-ChildItem -Recurse "$here/src/*.java" | ForEach-Object { $_.FullName -replace '\\','/' }
$lines = @("-classpath", """$CP""", "-d", "./build/classes", "-s", "./build/generated",
           "-encoding", "UTF-8") + $sources
$argfile = "$here/build/javac.args"
[System.IO.File]::WriteAllLines($argfile, $lines, (New-Object System.Text.UTF8Encoding($false)))

& javac "@$argfile"
if ($LASTEXITCODE -ne 0) { throw "compile failed" }

$blist = "$here/build/classes/META-INF/BenchmarkList"
if (-not (Test-Path $blist)) { throw "BenchmarkList missing - annotation processing did not run" }
Write-Host ("  benchmarks: " + (Get-Content $blist | Measure-Object -Line).Lines) -ForegroundColor DarkGray

Write-Host "[2/2] running JMH..." -ForegroundColor Cyan
if (-not $Out) { $Out = "$here/results/jmh-$(Get-Date -Format yyyyMMdd-HHmmss).txt" }
New-Item -ItemType Directory -Force -Path (Split-Path $Out) | Out-Null

$jmhArgs = @()
if ($Filter) { $jmhArgs += $Filter } else { $jmhArgs += "DvBatchBench" }
if ($Quick) { $jmhArgs += @('-f','1','-wi','2','-i','3') } else { $jmhArgs += @('-f','2','-wi','3','-i','5') }
$jmhArgs += @('-r','1s','-w','1s','-rf','json','-rff', ($Out -replace '\.txt$','.json'))

& java -cp "$here/build/classes;$CP" org.openjdk.jmh.Main @jmhArgs 2>&1 | Tee-Object -FilePath $Out

Write-Host ""
Write-Host "results -> $Out" -ForegroundColor Green
Write-Host "analyze  -> python report.py $($Out -replace '\.txt$','.json')" -ForegroundColor Green
