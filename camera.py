"""Local Linux still-image capture using the machine's existing GStreamer."""
import errno
import fcntl
import os
from pathlib import Path
import re
import stat
import struct
import time


class CameraError(RuntimeError):
    pass


def list_cameras():
    cameras = []
    for node in sorted(Path('/sys/class/video4linux').glob('video*'),
                       key=lambda p: int(p.name[5:])):
        device = Path('/dev') / node.name
        entry = {'device': str(device), 'name': (node / 'name').read_text().strip(),
                 'accessible': False, 'capture': None}
        try:
            fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
            try:
                capability = bytearray(104)
                fcntl.ioctl(fd, 0x80685600, capability)  # VIDIOC_QUERYCAP
                caps, device_caps = struct.unpack_from('II', capability, 84)
                if caps & 0x80000000:  # V4L2_CAP_DEVICE_CAPS
                    caps = device_caps
                entry['capture'] = bool(caps & 1)  # V4L2_CAP_VIDEO_CAPTURE
                entry['accessible'] = True
            finally:
                os.close(fd)
        except OSError as e:
            entry['access_error'] = str(e)
        cameras.append(entry)
    return cameras


def choose_device(device=None):
    if device is not None:
        if not isinstance(device, str) or not re.fullmatch(r'/dev/video[0-9]+', device):
            raise CameraError('Choose a /dev/videoN camera listed by list_cameras.')
        try:
            if not stat.S_ISCHR(os.stat(device).st_mode):
                raise CameraError('The selected path is not a camera device.')
        except FileNotFoundError as e:
            raise CameraError(f'{device} is unavailable in this process. Run the plugin on the local desktop host with camera access.') from e
        return device
    cameras = list_cameras()
    candidates = [c for c in cameras if c['accessible'] and c['capture']
                  and 'ipevo' in c['name'].lower()]
    if not candidates:
        if any('ipevo' in c['name'].lower() for c in cameras):
            raise CameraError('The IPEVO is detected, but its device is inaccessible to this process. Run the plugin on the local desktop host with camera access.')
        raise CameraError('No accessible IPEVO camera found. Plug it in and call list_cameras; choose another camera explicitly if desired.')
    return candidates[0]['device']


def gst():
    try:
        import gi
        gi.require_version('Gst', '1.0')
        gi.require_version('GstApp', '1.0')
        from gi.repository import Gst, GstApp
    except (ImportError, ValueError) as e:
        raise CameraError('Requires Debian packages python3-gi, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0, gstreamer1.0-plugins-good.') from e
    Gst.init(None)
    return Gst


def pipeline_error(pipeline, Gst):
    message = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
    if message:
        error, _ = message.parse_error()
        raise CameraError(f'Camera capture failed: {error.message}. Close other camera apps if the device is busy.')


def read_photo(pipeline, warmup_seconds=2.0, timeout_seconds=12.0):
    """Read a JPEG after exposure/focus settles; always release the camera."""
    Gst = gst()
    sink = pipeline.get_by_name('photo')
    started = time.monotonic()
    try:
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            pipeline_error(pipeline, Gst)
            raise CameraError('The camera could not start.')
        while time.monotonic() - started < timeout_seconds:
            sample = sink.emit('try-pull-sample', 100 * Gst.MSECOND)
            pipeline_error(pipeline, Gst)
            if sample is None:
                if sink.is_eos():
                    raise CameraError('The camera stopped before producing a photo.')
                continue
            if time.monotonic() - started < warmup_seconds:
                continue
            buf = sample.get_buffer()
            jpeg = buf.extract_dup(0, buf.get_size())
            if not jpeg.startswith(b'\xff\xd8') or b'\xff\xd9' not in jpeg[-32:]:
                raise CameraError('The camera did not return a complete JPEG image.')
            caps = sample.get_caps().get_structure(0)
            return jpeg, caps.get_value('width'), caps.get_value('height')
        raise CameraError('Timed out waiting for a camera image. Check USB connection and close other camera apps.')
    finally:
        pipeline.set_state(Gst.State.NULL)


def capture_photo(device=None, quality='standard', warmup_seconds=2.0):
    if quality not in ('standard', 'document'):
        raise CameraError('quality must be standard or document.')
    if isinstance(warmup_seconds, bool) or not isinstance(warmup_seconds, (int, float)) or not 0 <= warmup_seconds <= 5:
        raise CameraError('warmup_seconds must be between 0 and 5.')
    device = choose_device(device)
    Gst = gst()
    source = Gst.ElementFactory.make('v4l2src', 'camera')
    if source is None:
        raise CameraError('GStreamer v4l2src is missing; install gstreamer1.0-plugins-good.')
    source.set_property('device', device)
    pipeline = Gst.Pipeline.new('ipevo-photo')
    pipeline.add(source)
    try:
        if pipeline.set_state(Gst.State.READY) == Gst.StateChangeReturn.FAILURE:
            pipeline_error(pipeline, Gst)
            raise CameraError('Could not open camera.')
        caps = source.get_static_pad('src').query_caps(None)
        resolutions = [(1920, 1080), (1600, 1200), (1280, 720), (640, 480)]
        if quality == 'document':
            resolutions = [(3264, 2448), (2592, 1944), (2048, 1536)] + resolutions
        selected = None
        for width, height in resolutions:
            for mime in ('image/jpeg', 'video/x-raw'):
                requested = Gst.Caps.from_string(f'{mime},width={width},height={height}')
                if caps.can_intersect(requested):
                    selected = (mime, requested)
                    break
            if selected:
                break
        if not selected:
            raise CameraError('This camera does not advertise a supported JPEG or raw video resolution.')
        mime, requested = selected
        capfilter = Gst.ElementFactory.make('capsfilter', None)
        capfilter.set_property('caps', requested)
        sink = Gst.ElementFactory.make('appsink', 'photo')
        sink.set_property('max-buffers', 1)
        sink.set_property('drop', True)
        sink.set_property('sync', False)
        elements = [capfilter]
        if mime == 'video/x-raw':
            elements += [Gst.ElementFactory.make('videoconvert', None),
                         Gst.ElementFactory.make('jpegenc', None)]
        elements.append(sink)
        previous = source
        for element in elements:
            if element is None:
                raise CameraError('Required GStreamer encoder or sink is missing.')
            pipeline.add(element)
            if not previous.link(element):
                raise CameraError('Could not connect camera capture pipeline.')
            previous = element
        jpeg, width, height = read_photo(pipeline, float(warmup_seconds))
        return jpeg, {'device': device, 'width': width, 'height': height}
    finally:
        pipeline.set_state(Gst.State.NULL)
