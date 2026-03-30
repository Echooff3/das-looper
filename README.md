# das-looper

Simple stateless FastHTML app for Railway that loops an uploaded video 4 times with FFmpeg and returns a downloadable MP4.

## Features

- Upload video from browser
- FFmpeg concatenates the same input 4x
- Returns a downloadable `*-looped-x4.mp4`
- Stateless: all files stored temporarily in `/tmp` and removed after response

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:3000`.

## Deploy to Railway

This repo includes a `Dockerfile` with FFmpeg preinstalled.

1. Push this repo to GitHub.
2. In Railway, create a new project from the GitHub repo.
3. Railway will build with the provided `Dockerfile`.
4. App binds to `PORT` automatically.

## API

### `POST /loop`

Multipart form field:

- `video` — video file

Response: file download (`video/mp4`).

### `GET /health`

Returns JSON:

```json
{ "ok": true }
```
