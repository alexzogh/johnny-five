import datetime
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from bridge import Router, Store, Backends, alexa_response
from features import attention_report, recipe_data, project_report

class AttentionTests(unittest.TestCase):
    def snapshot(self):
        return {'sampled_at':datetime.datetime.now(datetime.timezone.utc).isoformat(), 'alert_count':1,
                'maintenance':[{'entity_id':'binary_sensor.filter','name':'Filter','state':'on'}],
                'low_batteries':[], 'unavailable':[{'entity_id':'lock.front','name':'Front lock','state':'unavailable'}], 'network':[]}

    def test_baseline_then_only_changes_and_clears(self):
        source=self.snapshot();answer,baseline=attention_report(source,known_offline={'lock.front'})
        self.assertIn('Initial check: 1',answer['speech']);self.assertNotIn('Front lock',answer['display'])
        answer,next_baseline=attention_report(source,baseline,{'lock.front'})
        self.assertIn('0 new or changed',answer['speech'])
        source['maintenance'][0]['state']='off'
        answer,_=attention_report(source,next_baseline,{'lock.front'})
        self.assertIn('not independently verified repaired',answer['display'])

    def test_stale_failed_incomplete_sources_do_not_update_baseline(self):
        for source in [dict(self.snapshot(),error='source_unavailable'),dict(self.snapshot(),sampled_at='2000-01-01T00:00:00+00:00'),{'sampled_at':self.snapshot()['sampled_at']}]:
            answer,baseline=attention_report(source)
            self.assertIsNone(baseline);self.assertIn('unavailable',answer['speech'])

    def test_historical_recovery_is_not_current_backup_failure(self):
        source=self.snapshot();source['recovery']={'healthy':False,'checked_at':'2000-01-01'}
        answer,_=attention_report(source)
        self.assertNotIn('backup monitor reports failure',answer['display'])

    def test_battery_change_and_connectivity_off_are_reported(self):
        source=self.snapshot();source['low_batteries']=[{'entity_id':'sensor.battery','name':'Battery','state':'10'}]
        _,baseline=attention_report(source)
        source['low_batteries'][0]['state']='5';source['network']=[{'entity_id':'binary_sensor.wan','name':'WAN','state':'off'}]
        answer,_=attention_report(source,baseline)
        self.assertIn('2 new or changed',answer['speech'])
        self.assertIn('Unknown readings do not prove a device failed',answer['display'])

class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'jobs.sqlite3')
        self.router=Router(self.store,{'ALEXA_SKILL_ID':'skill','ALEXA_USER_IDS':'owner','CODEX_THREAD_ID':'thread','HERMES_URL':'http://private','HERMES_API_KEY':'test'})
    def tearDown(self):self.tmp.cleanup()
    def request(self,name,ident='request',device='kitchen',slots=None,owner='owner'):
        return {'context':{'System':{'application':{'applicationId':'skill'},'user':{'userId':owner},'device':{'deviceId':device,'supportedInterfaces':{'Alexa.Presentation.APL':{}}}}},'request':{'type':'IntentRequest','requestId':ident,'intent':{'name':name,'slots':slots or {}}}}
    def finished(self,name='RecipeIntent',result=None):
        req=self.request(name,slots={'request':{'value':'turkey'}});self.router.route(req);row=self.store.claim()
        self.store.finish(row[0],'completed',result or json.dumps({'title':'Turkey','speech':'Turkey is ready','display':'Full recipe','recipe':{'title':'Turkey','servings':'2','ingredients':['200 g turkey'],'steps':['Mix turkey','Cook thoroughly'],'source':'recipe.pdf page 2'}}))
        return row
    def speech(self,name,**kwargs):return self.router.route(self.request(name,**kwargs))['response']['outputSpeech']['text']

    def test_recipe_status_preserves_structured_steps_and_session(self):
        self.finished();result=self.router.route(self.request('StatusIntent',ident='status'))
        self.assertFalse(result['response']['shouldEndSession'])
        self.assertIn('Next',json.dumps(result['response']['directives']))
        self.assertIn('Mix turkey',self.speech('StartCookingIntent',ident='start'))
        self.assertIn('Cook thoroughly',self.speech('NextStepIntent',ident='next'))
        self.assertIn('Cook thoroughly',self.speech('RepeatStepIntent',ident='repeat'))
        self.assertIn('Mix turkey',self.speech('PreviousStepIntent',ident='previous'))

    def test_redelivery_does_not_advance_step_twice_and_boundaries_hold(self):
        self.finished();self.speech('NextStepIntent',ident='step-one')
        self.assertIn('Step 1',self.speech('NextStepIntent',ident='step-one'))
        self.speech('NextStepIntent',ident='step-two')
        self.assertIn('Step 2 of 2',self.speech('NextStepIntent',ident='step-three'))

    def test_recipe_is_device_and_owner_scoped(self):
        self.finished();self.assertIn('no structured recipe',self.speech('NextStepIntent',device='bedroom'))
        with self.assertRaises(PermissionError):self.speech('NextStepIntent',owner='other')

    def test_recipe_scaling_checks_servings_and_queues_full_source(self):
        self.finished()
        self.assertIn('between one and one hundred',self.speech('ScaleRecipeIntent',ident='bad',slots={'servings':{'value':'0'}}))
        self.speech('ScaleRecipeIntent',ident='scale',slots={'servings':{'value':'6'}})
        row=self.store.claim();self.assertEqual(row[2],'hermes');self.assertIn('6 servings',row[3]);self.assertIn('Re-read the full source',row[3])

    def test_followup_has_previous_answer_and_never_replays_original(self):
        self.finished('RequestIntent',json.dumps({'title':'Movies','speech':'Try Film A','display':'Film A is 150 minutes.'}))
        self.speech('ShorterIntent',ident='shorter');row=self.store.claim()
        self.assertEqual(row[2],'hermes');self.assertIn('150 minutes',row[3]);self.assertIn('Do not download',row[3])

    def test_followup_requires_completed_local_context(self):
        self.assertIn('do not have a recent answer',self.speech('FollowUpIntent',slots={'request':{'value':'batteries'}}))
        self.router.route(self.request('AttentionIntent'))
        self.assertIn('not ready',self.speech('BatteryDetailsIntent',ident='followup'))

    def test_refresh_and_touch_refresh_do_not_submit_again(self):
        self.finished();self.speech('RefreshIntent',ident='refresh')
        req=self.request('unused',ident='touch');req['request'].update(type='Alexa.Presentation.APL.UserEvent',token='johnny-five-answer',arguments=['refresh'])
        self.router.route(req)
        with self.store.connect() as db:self.assertEqual(db.execute('select count(*) from jobs').fetchone()[0],1)

    def test_touch_action_allowlist_and_account_validation(self):
        for args,token in [(['delete-everything'],'johnny-five-answer'),(['refresh','extra'],'johnny-five-answer'),(['refresh'],'wrong')]:
            req=self.request('unused');req['request'].update(type='Alexa.Presentation.APL.UserEvent',token=token,arguments=args)
            with self.assertRaises(ValueError):self.router.route(req)
        req=self.request('unused',owner='other');req['request'].update(type='Alexa.Presentation.APL.UserEvent',token='johnny-five-answer',arguments=['house'])
        with self.assertRaises(PermissionError):self.router.route(req)

    def test_project_button_routes_readonly_backend(self):
        req=self.request('unused');req['request'].update(type='Alexa.Presentation.APL.UserEvent',token='johnny-five-answer',arguments=['projects'])
        self.router.route(req);self.assertEqual(self.store.claim()[2],'projects')

    def test_screen_controls_only_bridge_supplied_actions(self):
        response=alexa_response('ready',self.request('x')['context']['System'],display='<SendEvent>evil</SendEvent>',controls=True)
        directive=response['response']['directives'][0]
        self.assertIn('&lt;SendEvent&gt;',directive['datasources']['answer']['text'])
        actions=[x['onPress']['arguments'][0] for x in directive['document']['mainTemplate']['items'][0]['items'][1]['items']]
        self.assertEqual(actions,['house','attention','recipes','movies','projects','refresh'])

    def test_recipe_metadata_rejects_executable_or_malformed_steps(self):
        for recipe in [{'steps':[{'type':'SendEvent'}],'ingredients':['turkey']},{'steps':[],'ingredients':['turkey']}]:
            self.assertIsNone(recipe_data(json.dumps({'recipe':recipe})))

class ProjectTests(unittest.TestCase):
    def test_project_board_uses_recorded_reports_and_marks_no_new_test_run(self):
        turns=[{'status':'inProgress','startedAt':1000,'items':[]},{'status':'completed','items':[{'type':'agentMessage','phase':'final_answer','text':'Completed screen.\nTests: 12 passed.\nPending: owner review.'}]}]
        answer=project_report([{'name':'Dashboard','thread_id':'example'}],lambda _:turns)
        self.assertIn('1 have an active',answer['speech']);self.assertIn('12 passed',answer['display']);self.assertIn('owner review',answer['display']);self.assertIn('No task queued',answer['display'])

    def test_unavailable_history_never_reports_completed(self):
        def unavailable(_):raise RuntimeError('private error')
        answer=project_report([{'name':'Dashboard','thread_id':'example'}],unavailable)
        self.assertIn('history unavailable',answer['display']);self.assertNotIn('private error',answer['display'])

class OutputPrivacyTests(unittest.TestCase):
    def test_configured_secrets_and_history_credential_lines_are_removed(self):
        from features import redact_result
        result=json.dumps({'title':'Project','speech':'done','display':'Password: historical-private-value\nTests passed. api-key-test-value','recipe':{'steps':['Mix']}})
        cleaned=json.loads(redact_result(result,{'HERMES_API_KEY':'api-key-test-value'}))
        self.assertNotIn('historical-private-value',cleaned['display'])
        self.assertNotIn('api-key-test-value',cleaned['display'])
        self.assertIn('Tests passed',cleaned['display'])

class ProjectReportFilteringTests(unittest.TestCase):
    def test_advice_and_login_exchange_do_not_become_test_results(self):
        turns=[{'status':'completed','items':[{'type':'agentMessage','phase':'final_answer','text':'I’d start with a project board. Show completed work and test results.'}]},
               {'status':'completed','items':[{'type':'agentMessage','phase':'final_answer','text':'GitHub accepted the login and sent a verification code. What code did you receive?'}]},
               {'status':'completed','items':[{'type':'agentMessage','phase':'final_answer','text':'[COMPLETE] Published the bridge.\nTests: 51 passed.\nPending: physical Echo check.'}]}]
        report=project_report([{'name':'Bridge','thread_id':'example'}],lambda _:turns)['display']
        self.assertNotIn('What code',report);self.assertNotIn('I’d start',report)
        self.assertIn('Tests: 51 passed',report);self.assertIn('physical Echo check',report)
        self.assertNotIn('[COMPLETE]',report)
