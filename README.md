# ID Card Generator

A PyQt5 desktop app that captures a webcam photo and generates a professional
ID card with an embedded QR code — now restyled as a consistent two-screen
flow (**Details & Capture → Preview**), with two extended core features.

## What changed from the original

**Unified styling** — one palette + one QSS stylesheet (`APP_STYLESHEET`)
is shared by every screen: gradient header bar, card-style panels with
soft shadows, consistent buttons/inputs. Nothing is styled ad hoc anymore.

**Extended: Photo Capture**
- Live camera feed embedded directly in the window (`CameraWidget`) instead
  of a blocking `cv2.imshow` popup.
- Explicit **Start Camera → Capture → Retake** flow, so you see the shot
  before committing, instead of guessing when to hit Enter.

**Extended: Card Design**
- Real layout: gradient header band, rounded-corner photo, aligned field
  grid, footer band — instead of text drawn at hardcoded coordinates on a
  blank canvas.
- QR code now encodes a structured JSON payload (id, company, name, gender,
  phone, issue date) at high error-correction (`ERROR_CORRECT_H`), so it
  still scans if partially damaged or printed small. Drop a `logo.png`
  next to the script to auto-overlay a logo in the QR's center.
- Click the card in the Preview screen to view the QR full-size.

## Features

- 📷 Photo Capture — live embedded preview, capture/retake
- 🎨 Custom ID Card Design — gradient header, rounded photo, footer band
- 🔲 QR Code Integration — structured, high-error-correction payload
- 📝 Easy Data Input — simple form (company, name, gender, address, phone)

## Requirements

- Python 3.x
- PyQt5
- Pillow (PIL)
- opencv-python (cv2)
- qrcode[pil]

## Installation

```bash
pip install PyQt5 Pillow opencv-python qrcode[pil]
```

## Run

```bash
python id_gen.py
```

No `.ui` file is needed — the interface is built directly in code
(`id_gen.py`), so there's a single source of truth for the UI instead of
the form being split across a `.py` file and a separate Qt Designer `.ui`
file.
