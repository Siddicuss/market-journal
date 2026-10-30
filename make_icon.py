"""Draw the app icon (teal tile, rising line) into build/icon.ico without image libraries."""
import math
import struct
from pathlib import Path


def _segment_distance(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _pixel(x, y):
    """RGBA for a point in the 64x64 design space."""
    r = 14
    cx, cy = min(max(x, r), 64 - r), min(max(y, r), 64 - r)
    edge = r - math.hypot(x - cx, y - cy)
    if edge < -0.5:
        return 0, 0, 0, 0
    alpha = int(255 * min(1.0, edge + 0.5))
    if math.hypot(x - 50, y - 18) <= 5:
        return 0xfb, 0xbf, 0x24, alpha
    points = [(12, 44), (24, 32), (32, 38), (50, 18)]
    d = min(_segment_distance(x, y, *points[i], *points[i + 1]) for i in range(3))
    if d <= 3:
        return 255, 255, 255, alpha
    return 0x0f, 0x76, 0x6e, alpha


def _bmp(size):
    rows = []
    for row in range(size - 1, -1, -1):  # BMP rows go bottom-up
        line = bytearray()
        for col in range(size):
            r, g, b, a = _pixel((col + 0.5) * 64 / size, (row + 0.5) * 64 / size)
            line += bytes((b, g, r, a))
        rows.append(bytes(line))
    pixels = b''.join(rows)
    mask = b'\x00' * (((size + 31) // 32) * 4 * size)
    header = struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0, len(pixels) + len(mask), 0, 0, 0, 0)
    return header + pixels + mask


def write_icon(path, sizes=(16, 32, 48, 64, 128, 256)):
    images = [_bmp(s) for s in sizes]
    offset = 6 + 16 * len(sizes)
    out = bytearray(struct.pack('<HHH', 0, 1, len(sizes)))
    for size, data in zip(sizes, images):
        out += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for data in images:
        out += data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


if __name__ == '__main__':
    write_icon(Path(__file__).resolve().parent / 'build' / 'icon.ico')
