#!/usr/bin/env python3
"""
Book Reconstructor — Google Play Books screenshots → clean typeset PDF

Usage:
    python3 reconstruct.py /path/to/screenshots -o output.pdf
    python3 reconstruct.py /path/to/screenshots.zip -o output.pdf

Accepts a folder of screenshots or a .zip file. Produces a typeset PDF with:
  - OCR-extracted, cleaned text with full justification
  - Figures cropped from gallery pages, placed after their first text reference
  - Blue separator lines around figures, captions below (bold label + italic text)
  - Clickable table of contents and PDF sidebar bookmarks
  - Optional cover image (auto-detected from first screenshot)
"""

import argparse
import io
import re
import sys
import zipfile
import tempfile
import shutil
from pathlib import Path

try:
    from PIL import Image
    import numpy as np
except ImportError:
    sys.exit("pip install pillow numpy")

try:
    import pytesseract
except ImportError:
    sys.exit("pip install pytesseract  (and install Tesseract OCR)")

try:
    import fitz
except ImportError:
    sys.exit("pip install PyMuPDF")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}

CHROME_JUNK = [
    r"play\.google", r"all\s+bookmarks", r"new\s+chrome",
    r"google\s+pass", r"mail\s*[-—]\s*brou", r"password",
    r"account\s+home", r"open-sour", r"cover\s+page",
    r"course:\s+student", r"^\s*[<>|©@®™]+\s*$", r"^\s*[-—=_]{3,}\s*$",
    r"^\s*>+\s*$", r"books/reader", r"3d\s+printable",
    r"infocus@acs", r"student\s+li", r"what\s+are\s+y",
    r"europeptid", r"perwoll|parawiki", r"introductory\s+x",
]


# ── Phase 1: Screenshot Processing ──────────────────────────────────────


def collect_images(folder: Path) -> list[Path]:
    files = sorted(
        f for f in folder.iterdir()
        if f.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not files:
        sys.exit(f"No image files found in {folder}")
    return files


def crop_content(img: Image.Image) -> Image.Image:
    arr = np.array(img.convert("RGB"))
    h, w = arr.shape[:2]
    top = int(h * 0.18)
    bottom = int(h * 0.88)
    left = int(w * 0.03)
    right = w - int(w * 0.06)
    return Image.fromarray(arr[top:bottom, left:right])


def extract_cover(img: Image.Image, out_path: Path) -> bool:
    arr = np.array(img.convert("RGB"))
    h, w = arr.shape[:2]
    cropped = arr[int(h * 0.18):int(h * 0.88), int(w * 0.03):w - int(w * 0.06)]
    gray = cropped.mean(axis=2)
    rows = np.where(gray.mean(axis=1) < 240)[0]
    cols = np.where(gray.mean(axis=0) < 240)[0]
    if len(rows) < 50 or len(cols) < 50:
        return False
    region = cropped[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    rh, rw = region.shape[:2]
    if rh > rw * 0.8:
        Image.fromarray(region).save(out_path)
        return True
    return False


def clean_text(text: str) -> str:
    lines = text.split("\n")
    clean = []
    for line in lines:
        s = line.strip()
        if not s:
            clean.append("")
            continue
        skip = False
        for pat in CHROME_JUNK:
            if re.search(pat, s, re.IGNORECASE):
                skip = True
                break
        if skip:
            continue
        alnum = sum(1 for c in s if c.isalnum() or c.isspace())
        if len(s) > 3 and alnum / len(s) < 0.5:
            continue
        if re.match(r'^\d{1,3}$', s):
            continue
        s = s.replace("¢ ", "• ").replace("¢C", "• C")
        clean.append(s)

    result = []
    prev_blank = False
    for line in clean:
        if not line:
            if not prev_blank:
                result.append("")
            prev_blank = True
        else:
            result.append(line)
            prev_blank = False
    return "\n".join(result).strip()


def is_gallery_page(cropped: Image.Image) -> bool:
    arr = np.array(cropped)
    h, w = arr.shape[:2]
    rgb = arr.astype(float)
    blue_mask = (rgb[:, :, 0] < 80) & (rgb[:, :, 1] < 80) & (rgb[:, :, 2] > 40)

    blue_rows = []
    for row in range(h):
        frac = blue_mask[row].sum() / w
        if frac > 0.15:
            blue_rows.append(row)

    if len(blue_rows) < 4:
        return False

    groups = []
    current = [blue_rows[0]]
    for r in blue_rows[1:]:
        if r - current[-1] <= 3:
            current.append(r)
        else:
            groups.append((current[0], current[-1]))
            current = [r]
    groups.append((current[0], current[-1]))

    if len(groups) < 2:
        return False

    for i in range(len(groups) - 1):
        top = groups[i][1] + 1
        bottom = groups[i + 1][0]
        region_h = bottom - top
        if region_h < 100:
            continue
        region = arr[top:bottom, :]
        gray = region.mean(axis=2)
        text_rows = 0
        for r in range(region.shape[0]):
            dark_frac = (gray[r] < 100).sum() / gray.shape[1]
            if dark_frac > 0.08:
                text_rows += 1
        if text_rows / region.shape[0] < 0.15:
            return True

    return False


def extract_gallery_figures(cropped: Image.Image, figure_dir: Path,
                            fig_counter: list) -> list[str]:
    arr = np.array(cropped)
    h, w = arr.shape[:2]
    rgb = arr.astype(float)
    blue_mask = (rgb[:, :, 0] < 80) & (rgb[:, :, 1] < 80) & (rgb[:, :, 2] > 40)

    blue_rows = []
    for row in range(h):
        frac = blue_mask[row].sum() / w
        if frac > 0.15:
            blue_rows.append(row)

    if len(blue_rows) < 2:
        return []

    groups = []
    current = [blue_rows[0]]
    for r in blue_rows[1:]:
        if r - current[-1] <= 3:
            current.append(r)
        else:
            groups.append((current[0], current[-1]))
            current = [r]
    groups.append((current[0], current[-1]))

    if len(groups) < 2:
        return []

    names = []
    for i in range(len(groups) - 1):
        top = groups[i][1] + 1
        bottom = groups[i + 1][0]
        if bottom - top < 30:
            continue

        region = arr[top:bottom, :]
        gray = region.mean(axis=2)
        rows_c = np.where(gray.min(axis=1) < 240)[0]
        cols_c = np.where(gray.min(axis=0) < 240)[0]
        if len(rows_c) < 10 or len(cols_c) < 10:
            continue
        ry1, ry2 = max(0, rows_c[0] - 5), min(region.shape[0], rows_c[-1] + 5)
        rx1, rx2 = max(0, cols_c[0] - 5), min(region.shape[1], cols_c[-1] + 5)
        trimmed = region[ry1:ry2, rx1:rx2]

        if trimmed.shape[0] > 30 and trimmed.shape[1] > 30:
            fig_counter[0] += 1
            name = f"fig_{fig_counter[0]:03d}.png"
            Image.fromarray(trimmed).save(figure_dir / name)
            names.append(name)

    return names


def process_screenshot(img_path: Path, lang: str, dpi: int) -> str:
    img = Image.open(img_path).convert("RGB")
    cropped = crop_content(img)
    text = pytesseract.image_to_string(cropped, lang=lang, config=f"--dpi {dpi} --psm 3")
    return clean_text(text)


def match_figures_to_captions(md_text: str, figure_names: list[str]) -> dict[str, str]:
    caption_pat = re.compile(
        r'FIGURE\s+(\d+\.\d+)', re.IGNORECASE
    )
    captions_in_text = caption_pat.findall(md_text)
    seen = []
    for c in captions_in_text:
        if c not in seen:
            seen.append(c)

    mapping = {}
    for i, fig_id in enumerate(seen):
        if i < len(figure_names):
            mapping[fig_id] = figure_names[i]

    return mapping


def place_figures(md_text: str, fig_mapping: dict[str, str]) -> str:
    paragraphs = md_text.split("\n\n")

    placed = set()
    result = []

    for para in paragraphs:
        stripped = para.strip()
        if not stripped:
            continue

        result.append(stripped)

        refs = re.findall(r'\(FIGURE\s+(\d+\.\d+)\)', stripped, re.IGNORECASE)
        for ref in refs:
            if ref in fig_mapping and ref not in placed:
                placed.add(ref)
                fname = fig_mapping[ref]
                new_name = f"FIGURE_{ref}.png"
                result.append(f"FIGURE {ref}.")
                result.append(f"![FIGURE {ref}]({new_name})")

    for fig_id, fname in fig_mapping.items():
        if fig_id not in placed:
            new_name = f"FIGURE_{fig_id}.png"
            result.append(f"FIGURE {fig_id}.")
            result.append(f"![FIGURE {fig_id}]({new_name})")

    return "\n\n".join(result)


# ── Phase 2: PDF Compilation ────────────────────────────────────────────


def compile_pdf(md_path: Path, figure_dir: Path, output: Path,
                cover_path: Path = None):
    text = md_path.read_text(encoding="utf-8")

    doc = fitz.open()

    pw, ph = 468.0, 648.0
    ml, mr, mt, mb = 50.0, 50.0, 50.0, 50.0
    tw = pw - ml - mr
    y = mt

    if cover_path and cover_path.exists():
        cover_page = doc.new_page(width=pw, height=ph)
        cimg = Image.open(cover_path)
        ciw, cih = cimg.size
        scale = min(pw / ciw, ph / cih)
        cdw, cdh = ciw * scale, cih * scale
        cx = (pw - cdw) / 2
        cy = (ph - cdh) / 2
        cbuf = io.BytesIO()
        cimg.save(cbuf, format="PNG")
        cbuf.seek(0)
        cover_page.insert_image(
            fitz.Rect(cx, cy, cx + cdw, cy + cdh), stream=cbuf.read())

    page = doc.new_page(width=pw, height=ph)

    font_r = fitz.Font("helv")
    font_b = fitz.Font("hebo")
    font_i = fitz.Font("heit")

    BLUE = (0.15, 0.3, 0.55)
    DARK_BLUE_LINE = (0.1, 0.2, 0.5)

    chapter_bookmarks = []
    toc_entries = []

    def cur_page_idx():
        return len(doc) - 1

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=pw, height=ph)
        y = mt

    def ensure(amount):
        nonlocal y
        if y + amount > ph - mb:
            new_page()

    def draw_justified(s: str, size: float, bold=False, italic=False,
                       indent=0.0, color=(0, 0, 0), after=5.0,
                       center=False, justify=True, link_dest=None):
        nonlocal y
        f = font_b if bold else (font_i if italic else font_r)
        words = s.split()
        if not words:
            return

        avail = tw - indent
        lh = size * 1.4

        lines_out = []
        current = []
        current_w = 0
        space_w = f.text_length(" ", fontsize=size)

        for word in words:
            word_w = f.text_length(word, fontsize=size)
            test_w = current_w + (space_w if current else 0) + word_w
            if test_w <= avail or not current:
                current.append(word)
                current_w = test_w
            else:
                lines_out.append(current)
                current = [word]
                current_w = word_w

        if current:
            lines_out.append(current)

        first_line_y = None
        last_line_y = None

        for li, line_words in enumerate(lines_out):
            ensure(lh)
            is_last = (li == len(lines_out) - 1)

            if first_line_y is None:
                first_line_y = y

            if center:
                line_text = " ".join(line_words)
                line_w = f.text_length(line_text, fontsize=size)
                x = ml + (tw - line_w) / 2
                writer = fitz.TextWriter(page.rect)
                writer.append(fitz.Point(x, y), line_text, font=f, fontsize=size)
                writer.write_text(page, color=color)
            elif justify and not is_last and len(line_words) > 1:
                natural_w = sum(f.text_length(w, fontsize=size) for w in line_words)
                total_space = avail - natural_w
                gap = total_space / (len(line_words) - 1)
                x = ml + indent
                for wi, word in enumerate(line_words):
                    writer = fitz.TextWriter(page.rect)
                    writer.append(fitz.Point(x, y), word, font=f, fontsize=size)
                    writer.write_text(page, color=color)
                    x += f.text_length(word, fontsize=size) + gap
            else:
                line_text = " ".join(line_words)
                writer = fitz.TextWriter(page.rect)
                writer.append(fitz.Point(ml + indent, y), line_text,
                              font=f, fontsize=size)
                writer.write_text(page, color=color)

            last_line_y = y
            y += lh

        if link_dest is not None and first_line_y is not None:
            line_text = " ".join(words)
            text_w = min(f.text_length(line_text, fontsize=size), avail)
            link_rect = fitz.Rect(ml + indent, first_line_y - size,
                                  ml + indent + text_w, last_line_y + 2)
            toc_entries.append((cur_page_idx(), link_rect, link_dest))

        y += after

    def draw_blue_line():
        nonlocal y
        page.draw_line(
            fitz.Point(ml, y), fitz.Point(pw - mr, y),
            color=DARK_BLUE_LINE, width=1.0
        )
        y += 12

    def draw_caption(caption_text: str):
        nonlocal y
        CAP_COLOR = (0.3, 0.3, 0.3)
        cap_size = 8.5
        lh = cap_size * 1.4

        m = re.match(r'^(FIGURE\s+\d+\.\d+\.?\s*)', caption_text, re.IGNORECASE)
        if not m:
            draw_justified(caption_text, cap_size, italic=True, after=0,
                           color=CAP_COLOR, justify=False)
            return

        prefix = m.group(1).rstrip() + " "
        rest = caption_text[len(m.group(1)):].strip()

        spans = [(w, font_b) for w in prefix.split()]
        spans += [(w, font_i) for w in rest.split()]

        space_w = font_i.text_length(" ", fontsize=cap_size)
        x = ml
        ensure(lh)

        for word, font in spans:
            ww = font.text_length(word, fontsize=cap_size)
            if x > ml and x + ww > ml + tw:
                y += lh
                ensure(lh)
                x = ml
            writer = fitz.TextWriter(page.rect)
            writer.append(fitz.Point(x, y), word, font=font, fontsize=cap_size)
            writer.write_text(page, color=CAP_COLOR)
            x += ww + space_w

        y += lh

    def insert_fig(fig_path: Path, caption_text: str = None):
        nonlocal y
        if not fig_path.exists():
            draw_justified(f"[Missing: {fig_path.name}]", 8.0, italic=True,
                           color=(0.6, 0, 0), after=10, justify=False)
            return
        img = Image.open(fig_path)
        iw, ih = img.size

        max_w = tw
        max_h = (ph - mt - mb) * 0.8
        scale = min(max_w / iw, max_h / ih)
        dw, dh = iw * scale, ih * scale

        caption_h = 0
        if caption_text:
            cap_lh = 8.5 * 1.4
            cap_words = caption_text.split()
            cap_line_w = 0
            cap_lines = 1
            sp_w = font_i.text_length(" ", fontsize=8.5)
            for w in cap_words:
                ww = font_i.text_length(w, fontsize=8.5)
                if cap_line_w + (sp_w if cap_line_w > 0 else 0) + ww > tw:
                    cap_lines += 1
                    cap_line_w = ww
                else:
                    cap_line_w += (sp_w if cap_line_w > 0 else 0) + ww
            caption_h = cap_lines * cap_lh + 6

        total_needed = 8 + dh + 8 + caption_h + 15
        ensure(total_needed)

        draw_blue_line()

        x_off = ml + (tw - dw) / 2
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        page.insert_image(fitz.Rect(x_off, y, x_off + dw, y + dh),
                          stream=buf.read())
        y += dh + 8

        draw_blue_line()

        if caption_text:
            draw_caption(caption_text)
        y += 10

    # ── Render paragraphs ──

    paragraphs = text.split("\n\n")
    in_toc = False
    pending_caption = None
    skip_next = set()

    for pi, para in enumerate(paragraphs):
        if pi in skip_next:
            continue
        para = para.strip()
        if not para:
            continue

        if para == "---":
            in_toc = False
            continue

        if para in ("Brief Table of Contents", "Detailed Table of Contents"):
            in_toc = True
            y += 10
            draw_justified(para, 13.0, bold=True, after=8, color=BLUE,
                           justify=False)
            continue

        if in_toc:
            toc_lines = para.split("\n")
            for line in toc_lines:
                s = line.strip()
                if not s:
                    continue
                m_sub = re.match(r'^(\d+\.\d+\.\d+)\s+(.+)', s)
                m_sec = re.match(r'^(\d+\.\d+)\s+(.+)', s)
                m_chap = re.match(r'^(\d+)\.\s+(.+)', s)
                if m_sub:
                    draw_justified(s, 8.5, indent=35, after=2, justify=False)
                elif m_sec:
                    draw_justified(s, 9.0, bold=True, indent=20, after=3,
                                   justify=False, link_dest=m_sec.group(1))
                elif m_chap:
                    chap_num = int(m_chap.group(1))
                    draw_justified(s, 10.0, bold=True, indent=5, after=4,
                                   color=BLUE, justify=False,
                                   link_dest=f"chapter_{chap_num}")
                else:
                    draw_justified(s, 9.0, indent=5, after=2, justify=False)
            y += 4
            continue

        lines = para.split("\n")
        first = lines[0].strip()

        fig_match = re.match(r'!\[.*?\]\((.+?)\)', para)
        if fig_match:
            fig_file = figure_dir / fig_match.group(1)
            caption = pending_caption or ""
            pending_caption = None
            if pi + 1 < len(paragraphs):
                nxt = paragraphs[pi + 1].strip()
                nxt_first = nxt.split("\n")[0].strip() if nxt else ""
                if (nxt and nxt != "---"
                        and not nxt_first.isupper()
                        and not re.match(r'^\d+\.\d+', nxt_first)
                        and not re.match(r'^(CHAPTER|FIGURE|TABLE|!|[-•·])',
                                         nxt_first)
                        and not nxt_first.startswith("ISBN:")
                        and len(nxt) < 300):
                    cont = " ".join(l.strip() for l in nxt.split("\n")
                                    if l.strip())
                    caption = (caption + " " + cont).strip() if caption else cont
                    skip_next.add(pi + 1)
            insert_fig(fig_file, caption_text=caption if caption else None)
            continue

        if first.startswith("# "):
            new_page()
            y = ph * 0.35
            draw_justified(first[2:], 22.0, bold=True, after=12, center=True)
            for line in lines[1:]:
                s = line.strip()
                if s:
                    draw_justified(s, 12.0, after=6, center=True)
            continue

        chap_match = re.match(r'^CHAPTER\s+(\d+)', first, re.IGNORECASE)
        if chap_match:
            chap_num = int(chap_match.group(1))
            new_page()
            chapter_bookmarks.append((chap_num, first, cur_page_idx()))
            y += 30
            draw_justified(first, 18.0, bold=True, after=4, center=True)
            for line in lines[1:]:
                s = line.strip()
                if s:
                    draw_justified(s, 16.0, bold=True, after=6, center=True)
            lookahead = pi + 1
            if lookahead < len(paragraphs):
                next_p = paragraphs[lookahead].strip()
                if (next_p and not next_p.isupper() and len(next_p) < 50
                        and not re.match(r'^\d+\.\d+', next_p)
                        and not re.match(r'^(CHAPTER|FIGURE|TABLE|!)', next_p)
                        and next_p != "---"):
                    draw_justified(next_p, 16.0, bold=True, after=6,
                                   center=True)
                    skip_next.add(lookahead)
                    lookahead += 1
            if lookahead < len(paragraphs):
                next_p = paragraphs[lookahead].strip()
                if next_p and re.match(r'^\d+\.\d+\s',
                                       next_p.split("\n")[0]):
                    y += 8
                    for line in next_p.split("\n"):
                        s = line.strip()
                        if s:
                            draw_justified(s, 9.5, after=3, indent=15,
                                           color=(0.4, 0.4, 0.4),
                                           justify=False)
                    skip_next.add(lookahead)
            y += 15
            continue

        if (first.isupper() and 3 < len(first) < 80
                and not first.startswith("FIGURE")
                and not first.startswith("TABLE")):
            y += 10
            draw_justified(first, 13.0, bold=True, after=6, color=BLUE,
                           justify=False)
            body = [l.strip() for l in lines[1:] if l.strip()]
            if body:
                draw_justified(" ".join(body), 10.0, after=6)
            continue

        if re.match(r'^(\d+\.\d+(?:\.\d+)?)\s+\S', first):
            y += 8
            full = " ".join(l.strip() for l in lines if l.strip())
            draw_justified(full, 11.0, bold=True, after=6, color=BLUE,
                           justify=False)
            continue

        if re.match(r'^FIGURE\s+\d+\.\d+[\.\s]', first, re.IGNORECASE):
            pending_caption = " ".join(l.strip() for l in lines if l.strip())
            continue

        if re.match(r'^[-•·]\s', first):
            for line in lines:
                s = line.strip()
                if re.match(r'^[-•·]\s', s):
                    bt = re.sub(r'^[-•·]\s*', '', s)
                    draw_justified(f"•  {bt}", 10.0, indent=15, after=3)
                elif s:
                    draw_justified(s, 10.0, indent=25, after=3)
            y += 3
            continue

        if (first.startswith("ISBN:") or first.startswith("DOI:")
                or first.startswith("Copyright")):
            full = " ".join(l.strip() for l in lines if l.strip())
            draw_justified(full, 8.0, after=4, color=(0.4, 0.4, 0.4))
            continue

        full = " ".join(l.strip() for l in lines if l.strip())
        draw_justified(full, 10.0, after=6)

    # ── TOC links and bookmarks ──

    chap_page_map = {}
    for chap_num, title, page_idx in chapter_bookmarks:
        chap_page_map[f"chapter_{chap_num}"] = page_idx

    for toc_page_idx, link_rect, dest_key in toc_entries:
        target_page = None
        if dest_key in chap_page_map:
            target_page = chap_page_map[dest_key]
        else:
            m = re.match(r'^(\d+)\.\d+', dest_key)
            if m:
                chap_key = f"chapter_{m.group(1)}"
                if chap_key in chap_page_map:
                    target_page = chap_page_map[chap_key]
        if target_page is not None:
            toc_page = doc[toc_page_idx]
            toc_page.insert_link({
                "kind": fitz.LINK_GOTO,
                "from": link_rect,
                "page": target_page,
                "to": fitz.Point(ml, mt),
            })

    pdf_toc = []
    for chap_num, title, page_idx in chapter_bookmarks:
        pdf_toc.append([1, title, page_idx + 1])
    if pdf_toc:
        doc.set_toc(pdf_toc)

    doc.save(str(output), deflate=True, garbage=4)
    n_pages = len(doc)
    doc.close()
    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"  ✓ {output}  ({n_pages} pages, {size_mb:.1f} MB)")


# ── Main Pipeline ───────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Reconstruct a book from Google Play Books screenshots"
    )
    parser.add_argument("input", type=Path,
                        help="Folder of screenshots or .zip file")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output PDF path (default: <input>/reconstructed.pdf)")
    parser.add_argument("--lang", default="eng",
                        help="OCR language (default: eng)")
    parser.add_argument("--dpi", type=int, default=300,
                        help="OCR DPI (default: 300)")
    parser.add_argument("--no-cover", action="store_true",
                        help="Skip cover extraction")
    parser.add_argument("--cover", type=Path, default=None,
                        help="Use this cover image instead of auto-detecting")
    parser.add_argument("--markdown", type=Path, default=None,
                        help="Use pre-cleaned markdown (skip OCR)")
    parser.add_argument("--figures-dir", type=Path, default=None,
                        help="Use existing figures directory (skip extraction)")
    parser.add_argument("--junk", nargs="*", default=None,
                        help="Extra regex patterns to filter from OCR text")
    args = parser.parse_args()

    # ── Compile-only mode: pre-cleaned markdown + existing figures ──

    if args.markdown:
        if not args.markdown.exists():
            sys.exit(f"Markdown not found: {args.markdown}")
        fig_dir = args.figures_dir
        if not fig_dir:
            fig_dir = args.markdown.parent / "figures"
            if not fig_dir.exists():
                fig_dir = args.markdown.parent / "figures_cropped"
        if not fig_dir.exists():
            sys.exit(f"Figures dir not found: {fig_dir}  (use --figures-dir)")
        output = args.output or args.markdown.with_suffix(".pdf")
        cover = args.cover
        if not cover and not args.no_cover:
            for candidate in [args.markdown.parent / "cover.png",
                              fig_dir.parent / "cover.png"]:
                if candidate.exists():
                    cover = candidate
                    break
        print(f"Compiling {args.markdown.name} → {output.name}")
        print(f"  Figures: {fig_dir}")
        if cover:
            print(f"  Cover: {cover}")
        compile_pdf(args.markdown, fig_dir, output, cover_path=cover)
        print("\nDone!")
        return

    # ── Full pipeline: screenshots → PDF ──

    # Handle zip input
    tmp_dir = None
    if args.input.suffix.lower() == ".zip":
        if not args.input.exists():
            sys.exit(f"File not found: {args.input}")
        tmp_dir = Path(tempfile.mkdtemp(prefix="book_"))
        print(f"Extracting {args.input.name}...")
        with zipfile.ZipFile(args.input) as zf:
            zf.extractall(tmp_dir)
        folders = [d for d in tmp_dir.iterdir() if d.is_dir()]
        if len(folders) == 1:
            src_folder = folders[0]
        else:
            src_folder = tmp_dir
    else:
        if not args.input.is_dir():
            sys.exit(f"Not a directory or .zip: {args.input}")
        src_folder = args.input

    if args.junk:
        CHROME_JUNK.extend(args.junk)

    output = args.output or (src_folder / "reconstructed.pdf")
    work_dir = src_folder / "_work"
    work_dir.mkdir(exist_ok=True)
    figure_dir = args.figures_dir or (work_dir / "figures")
    extract_figs = args.figures_dir is None
    if extract_figs:
        if figure_dir.exists():
            for f in figure_dir.glob("*.png"):
                f.unlink()
        figure_dir.mkdir(exist_ok=True)

    images = collect_images(src_folder)
    print(f"Found {len(images)} screenshots\n")

    # ── Phase 1: OCR ──

    print("── Phase 1: OCR text extraction ──")
    all_text = []
    for idx, img_path in enumerate(images, 1):
        print(f"  [{idx:3d}/{len(images)}] {img_path.name}")
        page_text = process_screenshot(img_path, args.lang, args.dpi)
        all_text.append(page_text)

    raw_md = "\n\n---\n\n".join(all_text)
    md_path = work_dir / "extracted.md"
    md_path.write_text(raw_md, encoding="utf-8")
    print(f"\n  Text → {md_path}")

    # ── Phase 2: Figure extraction from gallery pages ──

    gallery_page_indices = set()

    if extract_figs:
        print("\n── Phase 2: Figure extraction ──")
        fig_counter = [0]
        gallery_figures = []

        for idx, img_path in enumerate(images, 1):
            img = Image.open(img_path).convert("RGB")
            cropped = crop_content(img)
            if is_gallery_page(cropped):
                names = extract_gallery_figures(cropped, figure_dir, fig_counter)
                if names:
                    print(f"  [{idx:3d}] Gallery page → {len(names)} figure(s)")
                    gallery_figures.extend(names)
                    gallery_page_indices.add(idx - 1)

        if not gallery_figures:
            print("  No gallery pages found; attempting inline extraction...")
            for idx, img_path in enumerate(images, 1):
                img = Image.open(img_path).convert("RGB")
                cropped = crop_content(img)
                ocr = pytesseract.image_to_data(
                    cropped, lang=args.lang,
                    config=f"--dpi {args.dpi} --psm 3",
                    output_type=pytesseract.Output.DICT
                )
                n = len(ocr["text"])
                for i in range(n):
                    word = ocr["text"][i].strip().upper()
                    if (int(ocr["conf"][i]) < 30
                            or word not in ("FIGURE", "TABLE")):
                        continue
                    arr = np.array(cropped)
                    ch, cw = arr.shape[:2]
                    y_cap = ocr["top"][i]
                    x_cap = ocr["left"][i]
                    col_start = 0 if x_cap < cw // 2 else cw // 2
                    col_end = cw // 2 if x_cap < cw // 2 else cw
                    fig_end = min(y_cap + 700, ch)
                    region = arr[y_cap:fig_end, col_start:col_end]
                    if region.shape[0] > 30 and region.shape[1] > 30:
                        fig_counter[0] += 1
                        name = f"fig_{fig_counter[0]:03d}.png"
                        Image.fromarray(region).save(figure_dir / name)
                        gallery_figures.append(name)

        print(f"  Total figures extracted: {len(gallery_figures)}")

        # Match figures to captions and rename
        fig_mapping = match_figures_to_captions(raw_md, gallery_figures)
        print(f"  Matched {len(fig_mapping)} figures to captions")
        for fig_id, old_name in fig_mapping.items():
            new_name = f"FIGURE_{fig_id}.png"
            src = figure_dir / old_name
            dst = figure_dir / new_name
            if src.exists():
                shutil.copy2(src, dst)

        final_md = place_figures(raw_md, fig_mapping)
    else:
        print("\n── Phase 2: Using existing figures ──")
        print(f"  {figure_dir}")
        final_md = raw_md

    # ── Phase 3: Cover extraction ──

    cover_path = args.cover
    if not cover_path and not args.no_cover:
        print("\n── Phase 3: Cover extraction ──")
        cover_path = work_dir / "cover.png"
        first_img = Image.open(images[0]).convert("RGB")
        if extract_cover(first_img, cover_path):
            print(f"  Cover → {cover_path}")
        else:
            print("  No cover detected in first screenshot")
            cover_path = None

    # ── Phase 4: Clean up and finalize markdown ──

    # Remove OCR text from gallery pages (mostly noise from figure images)
    if gallery_page_indices:
        page_texts = final_md.split("\n\n---\n\n")
        cleaned_pages = []
        for i, pt in enumerate(page_texts):
            if i in gallery_page_indices:
                continue
            cleaned_pages.append(pt)
        final_md = "\n\n---\n\n".join(cleaned_pages)

    final_path = work_dir / "final.md"
    final_path.write_text(final_md, encoding="utf-8")
    print(f"\n  Final markdown → {final_path}")

    # ── Phase 5: PDF compilation ──

    print("\n── Phase 5: Compiling PDF ──")
    compile_pdf(final_path, figure_dir, output, cover_path=cover_path)

    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("\nDone!")


if __name__ == "__main__":
    main()
