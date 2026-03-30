import asyncio
import os
import re
import tempfile
import uuid
from pathlib import Path

from fasthtml.common import Button, Div, Form, H1, Input, Main, P, Script, fast_app
from starlette.requests import Request
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, JSONResponse

app, rt = fast_app()


def _safe_basename(filename: str) -> str:
    stem = Path(filename or "video").stem
    cleaned = re.sub(r"[^a-zA-Z0-9-_]", "_", stem)
    return cleaned or "video"


async def _run_ffmpeg(input_path: str, output_path: str, list_path: str) -> None:
    escaped_input = input_path.replace("'", "'\\''")
    concat_list = (f"file '{escaped_input}'\n") * 4

    with open(list_path, "w", encoding="utf-8") as file_list:
        file_list.write(concat_list)

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_path,
        "-movflags",
        "+faststart",
        "-c",
        "copy",
        output_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exited with {proc.returncode}: {stderr.decode('utf-8', errors='ignore')}")


@rt("/")
def home():
    return Main(
        H1("Loop a video 4 times"),
        P("Upload a video (usually MP4), then download the looped result."),
        Form(
            Input(type="file", id="video", name="video", accept="video/*", required=True),
            Button("Generate looped video", id="submit", type="submit"),
            id="form",
        ),
        Div(id="status"),
        Div(id="result", style="margin-top: 1rem;"),
        P("Tip: on iPhone, open the downloaded file and tap Share → Save Video."),
        Script(
            """
const form = document.getElementById('form');
const input = document.getElementById('video');
const submit = document.getElementById('submit');
const status = document.getElementById('status');
const result = document.getElementById('result');
let currentObjectUrl = null;

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!input.files.length) {
    status.textContent = 'Choose a video first.';
    return;
  }

  submit.disabled = true;
  status.textContent = 'Processing... this can take a moment.';
  result.innerHTML = '';

  const body = new FormData();
  body.append('video', input.files[0]);

  try {
    const response = await fetch('/loop', { method: 'POST', body });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || 'Upload failed.');
    }

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const baseName = (input.files[0].name || 'video').replace(/\.[^.]+$/, '');
    const fileName = baseName + '-looped-x4.mp4';

    if (currentObjectUrl) {
      URL.revokeObjectURL(currentObjectUrl);
    }
    currentObjectUrl = url;

    const video = document.createElement('video');
    video.controls = true;
    video.src = url;
    video.style.display = 'block';
    video.style.maxWidth = '100%';
    video.style.marginTop = '0.5rem';

    const downloadButton = document.createElement('a');
    downloadButton.href = url;
    downloadButton.download = fileName;
    downloadButton.textContent = 'Download looped video';
    downloadButton.style.display = 'inline-block';
    downloadButton.style.marginTop = '0.75rem';
    downloadButton.style.padding = '0.5rem 0.75rem';
    downloadButton.style.border = '1px solid #ccc';
    downloadButton.style.borderRadius = '6px';
    downloadButton.style.textDecoration = 'none';

    result.appendChild(video);
    result.appendChild(downloadButton);

    status.textContent = 'Done! Preview your looped video below or download it.';
  } catch (error) {
    status.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

window.addEventListener('beforeunload', () => {
  if (currentObjectUrl) {
    URL.revokeObjectURL(currentObjectUrl);
  }
});
            """
        ),
        style="padding: 1.25rem; max-width: 48rem; margin: 0 auto;",
    )


@rt("/health")
def health():
    return JSONResponse({"ok": True})


@rt("/loop", methods=["POST"])
async def loop_video(request: Request):
    form = await request.form()
    upload = form.get("video")
    if upload is None:
        return JSONResponse({"error": "Please upload a video file."}, status_code=400)

    input_suffix = Path(getattr(upload, "filename", "video.mp4") or "video.mp4").suffix or ".mp4"
    input_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}{input_suffix}")
    list_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}-concat.txt")
    output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}-looped.mp4")
    download_name = f"{_safe_basename(getattr(upload, 'filename', 'video'))}-looped-x4.mp4"

    def cleanup(*paths: str) -> None:
        for path in paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

    try:
        content = await upload.read()
        with open(input_path, "wb") as f:
            f.write(content)

        await _run_ffmpeg(input_path, output_path, list_path)

        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename=download_name,
            background=BackgroundTask(cleanup, input_path, list_path, output_path),
        )
    except Exception as exc:
        cleanup(input_path, list_path, output_path)
        return JSONResponse(
            {
                "error": "Failed to process video. Confirm ffmpeg is installed and the file is valid.",
                "details": str(exc),
            },
            status_code=500,
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "3000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
