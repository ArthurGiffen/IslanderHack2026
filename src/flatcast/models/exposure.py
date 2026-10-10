"""Per-pixel exposure model: the probability that a pixel is dry, given the water level.

For pixel i at time t:

    effective level  eta_it = h_t + dx_i * s_x(t) + dy_i * s_y(t)
    water-surface slope    s(t) = A @ tau(t)          (m per km)
    P(dry)                 = logistic( k_i * (z_i - eta_it) ),   k_i > 0

h_t is the gauge level, tau(t) is the recent wind stress (u|U|, v|U|),
dx/dy are the pixel's distance from the gauge in km, and A is a 2x2 matrix
shared by the whole area: how strongly wind tilts the water surface. z_i is
the level at which the pixel is dry half the time (in effect its elevation)
and k_i how sharp that transition is. P(dry) always falls as the water rises,
so the fit is monotonic by construction.

Fitting alternates between vectorised Newton steps for every pixel's (z, log k)
and Newton steps for A. A small "lapse" probability keeps a few mislabelled
images (missed cloud, glint) from dragging a pixel's fit, and weak priors keep
pixels that are always wet or always dry finite. Recent images get more weight
(exponential decay with a configurable half-life) because the flats move.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.special import expit

from flatcast.imagery.water_index import DRY, WATER

FITTED, NEVER_DRY, NEVER_WET, SPARSE = 0, 1, 2, 3
STATUS_NAMES = {FITTED: "fitted", NEVER_DRY: "never seen dry", NEVER_WET: "never seen flooded", SPARSE: "too few clear images"}

LAPSE = 0.02
PRIOR_LOGK = (np.log(25.0), 1.0)  # transition width ~4 cm, loosely
PRIOR_Z_SD = 0.5  # m
PRIOR_A_SD = 2e-3  # (m/km) per (m^2/s^2): very weak
CHUNK = 4096


@dataclass
class ExposureModel:
    z: np.ndarray
    logk: np.ndarray
    status: np.ndarray
    n_obs: np.ndarray
    n_dry: np.ndarray
    A: np.ndarray
    dx: np.ndarray
    dy: np.ndarray
    level_range: tuple[float, float]
    meta: dict = field(default_factory=dict)

    @property
    def shape(self):
        return self.z.shape

    def slope(self, tau) -> np.ndarray:
        return np.asarray(tau, dtype=np.float64) @ self.A.T

    def effective_level(self, h: float, tau=(0.0, 0.0)) -> np.ndarray:
        s = self.slope(tau)
        return h + self.dx * s[0] + self.dy * s[1]

    def p_dry(self, h: float, tau=(0.0, 0.0), sigma: float = 0.0) -> np.ndarray:
        """Probability each pixel is dry at gauge level h (m).

        sigma (m) is the uncertainty of h itself; the logistic is blurred by a
        normal of that width (probit approximation), so an uncertain forecast
        gives less confident maps.
        """
        k = np.exp(self.logk)
        a = k * (self.z - self.effective_level(h, tau))
        if sigma > 0:
            a = a / np.sqrt(1.0 + (np.pi / 8.0) * (k * sigma) ** 2)
        p = expit(a).astype(np.float32)
        p[self.status == SPARSE] = np.nan
        return p

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            z=self.z.astype(np.float32),
            logk=self.logk.astype(np.float32),
            status=self.status,
            n_obs=self.n_obs,
            n_dry=self.n_dry,
            A=self.A,
            dx=self.dx,
            dy=self.dy,
            level_range=np.array(self.level_range),
            meta=np.array(repr(self.meta)),
        )
        return path

    @classmethod
    def load(cls, path: Path) -> "ExposureModel":
        d = np.load(path, allow_pickle=False)
        import ast

        return cls(
            z=d["z"],
            logk=d["logk"],
            status=d["status"],
            n_obs=d["n_obs"],
            n_dry=d["n_dry"],
            A=d["A"],
            dx=d["dx"],
            dy=d["dy"],
            level_range=tuple(d["level_range"].tolist()),
            meta=ast.literal_eval(str(d["meta"])),
        )


def recency_weights(times, half_life_years: float, reference=None) -> np.ndarray:
    t = np.asarray(times, dtype="datetime64[s]").astype("int64") / 86400.0
    ref = t.max() if reference is None else np.datetime64(reference, "s").astype("int64") / 86400.0
    age_years = np.maximum(ref - t, 0) / 365.25
    return (0.5 ** (age_years / half_life_years)).astype(np.float64)


def _eta(h, tau, A, dx, dy):
    s = tau @ A.T  # (T, 2)
    return h[:, None] + s[:, 0:1] * dx[None, :] + s[:, 1:2] * dy[None, :]


def _terms(dry, z, logk, eta):
    k = np.exp(logk)[None, :]
    a = k * (z[None, :] - eta)
    p = expit(a)
    P = LAPSE + (1 - 2 * LAPSE) * p
    dp = (1 - 2 * LAPSE) * p * (1 - p)
    denom = P * (1 - P)
    ga = (dry - P) * dp / denom  # d loglik / d a
    info = dp * dp / denom  # Fisher information for a
    return k, a, ga, info


def _init_z(eta, valid, w, dry):
    """Start each pixel at the level quantile matching how often it was dry."""
    f = (w[:, None] * dry * valid).sum(0) / np.maximum((w[:, None] * valid).sum(0), 1e-9)
    e = np.where(valid, eta, np.nan)
    e.sort(axis=0)  # NaNs go last
    n = valid.sum(0)
    idx = np.clip(np.round(f * (n - 1)).astype(int), 0, np.maximum(n - 1, 0))
    return np.take_along_axis(e, idx[None, :], 0)[0]


def _fit_pixels(dry, valid, w, h, tau, A, dx, dy, z, logk, mu_z, iterations):
    """Vectorised Newton steps for (z, log k) of a chunk of pixels.

    Works in float32 and drops pixels from the working set as they converge,
    which matters: most of the area converges in a handful of steps.
    """
    eta = _eta(h, tau, A, dx, dy).astype(np.float32)
    m = (valid * w[:, None]).astype(np.float32)
    dry = dry.astype(np.float32)
    mu_l, sd_l = PRIOR_LOGK
    z = np.asarray(z, dtype=np.float64).copy()
    logk = np.asarray(logk, dtype=np.float64).copy()
    if np.isnan(z).any():
        bad = np.isnan(z)
        z[bad] = _init_z(eta[:, bad], valid[:, bad], w, dry[:, bad])
        z[np.isnan(z)] = mu_z
    active = np.arange(len(z))
    for it in range(iterations):
        zc, lc = z[active].astype(np.float32), logk[active].astype(np.float32)
        k, a, ga, info = _terms(dry, zc, lc, eta)
        k = k[0]
        mga = m * ga
        minfo = m * info
        g_z = mga.sum(0, dtype=np.float64) * k - (zc - mu_z) / PRIOR_Z_SD**2
        g_l = (mga * a).sum(0, dtype=np.float64) - (lc - mu_l) / sd_l**2
        i_zz = minfo.sum(0, dtype=np.float64) * k * k + 1 / PRIOR_Z_SD**2
        i_zl = (minfo * a).sum(0, dtype=np.float64) * k
        i_ll = (minfo * a * a).sum(0, dtype=np.float64) + 1 / sd_l**2
        det = i_zz * i_ll - i_zl * i_zl
        dz = np.clip((i_ll * g_z - i_zl * g_l) / det, -0.1, 0.1)
        dl = np.clip((i_zz * g_l - i_zl * g_z) / det, -0.5, 0.5)
        z[active] += dz
        logk[active] += dl
        moving = (np.abs(dz) >= 1e-4) | (np.abs(dl) >= 1e-3)
        if not moving.any():
            break
        if it % 4 == 3 and moving.mean() < 0.7:  # shrink the working set
            keep = np.flatnonzero(moving)
            active = active[keep]
            eta, m, dry = eta[:, keep], m[:, keep], dry[:, keep]
    return z, logk


def _fit_setup(Y, valid_all, w, h, tau, A, dx, dy, z, logk, pixels, iterations=8):
    theta = A.reshape(4).astype(np.float64)
    for _ in range(iterations):
        G = np.zeros(4)
        I = np.zeros((4, 4))
        Acur = theta.reshape(2, 2)
        for lo in range(0, len(pixels), CHUNK):
            c = pixels[lo : lo + CHUNK]
            dry = (Y[:, c] == DRY).astype(np.float64)
            valid = valid_all[:, c]
            eta = _eta(h, tau, Acur, dx[c], dy[c])
            k, a, ga, info = _terms(dry, z[c], logk[c], eta)
            m = valid * w[:, None]
            q = -(m * ga * k)  # d loglik / d eta
            qx, qy = q @ dx[c], q @ dy[c]
            G += np.array([qx @ tau[:, 0], qx @ tau[:, 1], qy @ tau[:, 0], qy @ tau[:, 1]])
            r = m * info * k * k
            R = np.stack(
                [
                    np.stack([r @ (dx[c] * dx[c]), r @ (dx[c] * dy[c])], -1),
                    np.stack([r @ (dx[c] * dy[c]), r @ (dy[c] * dy[c])], -1),
                ],
                -2,
            )  # (T, 2, 2)
            TT = tau[:, :, None] * tau[:, None, :]
            I += np.einsum("tac,tbd->abcd", R, TT).reshape(4, 4)
        G -= theta / PRIOR_A_SD**2
        I += np.eye(4) / PRIOR_A_SD**2
        step = np.linalg.solve(I, G)
        theta = theta + step
        if np.abs(step).max() < 1e-7:
            break
    return theta.reshape(2, 2)


def fit(
    masks: np.ndarray,
    h: np.ndarray,
    tau: np.ndarray,
    times,
    dx: np.ndarray,
    dy: np.ndarray,
    *,
    half_life_years: float = 3.0,
    min_obs: int = 15,
    iterations: int = 30,
    rounds: int = 3,
    fit_setup: bool = True,
    progress=None,
    seed: int = 0,
) -> ExposureModel:
    """Fit the exposure model.

    masks: (T, H, W) uint8 with DRY / WATER / AMBIGUOUS / NODATA classes
    h:     (T,) gauge level at each image time (m)
    tau:   (T, 2) recent wind stress (m^2/s^2)
    dx,dy: (H, W) pixel offsets from the gauge (km)
    """
    T, H, W = masks.shape
    Y = masks.reshape(T, H * W)
    h = np.asarray(h, dtype=np.float64)
    tau = np.asarray(tau, dtype=np.float64).reshape(T, 2)
    dxf = dx.reshape(-1).astype(np.float64)
    dyf = dy.reshape(-1).astype(np.float64)
    w = recency_weights(times, half_life_years)

    valid_all = (Y == DRY) | (Y == WATER)
    n_obs = valid_all.sum(0)
    n_dry = (Y == DRY).sum(0)
    status = np.full(H * W, FITTED, dtype=np.uint8)
    status[n_dry == 0] = NEVER_DRY
    status[(n_obs - n_dry) == 0] = NEVER_WET
    status[n_obs < min_obs] = SPARSE

    fit_idx = np.flatnonzero(status != SPARSE)
    z = np.full(H * W, np.nan)
    logk = np.full(H * W, PRIOR_LOGK[0])
    A = np.zeros((2, 2))
    mu_z = float(np.median(h))

    transitional = np.flatnonzero((status == FITTED) & (n_dry >= 3) & (n_obs - n_dry >= 3))
    rng = np.random.default_rng(seed)
    sample = np.sort(rng.choice(transitional, size=min(len(transitional), 20000), replace=False)) if len(transitional) else transitional

    def fit_chunks(pixels, r, total):
        for lo in range(0, len(pixels), CHUNK):
            c = pixels[lo : lo + CHUNK]
            z[c], logk[c] = _fit_pixels(
                (Y[:, c] == DRY), valid_all[:, c], w, h, tau, A, dxf[c], dyf[c], z[c], logk[c], mu_z, iterations
            )
            if progress:
                progress(r, total, lo + len(c), len(pixels))

    # The wind tilt is learned by alternating on a sample of transitional pixels;
    # every pixel is then fitted once with the final tilt.
    setup_rounds = (rounds - 1) if fit_setup and len(sample) > 50 else 0
    total_rounds = setup_rounds + 1
    for r in range(setup_rounds):
        fit_chunks(sample, r, total_rounds)
        A = _fit_setup(Y, valid_all, w, h, tau, A, dxf, dyf, z, logk, sample)
    fit_chunks(fit_idx, setup_rounds, total_rounds)

    return ExposureModel(
        z=z.reshape(H, W).astype(np.float32),
        logk=logk.reshape(H, W).astype(np.float32),
        status=status.reshape(H, W),
        n_obs=n_obs.reshape(H, W).astype(np.int16),
        n_dry=n_dry.reshape(H, W).astype(np.int16),
        A=A,
        dx=dx.astype(np.float32),
        dy=dy.astype(np.float32),
        level_range=(float(np.min(h)), float(np.max(h))),
        meta={"n_images": int(T), "half_life_years": half_life_years, "first": str(np.min(times))[:10], "last": str(np.max(times))[:10]},
    )


def wind_stress(u, v):
    """Quadratic wind stress proxy (m^2/s^2): the force wind puts on the water scales with U|U|."""
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    s = np.hypot(u, v)
    return u * s, v * s
