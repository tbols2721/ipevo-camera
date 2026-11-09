#!/usr/bin/python3
"""Small stdio MCP server. No listener, credentials, or network calls."""
import argparse
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import uuid

from camera import CameraError, capture_photo, list_cameras
from controls import IDS, list_controls, set_controls, reset_controls

VERSION = '0.2.1'
PROTOCOLS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
TOOLS = [
    {
        'name': 'list_cameras',
        'description': 'List locally connected video devices, identify the IPEVO, and report capture capability and access. Does not take a picture.',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    },
    {
        'name': 'take_photo',
        'description': 'Take one photo from the local IPEVO document camera when requested by the user. Returns JPEG image content for inspection and saves a copy in ~/Pictures/IPEVO. Opens the camera briefly, then releases it. No network transmission by this server.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'device': {'type': 'string', 'pattern': '^/dev/video[0-9]+$', 'description': 'Optional device from list_cameras; automatically selects the IPEVO by default.'},
                'quality': {'type': 'string', 'enum': ['standard', 'document'], 'default': 'standard'},
                'warmup_seconds': {'type': 'number', 'minimum': 0, 'maximum': 5, 'default': 2},
            },
            'additionalProperties': False,
        },
        'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False},
    },
]

DEVICE_SCHEMA = {'type': 'string', 'pattern': '^/dev/video[0-9]+$'}
for name, description in [
    ('list_camera_controls', 'Read camera settings, supported ranges, menu choices, and automatic-mode dependencies. Does not capture an image.'),
    ('set_camera_controls', 'Change real hardware camera settings for future captures. Read list_camera_controls first. Manual focus requires focus_auto=0; manual exposure requires exposure_auto=1; manual white balance requires white_balance_auto=0. Values are checked and read back; failure attempts rollback. Does not capture an image.'),
    ('reset_camera_controls', 'Reset supported hardware camera settings to driver-advertised defaults, including focus, exposure, and white balance modes. Does not capture an image.'),
]:
    properties = {'device': DEVICE_SCHEMA}
    required = []
    if name == 'set_camera_controls':
        properties['settings'] = {'type': 'object', 'minProperties': 1,
                                 'properties': {k: {'type': ['integer', 'boolean']} for k in IDS},
                                 'additionalProperties': False}
        required = ['settings']
    TOOLS.append({'name': name, 'description': description,
                  'inputSchema': {'type': 'object', 'properties': properties,
                                  'required': required, 'additionalProperties': False},
                  'annotations': {'readOnlyHint': name == 'list_camera_controls',
                                  'destructiveHint': False, 'idempotentHint': True,
                                  'openWorldHint': False}})


def text_result(value, error=False):
    return {'content': [{'type': 'text', 'text': json.dumps(value)}], 'isError': error}


def photo_result(jpeg, metadata, output_dir=None):
    if len(jpeg) > 10 * 1024 * 1024:
        raise CameraError('Photo exceeds 10 MiB. Try standard quality.')
    directory = output_dir or Path.home() / 'Pictures' / 'IPEVO'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f'ipevo-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.jpg'
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(jpeg)
    metadata = {**metadata, 'path': str(path), 'bytes': len(jpeg)}
    return {'content': [
        {'type': 'text', 'text': json.dumps(metadata)},
        {'type': 'image', 'mimeType': 'image/jpeg', 'data': base64.b64encode(jpeg).decode('ascii')},
    ], 'isError': False}


def call_tool(name, arguments):
    if not isinstance(arguments, dict):
        return text_result({'error': 'Tool arguments must be an object.'}, True)
    try:
        if name == 'list_cameras':
            if arguments:
                raise CameraError('list_cameras does not accept arguments.')
            return text_result({'cameras': list_cameras()})
        if name == 'take_photo':
            if set(arguments) - {'device', 'quality', 'warmup_seconds'}:
                raise CameraError('Unknown take_photo argument.')
            jpeg, metadata = capture_photo(**arguments)
            return photo_result(jpeg, metadata)
        if name in ('list_camera_controls', 'set_camera_controls', 'reset_camera_controls'):
            allowed = {'device', 'settings'} if name == 'set_camera_controls' else {'device'}
            if set(arguments) - allowed:
                raise CameraError('Unknown camera-control argument.')
            operation = {'list_camera_controls': list_controls,
                         'set_camera_controls': set_controls,
                         'reset_camera_controls': reset_controls}[name]
            return text_result(operation(**arguments))
        return text_result({'error': f'Unknown tool: {name}'}, True)
    except (CameraError, OSError, TypeError) as e:
        return text_result({'error': str(e)}, True)


def handle(request):
    if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid request'}}
    method = request.get('method')
    if 'id' not in request:
        return None
    response = {'jsonrpc': '2.0', 'id': request['id']}
    params = request.get('params') or {}
    if not isinstance(params, dict):
        response['error'] = {'code': -32602, 'message': 'params must be an object'}
        return response
    if method == 'initialize':
        protocol = params.get('protocolVersion')
        response['result'] = {
            'protocolVersion': protocol if protocol in PROTOCOLS else PROTOCOLS[0],
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'ipevo-camera', 'version': VERSION},
            'instructions': 'Capture only on user request. take_photo returns an actual image and its saved path. Read list_camera_controls before changing requested hardware settings. Control tools do not capture images. Close other camera preview apps if the camera is busy.',
        }
    elif method == 'ping':
        response['result'] = {}
    elif method == 'tools/list':
        response['result'] = {'tools': TOOLS}
    elif method == 'tools/call':
        response['result'] = call_tool(params.get('name'), params.get('arguments', {}))
    else:
        response['error'] = {'code': -32601, 'message': f'Unknown method: {method}'}
    return response


def serve():
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            response = handle(request)
        except json.JSONDecodeError:
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        except Exception as e:
            print(f'IPEVO server error: {e}', file=sys.stderr)
            request_id = request.get('id') if isinstance(request, dict) else None
            response = {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32603, 'message': 'Internal error'}}
        if response is not None:
            print(json.dumps(response, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--list', action='store_true', help='List cameras without starting MCP')
    parser.add_argument('--capture', action='store_true', help='Save a photo and print its path')
    parser.add_argument('--quality', choices=['standard', 'document'], default='standard')
    args = parser.parse_args()
    if args.list:
        print(json.dumps(list_cameras(), indent=2))
    elif args.capture:
        try:
            jpeg, metadata = capture_photo(quality=args.quality)
            result = photo_result(jpeg, metadata)
            print(result['content'][0]['text'])
        except (CameraError, OSError) as e:
            parser.exit(1, f'{e}\n')
    else:
        serve()
