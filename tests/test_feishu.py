import unittest
from unittest.mock import patch
import feishu
from schedule_parser import parse_html, expand_events
from test_schedule import HTML


class FeishuTests(unittest.TestCase):
    def test_pagination_includes_second_page(self):
        replies = [{'data': {'items':[{'id':1}], 'has_more':True,'page_token':'next'}}, {'data':{'items':[{'id':2}], 'has_more':False}}]
        with patch('feishu.request', side_effect=replies) as call:
            self.assertEqual(list(feishu.paged('x','token')), [{'id':1},{'id':2}])
            self.assertIn('page_token=next',call.call_args[0][1])

    def test_import_skips_existing_and_writes_user_times(self):
        events=expand_events(parse_html(HTML)['courses'],'2026-09-07')
        progress=dict(created=0, skipped=0, processed=0, errors=[])
        with patch('feishu.paged',return_value=iter([{'description':feishu.MARKER+events[0]['key']}])):
            with patch('feishu.request',return_value={'code':0}) as call, patch('feishu.time.sleep'):
                feishu.import_events('user-token','calendar',events,progress)
                self.assertEqual(progress['created'],2)
                self.assertEqual(progress['skipped'],1)
                body=call.call_args[0][3]
                self.assertEqual(body['start_time']['timezone'],'Asia/Shanghai')
                self.assertEqual(call.call_args[0][2],'user-token')
                self.assertIn('idempotency_key=',call.call_args[0][1])

    def test_preflight_failure_never_writes(self):
        events=expand_events(parse_html(HTML)['courses'],'2026-09-07')
        with patch('feishu.paged',side_effect=feishu.FeishuError('denied')), patch('feishu.request') as write:
            with self.assertRaises(feishu.FeishuError): feishu.import_events('token','id',events,{})
            write.assert_not_called()

    def test_write_failure_stops_and_keeps_partial_counts(self):
        events=expand_events(parse_html(HTML)['courses'],'2026-09-07')
        progress=dict(created=0, skipped=0, processed=0, errors=[])
        with patch('feishu.paged',return_value=iter([])), patch('feishu.time.sleep'):
            with patch('feishu.request',side_effect=[{},feishu.FeishuError('network')]) as call:
                feishu.import_events('token','id',events,progress)
                self.assertEqual(call.call_count,2)
                self.assertEqual(progress['created'],1)
                self.assertTrue(progress['errors'])

    def test_calendar_role_filter(self):
        items=[{'role':'owner','type':'primary'}, {'role':'writer','type':'shared'}, {'role':'reader','type':'primary'}]
        with patch('feishu.paged',return_value=iter(items)):
            self.assertEqual(len(feishu.calendars('token')),2)

    def test_token_exchange_contract(self):
        with patch('feishu.request',return_value={'code':0,'access_token':'uat','expires_in':7200}) as call:
            token,_=feishu.exchange_code({'app_id':'cli_test','app_secret':'secret'},'code','http://127.0.0.1/cb')
            self.assertEqual(token,'uat')
            self.assertEqual(call.call_args.kwargs['body']['grant_type'],'authorization_code')

    def test_real_calendar_list_response_field(self):
        calendar={'calendar_id':'primary','role':'owner','type':'primary'}
        with patch('feishu.request',return_value={'data':{'calendar_list':[calendar],'has_more':False}}):
            self.assertEqual(feishu.calendars('token'),[calendar])

    def test_event_preflight_uses_pageable_anchor_not_truncated_time_range(self):
        events=expand_events(parse_html(HTML)['courses'],'2026-09-07')
        progress=dict(created=0,skipped=0,processed=0,errors=[])
        replies=[{'data':{'items':[], 'has_more':True,'page_token':'next'}},
                 {'data':{'items':[{'description':feishu.MARKER+e['key']} for e in events], 'has_more':False}}]
        with patch('feishu.request',side_effect=replies) as call:
            feishu.import_events('token','id',events,progress)
            self.assertEqual(progress['skipped'],3)
            self.assertIn('anchor_time=',call.call_args_list[0][0][1])
            self.assertNotIn('start_time=',call.call_args_list[0][0][1])
            self.assertIn('page_token=next',call.call_args_list[1][0][1])

if __name__=='__main__': unittest.main()
