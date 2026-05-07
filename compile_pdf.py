#!/usr/bin/env python3
"""
Compile cleaned markdown + figure images → typeset PDF.
"""

import argparse
import io
import re
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("pip install pillow")

try:
    import fitz
except ImportError:
    sys.exit("pip install PyMuPDF")


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
    in_toc = False

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
                writer.append(fitz.Point(ml + indent, y), line_text, font=f, fontsize=size)
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
            fitz.Point(ml, y),
            fitz.Point(pw - mr, y),
            color=DARK_BLUE_LINE, width=1.0
        )
        y += 12

    def draw_caption(caption_text: str):
        nonlocal y
        CAP_COLOR = (0.3, 0.3, 0.3)
        cap_size = 8.5
        lh = cap_size * 1.4
        avail = tw

        m = re.match(r'^(FIGURE\s+\d+\.\d+\.?\s*)', caption_text, re.IGNORECASE)
        if not m:
            draw_justified(caption_text, cap_size, italic=True, after=0,
                           color=CAP_COLOR, justify=False)
            return

        prefix = m.group(1).rstrip() + " "
        rest = caption_text[len(m.group(1)):].strip()

        spans = []
        for w in prefix.split():
            spans.append((w, font_b))
        for w in rest.split():
            spans.append((w, font_i))

        space_w = font_i.text_length(" ", fontsize=cap_size)
        x = ml
        ensure(lh)

        for si, (word, font) in enumerate(spans):
            ww = font.text_length(word, fontsize=cap_size)
            if x > ml and x + ww > ml + avail:
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
            cap_size = 8.5
            cap_lh = cap_size * 1.4
            cap_words = caption_text.split()
            cap_line_w = 0
            cap_lines = 1
            sp_w = font_i.text_length(" ", fontsize=cap_size)
            for w in cap_words:
                ww = font_i.text_length(w, fontsize=cap_size)
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
        page.insert_image(fitz.Rect(x_off, y, x_off + dw, y + dh), stream=buf.read())
        y += dh + 8

        draw_blue_line()

        if caption_text:
            draw_caption(caption_text)

        y += 10

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
            draw_justified(para, 13.0, bold=True, after=8, color=BLUE, justify=False)
            continue

        if in_toc:
            lines = para.split("\n")
            for line in lines:
                s = line.strip()
                if not s:
                    continue
                m_chap = re.match(r'^(\d+)\.\s+(.+)', s)
                m_sub = re.match(r'^(\d+\.\d+\.\d+)\s+(.+)', s)
                m_sec = re.match(r'^(\d+\.\d+)\s+(.+)', s)

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
                        and not re.match(r'^(CHAPTER|FIGURE|TABLE|!|[-•·])', nxt_first)
                        and not nxt_first.startswith("ISBN:")
                        and len(nxt) < 300):
                    cont = " ".join(l.strip() for l in nxt.split("\n") if l.strip())
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
                    draw_justified(next_p, 16.0, bold=True, after=6, center=True)
                    skip_next.add(lookahead)
                    lookahead += 1
            if lookahead < len(paragraphs):
                next_p = paragraphs[lookahead].strip()
                if next_p and re.match(r'^\d+\.\d+\s', next_p.split("\n")[0]):
                    y += 8
                    for line in next_p.split("\n"):
                        s = line.strip()
                        if s:
                            draw_justified(s, 9.5, after=3, indent=15,
                                           color=(0.4, 0.4, 0.4), justify=False)
                    skip_next.add(lookahead)
            y += 15
            continue

        if first.isupper() and 3 < len(first) < 80 and not first.startswith("FIGURE") and not first.startswith("TABLE"):
            y += 10
            draw_justified(first, 13.0, bold=True, after=6, color=BLUE, justify=False)
            body = [l.strip() for l in lines[1:] if l.strip()]
            if body:
                draw_justified(" ".join(body), 10.0, after=6)
            continue

        sec_match = re.match(r'^(\d+\.\d+(?:\.\d+)?)\s+\S', first)
        if sec_match:
            y += 8
            full = " ".join(l.strip() for l in lines if l.strip())
            draw_justified(full, 11.0, bold=True, after=6, color=BLUE, justify=False)
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

        if first.startswith("ISBN:") or first.startswith("DOI:") or first.startswith("Copyright"):
            full = " ".join(l.strip() for l in lines if l.strip())
            draw_justified(full, 8.0, after=4, color=(0.4, 0.4, 0.4))
            continue

        full = " ".join(l.strip() for l in lines if l.strip())
        draw_justified(full, 10.0, after=6)

    # Build chapter destination map
    chap_page_map = {}
    for chap_num, title, page_idx in chapter_bookmarks:
        chap_page_map[f"chapter_{chap_num}"] = page_idx

    # Add clickable links from TOC entries to chapter pages
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
            link = {
                "kind": fitz.LINK_GOTO,
                "from": link_rect,
                "page": target_page,
                "to": fitz.Point(ml, mt),
            }
            toc_page.insert_link(link)

    # Build PDF outline (bookmarks sidebar)
    pdf_toc = []
    for chap_num, title, page_idx in chapter_bookmarks:
        pdf_toc.append([1, title, page_idx + 1])
    if pdf_toc:
        doc.set_toc(pdf_toc)

    doc.save(str(output), deflate=True, garbage=4)
    n_pages = len(doc)
    doc.close()
    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"✓ {output}  ({n_pages} pages, {size_mb:.1f} MB)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("markdown", type=Path)
    parser.add_argument("-f", "--figures", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("-c", "--cover", type=Path, default=None)
    args = parser.parse_args()
    compile_pdf(args.markdown, args.figures, args.output, cover_path=args.cover)


if __name__ == "__main__":
    main()
