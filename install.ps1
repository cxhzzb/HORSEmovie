<#
.SYNOPSIS
  HORSEmovie 一键安装（Windows）—— install.sh 的 PowerShell 对应版本。

.DESCRIPTION
  把这套 AI 微电影生产线装到本机：
    1. 检查前置（~/.dsh、ComfyUI、启动脚本）
    2. skills → ~/.dsh/skills（horsemovie + comfyui），并写入本机 local.json
    3. 工作流 → <ComfyUI>\user\default\workflows（同名默认不覆盖，另存 "(bundle)" 副本）
    4. API 图 → ~\comfy-projects\_pipelines
    5. 点名核对必需模型（认 ComfyUI Desktop 的共享模型目录）
    6. 点名核对自定义节点
    7. 跑 doctor.py 出完整报告

  只读检查 + 拷贝。默认**不覆盖**你已有的同名工作流；要覆盖用 -Force（会先备份 .bak-时间戳）。

.EXAMPLE
  .\install.ps1 -DryRun                 # 先看它要做什么，不改任何东西
  .\install.ps1                         # 装到自动探测到的 ComfyUI
  .\install.ps1 -Comfy 'F:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI'
  .\install.ps1 -Force                  # 同名工作流覆盖（先备份）
  # 本机模型名和包里不一样时（量化版/越狱版改名）：
  .\install.ps1 -ModelAlias @{
      'text_encoders\qwen3vl_32b_minimax_h3_int8_convrot.safetensors' =
      'text_encoders\qwen3vl_32b_h3_ultra_uncensored_heretic_int8_convrot.safetensors' }
#>
[CmdletBinding()]
param(
  [string]    $Comfy,
  [string[]]  $ExtraModels = @(),
  [string]    $Launcher,
  [string]    $SkillsDir = (Join-Path $HOME '.dsh\skills'),
  [string]    $Projects = (Join-Path $HOME 'comfy-projects'),
  [hashtable] $ModelAlias = @{},
  [switch]    $Force,
  [switch]    $NoFastDisk,
  [switch]    $DryRun
)

$ErrorActionPreference = 'Stop'
$Bundle = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$step = 0
function Say  ($t) { $script:step++; Write-Host ""; Write-Host "[$script:step] $t" -ForegroundColor DarkGray }
function Ok   ($t) { Write-Host "    v $t" -ForegroundColor Green }
function Bad  ($t) { Write-Host "    x $t" -ForegroundColor Red }
function Warn ($t) { Write-Host "    ! $t" -ForegroundColor Yellow }
function Run  ([scriptblock]$b) { if ($DryRun) { Write-Host "    `$ $b" -ForegroundColor DarkGray } else { & $b } }

function Find-Comfy {
  if ($Comfy) { return (Resolve-Path -LiteralPath $Comfy).Path }
  $home1 = Join-Path $HOME 'ComfyUI'
  if (Test-Path (Join-Path $home1 'main.py')) { return $home1 }
  # ComfyUI Desktop：installations.json 里的 installPath 再往下走一层 ComfyUI。
  # 该文件是 UTF-8；PowerShell 5.1 默认按 ANSI(GBK) 读会把里面的中文读成乱码并让 JSON 解析失败，
  # 所以必须显式 -Encoding UTF8，且解析失败不能拖垮整个安装。
  $inst = Join-Path $env:APPDATA 'Comfy Desktop\installations.json'
  if (Test-Path $inst) {
    try {
      foreach ($e in (Get-Content $inst -Raw -Encoding UTF8 | ConvertFrom-Json)) {
        if ($e.installPath) {
          $cand = Join-Path $e.installPath 'ComfyUI'
          if (Test-Path (Join-Path $cand 'main.py')) { return $cand }
        }
      }
    } catch {
      Write-Host "    ! 读不了 installations.json：$($_.Exception.Message)" -ForegroundColor Yellow
      Write-Host "      用 -Comfy <路径> 指定 ComfyUI 根目录（该目录下要有 main.py）" -ForegroundColor Yellow
    }
  }
  return $null
}

function Find-SharedModels {
  $y = Join-Path $env:APPDATA 'Comfy Desktop\shared_model_paths.yaml'
  if (Test-Path $y) {
    $m = Select-String -Path $y -Pattern "^\s*base_path:\s*'?([^']+)'?" -Encoding UTF8 | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value.Trim() }
  }
  return $null
}

function Find-Python {
  foreach ($c in @(
      (Join-Path $HOME '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'),
      (Join-Path $HOME '.dsh\runtimes\source-launch\primary-runtime\dependencies\python\bin\python3'),
      (Get-Command python -ErrorAction SilentlyContinue).Source)) {
    if ($c -and (Test-Path $c)) { return $c }
  }
  return $null
}

Write-Host "================================================================"
Write-Host " HORSEmovie 安装（Windows）"
Write-Host "================================================================"
Write-Host "  包目录   : $Bundle"
Write-Host "  skills   : $SkillsDir"
Write-Host "  projects : $Projects"
if ($DryRun) { Write-Host "  模式     : DRY-RUN（不会真的改动）" -ForegroundColor Yellow }

# ------------------------------------------------------------------ 前置
Say "检查前置"
if (Test-Path (Join-Path $HOME '.dsh')) { Ok "~/.dsh 存在（DSH 已跑过）" }
  else { Bad "没有 ~/.dsh —— 先装 DSH 并至少启动一次" }

$ComfyRoot = Find-Comfy
if (-not $ComfyRoot) { throw "找不到 ComfyUI：用 -Comfy <路径> 指定（该目录下要有 main.py）" }
Ok "ComfyUI: $ComfyRoot"

$Shared = Find-SharedModels
if (-not $Shared) { $Shared = Join-Path $ComfyRoot 'models' }
if ($Shared -ne (Join-Path $ComfyRoot 'models')) { Ok "共享模型: $Shared" }
$ExtraModels = @($ExtraModels + @($Shared) | Where-Object { $_ -and $_ -ne (Join-Path $ComfyRoot 'models') } | Select-Object -Unique)

# --fast-disk：H3 视频链路要 20GB 主模型 + 25GB 文本编码器。ComfyUI 默认把权重拷进内存
# （日志 fast_disk=False），内存不够时会疯狂换页 —— 实测文本编码阶段卡 2 小时进不到采样。
# 从 NVMe mmap 权重后，加载+采样+解码 ≈33 分钟出片。写进 Desktop 的 launchArgs，
# 以后在 UI 里点 Restart 也会带上。
if (-not $NoFastDisk) {
  $instFile = Join-Path $env:APPDATA 'Comfy Desktop\installations.json'
  if (Test-Path $instFile) {
    try {
      $data = Get-Content $instFile -Raw -Encoding UTF8 | ConvertFrom-Json
      $changed = $false
      foreach ($e in $data) {
        if (-not $e.installPath) { continue }
        if (-not (Test-Path (Join-Path (Join-Path $e.installPath 'ComfyUI') 'main.py'))) { continue }
        $la = @()
        if ($e.launchArgs) { $la = @($e.launchArgs -split '\s+' | Where-Object { $_ }) }
        if ($la -notcontains '--fast-disk') {
          $la += '--fast-disk'
          $e.launchArgs = ($la -join ' ')
          $changed = $true
        }
      }
      if ($changed) {
        Run { Copy-Item $instFile "$instFile.bak-dsh" -Force }
        Run { [IO.File]::WriteAllText($instFile, ($data | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding $false)) }
        Ok "--fast-disk 已写入 Comfy Desktop 的 launchArgs（重启 ComfyUI 生效）"
      } else {
        Ok "launchArgs 里已经有 --fast-disk"
      }
    } catch {
      Warn "改 installations.json 失败：$($_.Exception.Message)"
      Warn "请手工在启动命令里加 --fast-disk（无头启动时）"
    }
  } else {
    Warn "没有 Comfy Desktop 配置；无头启动时请自己在命令行加 --fast-disk"
  }
}

if (-not $Launcher) {
  # 往上找三层：ComfyUI Desktop 的 start_comfyui.bat 通常放在安装树的上层
  $dir = Get-Item $ComfyRoot
  for ($i = 0; $i -lt 4 -and $dir; $i++) {
    foreach ($n in @('start_comfyui.bat', '启动ComfyUI.sh')) {
      $c = Join-Path $dir.FullName $n
      if (Test-Path $c) { $Launcher = $c; break }
    }
    if ($Launcher) { break }
    $dir = $dir.Parent
  }
}
if ($Launcher) { Ok "启动脚本: $Launcher" } else { Warn "没找到启动脚本（run_film.py 的看门狗要靠它拉起服务）" }

# ------------------------------------------------------------------ skills
Say "安装 DSH skills"
$localJson = [ordered]@{
  '_comment'    = '本机路径覆盖：由 install.ps1 写入，上游仓库里没有这个文件。'
  comfy_base    = $ComfyRoot
  workflow_dirs = @((Join-Path $ComfyRoot 'user\default\workflows'))
  projects      = $Projects
  launcher      = $Launcher
  python        = (Find-Python)
  extra_model_roots = @($ExtraModels)
  model_aliases = $ModelAlias
}
# DSH 只认 kebab-case 的 skill 名；仓库里目录名与 frontmatter 的 name 都已经是 kebab-case。
foreach ($name in @('horsemovie', 'comfyui')) {
  $src = Join-Path $Bundle "skills\$name"
  $dst = Join-Path $SkillsDir $name
  if (-not (Test-Path $src)) { Bad "包里没有 $name"; continue }
  # 本机文件（路径 / 节点 id / 标定结果 / 本机说明）要保住：复制前挪走、复制后放回。
  # 否则重装一次就把这台机器攒下的 local.json / pipelines.local.json / LOCAL.md 覆盖掉了。
  $keep = @()
  foreach ($n in 'local.json', 'pipelines.local.json', 'LOCAL.md') {
    $f = Join-Path $dst $n
    if (Test-Path $f) {
      $tmp = Join-Path $env:TEMP ("dsh-keep-" + [guid]::NewGuid().ToString('N') + "-" + $n)
      Copy-Item $f $tmp -Force
      $keep += , @($n, $tmp)
    }
  }
  Run { Copy-Item $src $dst -Recurse -Force }
  foreach ($pair in $keep) {
    Run { Copy-Item $pair[1] (Join-Path $dst $pair[0]) -Force }
    Run { Remove-Item $pair[1] -Force }
    Warn "$name\$($pair[0]) 是本机文件，已保留（没被包里的版本覆盖）"
  }
  Ok "$name -> $dst"
}
$localPath = Join-Path $SkillsDir 'comfyui\local.json'
if (Test-Path $localPath) { Warn "local.json 已存在，保留（要重写先删掉它）" }
else {
  # 不带 BOM：comfy.py / run_film.py 用 json.loads 读它，BOM 会让解析失败
  Run { [IO.File]::WriteAllText($localPath, ($localJson | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding $false)) }
  Ok "local.json -> $localPath"
}

# ------------------------------------------------------------------ 工作流
Say "安装 ComfyUI 工作流"
$wf = Join-Path $ComfyRoot 'user\default\workflows'
Run { New-Item -ItemType Directory -Force -Path $wf | Out-Null }
foreach ($f in Get-ChildItem (Join-Path $Bundle 'comfyui-workflows') -Filter *.json) {
  $dst = Join-Path $wf $f.Name
  if (Test-Path -LiteralPath $dst) {
    $same = (Get-FileHash -LiteralPath $dst).Hash -eq (Get-FileHash -LiteralPath $f.FullName).Hash
    if ($same) { Ok "$($f.Name)（已是最新，跳过）"; continue }
    if ($Force) {
      $bak = "$dst.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
      Run { Copy-Item -LiteralPath $dst $bak -Force }
      Warn "$($f.Name) 已存在，原文件备份为 $(Split-Path $bak -Leaf)"
    } else {
      # 不覆盖用户的工作流：bundle 版本另存为 "(bundle)" 副本
      $alt = Join-Path $wf ([IO.Path]::GetFileNameWithoutExtension($f.Name) + ' (bundle).json')
      Run { Copy-Item -LiteralPath $f.FullName $alt -Force }
      Warn "$($f.Name) 本机已有（内容不同）-> 另存为 $(Split-Path $alt -Leaf)"
      continue
    }
  }
  Run { Copy-Item -LiteralPath $f.FullName $dst -Force }
  Ok $f.Name
}

# ------------------------------------------------------------------ 管线
Say "安装 quick-txt2img 的 API 图"
$pp = Join-Path $Projects '_pipelines'
Run { New-Item -ItemType Directory -Force -Path $pp | Out-Null }
foreach ($f in Get-ChildItem (Join-Path $Bundle 'pipelines') -Filter *.json) {
  Run { Copy-Item -LiteralPath $f.FullName (Join-Path $pp $f.Name) -Force }
  Ok $f.Name
}

# ------------------------------------------------------------------ 模型
Say "模型检查"
$roots = @((Join-Path $ComfyRoot 'models')) + $ExtraModels
$models = @(
  @('diffusion_models\Minimax_H3\minimax_h3_ref2va_pruned_int8_convrot.safetensors', 19.5),
  @('text_encoders\qwen3vl_32b_minimax_h3_int8_convrot.safetensors', 25.3),
  @('vae\minimax_h3_video_vae_fp16.safetensors', 4.8),
  @('vae\minimax_h3_audio_vae_fp32.safetensors', 0.5),
  @('loras\minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors', 0.5),
  @('diffusion_models\qwen-image-2.1-UC-int8_convrot.safetensors', 6.7),
  @('text_encoders\qwen3vl_8b_int8_convrot.safetensors', 8.7),
  @('vae\qwen_image_2.1_vae_bf16.safetensors', 0.6)
)
$missing = 0
foreach ($m in $models) {
  $rel = $m[0]; $base = Split-Path $rel -Leaf
  $hit = $null
  $cands = @($rel)
  if ($ModelAlias.ContainsKey($rel)) { $cands += $ModelAlias[$rel] }
  foreach ($r in $roots) {
    foreach ($c in $cands) {
      $p = Join-Path $r $c
      if (Test-Path -LiteralPath $p) { $hit = $p; break }
    }
    if ($hit) { break }
    if (Test-Path $r) {
      # 同名文件在子目录里（ComfyUI Desktop 的 Qwen\ 之类）
      $f = Get-ChildItem $r -Recurse -File -Filter $base -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($f) { $hit = $f.FullName; break }
    }
  }
  if ($hit) { Ok ("{0,6:N1} GB  {1}{2}" -f ((Get-Item $hit).Length / 1GB), $rel, $(if ((Split-Path $hit -Leaf) -ne $base) { "  <- 本机叫 $(Split-Path $hit -Leaf)" } else { '' })) }
  else { Bad "缺 $rel"; $missing++ }
}
if ($missing) {
  Warn "还缺 $missing 个必需模型 —— 见 models.txt"
  Warn "有些机器上模型改了名（量化版/越狱版）：用 -ModelAlias @{'原名'='本机名'} 再装一次"
}

# ------------------------------------------------------------------ 自定义节点
Say "自定义节点检查"
$cn = Join-Path $ComfyRoot 'custom_nodes'
foreach ($n in @('ComfyUI-MiniMax-H3', 'rgthree-comfy', 'ComfyUI-KJNodes', 'Comfyui-Memory_Cleanup')) {
  if (Test-Path (Join-Path $cn $n)) { Ok $n } else { Bad "$n（必需，用 ComfyUI-Manager 装）" }
}
foreach ($n in @('ComfyUI-GGUF', 'H3PromptPolish', 'comfyUI-llama-TE', 'TE_MAN')) {
  if (Test-Path (Join-Path $cn $n)) { Ok $n } else { Warn "$n（影响功能/速度，建议装）" }
}

# ------------------------------------------------------------------ 自检
Say "环境自检"
$PY = Find-Python
if (-not $PY) { Warn "找不到 python，跳过自检"; return }
$doctor = Join-Path $Bundle 'doctor.py'
$argsList = @($doctor, '--comfy', $ComfyRoot)
foreach ($r in $ExtraModels) { $argsList += @('--extra-models', $r) }
if ($DryRun) { Write-Host "    `$ `"$PY`" $($argsList -join ' ')" -ForegroundColor DarkGray }
else { & $PY @argsList; $code = $LASTEXITCODE }

Write-Host ""
Write-Host "================================================================"
Write-Host " 装完了。接下来："
Write-Host "================================================================"
Write-Host "  1. 补齐上面标 x 的模型和自定义节点"
Write-Host "  2. 起 ComfyUI：  $Launcher（Desktop 用户直接开 Comfy Desktop；--fast-disk 已写进 launchArgs）"
Write-Host "  3. 读 skills\horsemovie\LOCAL.md：本机与源机器的差异、已完成的标定与身份验证结果"
Write-Host "  4. 换机器/换模型来源后要重做：标定 + 身份验证"
Write-Host "       & `"$PY`" `"$SkillsDir\horsemovie\scripts\calibrate.py`" --project <项目> --shot <镜号>"
Write-Host ""
