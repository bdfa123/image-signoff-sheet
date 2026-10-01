import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import signoff_sheet

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLE = os.path.join(HERE, 'example', 'items.json')
SCRIPT = os.path.join(HERE, 'signoff_sheet.py')


def store_key(items_path, out_path):
    signoff_sheet.build(items_path, out_path)
    return re.search(r'"store": "(signoff-[0-9a-f]+)"', Path(out_path).read_text(encoding='utf-8'))[1]


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

    def test_new_images_under_the_same_ids_do_not_reuse_old_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'sheet.html')
            first = store_key(EXAMPLE, out)
            self.assertEqual(store_key(EXAMPLE, out), first)
            copy = os.path.join(tmp, 'example')
            shutil.copytree(os.path.dirname(EXAMPLE), copy)
            shutil.copy(os.path.join(copy, 'images', 'blue-sign.png'), os.path.join(copy, 'images', 'red-mug.png'))
            self.assertNotEqual(store_key(os.path.join(copy, 'items.json'), out), first)


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

    def test_two_decisions_for_one_image_is_an_error(self):
        with self.assertRaisesRegex(ValueError, 'more than one decision'):
            signoff_sheet.parse('#1 tile-01 PASS\n#1 tile-01 FAIL: blurred')


class CommandLineTest(unittest.TestCase):
    RESULT = '﻿#1 tile-01 通过\n#2 mug-02 不通过: 杯口没有缺口\n#3 sign-03 还没看\n'

    def run_parse(self, *args):
        # A code page that cannot hold Chinese, as on many Windows machines.
        env = dict(os.environ, PYTHONIOENCODING='cp1252')
        return subprocess.run([sys.executable, SCRIPT, 'parse', *args, '--items', EXAMPLE],
                              capture_output=True, env=env, check=True).stdout

    def test_parse_writes_utf8_to_stdout_and_accepts_a_byte_order_mark(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp, 'result.txt')
            result.write_text(self.RESULT, encoding='utf-8')
            decisions = json.loads(self.run_parse(str(result)).decode('utf-8'))
        self.assertEqual([d['id'] for d in decisions], ['tile-01', 'mug-02', 'sign-03'])
        self.assertEqual(decisions[1]['reason'], '杯口没有缺口')

    def test_parse_out_writes_a_utf8_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, out = Path(tmp, 'result.txt'), Path(tmp, 'result.json')
            result.write_text(self.RESULT, encoding='utf-8')
            self.assertEqual(self.run_parse(str(result), '--out', str(out)), b'')
            self.assertEqual(json.loads(out.read_text(encoding='utf-8'))[1]['reason'], '杯口没有缺口')


if __name__ == '__main__':
    unittest.main()
