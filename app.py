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


async def _run_ffmpeg(input_paths: list[str], output_path: str, list_path: str) -> None:
    concat_list = ""
    for input_path in input_paths:
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
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
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
        H1("Stitch 4 videos together"),
        P("Upload exactly 4 videos (in order), then download the stitched result."),
        Form(
            Input(
                type="file",
                id="videos",
                name="videos",
                accept="video/*",
                multiple=True,
                required=True,
            ),
            Button("Generate stitched video", id="submit", type="submit"),
            id="form",
        ),
        Div(id="status"),
        P("Tip: on iPhone, open the downloaded file and tap Share → Save Video."),
        Script(
            """
const form = document.getElementById('form');
const input = document.getElementById('videos');
const submit = document.getElementById('submit');
const status = document.getElementById('status');

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (input.files.length !== 4) {
    status.textContent = 'Please choose exactly 4 videos.';
    return;
  }

  submit.disabled = true;
  status.textContent = 'Processing... this can take a moment.';

  const body = new FormData();
  for (const file of input.files) {
    body.append('videos', file);
  }

  try {
    const response = await fetch('/stitch', { method: 'POST', body });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || 'Upload failed.');
    }

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    const fileName = `stitched-${Date.now()}.mp4`;
    anchor.href = url;
    anchor.download = fileName;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);

    status.textContent = 'Done! Your stitched video has been downloaded.';
  } catch (error) {
    status.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});
            """
        ),
    )


@rt("/health")
def health():
    return JSONResponse({"ok": True})


@rt("/stitch", methods=["POST"])
async def stitch_videos(request: Request):
    form = await request.form()
    uploads = [upload for upload in form.getlist("videos") if upload and getattr(upload, "filename", "")]
    if len(uploads) != 4:
        return JSONResponse({"error": "Please upload exactly 4 video files."}, status_code=400)

    input_paths: list[str] = []
    list_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}-concat.txt")
    output_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}-stitched.mp4")
    first_name = getattr(uploads[0], "filename", "video")
    download_name = f"{_safe_basename(first_name)}-stitched.mp4"

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
