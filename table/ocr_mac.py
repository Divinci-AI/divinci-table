"""On-device text recognition with Apple's Vision framework (macOS only).

The Jetson will need a different backend (e.g. PaddleOCR or Tesseract) behind the same
`read_lines(image_bytes, hints) -> [Line]` function; nothing else in the table server knows
which one is in use.
"""
from __future__ import annotations

from dataclasses import dataclass

import Vision
from Foundation import NSData


@dataclass
class Line:
    text: str
    confidence: float
    box: tuple[float, float, float, float]   # normalised x, y, w, h; origin bottom-left


def read_lines(image_bytes: bytes, hints: list[str] | None = None) -> list[Line]:
    """Recognise every text line in a JPEG/PNG. `hints` (card names) bias the recogniser toward
    words it would otherwise misread; language correction stays off because card names are not
    dictionary words."""
    data = NSData.dataWithBytes_length_(image_bytes, len(image_bytes))
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    if hints:
        req.setCustomWords_(hints)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision text recognition failed: {err}")
    out = []
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        bb = obs.boundingBox()
        out.append(Line(str(cands[0].string()), float(cands[0].confidence()),
                        (bb.origin.x, bb.origin.y, bb.size.width, bb.size.height)))
    return out
