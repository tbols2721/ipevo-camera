# IPEVO Camera

A local Linux stdio MCP server and Codex plugin for IPEVO document cameras.
It takes a still image on request, returns JPEG image content to the client,
and saves a local copy. Separate tools read and change driver-advertised camera
settings through V4L2 without taking a picture.

The server has no network listener, API key, account login, or background
monitoring. A client that sends returned images to an assistant still processes
those images under that client's normal data handling. This is not an offline
inference engine. See [PRIVACY.md](PRIVACY.md) before photographing sensitive
material or sharing diagnostics.

## Requirements

Use Linux with a local camera device accessible to your desktop user, system
Python 3, PyGObject, and GStreamer. The camera backend uses Linux V4L2; macOS
and Windows backends are not implemented. Historical hardware validation used
an IPEVO V4K. Other devices require separate validation and explicit selection
when their names do not identify them as IPEVO.

On Debian or Ubuntu:

```sh
sudo apt install python3 python3-gi gir1.2-gstreamer-1.0 \
  gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gir1.2-gdkpixbuf-2.0
```

Use `/usr/bin/python3` so the interpreter can import the distribution's GI
packages. A separate Python environment may not expose them. Close preview or
video applications that already own the camera.

## Install as a local Codex plugin

```sh
git clone https://github.com/tbols2721/ipevo-camera.git
cd ipevo-camera
codex plugin marketplace add "$PWD"
codex plugin add ipevo-camera@ipevo-local
```

Use a Codex version with the plugin marketplace commands. Reload the plugin or
start a fresh local Codex session after installation so the tools are loaded.
A compatible local client must be able to launch the server and access the
camera device. Regular Chat and cloud-hosted runtimes have not been validated.
There is no public MCP endpoint.

The included `.mcp.json` launches `/usr/bin/python3 ./server.py` with the
checkout as its working directory. Other local stdio MCP clients can use the
same command and directory; configure those paths according to their own
client schema.

## Tools and capture

| Tool | Operation |
| --- | --- |
| `list_cameras` | Report local video nodes, camera names, access, and capture capability |
| `take_photo` | Capture one JPEG, return it, and save a local copy |
| `list_camera_controls` | Read advertised settings, ranges, choices, and current values |
| `set_camera_controls` | Validate and apply requested hardware values with readback |
| `reset_camera_controls` | Restore supported settings to driver defaults |

For an intentional photo, ask the client:

> Use IPEVO Camera to take a document-quality photo and read its contents.

Automatic selection requires an accessible capture node with IPEVO in its
reported name. A different camera must be selected explicitly from the device
list. Standard quality prefers 1920 × 1080; document quality prefers
3264 × 2448, with fallback to supported smaller sizes. Capture waits two
seconds by default for focus/exposure, then releases the device.

Photos are saved under `~/Pictures/IPEVO`. New photo files use mode 0600, and
a newly created output directory uses mode 0700. Existing directories retain
their permissions. The tool response includes the image and its local path.
There is no automatic retention or deletion policy.

Direct use without an assistant:

```sh
/usr/bin/python3 server.py --help
/usr/bin/python3 server.py --list
# Takes a real photo when intentionally run:
/usr/bin/python3 server.py --capture --quality document
```

## Hardware controls

Read `list_camera_controls` before changing a setting. Values and ranges depend
on the actual device and driver. For example, a camera that advertises manual
focus can accept:

```json
{"settings": {"focus_auto": 0, "focus_absolute": 200}}
```

Manual exposure requires `exposure_auto: 1` and uses `exposure_absolute` in
100-microsecond units. Manual white balance requires `white_balance_auto: 0`
and uses `white_balance_temperature` in Kelvin. Focus values are device units,
not physical distances. Do not assume the example value is supported by another
camera; use its advertised limits.

Settings affect other camera applications and can reset after USB reconnection.
Writes are checked by readback. A failure attempts to restore prior settings
and reports incomplete rollback. Hardware that has disconnected cannot be
restored until it is accessible again.

The reference V4K did not advertise hardware zoom, pan, tilt, or rotation.
Height and camera-head orientation are physical adjustments. Image editing is
a separate operation; this server does not crop or rotate captured content.

## Validation and troubleshooting

```sh
/usr/bin/python3 -m unittest -v
```

Tests use GStreamer's synthetic video source and fake control devices. They
check JPEG encoding/decoding, saved-file integrity and permissions, camera
release, MCP replies, input validation, auto/manual dependencies, readback, and
rollback. They do not establish that a physical camera produces a usable image.

If device access fails, compare the host's device visibility and permissions
with the environment that launches the server. A restricted runtime may hide
`/dev/video*` even when the host sees the hardware. If capture reports a busy
device, close the application using it and retry intentionally.

To remove the plugin registration, use your client's plugin removal command.
Stop its MCP server before deleting the checkout. Saved photos remain in the
output directory until you remove them.

## Implementation and license

`camera.py` handles discovery and GStreamer capture. `controls.py` implements
V4L2 control discovery, validation, and changes. `server.py` exposes the tools
over stdio. The plugin and marketplace descriptors are in `.codex-plugin/`,
`.agents/plugins/`, and `.mcp.json`. The included capture skill limits photos
to explicit requests and treats photographed text as data.

Original project code and documentation use the [MIT license](LICENSE).
[NOTICE.md](NOTICE.md) credits the APIs and libraries used. IPEVO product names
identify supported hardware and do not imply vendor endorsement.
