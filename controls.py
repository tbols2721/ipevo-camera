"""Read and change advertised Linux V4L2 controls; never starts a video stream."""
from contextlib import contextmanager
import errno
import fcntl
import os
import struct

from camera import CameraError, choose_device

# ioctl numbers/layouts from linux/videodev2.h (Linux native ABI).
QUERYCTRL = 0xc0445624
QUERYMENU = 0xc02c5625
GET = 0xc008561b
SET = 0xc008561c
NEXT = 0x80000000
DISABLED, GRABBED, READ_ONLY, INACTIVE = 1, 2, 4, 16
IDS = {
    'brightness': 0x980900, 'contrast': 0x980901,
    'saturation': 0x980902, 'hue': 0x980903,
    'white_balance_auto': 0x98090c, 'gamma': 0x980910,
    'power_line_frequency': 0x980918,
    'white_balance_temperature': 0x98091a, 'sharpness': 0x98091b,
    'backlight_compensation': 0x98091c,
    'exposure_auto': 0x9a0901, 'exposure_absolute': 0x9a0902,
    'focus_absolute': 0x9a090a, 'focus_auto': 0x9a090c,
}
KEYS = {v: k for k, v in IDS.items()}
DEPENDENCIES = {
    'focus_absolute': ('focus_auto', 0),
    'exposure_absolute': ('exposure_auto', 1),
    'white_balance_temperature': ('white_balance_auto', 0),
}


@contextmanager
def opened(device=None, write=False):
    device = choose_device(device)
    fd = os.open(device, (os.O_RDWR if write else os.O_RDONLY) | os.O_NONBLOCK)
    try:
        yield device, fd
    finally:
        os.close(fd)


def query(fd, cid):
    buf = bytearray(68)
    struct.pack_into('I', buf, 0, cid)
    fcntl.ioctl(fd, QUERYCTRL, buf)
    cid, typ, name, lo, hi, step, default, flags = struct.unpack_from('II32siiiiI', buf)
    return {'id': cid, 'key': KEYS.get(cid, f'control_{cid:08x}'),
            'name': name.split(b'\0')[0].decode('ascii', 'replace'),
            'type': typ, 'minimum': lo, 'maximum': hi, 'step': step,
            'default': default, 'flags': flags}


def get_value(fd, cid):
    buf = bytearray(struct.pack('Ii', cid, 0))
    fcntl.ioctl(fd, GET, buf)
    return struct.unpack('Ii', buf)[1]


def set_value(fd, cid, value):
    buf = bytearray(struct.pack('Ii', cid, value))
    fcntl.ioctl(fd, SET, buf)


def enumerate_controls(fd):
    result, last = [], 0
    for _ in range(1024):
        try:
            control = query(fd, last | NEXT)
        except OSError as e:
            if e.errno == errno.EINVAL:
                return result
            raise
        if control['id'] <= last:
            raise CameraError('The camera returned an invalid control enumeration.')
        last = control['id']
        if control['type'] == 6 or control['flags'] & DISABLED:
            continue
        control['supported_for_setting'] = control['id'] in KEYS and control['type'] in (1, 2, 3)
        control['inactive'] = bool(control['flags'] & INACTIVE)
        control['writable'] = control['supported_for_setting'] and not control['flags'] & (READ_ONLY | GRABBED | INACTIVE)
        if control['key'] in DEPENDENCIES:
            parent, value = DEPENDENCIES[control['key']]
            control['requires'] = {parent: value}
        try:
            control['value'] = get_value(fd, last)
        except OSError as e:
            control['read_error'] = str(e)
        if control['type'] == 3:
            choices = {}
            for index in range(control['minimum'], control['maximum'] + 1):
                buf = bytearray(44)
                struct.pack_into('II', buf, 0, last, index)
                try:
                    fcntl.ioctl(fd, QUERYMENU, buf)
                except OSError as e:
                    if e.errno == errno.EINVAL:
                        continue
                    raise
                choices[str(index)] = bytes(buf[8:40]).split(b'\0')[0].decode('ascii', 'replace')
            control['choices'] = choices
        result.append(control)
    raise CameraError('Too many camera controls.')


def list_controls(device=None):
    with opened(device) as (device, fd):
        return {'device': device, 'controls': enumerate_controls(fd),
                'note': 'Settings affect future captures and other camera apps; USB reconnection may reset them. Exposure units are 100 microseconds. Focus units are device-specific.'}


def validate(control, value):
    # Accept JSON booleans only for boolean controls, never floats or strings.
    if isinstance(value, bool):
        if control['type'] != 2:
            raise CameraError(f"{control['key']} requires an integer.")
        value = int(value)
    if not isinstance(value, int):
        raise CameraError(f"{control['key']} requires an integer.")
    if not control['supported_for_setting'] or control['flags'] & (DISABLED | READ_ONLY | GRABBED):
        raise CameraError(f"{control['key']} is not writable.")
    lo, hi, step = control['minimum'], control['maximum'], control['step']
    if not lo <= value <= hi or (step > 0 and (value - lo) % step):
        raise CameraError(f"{control['key']} must be {lo}..{hi}, step {step}.")
    if control['type'] == 3 and str(value) not in control['choices']:
        raise CameraError(f"{control['key']} must be one of {control['choices']}.")
    return value


def restore(fd, before, affected):
    """Best effort rollback including manual values before automatic modes."""
    errors = []
    def put(key, value):
        try:
            set_value(fd, before[key]['id'], value)
        except (OSError, CameraError) as e:
            errors.append(f'{key}: {e}')
    for key in affected:
        if key in DEPENDENCIES:
            parent, manual = DEPENDENCIES[key]
            if parent in before:
                put(parent, manual)
    parents = {p for p, _ in DEPENDENCIES.values()}
    for key in affected:
        if key not in parents and 'value' in before[key]:
            put(key, before[key]['value'])
    for key in affected | {DEPENDENCIES[k][0] for k in affected if k in DEPENDENCIES}:
        if key in parents and key in before and 'value' in before[key]:
            put(key, before[key]['value'])
    return errors


def apply(fd, before, operations):
    affected = set()
    try:
        for key, value in operations:
            control = query(fd, before[key]['id'])
            if control['flags'] & (DISABLED | READ_ONLY | GRABBED | INACTIVE):
                raise CameraError(f'{key} is currently inactive or locked; disable its automatic mode first.')
            affected.add(key)
            set_value(fd, control['id'], value)
            actual = get_value(fd, control['id'])
            if actual != value:
                raise CameraError(f'{key}: requested {value}, camera reported {actual}.')
        return enumerate_controls(fd)
    except (OSError, CameraError) as e:
        errors = restore(fd, before, affected)
        status = 'Previous settings restored.' if not errors else 'Rollback incomplete: ' + '; '.join(errors)
        raise CameraError(f'Camera settings failed: {e}. {status}') from e


def set_controls(settings, device=None):
    if not isinstance(settings, dict) or not settings:
        raise CameraError('settings must be a nonempty object of control keys and values.')
    if set(settings) - IDS.keys():
        raise CameraError('Unknown controls: ' + ', '.join(sorted(set(settings) - IDS.keys())))
    with opened(device, write=True) as (device, fd):
        before = {c['key']: c for c in enumerate_controls(fd)}
        normalized = {}
        for key, value in settings.items():
            if key not in before or 'value' not in before[key]:
                raise CameraError(f'{key} is unavailable or cannot be read on this camera.')
            normalized[key] = validate(before[key], value)
        for key in normalized:
            if key in DEPENDENCIES:
                parent, manual = DEPENDENCIES[key]
                current = before.get(parent, {}).get('value')
                if normalized.get(parent, current) != manual:
                    raise CameraError(f'Include {parent}={manual} to set {key}.')
            elif before[key]['inactive']:
                raise CameraError(f'{key} is inactive.')
        parents = {p for p, _ in DEPENDENCIES.values()}
        operations = sorted(normalized.items(), key=lambda item: item[0] not in parents)
        after = apply(fd, before, operations)
        return {'device': device, 'applied': normalized, 'controls': after}


def reset_controls(device=None):
    with opened(device, write=True) as (device, fd):
        before = {c['key']: c for c in enumerate_controls(fd)}
        defaults = {key: validate(c, c['default']) for key, c in before.items()
                    if c['supported_for_setting'] and 'value' in c and not c['flags'] & (READ_ONLY | GRABBED)}
        operations = []
        for key in defaults:
            if key in DEPENDENCIES:
                parent, manual = DEPENDENCIES[key]
                if parent not in defaults:
                    raise CameraError(f'Cannot reset {key}: {parent} unavailable.')
                operations.append((parent, validate(before[parent], manual)))
        parents = {p for p, _ in DEPENDENCIES.values()}
        operations += [(k, v) for k, v in defaults.items() if k not in parents]
        operations += [(k, v) for k, v in defaults.items() if k in parents]
        after = apply(fd, before, operations)
        return {'device': device, 'reset_to_defaults': defaults, 'controls': after}
