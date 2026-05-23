<!-- version: 1 -->
# Media Tools

## CameraCapture

Capture camera snapshots or analyze frames.

| Param | Description |
|-------|-------------|
| camera_entity | Camera entity or "list" |
| mode | snapshot / analyze |
| max_dim | Max dimension (default 640) |
| target_kb | Target size KB (default 40) |

### List Cameras

```json
{"camera_entity": "list"}
```

### Snapshot

```json
{"camera_entity": "camera.front_door", "mode": "snapshot"}
```

Returns: snapshot_url, markdown_hint

### Analyze (Vision)

```json
{"camera_entity": "camera.front_door", "mode": "analyze"}
```

Returns: base64 JPEG for vision analysis

## MediaAnalyze

Analyze uploaded images/GIFs/videos.

| Param | Description |
|-------|-------------|
| file_path | Path to media file |
| max_dim | Max dimension (default 640) |
| target_kb | Target size KB (default 40) |
| timestamps | List of seconds for video frames |

### Image

```json
{"file_path": "/tmp/photo.jpg"}
```

### Video - Auto Extract

```json
{"file_path": "/tmp/video.mp4"}
```

Returns overview frames with timestamp_sec.

### Video - Specific Frames

```json
{"file_path": "/tmp/video.mp4", "timestamps": [1.5, 3.0, 5.5]}
```

## Notes

- CameraCapture for live cameras
- MediaAnalyze for uploaded files
- Describe what you see in response
