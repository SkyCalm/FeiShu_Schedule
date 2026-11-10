"""飞书用户授权与日历 API，仅使用标准库。"""
import json
import re
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API = 'https://open.feishu.cn/open-apis/'
SCOPES = 'calendar:calendar:readonly calendar:calendar.event:create'
MARKER = 'schedule-widget:v1:'


class FeishuError(Exception): pass


def request(method, path, token=None, body=None):
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    if token: headers['Authorization'] = 'Bearer ' + token
    req = Request(API + path, data=json.dumps(body).encode() if body is not None else None,
                  headers=headers, method=method)
    try:
        with urlopen(req, timeout=25) as response: result = json.load(response)
    except HTTPError as exc:
        # 不回传令牌、凭证或远端响应体；仅保留便于排障的错误码。
        try: code = json.loads(exc.read()).get('code', exc.code)
        except (ValueError, AttributeError): code = exc.code
        raise FeishuError(f'飞书接口请求失败（{code}）。请检查权限、应用可用范围及授权是否过期。') from None
    except (URLError, TimeoutError, OSError):
        raise FeishuError('无法连接飞书接口，请检查网络后重试。') from None
    if result.get('code', 0) != 0 or result.get('error'):
        raise FeishuError(f'飞书返回错误（{result.get("code", "OAuth")}）。请核对应用配置和用户权限后重新授权。')
    return result


def exchange_code(config, code, redirect_uri):
    result = request('POST', 'authen/v2/oauth/token', body={
        'grant_type': 'authorization_code', 'client_id': config['app_id'],
        'client_secret': config['app_secret'], 'code': code, 'redirect_uri': redirect_uri})
    if not result.get('access_token'): raise FeishuError('飞书未返回用户令牌，请重新授权。')
    return result['access_token'], time.time() + int(result.get('expires_in', 7200)) - 60


def paged(path, token, *, list_key='items', **query):
    cursor, visited = '', set()
    while True:
        params = dict(query, page_size=100)
        if cursor: params['page_token'] = cursor
        data = request('GET', path + '?' + urlencode(params), token).get('data', {})
        yield from data.get(list_key, [])
        if not data.get('has_more'): return
        cursor = data.get('page_token')
        if not cursor or cursor in visited: raise FeishuError('飞书分页响应异常，请重试。')
        visited.add(cursor)


def calendars(token):
    return [c for c in paged('calendar/v4/calendars', token, list_key='calendar_list')
            if c.get('role') in {'owner','writer'} and c.get('type') in {'primary','shared'}]


def import_events(token, calendar_id, events, progress):
    """先完整读取去重信息，再逐条写入；写入不自动重试，避免不确定响应造成重复。"""
    path = 'calendar/v4/calendars/' + quote(calendar_id, safe='') + '/events'
    existing = set()
    # 飞书 start_time/end_time 区间查询会截断且不能分页；anchor_time 支持完整分页。
    for item in paged(path, token, anchor_time=min(e['start'] for e in events)):
        if item.get('status') == 'cancelled': continue
        existing.update(re.findall(MARKER + r'([0-9a-f]{64})', item.get('description') or ''))
    progress['phase'] = '正在写入课程'
    for index, event in enumerate(events):
        if event['key'] in existing:
            progress['skipped'] += 1
        else:
            body = dict(summary=event['title'],
                        description=f'教师：{event["teacher"]}\n教学周：第 {event["week"]} 周\n由课表小组件导入\n{MARKER}{event["key"]}',
                        start_time={'timestamp': event['start'], 'timezone': 'Asia/Shanghai'},
                        end_time={'timestamp': event['end'], 'timezone': 'Asia/Shanghai'},
                        need_notification=False, vchat={'vc_type': 'no_meeting'})
            if event['location']: body['location'] = {'name': event['location']}
            try:
                key = str(uuid.uuid5(uuid.NAMESPACE_URL, calendar_id + ':' + event['key']))
                request('POST', path + '?' + urlencode({'idempotency_key': key}), token, body)
                progress['created'] += 1
                existing.add(event['key'])
            except FeishuError as exc:
                progress['errors'].append(f'{event["date"]} {event["title"]}：{exc}')
                progress['phase'] = '导入已停止；请重新授权或检查网络，再次导入可跳过已写入的日程'
                break
            time.sleep(0.12)
        progress['processed'] = index + 1
