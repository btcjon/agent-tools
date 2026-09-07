import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ksu', Path(__file__).parents[1] / 'scripts' / 'ksu.py')
k = importlib.util.module_from_spec(spec); spec.loader.exec_module(k)

class KSU(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        k.save(self.state / 'config.json', dict(time='03:00', restart_policy='services', path=os.environ['PATH'], topgrade='/bin/false', retries=2, only=['brew_formula'], disable=[], coverage_reviewed=True))

    def test_duplicate_lock_rejected_and_released(self):
        with k.lock(self.state):
            with self.assertRaises(RuntimeError):
                with k.lock(self.state): pass
        with k.lock(self.state): pass

    def test_transient_retries_then_success(self):
        calls=[]
        def fake(argv, env, log):
            calls.append(argv)
            with log.open('a') as f: f.write('temporary failure\n' if len(calls)<3 else 'done\n')
            return 1 if len(calls)<3 else 0
        with patch.object(k,'execute',side_effect=fake), patch.object(k.time,'sleep'):
            self.assertEqual(k.run(self.state),0)
        r=k.read(self.state/'receipt.json')
        self.assertEqual(len(r['attempts']),3)
        self.assertEqual(r['status'],'engine_completed')
        self.assertIn('--only',calls[0])

    def test_unknown_and_permissions_do_not_retry(self):
        for text in ['broken database', 'password is required']:
            def fake(argv,env,log): log.write_text(text); return 1
            with patch.object(k,'execute',side_effect=fake) as ex:
                self.assertEqual(k.run(self.state),1)
                self.assertEqual(ex.call_count,1)

    def test_retry_budget_exhausted(self):
        def fake(argv,env,log):
            with log.open('a') as f: f.write('connection reset\n')
            return 1
        with patch.object(k,'execute',side_effect=fake) as ex, patch.object(k.time,'sleep'):
            k.run(self.state)
            self.assertEqual(ex.call_count,3)
        self.assertEqual(k.health(self.state)['status'],'needs_attention')

    def test_preview_does_not_mark_update_success(self):
        with patch.object(k,'execute',return_value=0): self.assertEqual(k.run(self.state,True),0)
        self.assertFalse((self.state/'receipt.json').exists())
        self.assertEqual(k.health(self.state)['status'],'never_run')

    def test_unreviewed_run_rejected(self):
        cfg=k.read(self.state/'config.json'); cfg['coverage_reviewed']=False; k.save(self.state/'config.json',cfg)
        with self.assertRaises(RuntimeError): k.run(self.state)

    def test_unknown_previous_transaction_blocks_replay(self):
        k.save(self.state/'receipt.json',dict(status='running'))
        with self.assertRaises(RuntimeError), patch.object(k,'execute') as ex:
            k.run(self.state)
        ex.assert_not_called()

    def test_stale_detected(self):
        k.save(self.state/'receipt.json',dict(started='2020-01-01T00:00:00+00:00',status='running'))
        self.assertEqual(k.health(self.state)['status'],'stale')

    def test_config_preserves_user_environment(self):
        cfg=k.read(self.state/'config.json')
        with patch.dict(os.environ, {'XDG_CONFIG_HOME':'/original/config'}):
            self.assertEqual(k.environment(self.state,cfg)['XDG_CONFIG_HOME'],'/original/config')
        path=self.state/'engine.toml'; k.engine_config(cfg,path)
        self.assertIn('updates_auto_reboot = "no"',path.read_text())

    def test_reboot_commands(self):
        for system, executable in [('Darwin','sudo'),('Linux','sudo'),('Windows','shutdown.exe')]:
            with patch.object(k.platform,'system',return_value=system):
                self.assertEqual(k.reboot_command()[0], executable)

    def test_mac_reboot_signal(self):
        log=self.state/'output'; log.write_text('A restart is required.')
        with patch.object(k.platform,'system',return_value='Darwin'):
            self.assertIs(k.reboot_required(log,os.environ.copy()),True)
            log.write_text('Completed')
            self.assertEqual(k.reboot_required(log,os.environ.copy()),'unknown')

    def test_scheduler_definitions(self):
        cfg=k.read(self.state/'config.json')
        for system in ['Darwin','Linux','Windows']:
            with self.subTest(system=system), patch.object(k.platform,'system',return_value=system), patch.object(k.Path,'home',return_value=self.state), patch.object(k,'checked') as check, patch.object(k.subprocess,'run'):
                k.install_scheduler(self.state,cfg)
                self.assertGreater(check.call_count,0)
        unit=(self.state/'.config/systemd/user/ksu-nightly.timer').read_text()
        self.assertIn('03:00:00',unit); self.assertIn('Persistent=true',unit)

if __name__=='__main__': unittest.main()
