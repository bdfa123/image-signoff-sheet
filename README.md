# image-signoff-sheet

Turn a list of images into one HTML page where a person signs off each image with **Pass** or
**Fail** and a reason, then hands the result back as text or JSON.

It was written for reviewing synthetic test images, where an automatic check flags images and a
person makes the final call. It works for any "look at each image and decide" task.

- **One file.** Every image is embedded, so the page opens offline from a double-click and can be
  sent to the reviewer as a single attachment.
- **Built for the reviewer.** Each image shows what it should show, the context, and why it needs
  a person. The reviewer clicks Pass or Fail and writes one line. Images enlarge on click. A filter
  hides the images already reviewed; a failed image stays in view until the reviewer moves on, and
  stays there if it has no reason yet.
- **Nothing is lost.** Decisions are kept in the browser's local storage, so the page can be closed
  and reopened. A sheet rebuilt with different images starts empty, so decisions about old images
  never show up next to new ones.
- **Easy to hand back.** "Copy result" gives plain text such as `#2 mug-02 FAIL: no chip on the
  rim`, which can be pasted into a chat. "Download JSON" gives a file. `parse` turns the text back
  into JSON for a script.
- **English or Chinese** interface (`--lang en|zh`).
- **Python standard library only.** [Pillow](https://pypi.org/project/pillow/) is optional, for
  previewing formats a browser cannot show, such as TIFF.

## Usage

```bash
python signoff_sheet.py build example/items.json sheet.html --lang en
# open sheet.html, review, click "Copy result" and save the text as result.txt
python signoff_sheet.py parse result.txt --items example/items.json --out result.json
```

With `--items`, `parse` checks that the result covers exactly the images in the sheet, once each.
The JSON is UTF-8. Without `--out` it goes to standard output; on Windows prefer `--out` to `>`,
which Windows PowerShell saves as UTF-16.

## Items file

```json
{
 "title": "Example sign-off sheet",
 "checklist": ["The image shows what the item says it should show", "No readable licence plate or face"],
 "items": [
  {
   "id": "tile-01",
   "image": "images/cracked-tile.png",
   "label": "Kitchen tile, close-up",
   "tag": "flagged",
   "expected": "A grey floor tile with one diagonal crack from corner to corner",
   "context": ["The claimant says a pan fell and cracked one floor tile."],
   "why": ["An automatic check said the crack may not reach the corner."]
  }
 ]
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Unique, no spaces. It appears in the result. |
| `image` | yes | Path relative to the items file. |
| `label` | no | Short name shown above the image; defaults to `id`. |
| `tag` | no | A badge such as `flagged` or `sampled`. |
| `expected` | no | What the image should show. |
| `context` | no | A string or a list of strings, for example the facts behind the image. |
| `why` | no | A string or a list of strings: why a person looks at this image. |

## Result

Copied text:

```text
Sign-off result 3/3
#1 tile-01 PASS
#2 mug-02 FAIL: no chip on the rim
#3 sign-03 NOT REVIEWED
```

JSON, from "Download JSON" or `parse`, is a list of `{n, id, decision, reason}`. `decision` is
`pass`, `fail` or `null` (not reviewed).

## Tests

```bash
python -m unittest -v
```

## Licence

MIT. See [LICENSE](LICENSE).
