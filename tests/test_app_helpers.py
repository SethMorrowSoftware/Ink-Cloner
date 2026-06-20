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


    def test_direct_spi_reader_uses_pigpio_raw_pn5180_commands(self):
        class FakePi:
            connected = True

            def __init__(self):
                self.modes = []
                self.writes = []
                self.transfers = []
                self.reads = [
                    [10, 0, 0, 0],  # RX_STATUS for inventory response length
                    [0, 0, 0, 0],  # IRQ_STATUS
                    [0x00, 0x00, 0x32, 0x96, 0x2E, 0xE3, 0x6A, 0x81, 0x07, 0xE0],
                    [1, 0, 0, 0],  # RX_STATUS for write response length
                    [0, 0, 0, 0],  # IRQ_STATUS
                    [0x00],
                ]

            def spi_open(self, channel, baud, flags):
                self.opened = (channel, baud, flags)
                return 7

            def set_mode(self, pin, mode):
                self.modes.append((pin, mode))

            def write(self, pin, value):
                self.writes.append((pin, value))

            def read(self, _pin):
                return 0

            def spi_xfer(self, handle, frame):
                data = list(frame)
                self.transfers.append(data)
                if all(byte == 0 for byte in data) and self.reads:
                    response = self.reads.pop(0)[:len(data)]
                    return len(response), bytearray(response)
                return len(data), bytearray(len(data))

        class FakePigpioModule:
            INPUT = 'INPUT'
            OUTPUT = 'OUTPUT'

            def __init__(self):
                self.pi_instance = FakePi()

            def pi(self):
                return self.pi_instance

        fake_pigpio = FakePigpioModule()

        with patch.object(app, 'pigpio_module', fake_pigpio):
            reader = app.DirectSpiPN5180Iso15693Reader()
            uid = reader.poll_uid()
            reader.write_block(uid, 5, bytes([1, 2, 3, 4]))

        self.assertEqual(uid, bytes([0xE0, 0x07, 0x81, 0x6A, 0xE3, 0x2E, 0x96, 0x32]))
        # A single sticker is found with the one-slot inventory (Nb_slots flag 0x20 -> flags 0x26).
        self.assertIn([0x09, 0x00, 0x26, 0x01, 0x00], fake_pigpio.pi_instance.transfers)
        self.assertIn([0x0A, 0x00], fake_pigpio.pi_instance.transfers)
        self.assertIn([0x09, 0x00, 0x22, 0x21, 0x32, 0x96, 0x2E, 0xE3, 0x6A, 0x81, 0x07, 0xE0, 0x05, 0x01, 0x02, 0x03, 0x04], fake_pigpio.pi_instance.transfers)



    def test_direct_spi_reader_keeps_chip_select_active_during_busy_wait_before_read(self):
        class FakePi:
            connected = True

            def __init__(self):
                self.events = []
                self.busy_reads = [1, 0]

            def spi_open(self, _channel, _baud, _flags):
                return 7

            def set_mode(self, *_args):
                pass

            def write(self, pin, value):
                self.events.append(('write', pin, value))

            def read(self, pin):
                self.events.append(('read', pin))
                return self.busy_reads.pop(0) if self.busy_reads else 0

            def spi_xfer(self, _handle, frame):
                data = list(frame)
                self.events.append(('xfer', data))
                return len(data), bytearray(len(data))

        class FakePigpioModule:
            INPUT = 'INPUT'
            OUTPUT = 'OUTPUT'

            def __init__(self):
                self.pi_instance = FakePi()

            def pi(self):
                return self.pi_instance

        fake_pigpio = FakePigpioModule()
        with patch.object(app, 'pigpio_module', fake_pigpio):
            reader = app.DirectSpiPN5180Iso15693Reader()
            reader._read_after_command([0x0A, 0x00], 1)

        events = fake_pigpio.pi_instance.events
        command_index = events.index(('xfer', [0x0A, 0x00]))
        response_index = events.index(('xfer', [0x00]))
        deselect_index = events.index(('write', app.PN5180_NSS_PIN, 1), command_index)
        self.assertGreater(deselect_index, response_index)

    def test_direct_spi_reader_decodes_rx_status_byte_count_bits(self):
        self.assertEqual(app.DirectSpiPN5180Iso15693Reader._rx_status_byte_count([0x0A, 0x00, 0x00, 0x00]), 10)
        self.assertEqual(app.DirectSpiPN5180Iso15693Reader._rx_status_byte_count([0x00, 0x01, 0x00, 0x00]), 256)
        self.assertEqual(app.DirectSpiPN5180Iso15693Reader._rx_status_byte_count([]), 0)

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


    def test_reset_pn5180_hardware_pulses_configured_reset_pin_via_pigpio(self):
        calls = []

        class FakePi:
            connected = True

            def set_mode(self, *args):
                calls.append(('set_mode', args))

            def write(self, *args):
                calls.append(('write', args))

            def stop(self):
                calls.append(('stop', ()))

        fake_pigpio = SimpleNamespace(OUTPUT='OUTPUT', pi=lambda: FakePi())
        with patch.object(app, 'pigpio_module', fake_pigpio):
            app.reset_pn5180_hardware()

        self.assertIn(('set_mode', (app.PN5180_RESET_PIN, fake_pigpio.OUTPUT)), calls)
        self.assertIn(('write', (app.PN5180_RESET_PIN, 0)), calls)
        self.assertIn(('write', (app.PN5180_RESET_PIN, 1)), calls)
        self.assertIn(('stop', ()), calls)

    def test_describe_hardware_error_adds_i2c_guidance(self):
        message = app.describe_hardware_error(ValueError('No I2C device at address: 0x24'))
        self.assertIn('direct PN5180 SPI reader', message)
        self.assertIn('pigpiod is running', message)

    def test_backend_name_is_defined_for_routes_and_history(self):
        self.assertEqual(app.NFC_READER_BACKEND, 'direct-spi')



    def test_initialize_hardware_auto_falls_back_to_direct_spi_when_pn5180pi_has_no_class(self):
        class FakeDirectReader:
            label = 'fake direct fallback'

        with (
            patch.object(app, 'PN5180_BACKEND', 'auto'),
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
        self.assertIn('self_test', response.json)

    def test_format_pn5180_version_orders_major_minor(self):
        self.assertEqual(app.format_pn5180_version(bytes([0x05, 0x03])), '3.5')
        self.assertEqual(app.format_pn5180_version(bytes([0x00, 0x04])), '4.0')
        self.assertEqual(app.format_pn5180_version(b''), 'unknown')
        self.assertEqual(app.format_pn5180_version(None), 'unknown')

    def test_pn5180_identity_responsive_rejects_blank_reads(self):
        self.assertFalse(app.pn5180_identity_responsive(bytes([0x00, 0x00]), bytes([0xFF, 0xFF])))
        self.assertFalse(app.pn5180_identity_responsive(None, b''))
        self.assertTrue(app.pn5180_identity_responsive(bytes([0x00, 0x00]), bytes([0x05, 0x03])))

    def _build_fake_pigpio(self, reads):
        class FakePi:
            connected = True

            def __init__(self):
                self.transfers = []
                self.reads = list(reads)

            def spi_open(self, *_args):
                return 7

            def set_mode(self, *_args):
                pass

            def write(self, *_args):
                pass

            def read(self, _pin):
                return 0

            def spi_xfer(self, _handle, frame):
                data = list(frame)
                self.transfers.append(data)
                if all(byte == 0 for byte in data) and self.reads:
                    response = self.reads.pop(0)[:len(data)]
                    return len(response), bytearray(response)
                return len(data), bytearray(len(data))

        class FakePigpioModule:
            INPUT = 'INPUT'
            OUTPUT = 'OUTPUT'

            def __init__(self):
                self.pi_instance = FakePi()

            def pi(self):
                return self.pi_instance

        return FakePigpioModule()

    def test_direct_spi_reader_self_test_reads_identity_eeprom(self):
        fake_pigpio = self._build_fake_pigpio([
            [0x01, 0x03],        # PRODUCT_VERSION -> 3.1
            [0x05, 0x03],        # FIRMWARE_VERSION -> 3.5
            [0x10, 0x00],        # EEPROM_VERSION -> 0.16
            list(range(16)),     # DIE_ID
        ])
        with patch.object(app, 'pigpio_module', fake_pigpio):
            reader = app.DirectSpiPN5180Iso15693Reader()
            info = reader.read_self_test()

        self.assertEqual(info['firmware_version'], '3.5')
        self.assertEqual(info['product_version'], '3.1')
        self.assertEqual(info['eeprom_version'], '0.16')
        self.assertTrue(info['responsive'])
        # READ_EEPROM frames: opcode 0x07 + address + length.
        self.assertIn([0x07, 0x10, 0x02], fake_pigpio.pi_instance.transfers)
        self.assertIn([0x07, 0x12, 0x02], fake_pigpio.pi_instance.transfers)
        self.assertIn([0x07, 0x00, 0x10], fake_pigpio.pi_instance.transfers)

    def test_direct_spi_reader_self_test_flags_unresponsive_chip(self):
        fake_pigpio = self._build_fake_pigpio([])  # every read returns zeros
        with patch.object(app, 'pigpio_module', fake_pigpio):
            reader = app.DirectSpiPN5180Iso15693Reader()
            info = reader.read_self_test()
        self.assertFalse(info['responsive'])

    def test_direct_spi_poll_uid_falls_back_to_anticollision_sweep(self):
        # No card ever answers; both inventory frames must be issued before giving up.
        fake_pigpio = self._build_fake_pigpio([])
        with (
            patch.object(app, 'pigpio_module', fake_pigpio),
            patch.object(app, 'PN5180_RESPONSE_TIMEOUT_SECONDS', 0.01),
        ):
            reader = app.DirectSpiPN5180Iso15693Reader()
            self.assertIsNone(reader.poll_uid())

        transfers = fake_pigpio.pi_instance.transfers
        self.assertIn([0x09, 0x00, 0x26, 0x01, 0x00], transfers)  # single-slot inventory
        self.assertIn([0x09, 0x00, 0x06, 0x01, 0x00], transfers)  # 16-slot anticollision sweep

    def test_describe_reader_self_test_reports_firmware(self):
        class FakeReader:
            def read_self_test(self):
                return {'responsive': True, 'firmware_version': '3.5'}

        self.assertIn('firmware 3.5', app.describe_reader_self_test(FakeReader()))

    def test_describe_reader_self_test_warns_when_identity_empty(self):
        class FakeReader:
            def read_self_test(self):
                return {'responsive': False}

        self.assertIn('check SPI wiring', app.describe_reader_self_test(FakeReader()))

    def test_describe_reader_self_test_ignores_backend_without_support(self):
        self.assertEqual(app.describe_reader_self_test(object()), '')

    def test_run_self_test_records_responsive_result(self):
        class FakeReader:
            label = 'fake'

            def read_self_test(self):
                return {
                    'responsive': True,
                    'firmware_version': '3.5',
                    'product_version': '3.1',
                    'eeprom_version': '0.16',
                    'die_id': '00',
                }

        with (
            patch.object(app, 'reader', FakeReader()),
            patch.object(app, 'operation_history', []),
        ):
            app.run_self_test()
            self.assertEqual(app.operation_history[-1]['operation'], 'self_test')
            self.assertEqual(app.operation_history[-1]['status'], 'success')

    def test_run_self_test_reports_failure_for_unresponsive_chip(self):
        class FakeReader:
            label = 'fake'

            def read_self_test(self):
                return {'responsive': False, 'firmware_version': 'unknown'}

        with (
            patch.object(app, 'reader', FakeReader()),
            patch.object(app, 'operation_history', []),
        ):
            app.run_self_test()
            self.assertEqual(app.operation_history[-1]['status'], 'fail')


if __name__ == '__main__':
    unittest.main()
