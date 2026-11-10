"""运行 python app.py，在本机浏览器使用。Python 3.10+，无第三方依赖。"""
import argparse
import json
import os
import secrets
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import feishu
from schedule_parser import parse_html, expand_events

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / '.local' / 'config.json'
MAX_BODY = 8 * 1024 * 1024


class WidgetServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port):
        super().__init__(('127.0.0.1', port), Handler)
        self.origin = f'http://127.0.0.1:{self.server_port}'
        self.sessions = {}
        self.lock = threading.RLock()
        self.config = {}
        if CONFIG.exists(): self.config = json.loads(CONFIG.read_text(encoding='utf-8'))
        for key in ('app_id','app_secret'):
            if os.getenv('FEISHU_' + key.upper()): self.config[key] = os.environ['FEISHU_' + key.upper()]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass  # OAuth 回调包含一次性 code，不写入访问日志。

    def reply(self, status, obj=None, *, raw=None, mime='application/json; charset=utf-8', headers=None):
        body = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if getattr(self, 'new_cookie', None): self.send_header('Set-Cookie', f'session={self.new_cookie}; HttpOnly; SameSite=Lax; Path=/')
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def initialize(self):
        self.new_cookie = None
        if self.headers.get('Host') != urlsplit(self.server.origin).netloc:
            self.reply(403, {'error': '请使用启动窗口中的 127.0.0.1 地址。'})
            return False
        cookie = SimpleCookie()
        try: cookie.load(self.headers.get('Cookie', ''))
        except Exception: pass
        sid = cookie['session'].value if 'session' in cookie else ''
        with self.server.lock:
            for old in list(self.server.sessions):
                s = self.server.sessions[old]
                if time.time() - s['seen'] > 12*3600 and not s.get('busy'): del self.server.sessions[old]
            if sid not in self.server.sessions:
                sid = self.new_cookie = secrets.token_urlsafe(32)
                self.server.sessions[sid] = {'csrf': secrets.token_urlsafe(32), 'seen': time.time()}
            self.session = self.server.sessions[sid]
            self.session['seen'] = time.time()
        return True

    def token(self):
        if self.session.get('expires', 0) <= time.time(): raise ValueError('请先授权飞书账号，或重新授权已过期的账号。')
        return self.session['token']

    def do_GET(self):
        if not self.initialize(): return
        path = urlsplit(self.path).path
        try:
            if path == '/api/status':
                self.reply(200, dict(configured=bool(self.server.config.get('app_id') and self.server.config.get('app_secret')),
                    app_id=self.server.config.get('app_id',''), connected=self.session.get('expires',0)>time.time(),
                    csrf=self.session['csrf'], callback=self.server.origin+'/oauth/callback', job=self.session.get('job')))
            elif path == '/api/calendars': self.reply(200, {'calendars': feishu.calendars(self.token())})
            elif path == '/api/draft': self.reply(200, self.session.get('draft', {'courses': [], 'warnings': []}))
            elif path == '/oauth/callback': self.callback()
            elif path in {'/', '/app.js', '/style.css'}:
                filename = 'index.html' if path == '/' else path[1:]
                mime = {'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','style.css':'text/css; charset=utf-8'}[filename]
                self.reply(200, raw=(ROOT/'static'/filename).read_bytes(), mime=mime)
            else: self.reply(404, {'error': '页面不存在'})
        except (ValueError, feishu.FeishuError) as exc: self.reply(400, {'error': str(exc)})
        except Exception: self.reply(500, {'error': '操作失败，请检查本机配置文件或重启小组件。'})

    def callback(self):
        query = parse_qs(urlsplit(self.path).query)
        expected = self.session.pop('oauth_state', '')
        if not expected or time.time() > self.session.pop('oauth_until', 0) or not secrets.compare_digest(expected, query.get('state',[''])[0]):
            raise ValueError('授权校验失败或已过期，请返回首页重新授权。')
        if query.get('error'): raise ValueError('你取消了飞书授权，请返回首页重试。')
        code = query.get('code',[''])[0]
        if not code: raise ValueError('未收到授权码，请重新授权。')
        config = self.session.pop('oauth_config')
        self.session['token'], self.session['expires'] = feishu.exchange_code(config, code, self.server.origin+'/oauth/callback')
        self.reply(303, {}, headers={'Location':'/?authorized=1'})

    def do_POST(self):
        if not self.initialize(): return
        if self.headers.get('Origin') != self.server.origin or not secrets.compare_digest(self.headers.get('X-CSRF-Token',''), self.session['csrf']):
            self.reply(403, {'error':'请求校验失败，请刷新页面。'})
            return
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= MAX_BODY: raise ValueError('文件过大或请求为空，最大支持 8 MB。')
            if self.headers.get_content_type() != 'application/json': raise ValueError('请求必须为 JSON。')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict): raise ValueError('请求格式错误。')
            path = urlsplit(self.path).path
            with self.server.lock:
                if self.session.get('busy'): raise ValueError('正在导入，请等待完成后再操作。')
                result = self.post(path, body)
            self.reply(200, result)
        except (ValueError, KeyError, TypeError, feishu.FeishuError) as exc: self.reply(400, {'error': str(exc)})
        except Exception: self.reply(500, {'error':'本机操作失败，请检查配置文件写入权限并重试。'})

    def post(self, path, body):
        if path == '/api/config':
            app_id, secret = body.get('app_id'), body.get('app_secret')
            if not isinstance(app_id, str) or not isinstance(secret, str):
                raise ValueError('配置文件必须包含字符串类型的 app_id 和 app_secret。')
            app_id, secret = app_id.strip(), secret.strip()
            if (not app_id.startswith('cli_') or len(app_id)<=4 or len(app_id)>256
                    or not secret or len(secret)>1024 or 'REPLACE_ME' in app_id or secret == 'REPLACE_ME'):
                raise ValueError('请使用管理员填写完成的配置文件，或填写有效的 App ID 和 App Secret。')
            config = {'app_id': app_id, 'app_secret': secret}
            CONFIG.parent.mkdir(exist_ok=True)
            temp = CONFIG.with_suffix('.tmp')
            temp.write_text(json.dumps(config), encoding='utf-8')
            temp.chmod(0o600)
            temp.replace(CONFIG)
            self.server.config = config
            for session in self.server.sessions.values():
                for key in ('token','expires','oauth_state','oauth_config','batch'): session.pop(key, None)
            return {'ok': True}
        if path == '/api/auth':
            if not self.server.config.get('app_secret'): raise ValueError('请先填写飞书应用配置。')
            state = self.session['oauth_state'] = secrets.token_urlsafe(32)
            self.session['oauth_until'] = time.time()+600
            self.session['oauth_config'] = self.server.config.copy()
            return {'url': 'https://accounts.feishu.cn/open-apis/authen/v1/authorize?'+urlencode(dict(
                client_id=self.server.config['app_id'], response_type='code', redirect_uri=self.server.origin+'/oauth/callback',
                scope=feishu.SCOPES, state=state))}
        if path == '/api/logout':
            for key in ('token','expires','oauth_state','oauth_config','batch'): self.session.pop(key, None)
            return {'ok': True}
        if path == '/api/parse':
            html = body.get('html')
            if not isinstance(html, str): raise ValueError('请选择 HTML 文件。')
            self.session.pop('batch', None)
            self.session['draft'] = parse_html(html)
            return self.session['draft']
        if path == '/api/preview':
            selected = body.get('selected', [])
            courses = [c for c in self.session.get('draft',{}).get('courses',[]) if c['id'] in selected]
            if not courses: raise ValueError('请至少勾选一条课程安排。')
            if any(c['conflict'] for c in courses) and not body.get('acknowledged'): raise ValueError('请核对时间冲突并确认后继续。')
            events = expand_events(courses, body.get('semester_start',''))
            if not events or len(events)>2000: raise ValueError('导入数量必须在 1–2000 条以内。')
            batch = self.session['batch'] = {'id':secrets.token_urlsafe(24), 'events':events}
            return {'batch_id':batch['id'], 'events':events, 'count':len(events)}
        if path == '/api/import':
            token = self.token()
            batch = self.session.get('batch')
            if not batch or body.get('batch_id') != batch['id']: raise ValueError('预览已失效，请重新生成预览。')
            calendar_id = body.get('calendar_id')
            allowed = feishu.calendars(token)
            calendar = next((c for c in allowed if c['calendar_id'] == calendar_id), None)
            if not calendar: raise ValueError('请选择当前账号有写入权限的日历。')
            job = dict(running=True, total=len(batch['events']), processed=0, created=0, skipped=0, errors=[], phase='正在检查已导入日程', calendar=calendar.get('summary',''))
            self.session['job'], self.session['busy'] = job, True
            session = self.session
            def run():
                try: feishu.import_events(token, calendar_id, batch['events'], job)
                except Exception as exc:
                    job['errors'].append(str(exc) if isinstance(exc, feishu.FeishuError) else '导入中断，请重新授权后重试。')
                finally:
                    job['running'], session['busy'] = False, False
                    if not job['errors']: job['phase'] = '导入完成'
            threading.Thread(target=run, daemon=True).start()
            return {'ok': True}
        raise ValueError('未知操作。')


def main():
    parser = argparse.ArgumentParser(description='本机课表 → 飞书日历')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try: server = WidgetServer(args.port)
    except OSError:
        print('端口被占用。请关闭已运行的小组件，或使用 python app.py --port 8766（并更新飞书回调地址）。')
        return 1
    print('课表小组件已启动：' + server.origin + '\n保持此窗口打开，按 Ctrl+C 停止。')
    if not args.no_browser: webbrowser.open(server.origin)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0


if __name__ == '__main__': raise SystemExit(main())
