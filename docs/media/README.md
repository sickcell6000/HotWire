# Demo media

Drop your demo clips into this folder with these **exact filenames** and they
will appear on the site automatically — no HTML editing needed. The page does
a `HEAD` request for each file on load and swaps the placeholder for a `<video>`
player when the file exists.

| File                     | Appears as                  |
| ------------------------ | --------------------------- |
| `a1-impersonation.mp4`   | A1 — Impersonation demo     |
| `a2-discharge.mp4`       | A2 — Forced discharge demo  |

Optional poster thumbnails (shown before the clip plays):

| File             |
| ---------------- |
| `a1-poster.jpg`  |
| `a2-poster.jpg`  |

## Encoding tips
- Format: **H.264 MP4** (`.mp4`), 16:9 aspect ratio.
- Keep each clip short (~5–15 s) and small (ideally ≤ 20 MB) so the page loads fast.
- Mute the audio track (these autoplay-as-preview style clips read best silent).
- GIF works too, but change the `<source type>` in `index.html` accordingly.

## Deploy
After adding files here:

```bash
git add docs/media/
git commit -m "docs: add demo clips to landing page"
git push origin main
```

GitHub Pages will redeploy automatically within ~1 minute.
