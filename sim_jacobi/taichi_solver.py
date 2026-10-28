"""Taichi 后端的 theta 除子求解器（GPU kernel，实时）。

与 :mod:`sim_jacobi.solver` 的 NumPy 版**同一套公式**（那边的模块文档写了完整式子），
只是把整张 nu×nv 网格的 θ1 求值、连乘、光晕、干涉格纹与环面顶点一次算完：

* 每格对每个零点算一次 θ1（6 项 Fourier 级数），累加 ``log|θ|`` 与 ``arg θ``；
* 零点光晕 ``Σ exp(−120·d²)`` 同时累加，用于凹陷与压暗；
* 顶点半径 ``r = R + r_minor + h``，``h = −halo·dip + (phase_grid − 0.5)·ripple``。

零点运动不在这里 —— 那是 :class:`sim_jacobi.solver.SphereParticles` 的 numpy 实现，
两个后端共用（O(N²) 的成对碰撞在 CPU 上比传数据到 GPU 更快，N ≤ 64）。

Taichi 的 ``ti.init`` 一个进程只做一次：Blender 里热重载模块时复用已有的 runtime，
不会因为重复初始化而报错。取不到 GPU 时退到 CPU（``ti.cpu``），再失败就由
:func:`sim_jacobi.solver.create` 回退到 NumPy 后端。
"""

# 注意：**不能**加 ``from __future__ import annotations`` —— Taichi 直接读
# ``__annotations__`` 来判断 kernel 参数类型，PEP 563 的字符串注解会让它报
# ``TaichiSyntaxError: Invalid type annotation``。

import sys

from .solver import DEFAULTS, MAX_ZEROS, SphereParticles, local_module_roots

try:
    import taichi as ti
except ImportError:  # pragma: no cover - 取决于本机安装
    for _root in local_module_roots():
        if _root not in sys.path:
            sys.path.append(_root)
    import taichi as ti

__all__ = ["TaichiJacobi", "ensure_init"]

#: 是否已完成 ``ti.init``（热重载会把模块级变量清掉，但 runtime 还在，会重新探测）。
_READY = False

#: 实际用的架构名（cuda / vulkan / metal / cpu / ...），只用于面板读数。
_ARCH = "?"


def ensure_init() -> str:
    """保证 Taichi 已初始化，返回实际架构名。优先 GPU，失败退 CPU。"""
    global _READY, _ARCH
    if _READY:
        return _ARCH

    runtime = ti.lang.impl.get_runtime()
    if getattr(runtime, "prog", None) is None:
        try:
            ti.init(arch=ti.gpu, offline_cache=True)
        except Exception:
            # 没有可用 GPU/驱动：CPU 后端照样正确，只是慢
            ti.init(arch=ti.cpu, offline_cache=True)
    try:
        _ARCH = str(ti.lang.impl.current_cfg().arch).split(".")[-1].lower()
    except Exception:
        _ARCH = "?"
    _READY = True
    return _ARCH


@ti.data_oriented
class TaichiJacobi(SphereParticles):
    """环面 theta 除子求解器（Taichi 后端，GPU 优先）。"""

    backend = "taichi"

    def __init__(
        self,
        nu: int = 150,
        nv: int = 100,
        major_r: float = 3.0,
        minor_r: float = 1.2,
        max_zeros: int = MAX_ZEROS,
        params: dict | None = None,
    ):
        arch = ensure_init()
        super().__init__(max_zeros=max_zeros, major_r=major_r, minor_r=minor_r)
        self.nu = int(nu)
        self.nv = int(nv)

        shape = (self.nu, self.nv)
        self.amplitude_field = ti.field(dtype=ti.f32, shape=shape)
        self.phase_field = ti.field(dtype=ti.f32, shape=shape)
        self.phase_grid_field = ti.field(dtype=ti.f32, shape=shape)
        self.potential_field = ti.field(dtype=ti.f32, shape=shape)
        self.verts_field = ti.Vector.field(3, dtype=ti.f32, shape=shape)

        self.zeros_u = ti.field(dtype=ti.f32, shape=self.max_zeros)
        self.zeros_v = ti.field(dtype=ti.f32, shape=self.max_zeros)

        # 参数放 0 维字段：kernel 里按常量读，改参数不用重编译
        self.s_num_zeros = ti.field(dtype=ti.i32, shape=())
        self.s_tau_re = ti.field(dtype=ti.f32, shape=())
        self.s_tau_im = ti.field(dtype=ti.f32, shape=())
        self.s_dip_depth = ti.field(dtype=ti.f32, shape=())
        self.s_disp_scale = ti.field(dtype=ti.f32, shape=())
        self.s_major_r = ti.field(dtype=ti.f32, shape=())
        self.s_minor_r = ti.field(dtype=ti.f32, shape=())

        self.params = dict(DEFAULTS)
        self.params.update(self._clean(params or {}))
        self.major_r = self.params["major_r"]
        self.minor_r = self.params["minor_r"]

        #: 后端说明（面板读数用）。
        self.note = f"taichi/{arch}"

        self.initialize(int(self.params["num_zeros"]))

    # --- kernel -------------------------------------------------------------

    @ti.func
    def theta1_func(self, z_re: ti.f32, z_im: ti.f32) -> ti.types.vector(2, ti.f32):
        r"""θ1(z | τ)（复数，返回 (实部, 虚部)），6 项 Fourier 级数。

        ``q = exp(iπτ)`` 即 ``|q| = exp(−π·τ_im)``、``arg q = π·τ_re``；
        ``sin((2n+1)πz)`` 用 ``sin(a+bi) = sin a·cosh b + i·cos a·sinh b`` 展开。
        """
        t_re = self.s_tau_re[None]
        t_im = self.s_tau_im[None]
        pi = ti.math.pi

        res_re = 0.0
        res_im = 0.0
        for n in range(6):
            sign = 1.0 if (n % 2 == 0) else -1.0
            half = float(n) + 0.5
            q_mag = ti.exp(-pi * t_im * half * half)
            q_phase = pi * t_re * half * half

            k = (2.0 * float(n) + 1.0) * pi
            arg_re = k * z_re
            arg_im = k * z_im

            sin_a = ti.sin(arg_re)
            cos_a = ti.cos(arg_re)
            cosh_b = 0.5 * (ti.exp(arg_im) + ti.exp(-arg_im))
            sinh_b = 0.5 * (ti.exp(arg_im) - ti.exp(-arg_im))

            term_re = sin_a * cosh_b
            term_im = cos_a * sinh_b

            q_re = q_mag * ti.cos(q_phase)
            q_im = q_mag * ti.sin(q_phase)

            res_re += sign * (term_re * q_re - term_im * q_im)
            res_im += sign * (term_re * q_im + term_im * q_re)

        return ti.Vector([2.0 * res_re, 2.0 * res_im])

    @ti.kernel
    def compute_kernel(self):
        """整张网格：θ1 连乘 → 光晕 / 相角 / 势井 / 干涉格纹 → 环面顶点。"""
        nu_val = self.nu
        nv_val = self.nv
        n_z = self.s_num_zeros[None]
        dip_depth = self.s_dip_depth[None]
        disp_scale = self.s_disp_scale[None]
        r_major = self.s_major_r[None]
        r_minor = self.s_minor_r[None]
        pi = ti.math.pi

        for i, j in self.amplitude_field:
            u = float(i) / float(nu_val)
            v = float(j) / float(nv_val)

            potential = 0.0
            phase_sum = 0.0
            halo = 0.0

            for k in range(n_z):
                du = u - self.zeros_u[k]
                dv = v - self.zeros_v[k]
                du = du - ti.round(du)
                dv = dv - ti.round(dv)

                theta = self.theta1_func(
                    du + self.s_tau_re[None] * dv, self.s_tau_im[None] * dv
                )
                th_re = theta[0]
                th_im = theta[1]

                magnitude = ti.sqrt(th_re * th_re + th_im * th_im + 1e-10)
                potential += ti.log(magnitude + 1e-4)
                phase_sum += ti.atan2(th_im, th_re)
                halo += ti.exp(-(du * du + dv * dv) * 120.0)

            fine_a = ti.cos(32.0 * pi * (u + v))
            fine_b = ti.cos(32.0 * pi * (u - v))
            micro = ti.sin(48.0 * pi * u) * ti.cos(48.0 * pi * v)
            spiral = ti.sin(4.0 * phase_sum)
            lattice = 0.5 + 0.175 * (fine_a + fine_b) + 0.15 * micro * spiral
            phase_grid = lattice * (1.0 - 0.25 * ti.tanh(halo))

            self.amplitude_field[i, j] = halo
            self.phase_field[i, j] = phase_sum
            self.potential_field[i, j] = potential
            self.phase_grid_field[i, j] = phase_grid

            height = -halo * dip_depth + (phase_grid - 0.5) * disp_scale
            r_eff = r_minor + height
            theta_ang = 2.0 * pi * u
            phi_ang = 2.0 * pi * v
            ring = r_major + r_eff * ti.cos(phi_ang)
            self.verts_field[i, j] = ti.Vector(
                [
                    ring * ti.cos(theta_ang),
                    ring * ti.sin(theta_ang),
                    r_eff * ti.sin(phi_ang),
                ]
            )

    # --- 一帧 ---------------------------------------------------------------

    def evaluate(self) -> tuple:
        """算一帧（与 NumPy 后端同签名，见 :meth:`sim_jacobi.solver.NumPyJacobi.evaluate`）。"""
        p = self.params
        n = self.count

        self.s_num_zeros[None] = n
        self.s_tau_re[None] = p["tau_re"]
        self.s_tau_im[None] = p["tau_im"]
        self.s_dip_depth[None] = p["zero_dip_depth"]
        self.s_disp_scale[None] = p["z_disp_scale"]
        self.s_major_r[None] = p["major_r"]
        self.s_minor_r[None] = p["minor_r"]

        self.zeros_u.from_numpy(self.pos_u)
        self.zeros_v.from_numpy(self.pos_v)

        self.compute_kernel()

        zeros = self.centers(
            p["minor_r"] - p["zero_dip_depth"] * 0.5 + p["sphere_radius"]
        )
        return (
            self.verts_field.to_numpy().reshape(-1, 3),
            self.amplitude_field.to_numpy().ravel(),
            self.phase_field.to_numpy().ravel(),
            self.phase_grid_field.to_numpy().ravel(),
            self.potential_field.to_numpy().ravel(),
            zeros,
        )
