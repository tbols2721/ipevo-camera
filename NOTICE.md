# Credits and dependencies

This independent plugin uses [GStreamer](https://gstreamer.freedesktop.org/)
through [PyGObject](https://pygobject.gnome.org/) for local capture, and the
[Linux V4L2 API](https://docs.kernel.org/userspace-api/media/v4l/v4l2.html) for
camera capabilities and hardware controls. These dependencies are installed
separately and retain their own licenses; their source is not vendored here.

The JSON-RPC tool interface follows the
[Model Context Protocol](https://modelcontextprotocol.io/). The project includes
a minimal server implementation rather than a bundled third-party MCP SDK.

IPEVO identifies the V4K as a [UVC document camera](https://us.ipevo.com/pages/v4k).
The hardware control implementation follows Linux's documented
[control discovery](https://docs.kernel.org/userspace-api/media/v4l/vidioc-queryctrl.html),
[reads/writes](https://docs.kernel.org/userspace-api/media/v4l/vidioc-g-ctrl.html),
and [focus/exposure controls](https://docs.kernel.org/userspace-api/media/v4l/ext-ctrls-camera.html).

Original implementation, tests, and project documentation use [MIT](LICENSE).
This project is not affiliated with IPEVO or the dependency maintainers.
