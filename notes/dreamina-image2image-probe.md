# dreamina image2image — empirical probe results

**Date**: 2026-04-08
**dreamina version**: `5a448f5-dirty` (build 2026-04-07)

## Summary

`dreamina image2image` is a generation command that:

1. Submits an image-to-image task to the dreamina backend
2. With `--poll N`, blocks for up to N seconds polling for the result
3. On success, prints a **JSON object** to stdout describing the task and result
4. Exits with code 0 on success, non-zero on failure
5. **Does NOT save the output image to local disk**. The result is a remote signed URL embedded in the JSON. The caller must download it.

## Critical gotcha: comma-separated images

The cobra `--images strings` flag accepts a `[]string`. The Examples section in `dreamina image2image -h` shows a single image, but the description says "one or more local images". Through trial and error:

- ✅ `--images path1,path2` (comma-separated, one flag) — **works**
- ❌ `--images path1 path2` (space-separated after one flag) — silently fails with exit 1, no stdout, no stderr, no log entries
- (Untested) `--images path1 --images path2` — probably also works, but comma form is simpler

**Use comma-separated form.** Build the argv as `["--images", ",".join(absolute_paths)]`.

## Successful response shape (single image)

Command:
```bash
dreamina image2image \
  --images /abs/path/to/input.jpg \
  --prompt "make it watercolor style" \
  --ratio 3:2 \
  --poll 90
```

Stdout (exit code 0):
```json
{
  "submit_id": "6e5308da6245ccee",
  "prompt": "make it watercolor style",
  "gen_status": "success",
  "result_json": {
    "images": [
      {
        "image_url": "https://p11-dreamina-sign.byteimg.com/tos-cn-i-tb4s082cfz/<hash>~tplv-tb4s082cfz-aigc_resize:0:0.jpeg?lk3s=...&x-expires=...&x-signature=...&format=.jpeg",
        "width": 4992,
        "height": 3328
      }
    ],
    "videos": []
  },
  "queue_info": {
    "queue_idx": 0,
    "priority": 6,
    "queue_status": "Finish",
    "queue_length": 0,
    "debug_info": "{...}"
  }
}
```

## Successful response shape (multi-image, comma-separated)

Same shape. Multi-image input works for cat-replacement style prompts; the output is in `result_json.images[0]`.

## Output image URL properties

- **Signed and time-limited**: the URL contains `x-expires=<unix-timestamp>` and `x-signature=<hmac>`. Download it promptly — it expires within hours.
- **Remote on byteimg.com CDN** — no local file is ever created by dreamina.
- **Format**: typically PNG or JPEG depending on the request, embedded in the URL's `format=` query param.
- **Resolution**: ~5K (4992x3328 in our tests), regardless of input size — dreamina upscales.

## Local file artifacts

- **None.** dreamina does NOT write the result to `~/.dreamina_cli/runs/` or anywhere else on disk. The CLI is purely a thin wrapper around the remote API.
- `~/.dreamina_cli/logs/YYYY-MM-DD/HH.log` is the only thing it writes locally — and only for errors and structured info.

## Failure modes observed

- **Login expired**: stderr contains `未检测到有效登录态`, exit non-zero.
- **Wrong --images form (space-separated)**: silent exit 1, nothing in stdout/stderr/logs. Hard to debug — easy to mistake for a network or auth error.
- **Quota exceeded**: untested but expected to surface in stderr as a Chinese error message.

## Implications for `mycat_meme.dreamina`

The original `dreamina.py` design assumed dreamina prints a local file path. That assumption is wrong. The corrected pipeline:

1. `build_image2image_argv` joins images with commas (not space-separated as separate argv tokens)
2. `run_image2image` returns the JSON stdout
3. New `parse_image2image_result(stdout) -> Image2ImageResult` with `submit_id`, `image_url`, `width`, `height`
4. New `download_image(url, dest_path)` does an HTTP GET and writes to disk
5. `pipeline.replace` chains: argv → run → parse → download → done

The `locate_output_image` function from the original spec is removed; there is nothing to locate locally.
