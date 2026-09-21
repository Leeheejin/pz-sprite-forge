# Drive an isolated Project Zomboid client window without SendInput: screenshot it, or
# PostMessage clicks / keys / mouse moves at CLIENT coordinates (the ones the screenshot
# shows). The client is found by the cachedir substring in its command line, never by
# process name: several sessions run their own clients, and killing or clicking by name
# hits someone else's game. ProjectZomboid64.exe re-execs itself, so a saved PID goes stale;
# the tag is looked up every call.
#
#   pzwin.ps1 -tag fpclient info
#   pzwin.ps1 -tag fpclient shot  -out C:\tmp\now.png
#   pzwin.ps1 -tag fpclient click -x 640 -y 360
#   pzwin.ps1 -tag fpclient move  -x 1270 -y 712
#   pzwin.ps1 -tag fpclient key   -key Escape
param([Parameter(Mandatory=$true)][string]$tag, [Parameter(Position=0)][string]$cmd = "info",
      [int]$x = 0, [int]$y = 0, [string]$key = "", [string]$out = "", [int]$procId = 0)
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class W {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
}
"@
# without this, a scaled desktop reports coordinates divided by the scale factor
[W]::SetProcessDPIAware() | Out-Null
if ($procId -eq 0) { $procId = [int](Get-CimInstance Win32_Process -Filter "Name = 'ProjectZomboid64.exe'" | Where-Object { $_.CommandLine -like "*$tag*" } | Select-Object -First 1 -ExpandProperty ProcessId) }
if (-not (Get-Process -Id $procId -ErrorAction SilentlyContinue)) { Write-Output "MYCLIENT_GONE pid=$procId"; exit 2 }
$hwnd = [IntPtr]::Zero
[W]::EnumWindows({ param($h, $l) $p = 0; [W]::GetWindowThreadProcessId($h, [ref]$p) | Out-Null
  if ($p -eq $procId -and [W]::IsWindowVisible($h)) { $sb = New-Object System.Text.StringBuilder 256; [W]::GetWindowText($h, $sb, 256) | Out-Null
    if ($sb.ToString().Length -gt 0) { $script:hwnd = $h; return $false } } ; return $true }, [IntPtr]::Zero) | Out-Null
if ($hwnd -eq [IntPtr]::Zero) { Write-Output "NOWINDOW"; exit 1 }
$r = New-Object W+RECT; [W]::GetClientRect($hwnd, [ref]$r) | Out-Null
$o = New-Object W+POINT; $o.X = 0; $o.Y = 0; [W]::ClientToScreen($hwnd, [ref]$o) | Out-Null
$lp = [IntPtr](($y -shl 16) -bor ($x -band 0xFFFF))
switch ($cmd) {
  "info"  { Write-Output ("hwnd={0} client={1}x{2} screenOrigin={3},{4}" -f $hwnd, $r.R, $r.B, $o.X, $o.Y) }
  "shot"  { Add-Type -AssemblyName System.Drawing
            $wr = New-Object W+RECT; [W]::GetWindowRect($hwnd, [ref]$wr) | Out-Null
            $ww = $wr.R - $wr.L; $wh = $wr.B - $wr.T
            $bmp = New-Object System.Drawing.Bitmap($ww, $wh); $g = [System.Drawing.Graphics]::FromImage($bmp)
            $hdc = $g.GetHdc(); [W]::PrintWindow($hwnd, $hdc, 2) | Out-Null; $g.ReleaseHdc($hdc)
            # crop the client area out of the full window image (client origin relative to window origin)
            $cx = $o.X - $wr.L; $cy = $o.Y - $wr.T
            $crop = $bmp.Clone((New-Object System.Drawing.Rectangle($cx, $cy, $r.R, $r.B)), $bmp.PixelFormat)
            $crop.Save($out, [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose(); $crop.Dispose()
            Write-Output ("saved {0} client {1}x{2}" -f $out, $r.R, $r.B) }
  "click" { [W]::PostMessage($hwnd, 0x0200, [IntPtr]0, $lp) | Out-Null; Start-Sleep -Milliseconds 60
            [W]::PostMessage($hwnd, 0x0201, [IntPtr]1, $lp) | Out-Null; Start-Sleep -Milliseconds 80
            [W]::PostMessage($hwnd, 0x0202, [IntPtr]0, $lp) | Out-Null; Write-Output ("clicked {0},{1}" -f $x, $y) }
  "move"  { [W]::PostMessage($hwnd, 0x0200, [IntPtr]0, $lp) | Out-Null; Write-Output ("moved {0},{1}" -f $x, $y) }
  "key"   { $vk = @{ Enter = 0x0D; Escape = 0x1B; Space = 0x20; Tab = 0x09 }[$key]
            [W]::PostMessage($hwnd, 0x0100, [IntPtr]$vk, [IntPtr]0) | Out-Null; Start-Sleep -Milliseconds 80
            [W]::PostMessage($hwnd, 0x0101, [IntPtr]$vk, [IntPtr]0) | Out-Null; Write-Output ("key {0}" -f $key) }
}
