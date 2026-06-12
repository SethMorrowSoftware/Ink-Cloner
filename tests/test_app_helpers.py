import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app


class HelperTests(unittest.TestCase):
    def test_parse_iso15693_uid_valid_response(self):
        response = bytes([1, 0, 0, 0, 0, 8, 0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32])
        self.assertEqual(app.parse_iso15693_uid(response), bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]))

    def test_parse_iso15693_uid_rejects_short_or_empty_responses(self):
        self.assertIsNone(app.parse_iso15693_uid(None))
        self.assertIsNone(app.parse_iso15693_uid(b''))
        self.assertIsNone(app.parse_iso15693_uid(bytes([1, 0, 0, 0, 0, 8, 0xE0])))

    def test_format_uid(self):
        self.assertEqual(app.format_uid(bytes([0xE0, 0x07, 0x81])), 'E0-07-81')

    def test_normalize_uid_accepts_common_pn5180_shapes(self):
        self.assertEqual(app.normalize_uid('E0:07:81:6A:E3:2E:96:32'), bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]))
        self.assertEqual(app.normalize_uid(0xE007816AE32E9632), bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]))
        self.assertEqual(app.normalize_uid([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]), bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]))

    def test_validate_iso15693_response_rejects_tag_error(self):
        with self.assertRaises(RuntimeError):
            app.validate_iso15693_response(bytes([0x01, 0x0F]))

    def test_record_operation_caps_history(self):
        with patch.object(app, 'operation_history', []):
            for index in range(105):
                app.record_operation('scan_tag', 'success', index=index)
            self.assertEqual(len(app.operation_history), 100)
            self.assertEqual(app.operation_history[0]['details']['index'], 5)
            self.assertEqual(app.operation_history[-1]['details']['index'], 104)

    @unittest.skipUnless(app.HAS_WEB_DEPS, 'Flask dependencies are not installed')
    def test_healthz_route_reports_status(self):
        client = app.app.test_client()
        response = client.get('/healthz')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['ok'])
        self.assertIn('hardware_status', response.json)
        self.assertIn('backend', response.json)


if __name__ == '__main__':
    unittest.main()
