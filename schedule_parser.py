"""厦门大学 / Wisedu 已保存课表解析；不执行 HTML 中任何脚本。"""
import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

SHANGHAI = timezone(timedelta(hours=8))


class Node:
    def __init__(self, tag='', attrs=None, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs or []), parent
        self.children = []

    def text(self):
        return ''.join(c if isinstance(c, str) else c.text() for c in self.children).strip()

    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, Node): yield from c.walk()

    def hidden(self):
        n = self
        while n:
            if 'hidden' in n.attrs or re.search(r'display\s*:\s*none', n.attrs.get('style', ''), re.I): return True
            n = n.parent
        return False


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, attrs, self.current))

    def handle_endtag(self, tag):
        n = self.current
        while n.parent:
            if n.tag == tag:
                self.current = n.parent
                return
            n = n.parent

    def handle_data(self, data): self.current.children.append(data)


def parse_weeks(text):
    text = text.strip().replace('，', ',').replace('、', ',').replace('－','-').replace('—','-').replace('~','-').replace('～','-')
    odd, even = '单' in text, '双' in text
    if odd and even: raise ValueError('无法识别混合单双周：' + text)
    cleaned = re.sub(r'[第周单双()（）\s]', '', text)
    if not re.fullmatch(r'\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*', cleaned):
        raise ValueError('无法识别周次：' + text)
    weeks = set()
    for item in cleaned.split(','):
        bounds = [int(v) for v in item.split('-')]
        a, b = bounds[0], bounds[-1]
        if not 1 <= a <= b <= 60: raise ValueError('周次必须在 1–60 内：' + text)
        weeks.update(range(a, b+1))
    return sorted(w for w in weeks if (not odd or w % 2 == 1) and (not even or w % 2 == 0))


def parse_html(html):
    doc = Document()
    doc.feed(html)
    tables = [n for n in doc.root.walk() if n.tag == 'table' and any('jc' in c.attrs and 'xq' in c.attrs for c in n.walk()) and not n.hidden()]
    if not tables: raise ValueError('未找到可识别的课表。请在教务系统切换到学期课表，等待课程显示后另存为 HTML。当前支持带 jc/xq 属性的 Wisedu（厦大）课表。')
    table = next((t for t in tables if t.attrs.get('id') == 'jsTbl_01'), tables[0])
    times, warnings, courses = {}, [], []
    for row in (n for n in table.walk() if n.tag == 'tr'):
        cells = [c for c in row.children if isinstance(c, Node) and c.tag in {'td','th'}]
        section = re.search(r'第\s*(\d+)\s*节', cells[0].text()) if cells else None
        timing = re.search(r'(\d{1,2}:\d{2})\s*[~～\-—]\s*(\d{1,2}:\d{2})', ' '.join(c.text() for c in cells[:2]))
        if section and timing: times[int(section[1])] = [v.zfill(5) for v in timing.groups()]
    seen = set()
    for cell in (n for n in table.walk() if n.tag == 'td' and 'jc' in n.attrs and not n.hidden()):
        for block in (n for n in cell.walk() if 'arrage' in n.attrs.get('class','').split() and not n.hidden()):
            fields = [c.text() for c in block.children if isinstance(c, Node) and c.tag == 'div']
            if len(fields) < 3:
                warnings.append('一条课程缺少名称、周次或地点，已跳过，请核对原文件。')
                continue
            week_text, title, teacher = fields[:3]
            location = fields[3] if len(fields) > 3 else ''
            if not location: warnings.append(f'{title}：原课表未提供教室，将保留为空。')
            try:
                weeks = parse_weeks(week_text)
                first, weekday = int(cell.attrs['jc']), int(cell.attrs['xq'])
                last = first + int(cell.attrs.get('rowspan', 1)) - 1
                if not 1 <= weekday <= 7 or not 1 <= first <= last <= 30: raise ValueError('星期或节次超出范围')
                start, end = times[first][0], times[last][1]
                if end <= start: raise ValueError('结束时间早于开始时间')
            except (ValueError, KeyError) as exc:
                warnings.append(f'{title}：无法确定时间（{exc}），已跳过。')
                continue
            key = (title, teacher, location, weekday, first, last, tuple(weeks))
            if key in seen: continue
            seen.add(key)
            identifier = hashlib.sha256(repr(key).encode()).hexdigest()[:16]
            courses.append(dict(id=identifier, title=title, teacher=teacher, location=location,
                                weekday=weekday, sections=f'{first}-{last}', start=start, end=end,
                                week_text=week_text, weeks=weeks, conflict=False))
    if not courses: raise ValueError('课表中没有可导入的课程。' + '；'.join(warnings[:3]))
    for i, a in enumerate(courses):
        for b in courses[i+1:]:
            if a['weekday'] == b['weekday'] and a['start'] < b['end'] and b['start'] < a['end'] and set(a['weeks']) & set(b['weeks']):
                a['conflict'] = b['conflict'] = True
                warnings.append(f'时间冲突：{a["title"]}（{a["location"]}）与 {b["title"]}（{b["location"]}）。请仅勾选正确安排；HTML 不包含调课的具体生效日期。')
    return dict(courses=courses, warnings=list(dict.fromkeys(warnings)), times=times)


def expand_events(courses, semester_start):
    monday = date.fromisoformat(semester_start)
    if monday.weekday() != 0: raise ValueError('第 1 周起始日期必须是周一。')
    events = []
    for c in courses:
        for week in c['weeks']:
            day = monday + timedelta(days=(week-1)*7+c['weekday']-1)
            start = datetime.fromisoformat(f'{day}T{c["start"]}:00').replace(tzinfo=SHANGHAI)
            end = datetime.fromisoformat(f'{day}T{c["end"]}:00').replace(tzinfo=SHANGHAI)
            identity = f'{c["title"]}|{c["teacher"]}|{c["location"]}|{start.isoformat()}|{end.isoformat()}'
            key = hashlib.sha256(identity.encode()).hexdigest()
            events.append(dict(key=key, title=c['title'], teacher=c['teacher'], location=c['location'],
                               date=str(day), week=week, start_iso=start.isoformat(), end_iso=end.isoformat(),
                               start=str(int(start.timestamp())), end=str(int(end.timestamp()))))
    return sorted(events, key=lambda e: (e['start'], e['title'], e['location']))
