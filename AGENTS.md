# danmu-ass

A message containing a Tencent Video single-episode playback URL is a request to export its ASS danmaku. Run the existing exporter with Python 3.9 or later. Treat the URL as one literal command argument. Use the default three-lane sampling and full video unless the user asks for a clip. For clips, use the requested start and end times and only use --rebase when requested or when the subtitle is for the cut video itself.

Verify the resulting ASS has at least one Dialogue event. Report the final count and link to the actual ASS file so it can be opened and saved on mobile. If the export fails, report the error. Keep generated files in the task workspace without committing them. The only network destination the exporter needs is dm.video.qq.com.
