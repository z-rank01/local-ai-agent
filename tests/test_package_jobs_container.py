"""Run inside skill-runner: no network requests or actual package installation."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
if sys.platform != 'linux' or not Path('/app/package_jobs.py').exists():
    raise unittest.SkipTest('Run in the skill-runner image')
sys.path.insert(0,'/app')
import package_jobs as jobs

class PackageJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch=patch.object(jobs,'_ROOT',Path(self.temp.name))
        self.patch.start();self.addCleanup(self.patch.stop)
        jobs._JOBS.clear();jobs._PROCESSES.clear()

    def test_existing_version_skips_without_running_pip(self):
        with patch.object(jobs,'inventory',return_value={'pandas':'2.2.3'}), patch.object(jobs,'_command',side_effect=AssertionError('pip must not run')):
            result=jobs.start(['pandas>=2'])
            self.assertEqual(result['status'],'succeeded')
            self.assertEqual(result['skipped'],['pandas>=2'])

    def test_job_continues_after_observation_and_streams_logs(self):
        with patch.object(jobs,'inventory',return_value={}), patch.object(jobs,'_command',return_value=[sys.executable,'-u','-c',"import time; print('downloading',flush=True); time.sleep(.5); print('installed',flush=True)"]):
            result=jobs.start(['example-package'])
            self.assertFalse(result['completed'])
            job_id=result['job_id']
            time.sleep(.1)
            current=jobs.status(job_id)
            self.assertIn('downloading',current['log'])
            self.assertFalse(current['completed'])
            done=jobs.status(job_id,2)
            self.assertEqual(done['status'],'succeeded')
            self.assertIn('installed',done['log'])

    def test_failure_keeps_logs(self):
        with patch.object(jobs,'inventory',return_value={}), patch.object(jobs,'_command',return_value=[sys.executable,'-u','-c',"print('network failure'); raise SystemExit(1)"]):
            result=jobs.start(['example-package'])
            done=jobs.status(result['job_id'],2)
            self.assertEqual(done['status'],'failed')
            self.assertIn('network failure',done['log'])

    def test_duplicate_request_and_cancel(self):
        with patch.object(jobs,'inventory',return_value={}), patch.object(jobs,'_command',return_value=[sys.executable,'-u','-c',"import time; print('waiting',flush=True); time.sleep(120)"]):
            result=jobs.start(['example-package'])
            second=jobs.start(['another-package'])
            self.assertFalse(second['request_started'])
            self.assertEqual(result['job_id'],second['job_id'])
            time.sleep(.1)
            cancelled=jobs.cancel(result['job_id'])
            self.assertEqual(cancelled['status'],'cancelled')
            for _ in range(100):
                if not jobs._PROCESSES: break
                time.sleep(.01)
            self.assertFalse(jobs._PROCESSES)

    def test_restart_marks_pending_job_interrupted(self):
        job={'job_id':'test','status':'running','started_at':time.time(),'packages':['x']}
        jobs._save(job)
        self.assertEqual(jobs.status('test')['status'],'interrupted')

if __name__=='__main__':unittest.main()
