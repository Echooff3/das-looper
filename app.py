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


async def _run_command(*args: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exited with {proc.returncode}: {stderr.decode('utf-8', errors='ignore')}")


async def _probe_dimensions(input_path: str) -> tuple[int, int]:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        input_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe exited with {proc.returncode}: {stderr.decode('utf-8', errors='ignore')}")
    raw = stdout.decode("utf-8", errors="ignore").strip()
    width, height = raw.split("x", 1)
    return int(width), int(height)


async def _normalize_video(input_path: str, output_path: str, width: int, height: int) -> None:
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p,fps=30"
    )
    await _run_command(
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        output_path,
    )


async def _run_ffmpeg(input_paths: list[str], output_path: str, list_path: str, normalized_paths: list[str]) -> None:
    width, height = await _probe_dimensions(input_paths[0])
    for input_path in input_paths:
        normalized_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}-normalized.mp4")
        await _normalize_video(input_path, normalized_path, width, height)
        normalized_paths.append(normalized_path)

    sequence = _concat_sequence(normalized_paths)
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
let statusTicker = null;

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
  result.innerHTML = '';

  const body = new FormData();
  selectedFiles.forEach((file, index) => {
    body.append(`video${index + 1}`, file);
  });

  try {
    const blob = await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/loop');
      xhr.responseType = 'blob';

      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable) {
          status.textContent = 'Uploading videos...';
          return;
        }
        const percent = Math.round((event.loaded / event.total) * 100);
        status.textContent = `Uploading videos... ${percent}%`;
      };

      let dots = 0;
      statusTicker = setInterval(() => {
        dots = (dots + 1) % 4;
        status.textContent = `Stitching videos${'.'.repeat(dots)}`;
      }, 500);

      xhr.onload = async () => {
        if (statusTicker) {
          clearInterval(statusTicker);
          statusTicker = null;
        }

        if (xhr.status < 200 || xhr.status >= 300) {
          const text = xhr.responseText || '';
          let message = 'Upload failed.';
          try {
            const data = JSON.parse(text);
            message = data.error || message;
          } catch (e) {
            // keep default message
          }
          reject(new Error(message));
          return;
        }
        resolve(xhr.response);
      };

      xhr.onerror = () => {
        if (statusTicker) {
          clearInterval(statusTicker);
          statusTicker = null;
        }
        reject(new Error('Network error while uploading.'));
      };

      xhr.send(body);
    });

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
    shareButton.style.background = '#0b5fff';
    shareButton.style.color = '#fff';
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
    if (statusTicker) {
      clearInterval(statusTicker);
      statusTicker = null;
    }
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
    normalized_paths: list[str] = []
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

        await _run_ffmpeg(input_paths, output_path, list_path, normalized_paths)

        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename=download_name,
            background=BackgroundTask(cleanup, *input_paths, *normalized_paths, list_path, output_path),
        )
    except Exception as exc:
        cleanup(*input_paths, *normalized_paths, list_path, output_path)
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
