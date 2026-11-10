import unittest
from schedule_parser import parse_html, parse_weeks, expand_events

HTML = '''<table id="jsTbl_01"><tr><td>第1节</td><td>08:00~08:45</td>
<td jc="1" xq="1" rowspan="2"><div class="arrage" data-bjdm="a"><span>本</span><div>1-5,8单周</div><div>算法(01)</div><div>王老师</div><div>A101</div></div></td></tr>
<tr><td>第2节</td><td>08:55~09:40</td><td jc="2" xq="1" style="display: none"><div class="arrage"><div>1-5,8单周</div><div>算法(01)</div><div>王老师</div><div>A101</div></div></td></tr></table>'''

class ScheduleTests(unittest.TestCase):
    def test_weeks(self):
        self.assertEqual(parse_weeks('1-5,8-13,16周'), [1,2,3,4,5,8,9,10,11,12,13,16])
        self.assertEqual(parse_weeks('9-15单周'), [9,11,13,15])
        self.assertEqual(parse_weeks('2-8周(双)'), [2,4,6,8])
        with self.assertRaises(ValueError): parse_weeks('待定')

    def test_merged_hidden_cells_and_times(self):
        result = parse_html(HTML)
        self.assertEqual(len(result['courses']), 1)
        c = result['courses'][0]
        self.assertEqual((c['start'],c['end'],c['sections']), ('08:00','09:40','1-2'))
        self.assertEqual(c['weeks'], [1,3,5])

    def test_dates_timezone_and_stable_identity(self):
        courses = parse_html(HTML)['courses']
        events = expand_events(courses, '2026-09-07')
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]['date'], '2026-09-07')
        self.assertEqual(events[0]['start_iso'], '2026-09-07T08:00:00+08:00')
        self.assertEqual(events, expand_events(courses, '2026-09-07'))
        with self.assertRaises(ValueError): expand_events(courses, '2026-09-08')

    def test_conflicting_locations_are_flagged(self):
        extra = '<div class="arrage"><div>1-5,8单周</div><div>算法(01)</div><div>王老师</div><div>A102</div></div>'
        result = parse_html(HTML.replace('</td></tr>', extra+'</td></tr>', 1))
        self.assertTrue(result['warnings'])
        self.assertTrue(all(c['conflict'] for c in result['courses']))

    def test_no_schedule_does_not_guess(self):
        with self.assertRaises(ValueError): parse_html('<script>alert(1)</script>')

    def test_missing_location_preserves_course(self):
        result = parse_html(HTML.replace('<div>A101</div>', ''))
        self.assertEqual(len(result['courses']), 1)
        self.assertEqual(result['courses'][0]['location'], '')
        self.assertTrue(result['warnings'])

if __name__ == '__main__': unittest.main()
