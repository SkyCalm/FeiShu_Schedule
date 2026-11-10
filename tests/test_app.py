import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from test_schedule import HTML


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.config_patch=patch('app.CONFIG',Path(self.temp.name)/'config.json')
        self.config_patch.start()
        self.server=app.WidgetServer(0)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.cookie=''
        _,status=self.request('GET','/api/status')
        self.csrf=status['csrf']

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.config_patch.stop();self.temp.cleanup()

    def request(self,method,path,body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        h={'Cookie':self.cookie}
        if method=='POST': h.update({'Content-Type':'application/json','Origin':self.server.origin,'X-CSRF-Token':getattr(self,'csrf','')})
        h.update(headers or {})
        conn.request(method,path,json.dumps(body) if body is not None else None,h)
        response=conn.getresponse()
        if response.getheader('Set-Cookie'): self.cookie=response.getheader('Set-Cookie').split(';')[0]
        raw=response.read();status=response.status;conn.close()
        return status,json.loads(raw)

    def test_csrf_and_host_rejected(self):
        self.assertEqual(self.request('POST','/api/parse',{'html':HTML},{'Origin':'https://evil.test'})[0],403)
        self.assertEqual(self.request('GET','/api/status',headers={'Host':'evil.test'})[0],403)

    def test_parse_preview_and_import_guard(self):
        status,draft=self.request('POST','/api/parse',{'html':HTML})
        self.assertEqual(status,200)
        status,preview=self.request('POST','/api/preview',{'selected':[draft['courses'][0]['id']],'semester_start':'2026-09-07'})
        self.assertEqual((status,preview['count']),(200,3))
        status,_=self.request('POST','/api/import',{'batch_id':preview['batch_id'],'calendar_id':'id'})
        self.assertEqual(status,400)

    def test_authorization_state_and_secret_not_exposed(self):
        status,_=self.request('POST','/api/config',{'app_id':'cli_test','app_secret':'private-secret'})
        self.assertEqual(status,200)
        _,status=self.request('GET','/api/status')
        self.assertNotIn('private-secret',json.dumps(status))
        _,auth=self.request('POST','/api/auth',{})
        self.assertIn('state=',auth['url'])
        code,_=self.request('GET','/oauth/callback?code=test&state=incorrect')
        self.assertEqual(code,400)

    def test_background_import_validates_calendar_and_reports_results(self):
        session=next(iter(self.server.sessions.values()))
        session.update(token='user-token',expires=time.time()+3600)
        _,draft=self.request('POST','/api/parse',{'html':HTML})
        _,preview=self.request('POST','/api/preview',{'selected':[draft['courses'][0]['id']],'semester_start':'2026-09-07'})
        def fake_import(token,calendar,events,job): job.update(created=len(events),processed=len(events))
        with patch('app.feishu.calendars',return_value=[{'calendar_id':'good','summary':'主日历'}]), patch('app.feishu.import_events',side_effect=fake_import):
            self.assertEqual(self.request('POST','/api/import',{'batch_id':preview['batch_id'],'calendar_id':'bad'})[0],400)
            self.assertEqual(self.request('POST','/api/import',{'batch_id':preview['batch_id'],'calendar_id':'good'})[0],200)
            for _ in range(20):
                if not session.get('busy'): break
                time.sleep(.01)
            _,status=self.request('GET','/api/status')
            self.assertEqual(status['job']['created'],3)
            self.assertFalse(status['job']['running'])

    def test_private_files_are_not_served(self):
        for path in ['/.local/config.json','/feishu-config.json','/app.py','/../app.py']:
            self.assertEqual(self.request('GET',path)[0],404)

    def test_imported_config_persists_without_exposing_secret(self):
        config={'app_id':'cli_enterprise','app_secret':'internal-test-secret'}
        code,result=self.request('POST','/api/config',config)
        self.assertEqual((code,result),(200,{'ok':True}))
        self.assertEqual(json.loads(app.CONFIG.read_text(encoding='utf-8')),config)
        _,status=self.request('GET','/api/status')
        self.assertTrue(status['configured'])
        self.assertEqual(status['app_id'],config['app_id'])
        self.assertNotIn(config['app_secret'],json.dumps(status))

    def test_bad_config_does_not_replace_existing_or_echo_secret(self):
        config={'app_id':'cli_enterprise','app_secret':'internal-test-secret'}
        self.request('POST','/api/config',config)
        for bad in [{'app_id':123,'app_secret':'secret'}, {'app_id':'cli_x','app_secret':123},
                    {'app_id':'cli_x','app_secret':''}, {'app_id':'cli_REPLACE_ME','app_secret':'REPLACE_ME'},
                    {'app_id':'bad','app_secret':'secret'}]:
            code,result=self.request('POST','/api/config',bad)
            self.assertEqual(code,400)
            self.assertNotIn('internal-test-secret',json.dumps(result))
            self.assertEqual(json.loads(app.CONFIG.read_text(encoding='utf-8')),config)

    def test_config_change_invalidates_existing_authorization(self):
        session=next(iter(self.server.sessions.values()))
        session.update(token='old-user-token',expires=time.time()+3600,batch={'id':'old'},oauth_state='old')
        self.request('POST','/api/config',{'app_id':'cli_enterprise','app_secret':'new-secret'})
        for key in ['token','expires','batch','oauth_state']: self.assertNotIn(key,session)

if __name__=='__main__': unittest.main()
