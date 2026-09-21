# Composes a Steam Workshop header image (1152x760, the size the Home Brewing promo uses)
# out of harness screenshots: a world shot on the left, a UI window and a tooltip on the
# right, a title band with item icons along the bottom. Crop rectangles are x,y,w,h in the
# screenshot's own pixels.
#
#   make_promo.ps1 -shots .\shots -tex <mod>\common\media\textures -out promo.png `
#       -world fp_promo_01.png -window fp_promo_02.png -tip fp_promo_03.png `
#       -worldRect 380,0,560,440 -windowRect 16,24,600,440 -tipRect 40,480,275,112 `
#       -title "Food Preservation" -subtitle "Smoking - Salting - Jerky   Project Zomboid Build 42" `
#       -icons Item_FPSaltedBeef.png,Item_FPSmokedBeef.png
param(
  [Parameter(Mandatory=$true)][string]$shots,
  [Parameter(Mandatory=$true)][string]$out,
  [Parameter(Mandatory=$true)][string]$world,
  [Parameter(Mandatory=$true)][string]$window,
  [Parameter(Mandatory=$true)][string]$tip,
  [Parameter(Mandatory=$true)][string]$title,
  [string]$subtitle = "",
  [string]$tex = "",
  [string[]]$icons = @(),
  [int[]]$worldRect = @(0, 0, 1280, 720),
  [int[]]$windowRect = @(0, 0, 600, 440),
  [int[]]$tipRect = @(0, 0, 280, 120)
)
Add-Type -AssemblyName System.Drawing
function Crop($path, $r) {
  $src = [System.Drawing.Image]::FromFile($path)
  $rect = New-Object System.Drawing.Rectangle($r[0], $r[1], $r[2], $r[3])
  $bmp = New-Object System.Drawing.Bitmap($r[2], $r[3])
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.DrawImage($src, (New-Object System.Drawing.Rectangle(0, 0, $r[2], $r[3])), $rect, [System.Drawing.GraphicsUnit]::Pixel)
  $g.Dispose(); $src.Dispose(); return $bmp
}
function Place($g, $img, $x, $y, $w, $h, $smooth) {
  if ($smooth) { $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic }
  else { $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor; $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half }
  $g.DrawImage($img, (New-Object System.Drawing.Rectangle($x, $y, $w, $h)))
  $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Default
}
function Frame($g, $x, $y, $w, $h) {
  $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 60, 62, 72), 2)
  $g.DrawRectangle($pen, $x - 1, $y - 1, $w + 1, $h + 1); $pen.Dispose()
}

$W = 1152; $H = 760
$canvas = New-Object System.Drawing.Bitmap($W, $H)
$g = [System.Drawing.Graphics]::FromImage($canvas)
$g.Clear([System.Drawing.Color]::FromArgb(255, 27, 28, 34))
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

# left: the world shot, up to 640x600
$worldImg = Crop (Join-Path $shots $world) $worldRect
$hs = [Math]::Min(640 / $worldRect[2], 600 / $worldRect[3])
$hw = [int]($worldRect[2] * $hs); $hh = [int]($worldRect[3] * $hs)
Place $g $worldImg 32 28 $hw $hh $true; Frame $g 32 28 $hw $hh

# right: the window, 420 wide, and the tooltip under it
$winImg = Crop (Join-Path $shots $window) $windowRect
$ws = 420 / $windowRect[2]
$ww = [int]($windowRect[2] * $ws); $wh = [int]($windowRect[3] * $ws)
$rx = 32 + $hw + 28
Place $g $winImg $rx 28 $ww $wh $true; Frame $g $rx 28 $ww $wh

$tipImg = Crop (Join-Path $shots $tip) $tipRect
$ts = 1.3
$tw = [int]($tipRect[2] * $ts); $th = [int]($tipRect[3] * $ts)
$ty = 28 + $wh + 22
Place $g $tipImg $rx $ty $tw $th $true; Frame $g $rx $ty $tw $th

# bottom band: title, subtitle and the icons
$bandY = 640
$g.FillRectangle((New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 20, 21, 26))), 0, $bandY, $W, $H - $bandY)
$titleFont = New-Object System.Drawing.Font("Segoe UI", 34, [System.Drawing.FontStyle]::Bold)
$subFont   = New-Object System.Drawing.Font("Segoe UI", 17, [System.Drawing.FontStyle]::Regular)
$white = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 240, 236, 226))
$grey  = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 170, 168, 160))
$g.DrawString($title, $titleFont, $white, 30, $bandY + 14)
if ($subtitle) { $g.DrawString($subtitle, $subFont, $grey, 34, $bandY + 68) }
if ($icons.Count -gt 0) {
  $ix = $W - 30 - ($icons.Count * 84)
  foreach ($n in $icons) {
    $ic = [System.Drawing.Image]::FromFile((Join-Path $tex $n))
    Place $g $ic $ix ($bandY + 28) 64 64 $false
    $ic.Dispose(); $ix += 84
  }
}

$canvas.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $canvas.Dispose(); $worldImg.Dispose(); $winImg.Dispose(); $tipImg.Dispose()
"wrote $out ${W}x${H}"
