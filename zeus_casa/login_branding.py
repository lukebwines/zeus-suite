"""Private ZEUS login photograph: stored in add-on persistent /data, not GitHub.

The served image is intentionally public on the login page, just like the old
login-cortile.jpg asset. Never include credentials or non-public documents here.
Uploaded JPEG metadata is stripped while resizing.
"""
from io import BytesIO
from pathlib import Path
import os
import tempfile
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD = 8 * 1024 * 1024
MAX_OUTPUT = 8 * 1024 * 1024
MAX_PIXELS = 24_000_000
NAME = "login-cortile.jpg"


def photo_path(data_dir):
    return Path(data_dir) / "branding" / NAME


def read_photo(data_dir):
    p = photo_path(data_dir)
    if not p.is_file() or p.is_symlink():
        return None
    if p.stat().st_size > MAX_OUTPUT:
        return None
    return p.read_bytes()


def save_photo(data_dir, raw):
    """Validate JPEG, decode, reduce and strip metadata, then atomically replace."""
    if not isinstance(raw, bytes) or not (128 <= len(raw) <= MAX_UPLOAD):
        raise ValueError("La fotografia deve essere un JPEG di massimo 8 MB.")
    try:
        with Image.open(BytesIO(raw)) as incoming:
            if incoming.format != "JPEG":
                raise ValueError("Formato non valido: carica una fotografia JPEG.")
            width, height = incoming.size
            if width < 200 or height < 200 or width * height > MAX_PIXELS:
                raise ValueError("Dimensioni della fotografia non consentite.")
            photo = ImageOps.exif_transpose(incoming)
            photo.thumbnail((2600, 1800), Image.Resampling.LANCZOS)
            rendered = BytesIO()
            photo.convert("RGB").save(rendered, format="JPEG", quality=85, optimize=True)
            clean = rendered.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError("Immagine JPEG danneggiata o non valida.") from exc
    if len(clean) > MAX_OUTPUT:
        raise ValueError("Immagine troppo grande dopo l'elaborazione.")
    destination = photo_path(data_dir)
    folder = destination.parent
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if folder.is_symlink() or destination.is_symlink():
        raise ValueError("Percorso immagine non sicuro.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".jpg", prefix=".zeus-login-", dir=folder, delete=False) as f:
            temporary = Path(f.name)
            os.chmod(f.name, 0o600)
            f.write(clean)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"ok": True, "bytes": len(clean)}
