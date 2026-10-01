import json
import os
import tempfile
import unittest
from pathlib import Path

import signoff_sheet

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLE = os.path.join(HERE, 'example', 'items.json')


class BuildTest(unittest.TestCase):
    def test_builds_one_card_per_item_with_embedded_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'sheet.html')
            self.assertEqual(signoff_sheet.build(EXAMPLE, out, 'en'), 3)
            page = Path(out).read_text(encoding='utf-8')
        self.assertEqual(page.count('<section class="card"'), 3)
        self.assertEqual(page.count('src="data:image/png;base64,'), 3)
        self.assertIn('data-id="tile-01"', page)
        self.assertNotIn('{{', page)

    def test_chinese_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'sheet.html')
            signoff_sheet.build(EXAMPLE, out, 'zh')
            page = Path(out).read_text(encoding='utf-8')
        self.assertIn('lang="zh-CN"', page)
        self.assertIn('✓ 通过', page)

    def test_rejects_duplicate_ids_and_missing_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            items = Path(tmp, 'items.json')
            items.write_text(json.dumps({'items': [{'id': 'a', 'image': 'missing.png'}]}))
            with self.assertRaisesRegex(ValueError, 'image not found'):
                signoff_sheet.load_items(items)
            Path(tmp, 'x.png').write_bytes(b'')
            items.write_text(json.dumps({'items': [{'id': 'a', 'image': 'x.png'}, {'id': 'a', 'image': 'x.png'}]}))
            with self.assertRaisesRegex(ValueError, 'duplicate item id'):
                signoff_sheet.load_items(items)


class ParseTest(unittest.TestCase):
    def test_reads_english_and_chinese_results(self):
        text = '\n'.join([
            'Sign-off result 3/3',
            '#1 tile-01 PASS',
            '#2 mug-02 FAIL: no chip on the rim',
            '#3 sign-03 不通过：（没写原因）',
        ])
        self.assertEqual(signoff_sheet.parse(text), [
            {'n': 1, 'id': 'tile-01', 'decision': 'pass', 'reason': ''},
            {'n': 2, 'id': 'mug-02', 'decision': 'fail', 'reason': 'no chip on the rim'},
            {'n': 3, 'id': 'sign-03', 'decision': 'fail', 'reason': ''},
        ])

    def test_not_reviewed_is_none(self):
        self.assertEqual(signoff_sheet.parse('#1 tile-01 NOT REVIEWED')[0]['decision'], None)
        self.assertEqual(signoff_sheet.parse('#1 tile-01 还没看')[0]['decision'], None)

    def test_checks_result_against_items(self):
        items = signoff_sheet.load_items(EXAMPLE)['items']
        with self.assertRaisesRegex(ValueError, 'missing'):
            signoff_sheet.parse('#1 tile-01 PASS', items)

    def test_unreadable_decision_is_an_error(self):
        with self.assertRaisesRegex(ValueError, 'cannot read'):
            signoff_sheet.parse('#1 tile-01 maybe')


if __name__ == '__main__':
    unittest.main()
