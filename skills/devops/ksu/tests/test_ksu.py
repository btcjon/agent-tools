import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).parents[1] / 'scripts'
sys.path.insert(0,str(SCRIPTS))
import ksu as k
import ksu_inventory as inv

class FakeProbe(inv.Probe):
    def __init__(self, home, commands=None, bins=None, repos=None):
        super().__init__(home=home,system='FixtureOS',path='/fixture/bin')
        self.commands=commands or {}; self.bins=bins or {}; self.repos=repos or {}
    def executables(self,name): return self.bins.get(name,[])
    def command(self,argv):
        answer=self.commands.get(tuple(argv))
        if answer is None:
            self.errors.append({'probe':argv[0],'error':'fixture unavailable'})
        return answer
    def git(self,path):
        return next((v for root,v in self.repos.items() if Path(root).resolve()==Path(path).resolve() or Path(root).resolve() in Path(path).resolve().parents),None)

class KSU(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.state=self.root/'state'; self.state.mkdir()
        self.home=self.root/'home'; self.home.mkdir()
        self.probe=FakeProbe(self.home)
    def row(self,name='tool',**kwargs):
        r=inv.item('npm',name,'/global',version='1',source='https://registry.npmjs.org',manager='/bin/npm',**kwargs)
        return r
    def scan(self,rows,probe=None):
        with patch.object(inv,'packages',return_value=rows), patch.object(inv,'harnesses',return_value=[]), patch.object(inv,'skill_rows',return_value=[]), patch.object(inv,'other_programs',return_value=[]):
            return inv.scan(self.state,probe=probe or self.probe)
    def selected(self,rows):
        data=self.scan(rows); inv.choose(self.state,[r['id'] for r in rows]); return data
    def config(self):
        k.save(self.state/'config.json',dict(time='03:00',restart_policy='services',path=os.environ['PATH'],retries=2))

    def test_exact_selection_and_new_items_stay_unselected(self):
        a=self.row('a'); b=self.row('b'); self.selected([a]); self.scan([a,b])
        plan=inv.plan(self.state)
        self.assertEqual([x['name'] for x in plan['actions']],['a'])
        self.assertIn(b['id'],plan['new_items'])
        self.assertEqual(plan['actions'][0]['argv'],['/bin/npm','update','--global','a'])

    def test_exclusions_persist_and_versions_do_not_revoke_selection(self):
        a=self.row('a'); b=self.row('b'); self.scan([a,b]); inv.choose(self.state,[a['id']],[b['id']])
        a['version']='2'; b['version']='3'; data=self.scan([a,b])
        self.assertEqual([x['selection'] for x in data['items']],['selected','excluded'])

    def test_source_change_requires_reselection(self):
        a=self.row(); self.selected([a]); a['source']='https://another.registry'; self.scan([a])
        self.assertFalse(inv.plan(self.state)['actions']); self.assertTrue(inv.plan(self.state)['blocked'])

    def test_removed_reappearing_item_requires_reselection(self):
        a=self.row(); self.selected([a]); self.scan([]); self.scan([a]); self.assertTrue(inv.plan(self.state)['blocked'])

    def test_stale_or_foreign_exports_rejected_atomically(self):
        a=self.row(); data=self.scan([a])
        for host,revision in [('elsewhere',data['revision']),(data['host']['id'],'old')]:
            with self.assertRaises(ValueError): inv.choose(self.state,document=dict(host_id=host,revision=revision,selected=[a['id']]))
        self.assertFalse((self.state/'selections.json').exists())

    def test_checkbox_export_import(self):
        a=self.row('a'); b=self.row('b'); data=self.scan([a,b])
        inv.choose(self.state,document=dict(host_id=data['host']['id'],revision=data['revision'],selected=[b['id']]))
        self.assertEqual([x['name'] for x in inv.plan(self.state)['actions']],['b'])

    def test_blocked_unknown_and_conflicting_ids_rejected(self):
        a=self.row(blocked='Pinned'); self.scan([a])
        for enable,exclude in [([a['id']],[]),(['missing'],[]),([a['id']],[a['id']])]:
            with self.assertRaises(ValueError): inv.choose(self.state,enable,exclude)
        self.assertFalse((self.state/'selections.json').exists())

    def test_injected_option_identifier_rejected(self):
        for name in ['--all','a;echo bad','a\nbad','*']:
            with self.assertRaises(ValueError): inv.action(self.row(name))

    def test_host_state_not_portable(self):
        self.scan([])
        other=FakeProbe(self.root/'other')
        with self.assertRaises(ValueError): self.scan([],other)

    def test_html_escapes_script_in_inventory(self):
        self.scan([self.row('</script><script>alert(1)</script>')])
        text=(self.state/'inventory.html').read_text()
        self.assertNotIn('</script><script>alert(1)',text)
        self.assertIn('\\u003c/script',text)

    def test_brew_and_npm_inventory_prioritizes_harnesses_and_pins(self):
        probe=FakeProbe(self.home,bins={'brew':['/brew'],'npm':['/npm']},commands={
            ('/brew','info','--json=v2','--installed'):json.dumps({'formulae':[dict(name='codex',full_name='codex',installed=[{'version':'1'}],pinned=True,tap='homebrew/core')],'casks':[]}),
            ('/npm','ls','--global','--depth=0','--json'):json.dumps({'path':'/global','dependencies':{'@anthropic-ai/claude-code':{'version':'2'}}}),
            ('/npm','config','get','registry'):'https://secret:password@registry.example/path?token=hidden'})
        rows=inv.packages(probe)
        self.assertEqual([r['category'] for r in rows],['harnesses','harnesses'])
        self.assertIn('Pinned',rows[0]['blocked'])
        self.assertEqual(rows[1]['source'],'https://registry.example/path')
        self.assertNotIn('password',json.dumps(rows))

    def test_probe_failure_visible_without_false_inventory(self):
        probe=FakeProbe(self.home,bins={'npm':['/npm']})
        self.assertEqual(inv.packages(probe),[]); self.assertTrue(probe.errors)

    def test_malformed_probe_json_visible(self):
        probe=FakeProbe(self.home,bins={'npm':['/npm']},commands={('/npm','ls','--global','--depth=0','--json'):'not json'})
        self.assertEqual(inv.packages(probe),[]); self.assertEqual(probe.errors[0]['error'],'invalid JSON')

    def test_skill_symlinks_dedup_and_custom_branch_blocked(self):
        repo=self.home/'source'; skill=repo/'skill'; skill.mkdir(parents=True); (skill/'SKILL.md').write_text('---\nname: fixture\n---')
        for root in ['.codex/skills','.claude/skills']:
            folder=self.home/root; folder.mkdir(parents=True); (folder/'fixture').symlink_to(skill,target_is_directory=True)
        git=dict(root=str(repo),branch='custom/work',upstream='origin/custom/work',dirty=False,origin='https://example.test/repo',head='abc')
        rows=inv.skill_rows(FakeProbe(self.home,repos={str(repo):git}),[],None)
        skills=[r for r in rows if r['kind']=='skill']; groups=[r for r in rows if r['kind']=='skill_repo']
        self.assertEqual(len(skills),1); self.assertEqual(len(skills[0]['projections']),2)
        self.assertIn('Custom',groups[0]['blocked'])

    def test_shared_skill_consumers_cannot_update_projection(self):
        root=self.home/'.agents/exported-skills'; p=root/'fixture'; p.mkdir(parents=True); (p/'SKILL.md').write_text('fixture')
        rows=inv.skill_rows(self.probe,[],dict(root=str(self.home/'authority'),warehouse=str(root),role='consumer'))
        self.assertTrue(all(r['blocked'] for r in rows)); self.assertFalse(any(r['kind']=='skill_repo' for r in rows))

    def test_dirty_repository_blocked(self):
        root=self.home/'skills'; p=root/'fixture'; p.mkdir(parents=True); (p/'SKILL.md').write_text('fixture')
        git=dict(root=str(root),branch='main',upstream='origin/main',dirty=True,origin='https://example.test/repo',head='abc')
        rows=inv.skill_rows(FakeProbe(self.home,repos={str(root):git}),[str(root)],None)
        self.assertIn('local changes',next(r for r in rows if r['kind']=='skill_repo')['blocked'])

    def test_exact_apt_target_no_full_upgrade(self):
        r=inv.item('apt','python3','/apt',manager='/apt')
        command=inv.action(r)['argv']
        self.assertIn('--only-upgrade',command); self.assertIn('--no-remove',command); self.assertEqual(command[-1],'python3')

    def test_strict_restart_policy_blocks_unenforceable_managers(self):
        self.selected([self.row()]); self.assertTrue(inv.plan(self.state,restart_policy='defer')['blocked'])

    def test_preview_never_executes(self):
        a=self.row(); data=self.selected([a]); self.config()
        with patch.object(inv,'scan',return_value=data),patch.object(k,'execute') as execute,patch('builtins.print'):
            self.assertEqual(k.run(self.state,True),0); execute.assert_not_called()
        self.assertFalse((self.state/'receipt.json').exists())

    def test_run_retries_only_failed_target_and_verifies(self):
        a=self.row('a'); b=self.row('b'); data=self.selected([a,b]); self.config(); calls=[]
        def fake(argv,env,log):
            calls.append(argv[-1]); fail=argv[-1]=='a' and calls.count('a')<3
            with log.open('a') as f:f.write('temporary failure\n' if fail else 'done\n')
            return 1 if fail else 0
        with patch.object(inv,'scan',return_value=data),patch.object(k,'execute',side_effect=fake),patch.object(k.time,'sleep'),patch.object(k,'reboot_required',return_value=False):
            self.assertEqual(k.run(self.state),0)
        self.assertEqual(calls,['a','a','a','b'])
        receipt=k.read(self.state/'receipt.json'); self.assertEqual(receipt['items'][0]['after'],'1')

    def test_unknown_failure_not_retried_other_items_continue(self):
        a=self.row('a'); b=self.row('b'); data=self.selected([a,b]); self.config()
        def fake(argv,env,log):
            with log.open('a') as f:f.write('unknown failure\n')
            return 1 if argv[-1]=='a' else 0
        with patch.object(inv,'scan',return_value=data),patch.object(k,'execute',side_effect=fake) as execute,patch.object(k,'reboot_required',return_value=False):
            self.assertEqual(k.run(self.state),1); self.assertEqual(execute.call_count,2)

    def test_running_receipt_blocks_replay(self):
        self.config(); k.save(self.state/'receipt.json',{'status':'running'})
        with self.assertRaises(RuntimeError),patch.object(k,'execute') as execute:k.run(self.state)
        execute.assert_not_called()

    def test_legacy_configuration_not_sufficient_authorization(self):
        self.config()
        with self.assertRaises(RuntimeError):k.run(self.state)

    def test_duplicate_lock_rejected_and_released(self):
        with k.lock(self.state):
            with self.assertRaises(RuntimeError):
                with k.lock(self.state):pass
        with k.lock(self.state):pass

    def test_stale_detected(self):
        self.config(); k.save(self.state/'receipt.json',dict(started='2020-01-01T00:00:00+00:00',status='running'))
        self.assertEqual(k.health(self.state)['status'],'stale')

    def test_noninteractive_path_includes_user_tools(self):
        path=self.home/'.local/bin'; path.mkdir(parents=True)
        tool=path/'codex'; tool.write_text('fixture'); tool.chmod(0o700)
        probe=inv.Probe(home=self.home,system='Linux',path='/unavailable')
        self.assertIn(str(tool),probe.executables('codex'))

    def test_collector_failure_does_not_erase_other_categories(self):
        row=inv.item('application','fixture','/Applications/fixture.app',blocked='No updater',category='programs')
        with patch.object(inv,'packages',side_effect=ValueError('malformed')),patch.object(inv,'harnesses',return_value=[]),patch.object(inv,'other_programs',return_value=[row]):
            data=inv.scan(self.state,probe=self.probe)
        self.assertEqual(data['items'][0]['name'],'fixture');self.assertTrue(data['errors'])

    def test_uv_local_source_blocked_and_constraint_change_invalidates(self):
        tools=self.home/'tools'; p=tools/'fixture'; p.mkdir(parents=True)
        receipt=p/'uv-receipt.toml'
        receipt.write_text('[tool]\nrequirements = [{name="fixture", specifier=">=1,<2"}]\n')
        probe=FakeProbe(self.home,bins={'uv':['/uv']},commands={('/uv','tool','list'):'fixture v1.2.0',('/uv','tool','dir'):str(tools)})
        rows=inv.packages(probe)
        if rows[0]['blocked'] and sys.version_info < (3,11): return
        self.selected(rows)
        receipt.write_text('[tool]\nrequirements = [{name="fixture", specifier=">=2,<3"}]\n')
        self.scan(inv.packages(probe)); self.assertTrue(inv.plan(self.state)['blocked'])
        receipt.write_text('[tool]\nrequirements = [{name="fixture", path="/local/source"}]\n')
        self.assertTrue(inv.packages(probe)[0]['blocked'])

    def test_missing_post_update_readback_is_not_success(self):
        data=self.selected([self.row()]); self.config(); after=dict(data,items=[])
        def fake(argv,env,log):log.write_text('done');return 0
        with patch.object(inv,'scan',side_effect=[data,after]),patch.object(k,'execute',side_effect=fake),patch.object(k,'reboot_required',return_value=False):
            self.assertEqual(k.run(self.state),1)
        self.assertEqual(k.read(self.state/'receipt.json')['items'][0]['status'],'verification_incomplete')

    def test_runtime_install_preserves_user_choices(self):
        self.selected([self.row()]); self.config(); before=(self.state/'selections.json').read_bytes()
        k.install_runtime(self.state)
        self.assertEqual((self.state/'selections.json').read_bytes(),before)
        self.assertTrue((self.state/'ksu_inventory.py').exists())
        k.save(self.state/'receipt.json',{'status':'running'})
        with self.assertRaises(RuntimeError): k.install_runtime(self.state)

    def test_skill_source_updates_are_fast_forward_only(self):
        row=inv.item('skill_repo','skills','/source',manager='/git')
        self.assertEqual(inv.action(row)['argv'],['/git','-C','/source','pull','--ff-only'])

    def test_winget_selects_one_exact_source_and_id(self):
        row=inv.item('winget','OpenAI.Codex','/winget',source='winget',manager='/winget')
        cmd=inv.action(row)['argv'];self.assertIn('--exact',cmd);self.assertNotIn('--all',cmd)
        self.assertEqual(cmd[cmd.index('--id')+1],'OpenAI.Codex')

    def test_winget_pins_fail_closed(self):
        self.assertTrue(inv.winget_pin_block(None,'Example.Tool'))
        self.assertTrue(inv.winget_pin_block('Name Id Type\n-------------------\nTool Example.Tool Pinning','Example.Tool'))
        self.assertEqual(inv.winget_pin_block('No pins found matching input criteria.','Example.Tool'),'')
        self.assertTrue(inv.winget_pin_block('unknown localized output','Example.Tool'))

    def test_cargo_build_options_preserved(self):
        home=self.home/'.cargo'; home.mkdir()
        inv.write_json(home/'.crates2.json',{'installs':{'fixture 1.0.0 (registry+https://example.test)':dict(version_req='^1',features=['special'],all_features=False,no_default_features=True,profile='release',target='x86_64-unknown-linux-gnu')}})
        probe=FakeProbe(self.home,bins={'cargo':['/cargo']},commands={('/cargo','install','--list'):'fixture v1.0.0:\n    fixture'})
        with patch.dict(os.environ,{'CARGO_HOME':str(home)}):rows=inv.packages(probe)
        command=inv.action(rows[0])['argv']
        self.assertIn('--features=special',command);self.assertIn('--no-default-features',command);self.assertIn('--version=^1',command)

    def test_codex_router_uses_native_updater_and_blocks_tracked_edits(self):
        root=self.home/'.local/share/codex-router'; (root/'bin').mkdir(parents=True)
        cli=root/'bin'/'codex-router'; cli.write_text('#!/bin/sh\n'); cli.chmod(0o700)
        (root/'package.json').write_text('{"version":"0.5.1"}')
        git=dict(root=str(root),branch='main',upstream='origin/main',dirty=True,tracked_dirty=False,
                 origin='https://github.com/duolahypercho/codex-router.git',head='abc1234')
        rows=inv.codex_router_rows(FakeProbe(self.home,repos={str(root):git}))
        self.assertEqual(len(rows),1); self.assertEqual(rows[0]['kind'],'codex_router')
        self.assertFalse(rows[0]['blocked'])
        self.assertEqual(inv.action(rows[0])['argv'],[str(cli),'update'])
        git['tracked_dirty']=True
        blocked=inv.codex_router_rows(FakeProbe(self.home,repos={str(root):git}))[0]
        self.assertIn('Tracked files',blocked['blocked'])
        git['tracked_dirty']=False; git['branch']='detached'
        self.assertIn('origin/main',inv.codex_router_rows(FakeProbe(self.home,repos={str(root):git}))[0]['blocked'])
        git['branch']='main'; git['origin']='https://example.test/fork'
        self.assertIn('recognized',inv.codex_router_rows(FakeProbe(self.home,repos={str(root):git}))[0]['blocked'])

    def test_codex_router_defer_policy_requires_review(self):
        root=self.home/'.local/share/codex-router'; (root/'bin').mkdir(parents=True)
        cli=root/'bin'/'codex-router'; cli.write_text('#!/bin/sh\n'); cli.chmod(0o700)
        (root/'package.json').write_text('{"version":"0.5.1"}')
        git=dict(root=str(root),branch='main',upstream='origin/main',dirty=False,tracked_dirty=False,
                 origin='https://github.com/duolahypercho/codex-router.git',head='abc1234')
        rows=inv.codex_router_rows(FakeProbe(self.home,repos={str(root):git}))
        self.selected(rows)
        self.assertTrue(inv.plan(self.state,restart_policy='defer')['blocked'])
        self.assertTrue(inv.plan(self.state,restart_policy='services')['actions'])

    def test_scheduler_definitions(self):
        for system in ['Darwin','Linux','Windows']:
            with self.subTest(system=system),patch.object(k.platform,'system',return_value=system),patch.object(k.Path,'home',return_value=self.state),patch.object(k,'checked') as check,patch.object(k.subprocess,'run'):
                k.install_scheduler(self.state,{'time':'03:00'}); self.assertGreater(check.call_count,0)
        self.assertIn('Persistent=true',(self.state/'.config/systemd/user/ksu-nightly.timer').read_text())

if __name__=='__main__':unittest.main()
