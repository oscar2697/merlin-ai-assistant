"""Avatar por capas (Pillow): parpadeo, ojos que siguen al cursor, inclinación
al escuchar, rebote al hablar y modo dormido (se hunde tras la barra).

~/merlin/avatar/ : base, iris_l, iris_r, mask_l, mask_r, blink (.png, mismo lienzo)
Todos los desplazamientos son >= 0 (hacia abajo): así nunca aparece un hueco
entre el personaje y el borde inferior (la barra de tareas).
"""
from __future__ import annotations

import logging
import math
import random
import time
from pathlib import Path

from PIL import Image, ImageChops

logger = logging.getLogger("merlin.gui.rig")


def _resolve_layers(root: Path) -> Path:
    """Layers live in ~/merlin/avatar; the pack may ship them one level deeper
    (~/merlin/avatar/avatar), so use that when base.png is only found there."""
    if (root / "base.png").exists():
        return root
    nested = root / "avatar"
    if (nested / "base.png").exists():
        return nested
    return root


LAYER_DIR = _resolve_layers(Path.home() / "merlin" / "avatar")


class AvatarRig:
    def __init__(self, width: int = 320, folder: Path = LAYER_DIR) -> None:
        self.folder = Path(folder)
        self.ok = False
        self.state = "idle"
        self.gaze = [0.0, 0.0]
        self.eye_center = (0.0, 0.0)
        self._lean = 0.0
        self._awake = 1.0
        self._blink_t0 = None
        self._next_blink = time.time() + 3.0
        try:
            self._load(width)
        except Exception:
            logger.exception("avatar rig failed to load")
            self.ok = False

    def _open(self, name: str, scale: float, mode: str = "RGBA"):
        path = self.folder / f"{name}.png"
        if not path.exists():
            return None
        im = Image.open(path).convert(mode)
        return im.resize((round(im.width * scale), round(im.height * scale)),
                         Image.LANCZOS)

    def _load(self, width: int) -> None:
        base_path = self.folder / "base.png"
        if not base_path.exists():
            return
        with Image.open(base_path) as im0:
            scale = width / im0.width
        self.base = self._open("base", scale)
        self.size = self.base.size
        self.blink = self._open("blink", scale)
        self.blink_half = None
        if self.blink is not None:
            self.blink_half = self.blink.copy()
            self.blink_half.putalpha(
                self.blink.getchannel("A").point(lambda v: v // 2))
        self.eyes = []
        for side in ("l", "r"):
            iris = self._open(f"iris_{side}", scale)
            mask = self._open(f"mask_{side}", scale, "L")
            if iris is None or mask is None:
                continue
            box = iris.getchannel("A").getbbox()
            if not box or iris.size != self.size or mask.size != self.size:
                logger.warning("layer iris_%s/mask_%s ignored (empty or size differs)",
                               side, side)
                continue
            w, h = box[2] - box[0], box[3] - box[1]
            self.eyes.append({"iris": iris, "mask": mask,
                              "tx": w * 0.16, "ty": h * 0.12,   # recorrido del iris
                              "c": ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)})
        if self.eyes:
            self.eye_center = (sum(e["c"][0] for e in self.eyes) / len(self.eyes),
                               sum(e["c"][1] for e in self.eyes) / len(self.eyes))
        self.ok = True

    def frame(self, now: float, look_at=None, level: float | None = None):
        """look_at: (dx, dy) en -1..1, dirección del cursor respecto a los ojos.
        level: 0..1 volumen de la voz (None = rebote sintético al hablar)."""
        st = self.state
        asleep = st == "sleep"
        if st == "thinking":
            tx, ty = 0.7, -0.9                   # mira arriba a la derecha
        elif asleep or look_at is None:
            tx = ty = 0.0
        else:
            tx, ty = look_at
        self.gaze[0] += (tx - self.gaze[0]) * 0.22   # suavizado (no salta)
        self.gaze[1] += (ty - self.gaze[1]) * 0.22
        self._awake += ((0.0 if asleep else 1.0) - self._awake) * 0.12
        self._lean += ((-0.03 if st == "listening" else 0.0) - self._lean) * 0.15

        # parpadeo: 0 = abierto, 0.5 = a medias, 1 = cerrado
        closed = 1.0 if (asleep or self._awake < 0.5) else 0.0
        if not closed:
            if self._blink_t0 is None and now >= self._next_blink:
                self._blink_t0 = now
            if self._blink_t0 is not None:
                t = now - self._blink_t0
                if t < 0.06:
                    closed = 0.5
                elif t < 0.12:
                    closed = 1.0
                elif t < 0.18:
                    closed = 0.5
                else:
                    self._blink_t0 = None
                    self._next_blink = now + random.uniform(2.5, 6.0)

        # desplazamiento vertical (siempre hacia abajo)
        bounce = 0.0
        if st == "speaking":
            lv = 0.6 if level is None else level
            bounce = lv * 4 * abs(math.sin(now * 14))
        breath = (1 + math.sin(now * (0.9 if asleep else 1.6))) / 2 * (1.5 if asleep else 1.0)
        sink = (1 - self._awake) * self.size[1] * 0.06      # dormido = se hunde un poco
        dy = sink + breath + bounce

        frame = self.base.copy()
        if closed < 1.0:
            for e in self.eyes:
                dx_, dy_ = self.gaze[0] * e["tx"], self.gaze[1] * e["ty"]
                moved = e["iris"].transform(self.size, Image.AFFINE,
                                            (1, 0, -dx_, 0, 1, -dy_), Image.BICUBIC)
                r, g, b, a = moved.split()
                moved.putalpha(ImageChops.multiply(a, e["mask"]))
                frame.alpha_composite(moved)
        if self.blink is not None and closed > 0:
            frame.alpha_composite(self.blink if closed >= 1 else self.blink_half)

        h = self.size[1]
        # inclinación = cizalla con la base fija; + desplazamiento vertical
        return frame.transform(self.size, Image.AFFINE,
                               (1, self._lean, -self._lean * h, 0, 1, -dy),
                               Image.BICUBIC)
