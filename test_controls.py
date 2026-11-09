import errno
import struct
import unittest
from unittest.mock import patch

import controls
import server
from camera import CameraError


class FakeDevice:
    def __init__(self):
        self.values = {'brightness': 0, 'focus_auto': 1, 'focus_absolute': 175,
                       'exposure_auto': 3, 'exposure_absolute': 166,
                       'white_balance_auto': 1, 'white_balance_temperature': 4600}
        self.writes = []
        self.fail_once = None

    def all(self, fd):
        specs = {'brightness': (1, -56, 56, 0), 'focus_auto': (2, 0, 1, 0),
                 'focus_absolute': (1, 100, 450, 256), 'exposure_auto': (3, 0, 3, 3),
                 'exposure_absolute': (1, 3, 2047, 166), 'white_balance_auto': (2, 0, 1, 1),
                 'white_balance_temperature': (1, 2200, 7500, 4600)}
        result = []
        for key, (typ, lo, hi, default) in specs.items():
            dep = controls.DEPENDENCIES.get(key)
            inactive = bool(dep and self.values[dep[0]] != dep[1])
            c = {'key': key, 'id': controls.IDS[key], 'type': typ, 'minimum': lo,
                 'maximum': hi, 'step': 1, 'default': default, 'flags': 16 if inactive else 0,
                 'value': self.values[key], 'supported_for_setting': True, 'inactive': inactive}
            if typ == 3:
                c['choices'] = {'1': 'Manual Mode', '3': 'Aperture Priority Mode'}
            result.append(c)
        return result

    def query(self, fd, cid):
        return next(c for c in self.all(fd) if c['id'] == cid)

    def put(self, fd, cid, value):
        key = controls.KEYS[cid]
        self.writes.append((key, value))
        if self.fail_once == key:
            self.fail_once = None
            raise OSError(errno.EIO, 'simulated USB error')
        if self.query(fd, cid)['flags'] & 16:
            raise OSError(errno.EACCES, 'inactive manual control')
        self.values[key] = value


class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDevice()
        replacements = {
            'choose_device': lambda device: '/dev/video0',
            'enumerate_controls': self.fake.all, 'query': self.fake.query,
            'get_value': lambda fd, cid: self.fake.values[controls.KEYS[cid]],
            'set_value': self.fake.put,
        }
        for key, replacement in replacements.items():
            p = patch.object(controls, key, replacement)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(controls.os, 'open', return_value=42)
        p.start(); self.addCleanup(p.stop)
        p = patch.object(controls.os, 'close')
        self.close = p.start(); self.addCleanup(p.stop)

    def test_invalid_batch_never_writes(self):
        for settings in ({'brightness': 10, 'focus_absolute': 999},
                         {'brightness': 10, 'exposure_auto': 2},
                         {'brightness': 1.0}, {'brightness': True}, {'zoom': 2}, {}):
            with self.subTest(settings=settings), self.assertRaises(CameraError):
                controls.set_controls(settings)
        self.assertEqual(self.fake.writes, [])

    def test_manual_mode_required_and_applied_first(self):
        with self.assertRaisesRegex(CameraError, 'focus_auto=0'):
            controls.set_controls({'focus_absolute': 200})
        controls.set_controls({'focus_absolute': 200, 'focus_auto': False})
        self.assertEqual(self.fake.writes, [('focus_auto', 0), ('focus_absolute', 200)])
        self.assertEqual(self.fake.values['focus_absolute'], 200)
        self.close.assert_called()

    def test_usb_failure_rolls_back_all_changes_and_modes(self):
        original = self.fake.values.copy()
        self.fake.fail_once = 'brightness'
        with self.assertRaisesRegex(CameraError, 'Previous settings restored'):
            controls.set_controls({'focus_absolute': 200, 'focus_auto': 0, 'brightness': 10})
        self.assertEqual(self.fake.values, original)

    def test_readback_mismatch_rolls_back(self):
        real_put = self.fake.put
        def clamping_put(fd, cid, value):
            real_put(fd, cid, value - 1 if value == 10 else value)
        with patch.object(controls, 'set_value', clamping_put):
            with self.assertRaisesRegex(CameraError, 'camera reported 9'):
                controls.set_controls({'brightness': 10})
        self.assertEqual(self.fake.values['brightness'], 0)

    def test_incomplete_rollback_is_reported(self):
        with patch.object(controls, 'set_value', side_effect=OSError(errno.EIO, 'USB gone')):
            with self.assertRaisesRegex(CameraError, 'Rollback incomplete'):
                controls.set_controls({'brightness': 10})

    def test_reset_temporarily_disables_auto_and_restores_default_modes(self):
        controls.reset_controls()
        self.assertEqual(self.fake.values, {'brightness': 0, 'focus_auto': 0, 'focus_absolute': 256,
                                           'exposure_auto': 3, 'exposure_absolute': 166,
                                           'white_balance_auto': 1, 'white_balance_temperature': 4600})

    def test_schema_and_server_errors(self):
        result = server.call_tool('set_camera_controls', {'settings': {'focus_absolute': 200}})
        self.assertTrue(result['isError'])
        result = server.call_tool('list_camera_controls', {'unexpected': 1})
        self.assertTrue(result['isError'])
        result = server.call_tool('set_camera_controls', {})
        self.assertTrue(result['isError'])


class ABIEnumerationTests(unittest.TestCase):
    def test_ioctl_layout_menu_holes_and_inactive_values(self):
        cid = controls.IDS['exposure_auto']
        def ioctl(fd, request, buf):
            if request == controls.QUERYCTRL:
                requested = struct.unpack_from('I', buf)[0]
                if requested & ~controls.NEXT >= cid:
                    raise OSError(errno.EINVAL, 'end')
                struct.pack_into('II32siiiiI', buf, 0, cid, 3, b'Auto Exposure', 0, 3, 1, 3, 0)
            elif request == controls.GET:
                struct.pack_into('i', buf, 4, 3)
            elif request == controls.QUERYMENU:
                index = struct.unpack_from('I', buf, 4)[0]
                if index not in (1, 3):
                    raise OSError(errno.EINVAL, 'menu hole')
                name = b'Manual Mode' if index == 1 else b'Aperture Priority Mode'
                struct.pack_into('32s', buf, 8, name)
        with patch.object(controls.fcntl, 'ioctl', ioctl):
            result = controls.enumerate_controls(42)
        self.assertEqual(result[0]['choices'], {'1': 'Manual Mode', '3': 'Aperture Priority Mode'})
        self.assertEqual(result[0]['value'], 3)


if __name__ == '__main__':
    unittest.main()
