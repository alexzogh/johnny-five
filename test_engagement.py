import json
import tempfile
import time
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
from bridge import Store, Router, Backends
from engagement import routine_settings, source_section, progress_answer, activity_data, activity_html, activity_server

class EngagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(Path(self.tmp.name)/'jobs.sqlite3')
        self.config={'ALEXA_SKILL_ID':'skill','ALEXA_USER_IDS':'owner,other','CODEX_THREAD_ID':'thread','HERMES_URL':'http://private','HERMES_API_KEY':'test-secret'}
        self.router=Router(self.store,self.config)
    def tearDown(self): self.tmp.cleanup()
    def request(self,name,ident='request',device='kitchen',slots=None,owner='owner'):
        return {'context':{'System':{'application':{'applicationId':'skill'},'user':{'userId':owner},'device':{'deviceId':device}}},'request':{'type':'IntentRequest','requestId':ident,'intent':{'name':name,'slots':slots or {}}}}
    def call(self,name,**kwargs): return self.router.route(self.request(name,**kwargs))['response']['outputSpeech']['text']
    def recipe(self):
        self.call('RecipeIntent',slots={'request':{'value':'turkey'}}); job=self.store.claim()
        self.store.finish(job[0],'completed',json.dumps({'title':'Turkey','speech':'Recipe ready','display':'Source-backed recipe','recipe':{'title':'Turkey','servings':'2','ingredients':['200 g turkey'],'steps':['Mix','Cook'],'source':'recipe.pdf page 2'}}))
        return job
    def test_progress_is_readonly_and_shows_queue_then_completed(self):
        self.call('AttentionIntent');self.assertIn('waiting',self.call('TaskProgressIntent',ident='progress'))
        job=self.store.claim();self.assertIn('running',self.call('TaskProgressIntent',ident='running'))
        self.store.finish(job[0],'completed','Done');self.assertIn('finished',self.call('TaskProgressIntent',ident='done'))
        self.assertEqual(len(self.store.recent(['owner'])),1)
        row=self.store.progress('owner'); self.assertIsNotNone(row[6]);self.assertIsNotNone(row[7])
    def test_progress_prefers_current_device_with_account_fallback(self):
        self.call('AttentionIntent');first=self.store.claim();self.store.finish(first[0],'completed','Kitchen result')
        self.call('ProjectBoardIntent',ident='other',device='bedroom');second=self.store.claim();self.store.finish(second[0],'completed','Bedroom result')
        self.assertIn('Kitchen result',self.call('TaskProgressIntent',ident='kitchen-status'))
        self.assertIn('Bedroom result',self.call('TaskProgressIntent',ident='new-device',device='office'))
    def test_progress_unknown_outcome_does_not_claim_success(self):
        answer=progress_answer(('job','hermes','prompt','uncertain','Unknown',100,None,None),now=120)
        self.assertIn('not confirmed',answer['speech']);self.assertNotIn('finished.',answer['speech'])
    def test_routines_only_fixed_sections_and_unknown_name_does_not_queue(self):
        self.assertIn('Choose a routine',self.call('RoutineIntent',slots={'routine':{'value':'erase files'}}))
        self.assertEqual(len(self.store.recent(['owner'])),0)
        self.call('MorningRoutineIntent',ident='morning');job=self.store.claim();self.assertEqual(job[2:4],('routine','morning'))
        file=Path(self.tmp.name)/'routines.json';file.write_text(json.dumps({'cleanup':{'title':'Bad','sections':['shell']}}));file.chmod(0o600)
        with self.assertRaises(ValueError):routine_settings({'ROUTINES_FILE':str(file)})
    def test_routine_partial_failure_and_disconnected_sources_are_honest(self):
        backend=Backends(self.config,self.store);original=backend.run
        def run(target,*args):
            if target=='attention': raise RuntimeError('source failed')
            if target=='projects':return 'completed',json.dumps({'title':'Projects','speech':'One active project','display':'Recorded tests passed'})
            return original(target,*args)
        with patch.object(backend,'run',side_effect=run):status,result=backend.run('routine','morning','job','owner')
        data=json.loads(result);self.assertIn('House: source unavailable',data['display']);self.assertIn('Calendar: not connected',data['display']);self.assertIn('Some connected sources',data['speech'])
    def test_external_snapshot_freshness_permissions_and_errors(self):
        file=Path(self.tmp.name)/'sources.json';file.write_text(json.dumps({'calendar':{'sampled_at':'2026-01-01T00:00:00Z','display':'Meeting'}}));file.chmod(0o600)
        config={'BRIEFING_SOURCES_FILE':str(file)}
        self.assertIn('stale',source_section('calendar',config,now=1800000000))
        import datetime
        now=datetime.datetime(2026,1,1,tzinfo=datetime.timezone.utc).timestamp()
        self.assertIn('Meeting',source_section('calendar',config,now=now+100))
        file.chmod(0o644);self.assertIn('unavailable',source_section('calendar',config,now=now))
    def test_troubleshooting_records_query_as_data_not_command(self):
        self.call('TroubleshootIntent',slots={'device':{'value':'front lock'}})
        job=self.store.claim();self.assertEqual(job[2],'troubleshoot')
        backend=Backends(self.config,self.store);original=backend.run
        def run(target,*args):
            if target=='hermes':return 'completed',args[0]
            return original(target,*args)
        with patch.object(backend,'run',side_effect=run):_,prompt=backend.run('troubleshoot',job[3],job[0],'owner')
        self.assertIn('READ-ONLY',prompt);self.assertIn('NEVER call service/control',prompt);self.assertIn('"front lock"',prompt)
    def test_handoff_requires_explicit_save_and_completed_request(self):
        self.recipe();self.assertIn('no unexpired',self.call('ResumeHandoffIntent',device='bedroom',ident='resume'))
        self.call('AttentionIntent',ident='pending');self.assertIn('Finish a request',self.call('SaveHandoffIntent',ident='save'))
    def test_handoff_restores_recipe_step_without_advancing_or_queueing(self):
        self.recipe();self.call('StartCookingIntent',ident='start');self.call('NextStepIntent',ident='next')
        self.assertIn('fifteen minutes',self.call('SaveHandoffIntent',ident='save'))
        self.assertIn('Step 2 of 2: Cook',self.call('ResumeHandoffIntent',device='bedroom',ident='resume'))
        self.assertIn('Step 1 of 2: Mix',self.call('PreviousStepIntent',device='bedroom',ident='previous'))
        self.assertEqual(len(self.store.recent(['owner'])),1)
    def test_handoff_account_isolation_and_expiry(self):
        self.recipe();self.call('SaveHandoffIntent',ident='save')
        self.assertIn('no unexpired',self.call('ResumeHandoffIntent',owner='other',ident='other'))
        with patch('bridge.time.time',return_value=time.time()+901):
            self.assertIn('no unexpired',self.call('ResumeHandoffIntent',device='bedroom',ident='expired'))
    def test_activity_filters_owner_escapes_markup_and_redacts_secrets(self):
        one=self.store.submit('one','owner','hermes','Password: private-value');self.store.finish(one,'completed','<script>alert(1)</script> test-secret')
        two=self.store.submit('two','other','hermes','Other owner');self.store.finish(two,'completed','Other answer')
        data=activity_data(self.store,['owner'],self.config);self.assertEqual(len(data['jobs']),1)
        encoded=json.dumps(data);self.assertNotIn('test-secret',encoded);self.assertNotIn('private-value',encoded);self.assertNotIn('Other answer',encoded)
        page=activity_html(data);self.assertNotIn('<script>',page);self.assertIn('&lt;script&gt;',page)
    def test_activity_listener_denies_missing_wrong_identity_and_all_writes(self):
        config={**self.config,'ACTIVITY_USERS':json.dumps({'owner@example.invalid':['owner']}),'ACTIVITY_PORT':'0'}
        server=activity_server(self.store,config);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:'+str(server.server_port)
        try:
            for headers in ({},{'Tailscale-User-Login':'wrong@example.invalid'}):
                with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(urllib.request.Request(url,headers=headers))
                self.assertEqual(error.exception.code,403)
            req=urllib.request.Request(url+'/api/activity',headers={'Tailscale-User-Login':'owner@example.invalid'})
            with urllib.request.urlopen(req) as response:
                self.assertEqual(response.headers['Cache-Control'],'no-store');self.assertTrue(json.load(response)['read_only'])
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(urllib.request.Request(url,data=b'{}',headers={'Tailscale-User-Login':'owner@example.invalid'}))
            self.assertEqual(error.exception.code,405)
        finally:server.shutdown();server.server_close()
    def test_activity_mapping_requires_enrolled_account(self):
        for mapping in ({},{'owner@example.invalid':['unapproved']},{'owner@example.invalid':[]}):
            with self.assertRaises(ValueError):activity_server(self.store,{**self.config,'ACTIVITY_USERS':json.dumps(mapping),'ACTIVITY_PORT':'0'})
    def test_lifecycle_addition_preserves_legacy_jobs_schema(self):
        job=self.store.submit('legacy','owner','hermes','Question')
        with self.store.connect() as db:self.assertEqual(len(db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone()),7)
        self.assertEqual(self.store.progress('other',job),None)
