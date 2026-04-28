# Error page backgrounds

Random backgrounds for the 404 and 503 error pages.

## How to add your own images

1. Drop any image files (`.jpg`, `.png`, `.webp`, `.svg`) into this folder.
2. Add their filenames to `manifest.json`:
   ```json
   {
     "images": ["1.svg", "2.svg", "3.svg", "my-photo.jpg", "another.webp"]
   }
   ```
3. Save. Reload the error pages — a random one is picked on every visit.

## Image guidelines

* **Any size works.** The CSS uses `background-size: cover`, so images of any
  dimensions are scaled to fill the screen. Image aspect ratio doesn't matter.
* **Recommended:** at least 1920×1080 to look sharp on big screens.
* **Dark/atmospheric images work best** — there's a dark gradient overlay on
  top to keep the text readable, but very bright backgrounds may still wash
  out the foreground.
* **File size:** keep individual images under 500 KB for fast load.

## Default images

The three SVGs that ship with the project are:

| File   | Style                                  |
|--------|----------------------------------------|
| 1.svg  | Topographic contour lines (gold)       |
| 2.svg  | Starfield with constellation           |
| 3.svg  | Geometric grid + dots                  |

Feel free to delete them after you add your own.