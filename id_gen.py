"""
ID Card Generator
------------------
A PyQt5 desktop app that captures a webcam photo and generates a
professional ID card with an embedded QR code.

This is a restyled, extended version of the original single-screen
project. It now has two screens (Input Form -> Card Preview) that
share one consistent visual theme, and two extended features:

  1. Photo Capture  - live embedded camera preview inside the app
     window (Retake / Confirm) instead of a blocking OpenCV popup.
  2. Card Design     - a real card layout (gradient header, rounded
     photo frame, footer band) instead of plain text on a blank
     canvas, with a QR code that encodes structured multi-field data
     at high error-correction (so it still scans even if partially
     covered or printed small).

Run:
    python id_gen.py

Requirements:
    PyQt5, Pillow, opencv-python, qrcode[pil]
"""

import sys
import os
import json
import random
import datetime

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap

from PIL import Image, ImageDraw, ImageFont, ImageOps
import qrcode

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


# --------------------------------------------------------------------------
# Shared theme — one palette + one stylesheet used by every screen so the
# app reads as a single, consistent product instead of a stack of forms.
# --------------------------------------------------------------------------
PALETTE = {
    "primary": "#2F5DFF",
    "primary_dark": "#1E3FCC",
    "accent": "#00C2A8",
    "bg": "#F2F5FC",
    "card": "#FFFFFF",
    "text": "#1B2130",
    "muted": "#69708A",
    "border": "#E1E5F0",
    "danger": "#E5484D",
}

APP_STYLESHEET = f"""
QWidget {{
    background: {PALETTE['bg']};
    color: {PALETTE['text']};
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 10.5pt;
}}
QFrame#card {{
    background: {PALETTE['card']};
    border: 1px solid {PALETTE['border']};
    border-radius: 16px;
}}
QFrame#headerBar {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                 stop:0 {PALETTE['primary']},
                                 stop:1 {PALETTE['accent']});
    border-radius: 0px;
}}
QLabel#appTitle {{
    color: white;
    font-size: 18pt;
    font-weight: 600;
}}
QLabel#appSubtitle {{
    color: rgba(255,255,255,0.85);
    font-size: 9.5pt;
}}
QLabel#sectionTitle {{
    font-size: 12pt;
    font-weight: 600;
    color: {PALETTE['text']};
}}
QLabel[role="fieldLabel"] {{
    color: {PALETTE['muted']};
    font-weight: 600;
    font-size: 9.5pt;
}}
QLabel#cameraHint {{
    color: {PALETTE['muted']};
    font-size: 9pt;
}}
QLineEdit, QComboBox {{
    background: white;
    border: 1px solid {PALETTE['border']};
    border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: {PALETTE['primary']};
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1.5px solid {PALETTE['primary']};
}}
QPushButton {{
    background: {PALETTE['primary']};
    color: white;
    border: none;
    border-radius: 10px;
    padding: 10px 18px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {PALETTE['primary_dark']};
}}
QPushButton:disabled {{
    background: #BEC8E8;
    color: #F1F3FB;
}}
QPushButton[role="secondary"] {{
    background: white;
    color: {PALETTE['primary']};
    border: 1.5px solid {PALETTE['primary']};
}}
QPushButton[role="secondary"]:hover {{
    background: {PALETTE['bg']};
}}
QPushButton[role="danger"] {{
    background: {PALETTE['danger']};
}}
QPushButton[role="danger"]:hover {{
    background: #C7383C;
}}
QFrame#cameraFrame {{
    background: #0B1020;
    border-radius: 12px;
}}
QFrame#qrPreview {{
    background: white;
    border: 1px solid {PALETTE['border']};
    border-radius: 10px;
}}
"""


def make_shadow(blur=24, y_offset=6, alpha=40):
    effect = QtWidgets.QGraphicsDropShadowEffect()
    effect.setBlurRadius(blur)
    effect.setOffset(0, y_offset)
    effect.setColor(QtGui.QColor(20, 25, 40, alpha))
    return effect


def header_bar(title, subtitle):
    bar = QtWidgets.QFrame()
    bar.setObjectName("headerBar")
    bar.setFixedHeight(84)
    layout = QtWidgets.QVBoxLayout(bar)
    layout.setContentsMargins(28, 12, 28, 12)
    layout.setSpacing(2)
    t = QtWidgets.QLabel(title)
    t.setObjectName("appTitle")
    s = QtWidgets.QLabel(subtitle)
    s.setObjectName("appSubtitle")
    layout.addWidget(t)
    layout.addWidget(s)
    return bar


def field_label(text):
    lbl = QtWidgets.QLabel(text)
    lbl.setProperty("role", "fieldLabel")
    return lbl


# --------------------------------------------------------------------------
# Extended feature 1: embedded live camera preview (replaces cv2.imshow)
# --------------------------------------------------------------------------
class CameraWidget(QtWidgets.QWidget):
    """Live webcam preview embedded directly in the app window, with
    Capture / Retake so the user can see the shot before committing to it,
    instead of the original blind 'press Enter over an OpenCV popup' flow.
    """

    photo_captured = QtCore.pyqtSignal(str)

    def __init__(self, save_path="person.jpg", parent=None):
        super().__init__(parent)
        self.save_path = save_path
        self.capture_dev = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_frame)
        self._last_frame = None
        self._frozen = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.view_frame = QtWidgets.QFrame()
        self.view_frame.setObjectName("cameraFrame")
        self.view_frame.setFixedSize(260, 200)
        vf_layout = QtWidgets.QVBoxLayout(self.view_frame)
        vf_layout.setContentsMargins(0, 0, 0, 0)

        self.video_label = QtWidgets.QLabel("Camera off")
        self.video_label.setStyleSheet("color:#9AA4C7; background:transparent;")
        self.video_label.setAlignment(Qt.AlignCenter)
        vf_layout.addWidget(self.video_label)
        layout.addWidget(self.view_frame, alignment=Qt.AlignHCenter)

        hint = QtWidgets.QLabel("Center your face in frame, then capture.")
        hint.setObjectName("cameraHint")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        btn_row = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton("Start Camera")
        self.btn_capture = QtWidgets.QPushButton("Capture")
        self.btn_retake = QtWidgets.QPushButton("Retake")
        self.btn_retake.setProperty("role", "secondary")
        self.btn_capture.setEnabled(False)
        self.btn_retake.setEnabled(False)

        self.btn_start.clicked.connect(self.start_camera)
        self.btn_capture.clicked.connect(self.capture_frame)
        self.btn_retake.clicked.connect(self.retake)

        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_capture)
        btn_row.addWidget(self.btn_retake)
        layout.addLayout(btn_row)

    def start_camera(self):
        if not HAS_CV2:
            QtWidgets.QMessageBox.warning(
                self, "Camera unavailable",
                "opencv-python is not installed, so live capture is disabled.\n"
                "Install it with: pip install opencv-python"
            )
            return
        self.capture_dev = cv2.VideoCapture(0)
        if not self.capture_dev.isOpened():
            QtWidgets.QMessageBox.warning(self, "Camera error", "Could not open webcam.")
            return
        self._frozen = False
        self.btn_start.setEnabled(False)
        self.btn_capture.setEnabled(True)
        self.timer.start(30)

    def _update_frame(self):
        if self._frozen or self.capture_dev is None:
            return
        ok, frame = self.capture_dev.read()
        if not ok:
            return
        frame = cv2.flip(frame, 1)
        self._last_frame = frame
        self._show_frame(frame)

    def _show_frame(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg).scaled(
            self.view_frame.width(), self.view_frame.height(),
            Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        self.video_label.setPixmap(pix)

    def capture_frame(self):
        if self._last_frame is None:
            return
        frame = self._last_frame
        h, w = frame.shape[:2]
        # crop to a centered square-ish portrait, matching the original's
        # center-crop behaviour but keeping it as a helper instead of magic
        # numbers scattered through generate_idcard().
        start_row, start_col = int(h * 0.10), int(w * 0.25)
        end_row, end_col = int(h * 0.95), int(w * 0.75)
        cropped = frame[start_row:end_row, start_col:end_col]
        cv2.imwrite(self.save_path, cropped)

        self._frozen = True
        self.timer.stop()
        if self.capture_dev is not None:
            self.capture_dev.release()
            self.capture_dev = None
        self._show_frame(cropped)
        self.btn_capture.setEnabled(False)
        self.btn_retake.setEnabled(True)
        self.photo_captured.emit(self.save_path)

    def retake(self):
        self.btn_retake.setEnabled(False)
        self.btn_start.setEnabled(True)
        self.video_label.setPixmap(QPixmap())
        self.video_label.setText("Camera off")

    def has_photo(self):
        return self._frozen and os.path.exists(self.save_path)


# --------------------------------------------------------------------------
# Extended feature 2: card design + structured, high-EC QR payload
# --------------------------------------------------------------------------
def _vertical_gradient(size, top_rgb, bottom_rgb):
    w, h = size
    grad = Image.new("RGB", (1, h), color=0)
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top_rgb[0] + (bottom_rgb[0] - top_rgb[0]) * t)
        g = int(top_rgb[1] + (bottom_rgb[1] - top_rgb[1]) * t)
        b = int(top_rgb[2] + (bottom_rgb[2] - top_rgb[2]) * t)
        grad.putpixel((0, y), (r, g, b))
    return grad.resize((w, h))


def _hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _load_font(size, bold=False):
    candidates = (
        ["arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold
        else ["arial.ttf", "DejaVuSans.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _rounded_photo(photo_path, size=(230, 230), radius=20):
    photo = Image.open(photo_path).convert("RGB")
    photo = ImageOps.fit(photo, size, Image.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0], size[1]], radius=radius, fill=255)
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.paste(photo, (0, 0), mask)
    return out


def build_qr_payload(data, id_no):
    """Structured payload (vs. the original's plain 'company+id' string) so
    a scanner sees every field, not just company + id."""
    payload = {
        "id": id_no,
        "company": data["company"],
        "name": data["name"],
        "gender": data.get("gender", ""),
        "phone": data.get("phone", ""),
        "issued": datetime.date.today().isoformat(),
    }
    return json.dumps(payload, separators=(",", ":"))


def make_qr_image(payload_text, box_size=6):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,  # tolerates a logo/damage
        box_size=box_size,
        border=2,
    )
    qr.add_data(payload_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    # Optional center logo overlay if the user drops a logo.png next to the
    # script — demonstrates the extension without requiring an asset.
    logo_path = "logo.png"
    if os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGBA")
        logo_size = img.size[0] // 4
        logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
        pad = 8
        pad_box = Image.new("RGB", (logo_size + pad * 2, logo_size + pad * 2), "white")
        pos = ((img.size[0] - pad_box.size[0]) // 2, (img.size[1] - pad_box.size[1]) // 2)
        img.paste(pad_box, pos)
        img.paste(logo, (pos[0] + pad, pos[1] + pad), logo)
    return img


def generate_id_card(data, photo_path, out_path="card.png"):
    """Builds a real card layout: gradient header, rounded photo, clean
    field grid, footer band with QR + issue date — replacing the original
    'draw text at hardcoded coordinates on a blank canvas' approach."""
    W, H = 1000, 620
    primary = _hex_to_rgb(PALETTE["primary"])
    accent = _hex_to_rgb(PALETTE["accent"])
    text_color = _hex_to_rgb(PALETTE["text"])
    muted = _hex_to_rgb(PALETTE["muted"])

    card = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(card)

    # Header band (gradient)
    header_h = 150
    header = _vertical_gradient((W, header_h), primary, accent)
    card.paste(header, (0, 0))

    font_company = _load_font(38, bold=True)
    font_tag = _load_font(16)
    draw.text((40, 34), data["company"] or "Company Name", font=font_company, fill="white")
    draw.text((40, 82), "OFFICIAL IDENTIFICATION CARD", font=font_tag, fill=(255, 255, 255))

    id_no = data["id_no"]
    font_id = _load_font(22, bold=True)
    id_text = f"ID: {id_no}"
    id_w = draw.textlength(id_text, font=font_id)
    draw.text((W - 40 - id_w, 95), id_text, font=font_id, fill="white")

    # Photo (rounded, with a subtle border) top-right of the body
    photo_size = (230, 230)
    photo_pos = (W - 40 - photo_size[0], header_h + 25)
    if os.path.exists(photo_path):
        photo = _rounded_photo(photo_path, size=photo_size, radius=18)
        card.paste(photo, photo_pos, photo)
    else:
        draw.rounded_rectangle(
            [photo_pos, (photo_pos[0] + photo_size[0], photo_pos[1] + photo_size[1])],
            radius=18, outline=muted, width=2
        )
        draw.text((photo_pos[0] + 60, photo_pos[1] + 100), "No Photo", font=font_tag, fill=muted)

    # Field grid, left column
    font_label = _load_font(16, bold=True)
    font_value = _load_font(24)
    fields = [
        ("FULL NAME", data["name"]),
        ("GENDER", data["gender"]),
        ("PHONE", data["phone"]),
        ("ADDRESS", data["address"]),
    ]
    y = header_h + 35
    for label, value in fields:
        draw.text((40, y), label, font=font_label, fill=muted)
        draw.text((40, y + 22), value or "-", font=font_value, fill=text_color)
        y += 90

    # Footer divider + QR + issue date
    footer_y = H - 130
    draw.line([(40, footer_y), (W - 40, footer_y)], fill=(*muted, 255) if len(muted) == 4 else muted, width=1)

    qr_payload = build_qr_payload(data, id_no)
    qr_img = make_qr_image(qr_payload, box_size=4)
    qr_img = qr_img.resize((100, 100), Image.LANCZOS)
    card.paste(qr_img, (40, footer_y + 15))

    font_footer_label = _load_font(14, bold=True)
    font_footer_value = _load_font(14)
    draw.text((160, footer_y + 20), "ISSUED", font=font_footer_label, fill=muted)
    draw.text((160, footer_y + 40), datetime.date.today().strftime("%d %b %Y"), font=font_footer_value, fill=text_color)

    draw.text((160, footer_y + 68), "SCAN TO VERIFY", font=font_footer_label, fill=muted)

    card.save(out_path)
    return out_path, qr_img


# --------------------------------------------------------------------------
# Screen 1: input form (with embedded camera capture)
# --------------------------------------------------------------------------
class FormWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ID Card Generator")
        self.resize(880, 640)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(header_bar("ID Card Generator", "Step 1 of 2 — Enter details & capture a photo"))

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QHBoxLayout(body)
        body_layout.setContentsMargins(28, 24, 28, 24)
        body_layout.setSpacing(20)
        outer.addWidget(body)

        # Left card: form fields
        form_card = QtWidgets.QFrame()
        form_card.setObjectName("card")
        form_card.setGraphicsEffect(make_shadow())
        form_layout = QtWidgets.QVBoxLayout(form_card)
        form_layout.setContentsMargins(24, 22, 24, 22)
        form_layout.setSpacing(10)

        title = QtWidgets.QLabel("Cardholder Details")
        title.setObjectName("sectionTitle")
        form_layout.addWidget(title)

        grid = QtWidgets.QFormLayout()
        grid.setVerticalSpacing(12)
        grid.setLabelAlignment(Qt.AlignLeft)

        self.company_edit = QtWidgets.QLineEdit()
        self.name_edit = QtWidgets.QLineEdit()
        self.gender_combo = QtWidgets.QComboBox()
        self.gender_combo.addItems(["Female", "Male", "Other", "Prefer not to say"])
        self.address_edit = QtWidgets.QLineEdit()
        self.phone_edit = QtWidgets.QLineEdit()

        grid.addRow(field_label("Company Name"), self.company_edit)
        grid.addRow(field_label("Full Name"), self.name_edit)
        grid.addRow(field_label("Gender"), self.gender_combo)
        grid.addRow(field_label("Address"), self.address_edit)
        grid.addRow(field_label("Phone Number"), self.phone_edit)

        form_layout.addLayout(grid)
        form_layout.addStretch(1)

        self.generate_btn = QtWidgets.QPushButton("Generate ID Card  →")
        self.generate_btn.clicked.connect(self.on_generate)
        form_layout.addWidget(self.generate_btn)

        # Right card: camera
        cam_card = QtWidgets.QFrame()
        cam_card.setObjectName("card")
        cam_card.setGraphicsEffect(make_shadow())
        cam_card.setFixedWidth(320)
        cam_layout = QtWidgets.QVBoxLayout(cam_card)
        cam_layout.setContentsMargins(24, 22, 24, 22)
        cam_title = QtWidgets.QLabel("Photo Capture")
        cam_title.setObjectName("sectionTitle")
        cam_layout.addWidget(cam_title)

        self.camera_widget = CameraWidget(save_path="person.jpg")
        cam_layout.addWidget(self.camera_widget)
        cam_layout.addStretch(1)

        body_layout.addWidget(form_card, 3)
        body_layout.addWidget(cam_card, 2)

        self.preview_window = None

    def _collect_data(self):
        return {
            "company": self.company_edit.text().strip(),
            "name": self.name_edit.text().strip(),
            "gender": self.gender_combo.currentText(),
            "address": self.address_edit.text().strip(),
            "phone": self.phone_edit.text().strip(),
        }

    def on_generate(self):
        data = self._collect_data()
        missing = [k for k in ("company", "name") if not data[k]]
        if missing:
            QtWidgets.QMessageBox.warning(
                self, "Missing details", "Please fill in at least Company Name and Full Name."
            )
            return
        if not self.camera_widget.has_photo():
            reply = QtWidgets.QMessageBox.question(
                self, "No photo captured",
                "You haven't captured a photo yet. Generate the card without one?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return

        data["id_no"] = random.randint(1000000, 9000000)
        out_path, _qr_img = generate_id_card(data, self.camera_widget.save_path, out_path="card.png")

        self.preview_window = PreviewWindow(out_path, data)
        self.preview_window.show()
        self.hide()


# --------------------------------------------------------------------------
# Screen 2: card preview (same theme, second "screen" of the flow)
# --------------------------------------------------------------------------
class PreviewWindow(QtWidgets.QMainWindow):
    def __init__(self, card_path, data):
        super().__init__()
        self.card_path = card_path
        self.data = data
        self.setWindowTitle("ID Card Generator — Preview")
        self.resize(760, 700)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(header_bar("ID Card Generator", "Step 2 of 2 — Review your card"))

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(28, 24, 28, 24)
        body_layout.setSpacing(16)
        outer.addWidget(body)

        preview_card = QtWidgets.QFrame()
        preview_card.setObjectName("card")
        preview_card.setGraphicsEffect(make_shadow())
        preview_layout = QtWidgets.QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(24, 22, 24, 22)

        title = QtWidgets.QLabel("Card Preview")
        title.setObjectName("sectionTitle")
        preview_layout.addWidget(title)

        self.image_label = QtWidgets.QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        pix = QPixmap(card_path).scaledToWidth(640, Qt.SmoothTransformation)
        self.image_label.setPixmap(pix)
        preview_layout.addWidget(self.image_label, alignment=Qt.AlignHCenter)

        hint = QtWidgets.QLabel("Tip: click the card to view the QR code full-size.")
        hint.setObjectName("cameraHint")
        hint.setAlignment(Qt.AlignCenter)
        preview_layout.addWidget(hint)
        self.image_label.mousePressEvent = self.show_qr_zoom

        body_layout.addWidget(preview_card)

        btn_row = QtWidgets.QHBoxLayout()
        self.new_card_btn = QtWidgets.QPushButton("New Card")
        self.new_card_btn.setProperty("role", "secondary")
        self.save_btn = QtWidgets.QPushButton("Save As…")
        self.new_card_btn.clicked.connect(self.on_new_card)
        self.save_btn.clicked.connect(self.on_save_as)
        btn_row.addWidget(self.new_card_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.save_btn)
        body_layout.addLayout(btn_row)

        self.form_window = None

    def show_qr_zoom(self, _event):
        payload = build_qr_payload(self.data, self.data["id_no"])
        qr_img = make_qr_image(payload, box_size=10)
        tmp_path = "_qr_zoom_tmp.png"
        qr_img.save(tmp_path)

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("QR Code")
        layout = QtWidgets.QVBoxLayout(dlg)
        lbl = QtWidgets.QLabel()
        lbl.setPixmap(QPixmap(tmp_path))
        layout.addWidget(lbl)
        info = QtWidgets.QLabel("Encodes: id, company, name, gender, phone, issue date.")
        info.setObjectName("cameraHint")
        layout.addWidget(info)
        dlg.exec_()

    def on_new_card(self):
        self.form_window = FormWindow()
        self.form_window.show()
        self.close()

    def on_save_as(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save ID Card", f"{self.data['name'] or 'id_card'}.png", "PNG Image (*.png)"
        )
        if path:
            Image.open(self.card_path).save(path)
            QtWidgets.QMessageBox.information(self, "Saved", f"Card saved to:\n{path}")


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)
    window = FormWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
