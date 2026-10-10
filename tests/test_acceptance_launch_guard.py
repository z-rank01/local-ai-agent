"""Exercise the PowerShell preflight without Docker, services or credentials."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name=='nt','Windows launcher guard')
class AcceptanceLaunchGuardTests(unittest.TestCase):
    def run_guard(self, settings, expected='simulation/isolated', listener=False):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'stock').mkdir();(root/'data/private').mkdir(parents=True)
            values=dict(root=str(root/'stock'),state_dir='simulation/isolated',
                        worker=False,enabled=True,port=17665)
            values.update(settings)
            settings=values
            (root/'data/private/daily-services.json').write_text(json.dumps(settings),encoding='utf-8')
            helper=Path(__file__).resolve().parents[1]/'scripts/service-supervisor.ps1'
            script=root/'check.ps1'
            script.write_text("$ErrorActionPreference='Stop'\n. '"+str(helper).replace("'","''")+"'\n"
                +"function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) "
                +("return @{Port=$LocalPort}" if listener else "return $null")+" }\n"
                +"Assert-DailyAcceptanceState '"+str(root).replace("'","''")+"' '"+expected+"'\n"
                +"Write-Output 'GUARD_PASSED'\n",encoding='utf-8')
            return subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script)],
                                  capture_output=True,text=True,timeout=15)

    def test_correct_paused_separate_state_passes(self):
        result=self.run_guard({})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('GUARD_PASSED',result.stdout)

    def test_wrong_state_enabled_worker_or_live_listener_rejects(self):
        for changes,listener in [({'state_dir':'simulation'},False),({'worker':True},False),
                                 ({'enabled':False},False),({},True)]:
            with self.subTest(changes=changes,listener=listener):
                result=self.run_guard(changes,listener=listener)
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('GUARD_PASSED',result.stdout)
                self.assertRegex(result.stderr,'Acceptance|Exit the running stack')

    def test_default_alias_and_outside_paths_reject(self):
        for state in ('simulation/.','simulation/../outside','C:/outside'):
            with self.subTest(state=state):
                result=self.run_guard({'state_dir':state},expected=state)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('Acceptance',result.stderr)

    def test_unique_entry_checks_before_first_side_effect(self):
        source=(Path(__file__).resolve().parents[1]/'scripts/start-daily.ps1').read_text(encoding='utf-8')
        self.assertLess(source.index('Assert-DailyAcceptanceState'),source.index('New-Item'))
        self.assertEqual(source.count('Assert-DailyAcceptanceState'),2)
