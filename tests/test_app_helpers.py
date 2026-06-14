import os
import sys
import unittest
from types import SimpleNamespace
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

    def test_parse_raw_iso15693_inventory_response_reverses_wire_uid(self):
        response = bytes([0x00, 0x00, 0x32, 0x96, 0x2E, 0xE3, 0x6A, 0x81, 0x07, 0xE0])
        self.assertEqual(app.parse_iso15693_inventory_response(response), bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]))

    def test_normalize_uid_accepts_common_pn5180_shapes(self):
        self.assertEqual(app.normalize_uid('E0:07:81:6A:E3:2E:96:32'), bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]))
        self.assertEqual(app.normalize_uid(0xE007816AE32E9632), bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]))
        self.assertEqual(app.normalize_uid([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]), bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]))

    def test_validate_iso15693_response_rejects_tag_error(self):
        with self.assertRaises(RuntimeError):
            app.validate_iso15693_response(bytes([0x01, 0x0F]))


    def test_resolve_pn5180_class_accepts_uppercase_export(self):
        class FakePN5180:
            pass

        module = SimpleNamespace(PN5180=FakePN5180)
        self.assertIs(app.resolve_pn5180_class(module), FakePN5180)


    def test_direct_spi_reader_uses_raw_pn5180_commands(self):
        class FakeSpi:
            def __init__(self):
                self.frames = []
                self.max_speed_hz = 0
                self.no_cs = False
                self.reads = [
                    [10, 0, 0, 0],  # RX_STATUS for inventory response length
                    [0, 0, 0, 0],  # IRQ_STATUS
                    [0x00, 0x00, 0x32, 0x96, 0x2E, 0xE3, 0x6A, 0x81, 0x07, 0xE0],
                    [1, 0, 0, 0],  # RX_STATUS for write response length
                    [0, 0, 0, 0],  # IRQ_STATUS
                    [0x00],
                ]

            def open(self, bus, device):
                self.opened = (bus, device)

            def writebytes(self, frame):
                self.frames.append(list(frame))

            def readbytes(self, length):
                data = self.reads.pop(0)
                return data[:length]

        class FakeSpidevModule:
            def __init__(self):
                self.spi = FakeSpi()

            def SpiDev(self):
                return self.spi

        fake_spidev = FakeSpidevModule()
        fake_gpio = SimpleNamespace(BCM='BCM', IN='IN', OUT='OUT', HIGH=1, LOW=0, setmode=lambda _mode: None, setup=lambda *_args, **_kwargs: None, output=lambda *_args: None, input=lambda _pin: 0)

        with (
            patch.object(app, 'spidev_module', fake_spidev),
            patch.object(app, 'gpio_module', fake_gpio),
        ):
            reader = app.DirectSpiPN5180Iso15693Reader()
            uid = reader.poll_uid()
            reader.write_block(uid, 5, bytes([1, 2, 3, 4]))

        self.assertEqual(uid, bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]))
        self.assertIn([0x09, 0x00, 0x06, 0x01, 0x00], fake_spidev.spi.frames)
        self.assertIn([0x09, 0x00, 0x22, 0x21, 0x32, 0x96, 0x2E, 0xE3, 0x6A, 0x81, 0x07, 0xE0, 0x05, 0x01, 0x02, 0x03, 0x04], fake_spidev.spi.frames)



    def test_direct_spi_reader_checks_later_iso15693_inventory_slots(self):
        class FakeSpi:
            def __init__(self):
                self.frames = []
                self.max_speed_hz = 0
                self.no_cs = False
                self.reads = [
                    [0, 0, 0, 0],  # slot 0 RX_STATUS: no card
                    [0, 0, 0, 0],  # slot 0 IRQ_STATUS
                    [10, 0, 0, 0],  # slot 1 RX_STATUS: response
                    [0, 0, 0, 0],  # slot 1 IRQ_STATUS
                    [0x00, 0x00, 0x32, 0x96, 0x2E, 0xE3, 0x6A, 0x81, 0x07, 0xE0],
                ]

            def open(self, _bus, _device):
                pass

            def writebytes(self, frame):
                self.frames.append(list(frame))

            def readbytes(self, length):
                data = self.reads.pop(0)
                return data[:length]

        class FakeSpidevModule:
            def __init__(self):
                self.spi = FakeSpi()

            def SpiDev(self):
                return self.spi

        fake_spidev = FakeSpidevModule()
        fake_gpio = SimpleNamespace(BCM='BCM', IN='IN', OUT='OUT', HIGH=1, LOW=0, setmode=lambda _mode: None, setup=lambda *_args, **_kwargs: None, output=lambda *_args: None, input=lambda _pin: 0)
        with (
            patch.object(app, 'spidev_module', fake_spidev),
            patch.object(app, 'gpio_module', fake_gpio),
        ):
            reader = app.DirectSpiPN5180Iso15693Reader()
            uid = reader.poll_uid()

        self.assertEqual(uid, bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]))

    def test_pn5180_reader_uses_library_iso15693_inventory_when_available(self):
        class FakePn5180:
            def __init__(self, _nss, _busy, _reset):
                self.writes = []

            def setup_iso15693(self):
                self.prepared = getattr(self, 'prepared', 0) + 1

            def inventory_iso15693(self):
                return bytes([0x00, 0x00, 0x32, 0x96, 0x2E, 0xE3, 0x6A, 0x81, 0x07, 0xE0])

            def write_single_block_iso15693(self, uid, block_index, data):
                self.writes.append((uid, block_index, data))

        with patch.object(app, 'PN5180_CLASS', FakePn5180):
            reader = app.PN5180Iso15693Reader()
            uid = reader.poll_uid()
            reader.write_block(uid, 5, bytes([1, 2, 3, 4]))

        self.assertEqual(uid, bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]))
        self.assertEqual(reader.device.prepared, 1)
        self.assertEqual(reader.device.writes, [(uid, 5, bytes([1, 2, 3, 4]))])

    def test_pn5180_reader_uses_raw_iso15693_frames(self):
        class FakePn5180:
            def __init__(self, nss, busy, reset):
                self.pins = (nss, busy, reset)
                self.frames = []
                self.prepared = 0

            def setup_iso15693(self):
                self.prepared += 1

            def send_data(self, frame):
                self.frames.append(bytes(frame))

            def receive_data(self):
                if len(self.frames) == 1:
                    return bytes([0x00, 0x00, 0x32, 0x96, 0x2E, 0xE3, 0x6A, 0x81, 0x07, 0xE0])
                return bytes([0x00])

        with patch.object(app, 'PN5180_CLASS', FakePn5180):
            reader = app.PN5180Iso15693Reader()
            uid = reader.poll_uid()
            reader.write_block(uid, 5, bytes([1, 2, 3, 4]))

        self.assertEqual(reader.device.pins, (app.PN5180_NSS_PIN, app.PN5180_BUSY_PIN, app.PN5180_RESET_PIN))
        self.assertEqual(reader.device.prepared, 2)
        self.assertEqual(reader.device.frames[0], bytes([0x06, 0x01, 0x00]))
        self.assertEqual(reader.device.frames[1], bytes([0x22, 0x21, 0x32, 0x96, 0x2E, 0xE3, 0x6A, 0x81, 0x07, 0xE0, 0x05, 0x01, 0x02, 0x03, 0x04]))

    def test_pn5180_reader_writes_magic_uid_backdoor_frame(self):
        class FakePn5180:
            def __init__(self, _nss, _busy, _reset):
                self.frames = []
                self.prepared = 0

            def setup_iso15693(self):
                self.prepared += 1

            def send_data(self, frame):
                self.frames.append(bytes(frame))

            def receive_data(self):
                return bytes([0x00])

        with patch.object(app, 'PN5180_CLASS', FakePn5180):
            reader = app.PN5180Iso15693Reader()
            reader.write_uid_backdoor(app.TARGET_UID)

        self.assertEqual(reader.device.frames[0], bytes([0x02, 0xB4, 0x00]) + app.TARGET_UID)


    def test_reset_pn5180_hardware_pulses_configured_reset_pin(self):
        calls = []
        fake_gpio = SimpleNamespace(
            BCM='BCM',
            OUT='OUT',
            HIGH=1,
            LOW=0,
            setmode=lambda mode: calls.append(('setmode', mode)),
            setup=lambda *args, **kwargs: calls.append(('setup', args, kwargs)),
            output=lambda *args: calls.append(('output', args)),
        )
        with patch.object(app, 'gpio_module', fake_gpio):
            app.reset_pn5180_hardware()

        self.assertIn(('output', (app.PN5180_RESET_PIN, fake_gpio.LOW)), calls)
        self.assertIn(('output', (app.PN5180_RESET_PIN, fake_gpio.HIGH)), calls)

    def test_describe_hardware_error_adds_i2c_guidance(self):
        message = app.describe_hardware_error(ValueError('No I2C device at address: 0x24'))
        self.assertIn('direct PN5180 SPI reader', message)
        self.assertIn('pigpiod is running', message)

    def test_backend_name_is_defined_for_routes_and_history(self):
        self.assertEqual(app.NFC_READER_BACKEND, 'auto')



    def test_initialize_hardware_falls_back_to_direct_spi_when_pn5180pi_has_no_class(self):
        class FakeDirectReader:
            label = 'fake direct fallback'

        with (
            patch.object(app, 'PN5180_BACKEND', 'pn5180pi'),
            patch.object(app, 'PN5180_CLASS', None),
            patch.object(app, 'DirectSpiPN5180Iso15693Reader', FakeDirectReader),
        ):
            app.initialize_hardware()

        self.assertEqual(app.hardware_status, 'Connected: fake direct fallback')
        self.assertIsInstance(app.reader, FakeDirectReader)

    def test_initialize_hardware_uses_direct_spi_when_configured(self):
        class FakeDirectReader:
            label = 'fake direct'

        with (
            patch.object(app, 'PN5180_BACKEND', 'direct-spi'),
            patch.object(app, 'DirectSpiPN5180Iso15693Reader', FakeDirectReader),
        ):
            app.initialize_hardware()

        self.assertEqual(app.hardware_status, 'Connected: fake direct')
        self.assertIsInstance(app.reader, FakeDirectReader)

    def test_record_operation_allows_detail_named_status(self):
        with patch.object(app, 'operation_history', []):
            app.record_operation('reconnect_reader', 'fail', status='Error: offline')
            self.assertEqual(app.operation_history[0]['status'], 'fail')
            self.assertEqual(app.operation_history[0]['details']['status'], 'Error: offline')

    def test_run_reconnect_failure_records_hardware_status(self):
        with (
            patch.object(app, 'PN5180_CLASS', None),
            patch.object(app, 'operation_history', []),
        ):
            app.run_reconnect()
            self.assertEqual(app.operation_history[-1]['operation'], 'reconnect_reader')
            self.assertEqual(app.operation_history[-1]['status'], 'fail')
            self.assertIn('hardware_status', app.operation_history[-1]['details'])

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
