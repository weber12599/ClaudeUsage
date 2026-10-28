"""Status-bar title drawing: per account a short label, a tiny two-bar gauge
(5h on top, 7d below — or a single monthly bar) and, optionally, the 5h percentage.

Two styles (config `menubar_style`):
- "color": an NSAttributedString with the gauges as inline image attachments, so
  the text keeps the menubar's own light/dark colour while the bars stay coloured;
- "mono": the same layout drawn in black into one template image, which macOS
  tints like its own status icons (white on a dark bar, inverted when clicked).

Status marks (blocked / expired / no credentials) are SF Symbols, not emoji.
Input is `Registry.menubar_segments()`.
"""

from __future__ import annotations

import math
from typing import List, Optional

from AppKit import (
    NSAttributedString,
    NSBezierPath,
    NSColor,
    NSCompositingOperationSourceAtop,
    NSFont,
    NSFontAttributeName,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSImage,
    NSImageOnly,
    NSImageSymbolConfiguration,
    NSMutableAttributedString,
    NSRectFillUsingOperation,
    NSStatusBar,
    NSTextAttachment,
)
from Foundation import NSMakeRect, NSMakeSize

from .state import pct_bucket

BAR_W, BAR_H, BAR_GAP = 18.0, 3.5, 2.0
SEGMENT_SEP = "   "

# severity -> (SF Symbol, text fallback, colour in "color" style)
_MARKS = {
    "blocked": ("nosign", "⊘", NSColor.systemRedColor),
    "expired": ("exclamationmark.triangle.fill", "!", NSColor.systemOrangeColor),
    "nocreds": ("minus.circle", "—", NSColor.secondaryLabelColor),
}


class _Palette:
    """Colours for one style. In "mono" everything is black + alpha (template image)."""

    def __init__(self, mono: bool):
        self.mono = mono
        black = NSColor.blackColor()
        grey = NSColor.colorWithWhite_alpha_(0.5, 0.35)
        self.track = black.colorWithAlphaComponent_(0.25) if mono else grey
        # An attributed title with no colour draws black even on a dark menubar, so name
        # the dynamic label colour; it resolves against the status button's appearance.
        self.text = black if mono else NSColor.labelColor()
        self.dim_text = black.colorWithAlphaComponent_(0.45) if mono else NSColor.secondaryLabelColor()

    def fill(self, pct: Optional[float], blocked: bool) -> NSColor:
        if self.mono:
            return NSColor.blackColor()
        if blocked:
            return NSColor.systemRedColor()
        return {
            "crit": NSColor.systemRedColor(),
            "warn": NSColor.systemOrangeColor(),
        }.get(pct_bucket(pct), NSColor.systemGreenColor())

    def mark(self, sev: str) -> NSColor:
        return NSColor.blackColor() if self.mono else _MARKS[sev][2]()


def _gauge(bars: List[Optional[float]], blocked: bool, dim: bool, pal: _Palette) -> NSImage:
    rows = bars[:2] or [None, None]
    height = len(rows) * BAR_H + (len(rows) - 1) * BAR_GAP

    def draw(_rect):
        for i, pct in enumerate(rows):
            y = height - (i + 1) * BAR_H - i * BAR_GAP          # first row on top
            r = BAR_H / 2
            pal.track.setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(0, y, BAR_W, BAR_H), r, r).fill()
            frac = 1.0 if blocked else max(0.0, min(1.0, (pct or 0) / 100.0))
            if frac <= 0:
                continue
            color = pal.fill(pct, blocked)
            (color.colorWithAlphaComponent_(0.45) if dim else color).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(0, y, max(BAR_H, BAR_W * frac), BAR_H), r, r).fill()
        return True

    return NSImage.imageWithSize_flipped_drawingHandler_(NSMakeSize(BAR_W, height), False, draw)


def _symbol(name: str, color: NSColor, point_size: float) -> Optional[NSImage]:
    base = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if base is None:
        return None
    cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(point_size, NSFontWeightSemibold)
    base = base.imageWithSymbolConfiguration_(cfg)

    def draw(rect):
        base.drawInRect_(rect)
        color.set()
        NSRectFillUsingOperation(rect, NSCompositingOperationSourceAtop)
        return True

    return NSImage.imageWithSize_flipped_drawingHandler_(base.size(), False, draw)


def _text(s: str, font: NSFont, color: NSColor) -> NSAttributedString:
    attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: color}
    return NSAttributedString.alloc().initWithString_attributes_(s, attrs)


def _image(img: NSImage, font: NSFont) -> NSAttributedString:
    att = NSTextAttachment.alloc().init()
    att.setImage_(img)
    w, h = img.size().width, img.size().height
    att.setBounds_(NSMakeRect(0, round((font.capHeight() - h) / 2), w, h))   # centre on cap height
    return NSAttributedString.attributedStringWithAttachment_(att)


def attributed_title(segments: List[dict], mono: bool = False, show_pct: bool = True) -> NSAttributedString:
    pal = _Palette(mono)
    size = NSFont.menuBarFontOfSize_(0).pointSize()
    label_font = NSFont.systemFontOfSize_weight_(size - 2, NSFontWeightSemibold)
    num_font = NSFont.monospacedDigitSystemFontOfSize_weight_(size - 1, NSFontWeightRegular)

    out = NSMutableAttributedString.alloc().init()
    if not segments:
        out.appendAttributedString_(_text("Claude —", num_font, pal.text))
        return out

    for i, seg in enumerate(segments):
        if i:
            out.appendAttributedString_(_text(SEGMENT_SEP, num_font, pal.text))
        sev = seg["severity"]
        dim = seg["stale"] or sev in ("offline", "nodata")
        out.appendAttributedString_(_text(seg["short"] + " ", label_font, pal.dim_text if dim else pal.text))

        bars = seg["bars"] if sev not in ("expired", "nocreds") else []
        out.appendAttributedString_(_image(_gauge(bars, sev == "blocked", dim, pal), num_font))

        if sev in _MARKS:
            name, fallback, _ = _MARKS[sev]
            sym = _symbol(name, pal.mark(sev), size - 3)
            out.appendAttributedString_(_text(" ", num_font, pal.text))
            out.appendAttributedString_(_image(sym, num_font) if sym is not None
                                        else _text(fallback, num_font, pal.mark(sev)))
        elif show_pct and seg["pct"] is not None:
            pct = f" {'~' if dim else ''}{seg['pct']:.0f}%"
            out.appendAttributedString_(_text(pct, num_font, pal.dim_text if dim else pal.text))
    return out


def _template_image(title: NSAttributedString) -> NSImage:
    sz = title.size()
    h = NSStatusBar.systemStatusBar().thickness()

    def draw(_rect):
        title.drawAtPoint_((0, (h - sz.height) / 2))
        return True

    img = NSImage.imageWithSize_flipped_drawingHandler_(NSMakeSize(math.ceil(sz.width), h), False, draw)
    img.setTemplate_(True)
    return img


def apply(button, segments: List[dict], style: str = "color", show_pct: bool = True) -> None:
    """Draw the status item title onto `button` (an NSStatusBarButton)."""
    mono = style == "mono"
    title = attributed_title(segments, mono=mono, show_pct=show_pct)
    if mono:
        button.setTitle_("")
        button.setImage_(_template_image(title))
        button.setImagePosition_(NSImageOnly)
    else:
        button.setImage_(None)
        button.setAttributedTitle_(title)
