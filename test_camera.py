import base64
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import camera
import server


class LocalCameraTests(unittest.TestCase):
    def test_real_gstreamer_jpeg_pipeline_and_mcp_image(self):
        Gst = camera.gst()
        pipeline = Gst.parse_launch(
            'videotestsrc is-live=true pattern=ball ! '
            'video/x-raw,width=640,height=480 ! jpegenc ! '
            'appsink name=photo max-buffers=1 drop=true sync=false')
        jpeg, width, height = camera.read_photo(pipeline, warmup_seconds=0.15)
        self.assertEqual((width, height), (640, 480))
        self.assertEqual(pipeline.get_state(Gst.SECOND)[1], Gst.State.NULL)
        with tempfile.TemporaryDirectory() as directory:
            result = server.photo_result(jpeg, {'width': width, 'height': height}, Path(directory))
            metadata = json.loads(result['content'][0]['text'])
            self.assertEqual(Path(metadata['path']).read_bytes(), jpeg)
            self.assertEqual(base64.b64decode(result['content'][1]['data']), jpeg)
            self.assertEqual(result['content'][1]['mimeType'], 'image/jpeg')
            import gi
            gi.require_version('GdkPixbuf', '2.0')
            from gi.repository import GdkPixbuf
            decoded = GdkPixbuf.Pixbuf.new_from_file(metadata['path'])
            self.assertEqual((decoded.get_width(), decoded.get_height()), (640, 480))

    def test_saved_photo_is_private_with_a_permissive_umask(self):
        # A synthetic byte string is sufficient for file-creation permissions.
        with tempfile.TemporaryDirectory() as directory:
            previous = os.umask(0)
            try:
                output = Path(directory) / 'photos'
                result = server.photo_result(b'synthetic-test-data', {}, output)
            finally:
                os.umask(previous)
            metadata = json.loads(result['content'][0]['text'])
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(Path(metadata['path']).stat().st_mode), 0o600)

    def test_stdio_handshake_and_recovery_after_invalid_json(self):
        requests = [
            '{bad json}',
            json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}}),
            json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}),
            json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}),
            json.dumps({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'list_cameras', 'arguments': {}}}),
            json.dumps({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'take_photo', 'arguments': {'device': '/etc/passwd'}}}),
        ]
        process = subprocess.run([sys.executable, 'server.py'], input='\n'.join(requests)+'\n', text=True, capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(replies), 5)
        self.assertEqual(replies[0]['error']['code'], -32700)
        self.assertEqual(replies[1]['result']['protocolVersion'], '2025-06-18')
        self.assertEqual({t['name'] for t in replies[2]['result']['tools']}, {
            'list_cameras', 'take_photo', 'list_camera_controls',
            'set_camera_controls', 'reset_camera_controls'})
        self.assertFalse(replies[3]['result']['isError'])
        self.assertTrue(replies[4]['result']['isError'])

    def test_capture_rejects_invalid_inputs(self):
        for kwargs in [{'quality': 'invalid'}, {'warmup_seconds': float('nan')},
                       {'warmup_seconds': 100}, {'warmup_seconds': True},
                       {'device': '/dev/video0 ! fakesink'}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(camera.CameraError):
                camera.capture_photo(**kwargs)

    def test_no_automatic_capture_of_an_unrelated_camera(self):
        from unittest.mock import patch
        with patch.object(camera, 'list_cameras', return_value=[{'device': '/dev/video0', 'name': 'Unrelated webcam', 'accessible': True, 'capture': True}]):
            with self.assertRaises(camera.CameraError):
                camera.choose_device()

    def test_camera_released_after_capture_failure(self):
        Gst = camera.gst()
        pipeline = Gst.parse_launch('videotestsrc num-buffers=0 ! jpegenc ! appsink name=photo')
        with self.assertRaises(camera.CameraError):
            camera.read_photo(pipeline, warmup_seconds=0)
        self.assertEqual(pipeline.get_state(Gst.SECOND)[1], Gst.State.NULL)


if __name__ == '__main__':
    unittest.main()
