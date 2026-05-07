# Book Reconstructor

Reconstruct a clean, typeset PDF book from Google Play Books screenshots.

Takes a folder of screenshots (or a `.zip`), extracts text via OCR, crops figures from gallery pages, and compiles everything into a properly formatted PDF with justified text, figures with captions, clickable table of contents, and sidebar bookmarks.

## What it produces

- Full-justified body text with proper paragraph spacing
- Figures cropped from gallery screenshots, framed by dark blue separator lines
- Figure captions below images: **FIGURE X.X.** *italic description* (mixed fonts, same line)
- Chapter title pages with subtitle and mini table of contents
- Section headings with hierarchical formatting
- Clickable TOC entries linking to chapter pages
- PDF sidebar bookmarks for chapter navigation
- Cover image on first page (auto-detected or provided)
- Bullet lists, metadata blocks (ISBN, DOI, Copyright)

## Installation

```bash
pip install -r requirements.txt
brew install tesseract  # macOS — or apt install tesseract-ocr on Linux
```

Requirements: Python 3.10+, Tesseract OCR (for full pipeline only).

## Usage

### Full pipeline (screenshots to PDF)

```bash
python3 reconstruct.py /path/to/screenshots -o book.pdf
```

Also accepts a `.zip` file:

```bash
python3 reconstruct.py screenshots.zip -o book.pdf
```

### Compile-only mode (recommended for quality output)

If you have pre-cleaned markdown and extracted figures:

```bash
python3 reconstruct.py /path/to/screenshots \
  --markdown clean_text.md \
  --figures-dir figures/ \
  --cover cover.png \
  -o book.pdf
```

### Standalone PDF compiler

`compile_pdf.py` can compile markdown + figures without the full pipeline:

```bash
python3 compile_pdf.py clean_text.md -f figures/ -o book.pdf -c cover.png
```

### Options

| Flag | Description |
|------|-------------|
| `-o, --output` | Output PDF path (default: `<input>/reconstructed.pdf`) |
| `--markdown` | Path to pre-cleaned markdown file (skips OCR) |
| `--figures-dir` | Path to existing figure images (skips extraction) |
| `--cover` | Path to cover image |
| `--no-cover` | Skip cover extraction entirely |
| `--lang` | Tesseract OCR language (default: `eng`) |
| `--dpi` | OCR DPI setting (default: `300`) |
| `--junk` | Extra regex patterns to filter from OCR output |

## What works well

**Figure extraction** is reliable. The script detects Google Play Books gallery pages by finding dark blue horizontal separator lines (R<80, G<80, B>40, covering >15% of row width). It requires 2+ line groups AND low text density (<15% text rows) between them, which prevents false positives on regular text pages. Figures are cropped between the blue lines and trimmed of whitespace. This consistently extracts all figures from the book's image gallery.

**PDF compilation** produces publication-quality output. Text justification distributes space between words properly (last line left-aligned). Figures and their captions are kept together across page breaks. The TOC is clickable with working sidebar bookmarks. Chapter pages get proper formatting with subtitle and section list.

**Screenshot cropping** reliably strips Google Play Books browser chrome: top 18%, bottom 12%, left 3%, right 6% of each screenshot. These percentages work for standard desktop Chrome at default zoom.

## What doesn't work well

**OCR quality is poor.** Tesseract produces noisy output from screenshots — browser chrome text bleeds through despite filtering, words get garbled, special characters are misread, and paragraph boundaries are often wrong. The built-in `clean_text()` function catches common junk patterns (Google account menus, browser UI text) but can't fix fundamental OCR errors.

The raw OCR output is usable as a rough draft but not for a final PDF. For production quality, you need to clean the text manually or with an LLM.

## Recommended workflow

### Step 1: Run the full pipeline to extract figures and a text draft

```bash
python3 reconstruct.py /path/to/screenshots -o draft.pdf
```

This creates a `_work/` directory containing:
- `extracted.md` — raw OCR text (needs cleanup)
- `figures/` — cropped figure images (usually good as-is)
- `cover.png` — extracted cover image

### Step 2: Clean up the text

Open `_work/extracted.md` and fix OCR errors. You can do this manually or by feeding the raw text to an LLM in chunks.

**LLM prompt for OCR cleanup:**

> You are cleaning up OCR-extracted text from a book. The input comes from screenshots
> and contains errors: garbled words, browser UI noise, broken paragraphs, and
> misread special characters.
>
> Rules:
> - Fix obvious OCR errors (e.g., "rn" misread as "m", "l" misread as "1")
> - Remove any browser chrome text (Google account, bookmarks bar, URLs, tab titles)
> - Merge lines that are part of the same paragraph
> - Preserve paragraph breaks (double newline between paragraphs)
> - Keep figure references exactly as: `![FIGURE X.X](FIGURE_X.X.png)`
> - Keep chapter headings as: `CHAPTER X` on its own line
> - Keep section headings as: `X.X Section Title` on its own line
> - Keep `FIGURE X.X. Caption text` as a separate paragraph before each figure reference
> - Do NOT add any text that isn't in the original — only fix errors
> - Do NOT change the order of content
> - Mark anything you're uncertain about with [?]
>
> Process this chunk of OCR text:

Feed the text in chunks of ~3000 words. Review the output for hallucinated text — LLMs sometimes "improve" sentences beyond what was in the original.

### Step 3: Rename figures to match the text

The auto-extracted figures are named `fig_001.png`, `fig_002.png`, etc. Rename them to match the references in your cleaned markdown:

```bash
# Example: if your markdown references ![FIGURE 1.1](FIGURE_1.1.png)
mv _work/figures/fig_001.png _work/figures/FIGURE_1.1.png
mv _work/figures/fig_002.png _work/figures/FIGURE_1.2.png
# ... etc
```

The script's `match_figures_to_captions()` function attempts this automatically during the full pipeline, but manual verification is recommended.

### Step 4: Compile the final PDF

```bash
python3 reconstruct.py /path/to/screenshots \
  --markdown _work/extracted_clean.md \
  --figures-dir _work/figures/ \
  -o final.pdf
```

## Markdown format

The compiler expects this structure:

```markdown
# Book Title
Author Name

Brief Table of Contents

1. Chapter Title
2. Another Chapter

---

Detailed Table of Contents

1. Chapter Title
1.1 Section One
1.2 Section Two

2. Another Chapter
2.1 First Section

---

CHAPTER 1
Chapter Title

1.1 Section Heading

Body text paragraph. Regular paragraphs are fully justified.
Another sentence in the same paragraph.

FIGURE 1.1. Caption text describing the figure.

![FIGURE 1.1](FIGURE_1.1.png)

More body text continues here.

- Bullet point one
- Bullet point two

CHAPTER 2
Second Chapter Title
...
```

Key formatting rules:
- `# Title` on first line = title page (centered, large)
- `CHAPTER X` = starts a new page with chapter heading
- `X.X Title` = section heading (blue, bold)
- `X.X.X Title` = subsection heading
- `ALL CAPS LINE` = section header (blue, bold)
- `FIGURE X.X. Caption` = stored as pending caption for next figure
- `![...](filename.png)` = insert figure image
- `- text` or `• text` = bullet list item
- `---` = section break (ignored in output)
- Double newline = paragraph break

## Adjusting for different screenshot sources

The cropping percentages in `crop_content()` are tuned for Google Play Books in desktop Chrome. For other sources, adjust these values:

```python
def crop_content(img):
    top = int(h * 0.18)     # browser chrome + toolbar
    bottom = int(h * 0.88)  # bottom bar / navigation
    left = int(w * 0.03)    # left margin
    right = w - int(w * 0.06)  # right margin + scrollbar
```

The gallery page detection in `is_gallery_page()` looks for dark blue horizontal lines — this is specific to Google Play Books' figure display style. Other e-book platforms may use different visual separators.

The `CHROME_JUNK` list at the top of `reconstruct.py` contains regex patterns for filtering browser UI text from OCR output. Add patterns for your specific browser/OS:

```bash
python3 reconstruct.py screenshots/ --junk "safari\s+file" "reading\s+list" -o book.pdf
```

## License

MIT
