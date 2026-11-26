"""Steady-state thermal ESTIMATE from a per-cell power map.

Model (stated in the report, not hidden):
  k*t*lap(T) - h*(T - Ta) + q = 0   on the die, adiabatic edges,
  h = 1 / (theta_JA * A_die)  so that a uniform power P gives T - Ta = P*theta_JA,
  k = 148 W/(m K) (silicon), t = 300 um die thickness.
It is a first-order package+spreading model, good for spotting hotspots and
comparing designs; it is not a sign-off thermal analysis.
"""
from __future__ import annotations

import json
import sys

import numpy as np

K_SI, T_DIE = 148.0, 300e-6


def solve(csv: str, n: int = 64, theta_ja: float = 40.0, t_amb: float = 25.0) -> dict:
    die = None
    rows = []
    for line in open(csv):
        if line.startswith("#die"):
            die = [float(x) for x in line.strip().split(",")[1:]]
            continue
        x0, y0, x1, y1, p = line.strip().split(",")
        rows.append((float(x0), float(y0), float(x1), float(y1), float(p)))
    assert die, "no die area"
    a = np.array(rows) if rows else np.zeros((0, 5))
    W, H = die[2] - die[0], die[3] - die[1]
    nx = n
    ny = max(8, int(round(n * H / W))) if W > 0 else n
    pmap = np.zeros((ny, nx))
    if len(a):
        cx = ((a[:, 0] + a[:, 2]) / 2 - die[0]) / W * nx
        cy = ((a[:, 1] + a[:, 3]) / 2 - die[1]) / H * ny
        ix = np.clip(cx.astype(int), 0, nx - 1)
        iy = np.clip(cy.astype(int), 0, ny - 1)
        np.add.at(pmap, (iy, ix), np.nan_to_num(a[:, 4]))
    P = float(pmap.sum())
    dx, dy = W * 1e-6 / nx, H * 1e-6 / ny
    area = W * H * 1e-12
    h = 1.0 / (theta_ja * area)
    q = pmap / (dx * dy)  # W/m^2
    g = K_SI * T_DIE
    cxx, cyy = g / dx**2, g / dy**2
    diag = 2 * cxx + 2 * cyy + h

    def A(T):  # symmetric positive-definite operator, adiabatic (mirror) edges
        Tp = np.pad(T, 1, mode="edge")
        nb = cxx * (Tp[1:-1, :-2] + Tp[1:-1, 2:]) + cyy * (Tp[:-2, 1:-1] + Tp[2:, 1:-1])
        return diag * T - nb

    # conjugate gradient, matrix-free
    T = np.full((ny, nx), P * theta_ja)  # rise above ambient
    r = q - A(T)
    p_ = r.copy()
    rs = float((r * r).sum())
    bn = float((q * q).sum()) ** 0.5 or 1.0
    iters = 0
    for iters in range(1, 20 * nx * ny):
        Ap = A(p_)
        alpha = rs / float((p_ * Ap).sum())
        T += alpha * p_
        r -= alpha * Ap
        rs_new = float((r * r).sum())
        if rs_new ** 0.5 < 1e-10 * bn:
            break
        p_ = r + (rs_new / rs) * p_
        rs = rs_new
    T = T + t_amb
    iy, ix = np.unravel_index(int(np.argmax(T)), T.shape)
    return {
        "model": "first-order: package theta_JA + lateral spreading in Si (estimate)",
        "theta_ja": theta_ja, "t_amb": t_amb, "die_um": die, "grid": [ny, nx],
        "cells": int(len(a)), "power_w": P, "cg_iterations": iters,
        "check_uniform_rise_k": P * theta_ja,
        "t_max": float(T.max()), "t_min": float(T.min()), "t_mean": float(T.mean()),
        "hotspot_um": [die[0] + (ix + 0.5) * W / nx, die[1] + (iy + 0.5) * H / ny],
        "temp": np.round(T, 6).tolist(),
        "power_density_w_mm2": np.round(pmap / (dx * dy) * 1e-6, 9).tolist(),
    }


if __name__ == "__main__":
    csv, out = sys.argv[1], sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 64
    th = float(sys.argv[4]) if len(sys.argv) > 4 else 40.0
    ta = float(sys.argv[5]) if len(sys.argv) > 5 else 25.0
    r = solve(csv, n, th, ta)
    with open(out, "w") as f:
        json.dump(r, f)
    print(f"[kahroba] thermal: P={r['power_w']:.3e} W  Tmax={r['t_max']:.4f} C  Tmean={r['t_mean']:.4f} C")
