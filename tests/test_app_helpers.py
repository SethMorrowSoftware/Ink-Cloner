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
                    [0x01, 0, 0, 0],  # IRQ_STATUS: RX_IRQ set (reception complete)
                    [10, 0, 0, 0],  # RX_STATUS for inventory response length
                    [0x00, 0x00, 0x32, 0x96, 0x2E, 0xE3, 0x6A, 0x81, 0x07, 0xE0],
                    [0x01, 0, 0, 0],  # IRQ_STATUS: RX_IRQ set
                    [1, 0, 0, 0],  # RX_STATUS for write response length
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



    def test_direct_spi_reader_toggles_nss_between_command_and_response(self):
        # PN5180 framing: NSS must rise after the command (ending that frame) and
        # the BUSY wait happens with NSS high, then NSS falls again for the response.
        class FakePi:
            connected = True

            def __init__(self):
                self.events = []

            def spi_open(self, _channel, _baud, _flags):
                return 7

            def set_mode(self, *_args):
                pass

            def write(self, pin, value):
                if pin == app.PN5180_NSS_PIN:
                    self.events.append(('nss', value))

            def read(self, _pin):
                return 0  # BUSY always ready

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
            fake_pigpio.pi_instance.events.clear()  # drop reset/init writes
            reader._read_after_command([0x0A, 0x00], 1)

        self.assertEqual(fake_pigpio.pi_instance.events, [
            ('nss', 0),            # select for command frame
            ('xfer', [0x0A, 0x00]),
            ('nss', 1),            # raise NSS to end command frame (BUSY wait here)
            ('nss', 0),            # select again for response frame
            ('xfer', [0x00]),
            ('nss', 1),            # end response frame
        ])

    def test_direct_spi_read_completes_when_busy_clears_after_nss_high(self):
        # Models real PN5180 behavior: BUSY is asserted while selected and only
        # clears once NSS is raised. The old single-frame read hung here forever.
        class FakePi:
            connected = True

            def __init__(self):
                self.nss = 1
                self.busy = False

            def spi_open(self, *_args):
                return 7

            def set_mode(self, *_args):
                pass

            def write(self, pin, value):
                if pin == app.PN5180_NSS_PIN:
                    self.nss = value
                    if value == 1:      # frame ended -> chip completes, BUSY clears
                        self.busy = False

            def read(self, _pin):
                return 1 if self.busy else 0

            def spi_xfer(self, _handle, frame):
                if self.nss == 0:       # clocking while selected raises BUSY
                    self.busy = True
                return len(frame), bytearray(len(frame))

        class FakePigpioModule:
            INPUT = 'INPUT'
            OUTPUT = 'OUTPUT'

            def __init__(self):
                self.pi_instance = FakePi()

            def pi(self):
                return self.pi_instance

        fake_pigpio = FakePigpioModule()
        with (
            patch.object(app, 'pigpio_module', fake_pigpio),
            patch.object(app, 'PN5180_BUSY_TIMEOUT_SECONDS', 0.2),
        ):
            reader = app.DirectSpiPN5180Iso15693Reader()
            result = reader._read_after_command([0x0A, 0x00], 1)

        self.assertEqual(result, [0x00])


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

        # Gen2 magic UID-set: UID in wire order (LSB first), high 4 via 0x40, low 4 via 0x41.
        wire = app.TARGET_UID[::-1]
        self.assertEqual(reader.device.frames[0], bytes([0x02, 0xE0, 0x09, 0x40]) + wire[0:4])
        self.assertEqual(reader.device.frames[1], bytes([0x02, 0xE0, 0x09, 0x41]) + wire[4:8])


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

    def _build_fake_pigpio(self, reads, busy_value=0):
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
                return busy_value

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

    def test_wait_ready_times_out_when_busy_stuck_high(self):
        fake_pigpio = self._build_fake_pigpio([], busy_value=1)
        with (
            patch.object(app, 'pigpio_module', fake_pigpio),
            patch.object(app, 'PN5180_BUSY_TIMEOUT_SECONDS', 0.05),
        ):
            reader = app.DirectSpiPN5180Iso15693Reader()
            with self.assertRaises(RuntimeError):
                reader._wait_ready()

    def _build_reset_recording_pigpio(self, busy_value):
        reset_writes = []

        class FakePi:
            connected = True

            def spi_open(self, *_args):
                return 7

            def set_mode(self, *_args):
                pass

            def write(self, pin, value):
                if pin == app.PN5180_RESET_PIN:
                    reset_writes.append(value)

            def read(self, _pin):
                return busy_value

            def spi_xfer(self, _handle, frame):
                return len(frame), bytearray(len(frame))

        class FakePigpioModule:
            INPUT = 'INPUT'
            OUTPUT = 'OUTPUT'

            def __init__(self):
                self.pi_instance = FakePi()

            def pi(self):
                return self.pi_instance

        return FakePigpioModule(), reset_writes

    def test_recover_if_busy_stuck_hardware_resets_chip(self):
        fake_pigpio, reset_writes = self._build_reset_recording_pigpio(busy_value=1)
        with patch.object(app, 'pigpio_module', fake_pigpio):
            reader = app.DirectSpiPN5180Iso15693Reader()
            reset_writes.clear()  # drop the constructor's reset pulse
            reader._recover_if_busy_stuck()
        # A stuck BUSY line triggers a reset pulse (RESET driven low then high).
        self.assertIn(0, reset_writes)
        self.assertIn(1, reset_writes)

    def test_recover_if_busy_stuck_is_noop_when_ready(self):
        fake_pigpio, reset_writes = self._build_reset_recording_pigpio(busy_value=0)
        with patch.object(app, 'pigpio_module', fake_pigpio):
            reader = app.DirectSpiPN5180Iso15693Reader()
            reset_writes.clear()
            reader._recover_if_busy_stuck()
        self.assertEqual(reset_writes, [])  # ready chip is not reset

    def test_card_has_responded_requires_rx_irq(self):
        # Bytes are present but RX_IRQ never fires: the frame is not yet complete,
        # so the reader must not grab a (truncated) response.
        class FakePi:
            connected = True

            def __init__(self):
                self._pending = None

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
                if data == [0x04, 0x02]:        # READ_REGISTER IRQ_STATUS
                    self._pending = 'irq'
                elif data == [0x04, 0x13]:      # READ_REGISTER RX_STATUS
                    self._pending = 'rx'
                elif len(data) == 4 and all(b == 0 for b in data):
                    if self._pending == 'irq':
                        return 4, bytearray([0x00, 0, 0, 0])   # RX_IRQ never set
                    return 4, bytearray([10, 0, 0, 0])         # bytes present anyway
                return len(data), bytearray(len(data))

        class FakePigpioModule:
            INPUT = 'INPUT'
            OUTPUT = 'OUTPUT'

            def __init__(self):
                self.pi_instance = FakePi()

            def pi(self):
                return self.pi_instance

        fake_pigpio = FakePigpioModule()
        with (
            patch.object(app, 'pigpio_module', fake_pigpio),
            patch.object(app, 'PN5180_RESPONSE_TIMEOUT_SECONDS', 0.02),
        ):
            reader = app.DirectSpiPN5180Iso15693Reader()
            self.assertFalse(reader._card_has_responded())

    def test_describe_reader_self_test_survives_busy_timeout(self):
        # A stuck BUSY line must not hang startup: self-test fails gracefully so
        # initialize_hardware can still reach socketio.run and serve the UI.
        fake_pigpio = self._build_fake_pigpio([], busy_value=1)
        with (
            patch.object(app, 'pigpio_module', fake_pigpio),
            patch.object(app, 'PN5180_BUSY_TIMEOUT_SECONDS', 0.05),
        ):
            reader = app.DirectSpiPN5180Iso15693Reader()
            detail = app.describe_reader_self_test(reader)
        self.assertIn('identity read failed', detail)

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

    def test_parse_iso15693_block_response_extracts_data(self):
        self.assertEqual(
            app.parse_iso15693_block_response(bytes([0x00, 0x11, 0x22, 0x33, 0x44]), 0),
            bytes([0x11, 0x22, 0x33, 0x44]),
        )

    def test_parse_iso15693_block_response_rejects_error_and_short(self):
        with self.assertRaises(RuntimeError):
            app.parse_iso15693_block_response(bytes([0x01, 0x0F]), 3)   # error flag set
        with self.assertRaises(RuntimeError):
            app.parse_iso15693_block_response(bytes([0x00, 0x11]), 2)   # too few data bytes

    def test_direct_spi_read_block_returns_block_data(self):
        fake_pigpio = self._build_fake_pigpio([
            [0x01, 0, 0, 0],                      # IRQ_STATUS: RX_IRQ set
            [5, 0, 0, 0],                         # RX_STATUS: flags + 4 data bytes
            [0x00, 0x11, 0x22, 0x33, 0x44],       # response: flags + block data
        ])
        with patch.object(app, 'pigpio_module', fake_pigpio):
            reader = app.DirectSpiPN5180Iso15693Reader()
            data = reader.read_block(bytes(range(8)), 7)

        self.assertEqual(data, bytes([0x11, 0x22, 0x33, 0x44]))
        uid_reversed = list(bytes(range(8))[::-1])
        expected_frame = [0x09, 0x00, 0x22, 0x20] + uid_reversed + [7]
        self.assertIn(expected_frame, fake_pigpio.pi_instance.transfers)

    def test_run_verify_passes_when_uid_and_blocks_match(self):
        class FakeReader:
            label = 'fake'

            def poll_uid(self):
                return app.TARGET_UID

            def read_block(self, _uid, index):
                return app.CLEARED_DATA_BLOCKS[index]

        with (
            patch.object(app, 'reader', FakeReader()),
            patch.object(app, 'operation_history', []),
        ):
            app.run_verify()
            record = app.operation_history[-1]
        self.assertEqual(record['operation'], 'verify')
        self.assertEqual(record['status'], 'success')
        self.assertTrue(record['details']['uid_matches'])

    def test_run_verify_flags_block_mismatch(self):
        class FakeReader:
            label = 'fake'

            def poll_uid(self):
                return app.TARGET_UID

            def read_block(self, _uid, index):
                data = bytearray(app.CLEARED_DATA_BLOCKS[index])
                if index == 5:
                    data[0] ^= 0xFF
                return bytes(data)

        with (
            patch.object(app, 'reader', FakeReader()),
            patch.object(app, 'operation_history', []),
        ):
            app.run_verify()
            record = app.operation_history[-1]
        self.assertEqual(record['status'], 'fail')
        self.assertIn(5, record['details']['mismatched_blocks'])

    def test_run_verify_reports_uid_not_cloned(self):
        other_uid = bytes([0xE0, 0x53, 0x01, 0x10, 0x65, 0x34, 0x8E, 0x18])

        class FakeReader:
            label = 'fake'

            def poll_uid(self):
                return other_uid

            def read_block(self, _uid, index):
                return app.CLEARED_DATA_BLOCKS[index]

        with (
            patch.object(app, 'reader', FakeReader()),
            patch.object(app, 'operation_history', []),
        ):
            app.run_verify()
            record = app.operation_history[-1]
        self.assertEqual(record['status'], 'fail')          # not an exact clone
        self.assertFalse(record['details']['uid_matches'])
        self.assertEqual(record['details']['mismatched_blocks'], [])  # but data is faithful

    def test_direct_spi_write_uid_backdoor_sends_gen2_magic_frames(self):
        fake_pigpio = self._build_fake_pigpio([
            [0x01, 0, 0, 0], [1, 0, 0, 0], [0x00],   # exchange 1: high UID half
            [0x01, 0, 0, 0], [1, 0, 0, 0], [0x00],   # exchange 2: low UID half
        ])
        with patch.object(app, 'pigpio_module', fake_pigpio):
            reader = app.DirectSpiPN5180Iso15693Reader()
            reader.write_uid_backdoor(app.TARGET_UID)

        transfers = fake_pigpio.pi_instance.transfers
        wire = app.TARGET_UID[::-1]
        self.assertIn([0x09, 0x00, 0x02, 0xE0, 0x09, 0x40] + list(wire[0:4]), transfers)
        self.assertIn([0x09, 0x00, 0x02, 0xE0, 0x09, 0x41] + list(wire[4:8]), transfers)

    def test_parse_iso15693_system_info_parses_all_fields(self):
        wire = bytes([0x32, 0x96, 0x2E, 0xE3, 0x6A, 0x81, 0x07, 0xE0])
        response = bytes([0x00, 0x0F]) + wire + bytes([0x00, 0x05, 0x3F, 0x03, 0x8B])
        info = app.parse_iso15693_system_info(response)
        self.assertEqual(info['uid'], 'E0-07-81-6A-E3-2E-96-32')
        self.assertEqual(info['dsfid'], '0x00')
        self.assertEqual(info['afi'], '0x05')
        self.assertEqual(info['block_count'], 64)
        self.assertEqual(info['block_size'], 4)
        self.assertEqual(info['ic_reference'], '0x8B')

    def test_parse_iso15693_system_info_rejects_error_flag(self):
        with self.assertRaises(RuntimeError):
            app.parse_iso15693_system_info(bytes([0x01, 0x0F]))

    def test_direct_spi_read_system_info_frame_and_fields(self):
        wire = bytes([0x32, 0x96, 0x2E, 0xE3, 0x6A, 0x81, 0x07, 0xE0])
        sysinfo = [0x00, 0x0F] + list(wire) + [0x00, 0x00, 0x3F, 0x03, 0x8B]
        fake_pigpio = self._build_fake_pigpio([
            [0x01, 0, 0, 0],            # IRQ_STATUS: RX_IRQ set
            [len(sysinfo), 0, 0, 0],    # RX_STATUS
            sysinfo,                    # Get System Information response
        ])
        with patch.object(app, 'pigpio_module', fake_pigpio):
            reader = app.DirectSpiPN5180Iso15693Reader()
            info = reader.read_system_info(bytes(range(8)))

        self.assertEqual(info['block_count'], 64)
        self.assertEqual(info['ic_reference'], '0x8B')
        expected_frame = [0x09, 0x00, 0x22, 0x2B] + list(bytes(range(8))[::-1])
        self.assertIn(expected_frame, fake_pigpio.pi_instance.transfers)

    def test_run_tag_info_records_profile(self):
        class FakeReader:
            label = 'fake'

            def poll_uid(self):
                return app.TARGET_UID

            def read_system_info(self, _uid):
                return {
                    'uid': 'E0-07-81-6A-E3-2E-96-32',
                    'dsfid': '0x00', 'afi': '0x00',
                    'block_count': 64, 'block_size': 4,
                    'ic_reference': '0x8B', 'info_flags': '0x0F',
                }

        with (
            patch.object(app, 'reader', FakeReader()),
            patch.object(app, 'operation_history', []),
        ):
            app.run_tag_info()
            record = app.operation_history[-1]
        self.assertEqual(record['operation'], 'tag_info')
        self.assertEqual(record['status'], 'success')
        self.assertEqual(record['details']['block_count'], 64)

    def test_parse_iso15693_block_security_flags_locked_blocks(self):
        response = bytes([0x00, 0x01, 0x00, 0x00, 0x00])  # flags + 4 status bytes
        self.assertEqual(app.parse_iso15693_block_security(response, 4), [True, False, False, False])

    def test_parse_iso15693_block_security_rejects_short(self):
        with self.assertRaises(RuntimeError):
            app.parse_iso15693_block_security(bytes([0x00, 0x01]), 4)

    def test_direct_spi_read_block_security_frame_and_locks(self):
        security = [0x00, 0x01, 0x00, 0x00, 0x00]  # flags + block0 locked, 1-3 open
        fake_pigpio = self._build_fake_pigpio([
            [0x01, 0, 0, 0],              # IRQ_STATUS: RX_IRQ set
            [len(security), 0, 0, 0],     # RX_STATUS
            security,                     # security status response
        ])
        with patch.object(app, 'pigpio_module', fake_pigpio):
            reader = app.DirectSpiPN5180Iso15693Reader()
            locks = reader.read_block_security(bytes(range(8)), 0, 4)

        self.assertEqual(locks, [True, False, False, False])
        expected_frame = [0x09, 0x00, 0x22, 0x2C] + list(bytes(range(8))[::-1]) + [0x00, 0x03]
        self.assertIn(expected_frame, fake_pigpio.pi_instance.transfers)

    def test_run_dump_tag_records_data_and_locks(self):
        class FakeReader:
            label = 'fake'

            def poll_uid(self):
                return app.TARGET_UID

            def read_block(self, _uid, index):
                return app.CLEARED_DATA_BLOCKS[index]

            def read_block_security(self, _uid, _first, count):
                return [index in (1, 3) for index in range(count)]

        with (
            patch.object(app, 'reader', FakeReader()),
            patch.object(app, 'operation_history', []),
        ):
            app.run_dump_tag()
            record = app.operation_history[-1]

        self.assertEqual(record['operation'], 'dump')
        self.assertEqual(record['status'], 'success')
        self.assertEqual(record['details']['locked_blocks'], [1, 3])
        self.assertEqual(
            record['details']['data'],
            ''.join(block.hex() for block in app.CLEARED_DATA_BLOCKS),
        )

    def test_run_set_counter_writes_counter_block_little_endian(self):
        class FakeReader:
            label = 'fake'

            def __init__(self):
                self.writes = []

            def poll_uid(self):
                return app.TARGET_UID

            def write_block(self, _uid, index, data):
                self.writes.append((index, bytes(data)))

            def read_block(self, _uid, index):
                if index == app.PRINT_COUNTER_BLOCK:
                    return bytes([0xFF, 0x02, 0x00, 0x00])
                return b''

        fake = FakeReader()
        with (
            patch.object(app, 'reader', fake),
            patch.object(app, 'operation_history', []),
        ):
            app.run_set_counter(0x02FF)  # 767
            record = app.operation_history[-1]

        self.assertEqual(record['operation'], 'set_counter')
        self.assertEqual(record['status'], 'success')
        self.assertIn((app.PRINT_COUNTER_BLOCK, bytes([0xFF, 0x02, 0x00, 0x00])), fake.writes)

    def test_direct_spi_lock_block_frame(self):
        fake_pigpio = self._build_fake_pigpio([[0x01, 0, 0, 0], [1, 0, 0, 0], [0x00]])
        with patch.object(app, 'pigpio_module', fake_pigpio):
            reader = app.DirectSpiPN5180Iso15693Reader()
            reader.lock_block(bytes(range(8)), 7)
        expected_frame = [0x09, 0x00, 0x22, 0x22] + list(bytes(range(8))[::-1]) + [7]
        self.assertIn(expected_frame, fake_pigpio.pi_instance.transfers)

    def test_run_lock_to_master_locks_target_blocks(self):
        class FakeReader:
            label = 'fake'

            def __init__(self):
                self.locked = []

            def poll_uid(self):
                return app.TARGET_UID

            def read_block(self, _uid, index):
                return app.CLEARED_DATA_BLOCKS[index]

            def lock_block(self, _uid, index):
                self.locked.append(index)

            def read_block_security(self, _uid, _first, count):
                return [index in self.locked for index in range(count)]

        fake = FakeReader()
        with (
            patch.object(app, 'reader', fake),
            patch.object(app, 'operation_history', []),
        ):
            app.run_lock_to_master()
            record = app.operation_history[-1]

        self.assertEqual(record['operation'], 'lock_to_master')
        self.assertEqual(record['status'], 'success')
        self.assertEqual(fake.locked, app.TARGET_LOCKED_BLOCKS)

    def test_run_lock_to_master_refuses_when_not_master(self):
        other_uid = bytes([0xE0, 0x53, 0x01, 0x10, 0x65, 0x34, 0x8E, 0x18])

        class FakeReader:
            label = 'fake'

            def __init__(self):
                self.locked = []

            def poll_uid(self):
                return other_uid

            def read_block(self, _uid, index):
                return app.CLEARED_DATA_BLOCKS[index]

            def lock_block(self, _uid, index):
                self.locked.append(index)

        fake = FakeReader()
        with (
            patch.object(app, 'reader', fake),
            patch.object(app, 'operation_history', []),
        ):
            app.run_lock_to_master()
            record = app.operation_history[-1]

        self.assertEqual(record['status'], 'fail')
        self.assertEqual(fake.locked, [])  # nothing was locked

    def test_guess_iso14443a_type(self):
        self.assertIn('Classic 1K', app.guess_iso14443a_type(b'\x04\x00', 0x08))
        self.assertIn('Ultralight', app.guess_iso14443a_type(b'\x44\x00', 0x00))
        self.assertIn('14443-4', app.guess_iso14443a_type(b'\x44\x03', 0x20))

    def test_direct_spi_detect_iso14443a_activates_and_returns_uid(self):
        fake_pigpio = self._build_fake_pigpio([
            [0x01, 0, 0, 0], [2, 0, 0, 0], [0x04, 0x00],                    # REQA -> ATQA
            [0x01, 0, 0, 0], [5, 0, 0, 0], [0xDE, 0xAD, 0xBE, 0xEF, 0x22],  # anticoll -> UID+BCC
            [0x01, 0, 0, 0], [1, 0, 0, 0], [0x08],                          # SELECT -> SAK
        ])
        with patch.object(app, 'pigpio_module', fake_pigpio):
            reader = app.DirectSpiPN5180Iso15693Reader()
            tag = reader.detect_iso14443a()

        self.assertEqual(tag['uid'], 'DE-AD-BE-EF')
        self.assertEqual(tag['atqa'], '0400')
        self.assertEqual(tag['sak'], '0x08')
        self.assertIn('Classic 1K', tag['type'])
        transfers = fake_pigpio.pi_instance.transfers
        self.assertIn([0x09, 0x07, 0x26], transfers)                       # REQA (7-bit short frame)
        self.assertIn([0x09, 0x00, 0x93, 0x20], transfers)                 # anticollision CL1
        self.assertIn([0x09, 0x00, 0x93, 0x70, 0xDE, 0xAD, 0xBE, 0xEF, 0x22], transfers)  # SELECT CL1

    def test_run_identify_reports_protocols(self):
        class FakeReader:
            label = 'fake'

            def detect_iso14443a(self):
                return {'uid': 'DE-AD-BE-EF', 'atqa': '0400', 'sak': '0x08', 'type': 'MIFARE Classic 1K'}

            def poll_uid(self):
                return None

        with (
            patch.object(app, 'reader', FakeReader()),
            patch.object(app, 'operation_history', []),
        ):
            app.run_identify()
            record = app.operation_history[-1]

        self.assertEqual(record['operation'], 'identify')
        self.assertEqual(record['status'], 'success')
        self.assertIn('iso14443a', record['details']['protocols'])

    def test_direct_spi_mifare_authenticate_frame(self):
        fake_pigpio = self._build_fake_pigpio([[0x00]])  # auth status byte = success
        key = bytes.fromhex('FFFFFFFFFFFF')
        with patch.object(app, 'pigpio_module', fake_pigpio):
            reader = app.DirectSpiPN5180Iso15693Reader()
            ok = reader._mifare_authenticate(bytes([0x7D, 0xAE, 0x1E, 0x52]), 4, key, 0x60)
        self.assertTrue(ok)
        expected = [0x0C] + list(key) + [0x60, 4, 0x7D, 0xAE, 0x1E, 0x52]
        self.assertIn(expected, fake_pigpio.pi_instance.transfers)

    def test_run_dump_mifare_reports_sectors(self):
        class FakeReader:
            label = 'fake'

            def dump_mifare_classic(self, sectors=16, keys=None):
                return {
                    'uid': '7D-AE-1E-52',
                    'sectors': {
                        0: {'key': 'ffffffffffff', 'key_type': 'A',
                            'blocks': ['00' * 16, '11' * 16, '22' * 16, '33' * 16]},
                    },
                    'failed_sectors': [1],
                }

        with (
            patch.object(app, 'reader', FakeReader()),
            patch.object(app, 'operation_history', []),
        ):
            app.run_dump_mifare()
            record = app.operation_history[-1]

        self.assertEqual(record['operation'], 'dump_mifare')
        self.assertEqual(record['status'], 'success')
        self.assertEqual(record['details']['opened_sectors'], 1)


if __name__ == '__main__':
    unittest.main()
