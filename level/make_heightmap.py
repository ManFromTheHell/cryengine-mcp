"""Generate a 1024x1024 float32 heightmap for the CryEngine level.

Why not the engine's own generator: CHeightmap::GenerateTerrain always calls
Normalize() and resets max height to terrainSize/4 + waterLevel, and its
"cover"/"sharpness" arguments are accepted and then ignored. It can only ever
hand back a smooth dome. Everything that makes terrain read as landscape --
ridge lines, drainage, flat valley floors, a shoreline -- has to be built here
and installed with bridge.terrain.set_heights_raw.

Three ideas do most of the work:

  * ridged multifractal (1 - |noise|, accumulated with a per-octave weight)
    produces creases rather than blobs, which is what a mountain ridge is;
  * domain warping (offsetting the sample point by another noise field) bends
    those ridges so they meander instead of running in straight lines;
  * droplet erosion carves the drainage network. Nothing else makes terrain
    look eroded, because the dendritic valley pattern is not a noise function --
    it is the trace of water actually running downhill.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)

import numpy as np

N = 1024
SEED = 20260911
rng = np.random.default_rng(SEED)


# ----------------------------------------------------------------- noise
def _lattice(res, gen):
    """Value noise on a res x res lattice, bicubically upsampled to N x N.

    Value noise rather than Perlin: at these octave counts the difference is
    invisible once ridged and warped, and this needs no gradient table."""
    g = gen.random((res + 3, res + 3)).astype(np.float32)

    # Sample positions in lattice space.
    t = np.linspace(0, res, N, endpoint=False, dtype=np.float32)
    i0 = np.floor(t).astype(np.int32)
    f = (t - i0).astype(np.float32)

    def cubic(p0, p1, p2, p3, x):
        # Catmull-Rom: smooth and interpolating, so lattice points are exact.
        return (
            p1
            + 0.5 * x * (p2 - p0
                         + x * (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3
                                + x * (3.0 * (p1 - p2) + p3 - p0)))
        )

    # Interpolate along x for the four needed rows, then along y.
    cols = [g[:, np.clip(i0 + k, 0, res + 2)] for k in (-1, 0, 1, 2)]
    rowwise = cubic(cols[0], cols[1], cols[2], cols[3], f[None, :])

    rows = [rowwise[np.clip(i0 + k, 0, res + 2), :] for k in (-1, 0, 1, 2)]
    return cubic(rows[0], rows[1], rows[2], rows[3], f[:, None])


def fbm(octaves, base_res, lacunarity=2.0, gain=0.5, ridged=False, seed=0):
    gen = np.random.default_rng(SEED + seed)
    total = np.zeros((N, N), np.float32)
    amp, res, norm = 1.0, base_res, 0.0
    weight = np.ones((N, N), np.float32)
    for _ in range(octaves):
        n = _lattice(int(round(res)), gen)
        if ridged:
            n = 1.0 - np.abs(n * 2.0 - 1.0)
            n *= n
            # Weighting each octave by the previous one is what keeps ridges
            # sharp at the crest and lets detail fill the flanks.
            n = n * weight
            weight = np.clip(n * 2.0, 0.0, 1.0)
        total += n * amp
        norm += amp
        amp *= gain
        res *= lacunarity
    return total / norm


def warp(field_seed, strength, res):
    """Offset sampling coordinates by a low-frequency field, so ridges bend."""
    dx = (fbm(3, res, seed=field_seed) - 0.5) * strength
    dy = (fbm(3, res, seed=field_seed + 40) - 0.5) * strength
    return dx, dy


def sample_warped(field, dx, dy):
    ys, xs = np.meshgrid(np.arange(N), np.arange(N), indexing="ij")
    sx = np.clip(xs + dx, 0, N - 1)
    sy = np.clip(ys + dy, 0, N - 1)
    x0 = np.floor(sx).astype(np.int32)
    y0 = np.floor(sy).astype(np.int32)
    x1 = np.minimum(x0 + 1, N - 1)
    y1 = np.minimum(y0 + 1, N - 1)
    fx = (sx - x0).astype(np.float32)
    fy = (sy - y0).astype(np.float32)
    top = field[y0, x0] * (1 - fx) + field[y0, x1] * fx
    bot = field[y1, x0] * (1 - fx) + field[y1, x1] * fx
    return top * (1 - fy) + bot * fy


def norm01(a):
    lo, hi = float(a.min()), float(a.max())
    return (a - lo) / (hi - lo) if hi > lo else a * 0.0


# ----------------------------------------------------------------- erosion
def erode(height, droplets=260000, batch=20000, steps=64,
          inertia=0.05, capacity=3.0, deposition=0.15, erosion=0.25,
          evaporation=0.02, gravity=4.0, radius=2,
          max_speed=3.0, max_capacity=6.0, max_edit=0.35):
    """Droplet erosion, with droplets stepped in parallel batches.

    Per-droplet Python would take hours at this resolution; carrying tens of
    thousands of droplets as arrays makes it a couple of minutes. The physics is
    the standard one: a droplet picks up sediment where flow is fast and the
    slope steep, drops it where it slows, and evaporates."""
    h = height.copy()

    # Brush weights spread each droplet's edit over a small disc, otherwise the
    # result is single-pixel noise rather than a valley.
    offs = []
    for oy in range(-radius, radius + 1):
        for ox in range(-radius, radius + 1):
            d = np.hypot(ox, oy)
            if d <= radius:
                offs.append((ox, oy, 1.0 - d / (radius + 1e-6)))
    wsum = sum(w for _, _, w in offs)
    offs = [(ox, oy, w / wsum) for ox, oy, w in offs]

    def grad_and_height(px, py):
        x0 = np.clip(px.astype(np.int32), 0, N - 2)
        y0 = np.clip(py.astype(np.int32), 0, N - 2)
        fx = px - x0
        fy = py - y0
        h00 = h[y0, x0]
        h10 = h[y0, x0 + 1]
        h01 = h[y0 + 1, x0]
        h11 = h[y0 + 1, x0 + 1]
        gx = (h10 - h00) * (1 - fy) + (h11 - h01) * fy
        gy = (h01 - h00) * (1 - fx) + (h11 - h10) * fx
        hh = (h00 * (1 - fx) + h10 * fx) * (1 - fy) + (h01 * (1 - fx) + h11 * fx) * fy
        return gx, gy, hh, x0, y0, fx, fy

    done = 0
    while done < droplets:
        n = min(batch, droplets - done)
        done += n
        px = rng.uniform(1, N - 2, n).astype(np.float32)
        py = rng.uniform(1, N - 2, n).astype(np.float32)
        dx = np.zeros(n, np.float32)
        dy = np.zeros(n, np.float32)
        speed = np.ones(n, np.float32)
        water = np.ones(n, np.float32)
        sediment = np.zeros(n, np.float32)
        alive = np.ones(n, bool)

        for _ in range(steps):
            if not alive.any():
                break
            gx, gy, h_old, x0, y0, fx, fy = grad_and_height(px, py)

            dx = dx * inertia - gx * (1 - inertia)
            dy = dy * inertia - gy * (1 - inertia)
            length = np.hypot(dx, dy)
            flat = length < 1e-5
            # A droplet on flat ground has no downhill to follow; give it a
            # random shove instead of letting it stall in place.
            if flat.any():
                ang = rng.uniform(0, 2 * np.pi, int(flat.sum())).astype(np.float32)
                dx[flat] = np.cos(ang)
                dy[flat] = np.sin(ang)
                length = np.hypot(dx, dy)
            dx /= np.maximum(length, 1e-6)
            dy /= np.maximum(length, 1e-6)

            nx = px + dx
            ny = py + dy
            inside = (nx > 1) & (nx < N - 2) & (ny > 1) & (ny < N - 2)
            alive &= inside
            if not alive.any():
                break

            _, _, h_new, _, _, _, _ = grad_and_height(np.clip(nx, 1, N - 2), np.clip(ny, 1, N - 2))
            dh = h_new - h_old

            # Every term here needs a ceiling. Without one the loop is a
            # positive feedback: a steep step raises speed, higher speed raises
            # carrying capacity, more capacity digs deeper, which makes the next
            # step steeper. The first version of this ran away to NaN inside one
            # batch -- so speed, capacity and the per-step edit are all capped.
            cap = np.clip(np.maximum(-dh, 0.0) * speed * water * capacity, 0.0, max_capacity)

            drop = np.where(dh > 0,
                            np.minimum(dh, sediment),
                            (sediment - cap) * deposition)
            drop = np.where(sediment > cap, np.maximum(drop, 0.0), drop)
            take = np.where((sediment < cap) & (dh <= 0),
                            np.minimum((cap - sediment) * erosion, -dh),
                            0.0)

            delta = np.clip(drop - take, -max_edit, max_edit)
            delta = np.where(alive, delta, 0.0)
            sediment = np.clip(sediment + np.where(alive, take - drop, 0.0), 0.0, max_capacity)

            xi = np.clip(px.astype(np.int32), radius, N - 1 - radius)
            yi = np.clip(py.astype(np.int32), radius, N - 1 - radius)
            for ox, oy, w in offs:
                np.add.at(h, (yi + oy, xi + ox), delta * w)

            speed = np.clip(np.sqrt(np.maximum(speed * speed + (-dh) * gravity, 0.01)),
                            0.01, max_speed)
            water *= (1 - evaporation)
            # Dead droplets are parked in bounds rather than left to drift off
            # the grid: their contributions are already masked out, and a
            # coordinate that wanders to inf poisons every gather afterwards.
            px = np.where(alive, nx, N * 0.5)
            py = np.where(alive, ny, N * 0.5)

    if not np.isfinite(h).all():
        raise RuntimeError("erosion produced non-finite heights -- the clamps failed")
    return h


# ----------------------------------------------------------------- compose
def ridge_backbone(points, width, sharp=1.6):
    """Height field from distance to a curved path -- a mountain RANGE.

    Isotropic ridged noise alone gives uniformly bumpy ground: plenty of
    texture, no landforms. A level 1 km across wants two or three things you
    can point at, so the large scale is authored (a range here, a basin there)
    and noise is left to decorate it."""
    ys, xx = np.mgrid[0:N, 0:N].astype(np.float32)
    pts = np.asarray(points, np.float32) * N

    # Densify the polyline so distance-to-segment is well approximated by
    # distance-to-vertex.
    dense = []
    for a, b in zip(pts[:-1], pts[1:]):
        steps = max(int(np.hypot(*(b - a)) / 2.0), 2)
        for t in np.linspace(0, 1, steps, endpoint=False):
            dense.append(a + (b - a) * t)
    dense.append(pts[-1])
    dense = np.asarray(dense, np.float32)

    d2 = np.full((N, N), np.inf, np.float32)
    for px, py in dense:
        np.minimum(d2, (xx - px) ** 2 + (ys - py) ** 2, out=d2)
    d = np.sqrt(d2)

    prof = np.exp(-((d / width) ** 2))
    return np.clip(prof, 0.0, 1.0) ** (1.0 / sharp)


def build():
    yy, xx = np.mgrid[0:N, 0:N].astype(np.float32)

    # --- large scale: one range with a bend in it, plus a lower spur --------
    main = ridge_backbone([(0.92, 0.06), (0.74, 0.30), (0.70, 0.52),
                           (0.55, 0.72), (0.40, 0.95)], width=0.20 * N)
    spur = ridge_backbone([(0.62, 0.10), (0.44, 0.24), (0.28, 0.30)],
                          width=0.11 * N)

    # Warping the range mask makes the crest wander instead of tracing the
    # polyline exactly.
    dx, dy = warp(11, 70.0, 3)
    main = sample_warped(main, dx, dy)
    spur = sample_warped(spur, dx, dy)

    massif = np.clip(main + spur * 0.55, 0.0, 1.0)

    # --- ridged detail, confined to the range ------------------------------
    dx2, dy2 = warp(21, 55.0, 4)
    ridged = sample_warped(fbm(5, 4, ridged=True, seed=31), dx2, dy2)
    ridged = norm01(ridged) ** 1.3

    # --- gentle relief everywhere ------------------------------------------
    hills = norm01(fbm(5, 5, gain=0.5, seed=32))
    fine = norm01(fbm(4, 16, gain=0.45, seed=33)) - 0.5

    # Peaks come almost entirely from the range; the plain keeps a little roll
    # so it is not a billiard table, and fine detail is scaled down on flat
    # ground so the lowland does not look like sandpaper.
    h = (massif * (0.35 + 0.65 * ridged) * 1.00
         + hills * (0.10 + 0.22 * massif)
         + fine * (0.012 + 0.05 * massif))

    # --- basin for the lake, in the low corner -----------------------------
    bx, by = 0.24 * N, 0.80 * N
    basin = np.exp(-(((xx - bx) ** 2 + (yy - by) ** 2) / (2 * (0.15 * N) ** 2)))
    h -= basin * 0.22
    h = norm01(h)

    LOW, HIGH = 4.0, 152.0
    h = (LOW + h * (HIGH - LOW)).astype(np.float32)

    print("pre-erosion   min %.1f max %.1f mean %.1f" % (h.min(), h.max(), h.mean()))
    before = h.copy()
    h = erode(h, droplets=650000, batch=25000, steps=80, max_edit=0.8)
    moved = float(np.abs(h - before).mean())
    print("post-erosion  min %.1f max %.1f mean %.1f  (mean |change| %.2f m)"
          % (h.min(), h.max(), h.mean(), moved))

    k = np.array([1.0, 2.0, 1.0], np.float32)
    k /= k.sum()
    h = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 0, h)
    h = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 1, h)

    h = np.clip(h, 0.0, 260.0).astype(np.float32)
    print("final         min %.1f max %.1f mean %.1f" % (h.min(), h.max(), h.mean()))
    print("below water level (16 m): %.1f%%" % ((h < 16.0).mean() * 100))
    print("ground steeper than 30 deg: %.1f%%" % (_slope_fraction(h) * 100))
    return h


def _slope_fraction(h, threshold_deg=30.0):
    gy, gx = np.gradient(h)           # 1 m spacing
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    return float((slope > threshold_deg).mean())


if __name__ == "__main__":
    h = build()
    import os
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_paths.ROOT, "engine_data", "heightmap.r32")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    h.tofile(out)
    print("output: %s" % out)
    print("wrote %d bytes" % (h.size * 4))
