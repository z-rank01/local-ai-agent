import os, subprocess, tempfile, unittest
from pathlib import Path

@unittest.skipUnless(os.name == 'nt', 'Windows launcher')
class LauncherRecoveryTests(unittest.TestCase):
    def check_branch(self, controller, occupied=False, build=False):
        source=Path('scripts/start-daily.ps1').read_text(encoding='utf-8-sig')
        block=source[source.index('$dailyExisting ='):source.index('# Use the stock service')]
        pre="""$ErrorActionPreference='Stop'
$dailyWebToken='test'; $dailyBrowserUrl='test'; $dailyRoot='test'
function Invoke-RestMethod { param($Uri,$Headers,$TimeoutSec)
 if ($Uri -like '*9510*') { return @{daily_services=$true} }
 CONTROLLER
}
function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) OCCUPIED }
function New-DailyServiceSupervisor { 'NEW'; return @{id=1} }
function Write-DailyProcessIdentity { 'IDENTITY' }
function Write-DailySupervisorLog {}
function Write-Warn2 {}
function Start-Process { 'OPEN' }
function Wait-DailyServiceSupervisor { 'WAIT' }
""".replace('CONTROLLER',controller).replace('OCCUPIED','return @{id=1}' if occupied else 'return $null')
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'test.ps1'; script.write_text(pre+('$Build=$true\n' if build else '')+block,encoding='utf-8-sig')
            return subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script)],capture_output=True,text=True,errors='replace',timeout=20, env={**os.environ, 'TEMP':folder, 'TMP':folder})
    def test_missing_controller_recovers_and_waits(self):
        r=self.check_branch("throw 'offline'"); self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('IDENTITY',r.stdout); self.assertIn('WAIT',r.stdout)
    def test_healthy_controller_only_opens(self):
        r=self.check_branch("return @{service='daily-launcher-service-supervisor'}")
        self.assertEqual(r.returncode,0,r.stderr); self.assertEqual(r.stdout.strip(),'OPEN')
    def test_occupied_unverified_controller_rejected(self):
        r=self.check_branch("throw 'unknown'",occupied=True)
        self.assertNotEqual(r.returncode,0); self.assertNotIn('OPEN',r.stdout); self.assertNotIn('NEW',r.stdout)
    def test_live_build_rejected_before_mutation(self):
        r=self.check_branch("throw 'offline'",build=True)
        self.assertNotEqual(r.returncode,0); self.assertNotIn('NEW',r.stdout)

    def test_controller_socket_is_not_inheritable(self):
        helper=Path('scripts/service-supervisor.ps1').read_text(encoding='utf-8-sig')
        helper=helper.replace('[System.Net.IPAddress]::Loopback, 9511','[System.Net.IPAddress]::Loopback, 0')
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'socket.ps1'
            script.write_text(helper+"\n$c=New-DailyServiceSupervisor '"+folder.replace("'","''")+"'\n"+'''Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class InspectSocket {
 [DllImport("kernel32.dll", SetLastError=true)]
 public static extern bool GetHandleInformation(IntPtr h, out uint flags);
}
"@
try {
 [uint32]$flags=0
 if (-not [InspectSocket]::GetHandleInformation($c.Listener.Server.Handle,[ref]$flags)) { throw 'inspect failed' }
 if ($flags -band 1) { throw 'socket is inheritable' }
 'NONINHERITABLE'
} finally { $c.Listener.Stop() }
''',encoding='utf-8-sig')
            r=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script)],capture_output=True,text=True,errors='replace',timeout=20, env={**os.environ, 'TEMP':folder, 'TMP':folder})
            self.assertEqual(r.returncode,0,r.stderr); self.assertIn('NONINHERITABLE',r.stdout)
