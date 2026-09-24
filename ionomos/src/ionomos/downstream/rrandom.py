"""
R's default random numbers, so seeded steps give the numbers R gives.

FragPipe-Analyst / FragPipeAnalystR impute missing values with
set.seed(123); rnorm(...). This module reproduces R's defaults exactly:
Mersenne-Twister with R's seed scrambling (RNG.c: RNG_Init / MT_genrand /
fixup) and normal draws by inversion (snorm.c INVERSION, qnorm.c AS241).
Checked against R in tests/test_fpa.py.

    rng = RRandom(123)
    rng.rnorm(5, mean=20.1, sd=0.4)      # == R: set.seed(123); rnorm(5, 20.1, 0.4)
"""
from __future__ import annotations

import math

_N, _M = 624, 397
_MATRIX_A, _UPPER, _LOWER = 0x9908B0DF, 0x80000000, 0x7FFFFFFF
_I2_32M1 = 2.328306437080797e-10  # 1 / (2^32 - 1)
_BIG = 134217728  # 2^27


class RRandom:
    def __init__(self, seed: int = 123):
        self.set_seed(seed)

    def set_seed(self, seed: int) -> None:
        s = int(seed) & 0xFFFFFFFF
        for _ in range(50):  # initial scrambling
            s = (69069 * s + 1) & 0xFFFFFFFF
        state = []
        for _ in range(_N + 1):  # dummy[0] (mti) + mt[624]
            s = (69069 * s + 1) & 0xFFFFFFFF
            state.append(s)
        self._mt = state[1:]
        self._mti = _N  # FixupSeeds(initial): dummy[0] = 624 -> regenerate on first draw

    def _genrand(self) -> float:
        mt = self._mt
        if self._mti >= _N:
            for kk in range(_N - _M):
                y = (mt[kk] & _UPPER) | (mt[kk + 1] & _LOWER)
                mt[kk] = mt[kk + _M] ^ (y >> 1) ^ (_MATRIX_A if y & 1 else 0)
            for kk in range(_N - _M, _N - 1):
                y = (mt[kk] & _UPPER) | (mt[kk + 1] & _LOWER)
                mt[kk] = mt[kk + (_M - _N)] ^ (y >> 1) ^ (_MATRIX_A if y & 1 else 0)
            y = (mt[_N - 1] & _UPPER) | (mt[0] & _LOWER)
            mt[_N - 1] = mt[_M - 1] ^ (y >> 1) ^ (_MATRIX_A if y & 1 else 0)
            self._mti = 0
        y = mt[self._mti]
        self._mti += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        y ^= y >> 18
        return (y & 0xFFFFFFFF) * 2.3283064365386963e-10

    def unif_rand(self) -> float:
        v = self._genrand()
        if v <= 0.0:
            return 0.5 * _I2_32M1
        if 1.0 - v <= 0.0:
            return 1.0 - 0.5 * _I2_32M1
        return v

    def norm_rand(self) -> float:
        u = self.unif_rand()
        u = int(_BIG * u) + self.unif_rand()
        return qnorm(u / _BIG)

    def rnorm(self, n: int, mean: float = 0.0, sd: float = 1.0) -> list[float]:
        if math.isnan(mean) or not math.isfinite(sd) or sd < 0:
            return [math.nan] * n
        if sd == 0 or not math.isfinite(mean):
            return [mean] * n
        return [mean + sd * self.norm_rand() for _ in range(n)]

    def runif(self, n: int, a: float = 0.0, b: float = 1.0) -> list[float]:
        return [a + (b - a) * self.unif_rand() for _ in range(n)]


def qnorm(p: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    """Normal quantile, R's qnorm5 (Wichura AS241, lower tail, not log)."""
    if math.isnan(p) or p < 0 or p > 1:
        return math.nan
    if p == 0:
        return -math.inf
    if p == 1:
        return math.inf
    q = p - 0.5
    if abs(q) <= 0.425:
        r = 0.180625 - q * q
        val = q * (((((((r * 2509.0809287301226727 + 33430.575583588128105) * r + 67265.770927008700853) * r
                        + 45921.953931549871457) * r + 13731.693765509461125) * r + 1971.5909503065514427) * r
                     + 133.14166789178437745) * r + 3.387132872796366608) \
            / (((((((r * 5226.495278852545925 + 28729.085735721942674) * r + 39307.89580009271061) * r
                   + 21213.794301586595867) * r + 5394.1960214247511077) * r + 687.1870074920579083) * r
                + 42.313330701600911252) * r + 1.0)
        return mu + sigma * val
    r = math.sqrt(-math.log((0.5 - p + 0.5) if q > 0 else p))
    if r <= 5.0:
        r -= 1.6
        val = (((((((r * 7.7454501427834140764e-4 + 0.0227238449892691845833) * r + 0.24178072517745061177) * r
                   + 1.27045825245236838258) * r + 3.64784832476320460504) * r + 5.7694972214606914055) * r
                + 4.6303378461565452959) * r + 1.42343711074968357734) \
            / (((((((r * 1.05075007164441684324e-9 + 5.475938084995344946e-4) * r + 0.0151986665636164571966) * r
                   + 0.14810397642748007459) * r + 0.68976733498510000455) * r + 1.6763848301838038494) * r
                + 2.05319162663775882187) * r + 1.0)
    else:
        r -= 5.0
        val = (((((((r * 2.01033439929228813265e-7 + 2.71155556874348757815e-5) * r + 0.0012426609473880784386) * r
                   + 0.026532189526576123093) * r + 0.29656057182850489123) * r + 1.7848265399172913358) * r
                + 5.4637849111641143699) * r + 6.6579046435011037772) \
            / (((((((r * 2.04426310338993978564e-15 + 1.4215117583164458887e-7) * r + 1.8463183175100546818e-5) * r
                   + 7.868691311456132591e-4) * r + 0.0148753612908506148525) * r + 0.13692988092273580531) * r
                + 0.59983220655588793769) * r + 1.0)
    if q < 0.0:
        val = -val
    return mu + sigma * val
