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


def _concat_sequence(input_paths: list[str]) -> list[str]:
    count = len(input_paths)
    if count == 1:
        return input_paths * 4
    if count == 2:
        return [input_paths[0], input_paths[1], input_paths[0], input_paths[1]]
    return input_paths


async def _run_ffmpeg(input_paths: list[str], output_path: str, list_path: str) -> None:
    sequence = _concat_sequence(input_paths)
    concat_list = ""
    for input_path in sequence:
        escaped_input = input_path.replace("'", "'\\''")
        concat_list += f"file '{escaped_input}'\n"

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
        H1("Build a stitched loop from up to 4 videos"),
        P("Upload 1 to 4 videos, then download the stitched result."),
        Form(
            Input(type="file", id="video1", name="video1", accept="video/*", required=True, multiple=True),
            Input(type="file", id="video2", name="video2", accept="video/*", multiple=True),
            Input(type="file", id="video3", name="video3", accept="video/*", multiple=True),
            Input(type="file", id="video4", name="video4", accept="video/*", multiple=True),
            Button("Generate stitched video", id="submit", type="submit"),
            id="form",
            style="display: grid; gap: 0.75rem;",
        ),
        Div(id="status"),
        Div(id="result", style="margin-top: 1rem;"),
        P("Tip: on iPhone, open the downloaded file and tap Share → Save Video."),
        Script(
            """
const form = document.getElementById('form');
const inputs = [
  document.getElementById('video1'),
  document.getElementById('video2'),
  document.getElementById('video3'),
  document.getElementById('video4')
];
const submit = document.getElementById('submit');
const status = document.getElementById('status');
const result = document.getElementById('result');
let currentObjectUrl = null;

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const selectedFiles = inputs
    .flatMap((input) => Array.from(input.files || []))
    .slice(0, 4);

  if (!selectedFiles.length) {
    status.textContent = 'Choose at least one video first.';
    return;
  }
  if (selectedFiles.length === 4 && inputs.some((input) => (input.files || []).length > 1)) {
    status.textContent = 'Using the first 4 selected videos.';
  }

  submit.disabled = true;
  status.textContent = 'Processing... this can take a moment.';
  result.innerHTML = '';

  const body = new FormData();
  selectedFiles.forEach((file, index) => {
    body.append(`video${index + 1}`, file);
  });

  try {
    const response = await fetch('/loop', { method: 'POST', body });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || 'Upload failed.');
    }

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const baseName = (selectedFiles[0].name || 'video').replace(/\.[^.]+$/, '');
    const fileName = baseName + '-stitched.mp4';
    const file = new File([blob], fileName, { type: 'video/mp4' });

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
    downloadButton.textContent = 'Download stitched video';
    downloadButton.style.display = 'inline-block';
    downloadButton.style.marginTop = '0.75rem';
    downloadButton.style.padding = '0.5rem 0.75rem';
    downloadButton.style.border = '1px solid #ccc';
    downloadButton.style.borderRadius = '6px';
    downloadButton.style.textDecoration = 'none';

    const shareButton = document.createElement('button');
    shareButton.type = 'button';
    shareButton.textContent = 'Share video';
    shareButton.style.display = 'inline-block';
    shareButton.style.marginTop = '0.75rem';
    shareButton.style.marginLeft = '0.5rem';
    shareButton.style.padding = '0.5rem 0.75rem';
    shareButton.style.border = '1px solid #ccc';
    shareButton.style.borderRadius = '6px';
    shareButton.style.background = '#fff';
    shareButton.style.cursor = 'pointer';

    shareButton.addEventListener('click', async () => {
      const canShareFile = navigator.canShare && navigator.canShare({ files: [file] });
      if (!canShareFile) {
        status.textContent = 'Sharing is not supported here. Downloading instead.';
        downloadButton.click();
        return;
      }

      try {
        await navigator.share({ files: [file], title: fileName });
        status.textContent = 'Share sheet opened.';
      } catch (error) {
        status.textContent = 'Could not share. Downloading instead.';
        downloadButton.click();
      }
    });

    result.appendChild(video);
    result.appendChild(downloadButton);
    result.appendChild(shareButton);

    status.textContent = 'Done! Preview your stitched video below or download it.';
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
    uploads = []
    for i in range(1, 5):
        for upload in form.getlist(f"video{i}"):
            if upload and getattr(upload, "filename", ""):
                uploads.append(upload)
                if len(uploads) == 4:
                    break
        if len(uploads) == 4:
            break
    if not uploads:
        return JSONResponse({"error": "Please upload at least one video file."}, status_code=400)

    input_paths: list[str] = []
    first_upload_name = getattr(uploads[0], "filename", "video")
    list_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}-concat.txt")
    output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}-looped.mp4")
    download_name = f"{_safe_basename(first_upload_name)}-stitched.mp4"

    def cleanup(*paths: str) -> None:
        for path in paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

    try:
        for upload in uploads:
            input_suffix = Path(getattr(upload, "filename", "video.mp4") or "video.mp4").suffix or ".mp4"
            input_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}{input_suffix}")
            content = await upload.read()
            with open(input_path, "wb") as f:
                f.write(content)
            input_paths.append(input_path)

        await _run_ffmpeg(input_paths, output_path, list_path)

        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename=download_name,
            background=BackgroundTask(cleanup, *input_paths, list_path, output_path),
        )
    except Exception as exc:
        cleanup(*input_paths, list_path, output_path)
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
