import time

import taichi as ti
import taichi.math as tm

# --- 1. Taichi Initialization ---
try:
    ti.init(arch=ti.gpu)
except Exception:
    ti.init(arch=ti.cpu)

# --- 2. Math Types and Constants ---
vec2 = tm.vec2
vec3 = tm.vec3
vec4 = tm.vec4
PI = 3.14159265


# --- 3. Custom Math Utility Functions ---
@ti.func
def clamp(x: ti.f32, x_min: ti.f32, x_max: ti.f32) -> ti.f32:
    """Clamp value to range [x_min, x_max]."""
    return ti.max(x_min, ti.min(x_max, x))


@ti.func
def smoothstep(edge0: ti.f32, edge1: ti.f32, x: ti.f32) -> ti.f32:
    """Standard smoothstep interpolation."""
    t = clamp((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


@ti.func
def palette(t: ti.f32) -> vec3:
    """Cosine-based color palette generation."""
    a = vec3(0.5, 0.5, 0.5)
    b = vec3(0.5, 0.5, 0.5)
    c = vec3(1.0, 1.0, 1.0)
    d = vec3(0.1, 0.4, 0.5)

    val = 2.0 * PI * (c * t + d)
    cos_val = vec3(ti.cos(val.x), ti.cos(val.y), ti.cos(val.z))
    return a + b * cos_val


@ti.func
def wave(
    uv: vec2, amp: ti.f32, freq: ti.f32, phase: ti.f32, thick: ti.f32, hue: vec3
) -> vec4:
    """Calculate wave layer color contribution."""
    x = uv.x - phase
    y = uv.y + amp * ti.sin(freq * x)
    bright = smoothstep(0.0, 1.0, 1.0 - ti.abs(y) / thick)
    return vec4(bright * hue.x, bright * hue.y, bright * hue.z, 1.0)


@ti.func
def mainImage(coord: vec2, resolution: vec2, time: ti.f32) -> vec4:
    """Evaluate shader at pixel coordinate."""
    uv = (2.0 * coord - resolution) / resolution.y
    color_rgb = vec3(0.0, 0.0, 0.0)

    for step in range(10):
        layer = ti.cast(step, ti.f32) * 0.1
        amp = 0.25 + 0.25 * ti.sin(time + layer) * (1.0 - layer)
        freq = 2.0
        phase = time * (1.0 - layer)

        # Calculate thickness: thick = 0.01 + 0.001 * pow(abs(uv.x), 8.0)
        abs_x = ti.abs(uv.x)
        pow_x = abs_x * abs_x * abs_x * abs_x * abs_x * abs_x * abs_x * abs_x
        thick = 0.01 + 0.001 * pow_x

        # Hue based on palette
        hue = palette(0.5 * uv.x + 1.0 * layer - 0.5 * time)

        # Accumulate wave colors
        w_val = uv.x - phase
        w_y = uv.y + amp * ti.sin(freq * w_val)
        bright = smoothstep(0.0, 1.0, 1.0 - ti.abs(w_y) / thick)
        color_rgb += bright * hue

    return vec4(color_rgb.x, color_rgb.y, color_rgb.z, 1.0)


# --- 4. Taichi Painting Kernel ---
@ti.kernel
def paint_shader(pixels: ti.template(), time: ti.f32):
    """Parallel kernel to render the shader to pixels array."""
    width_val = ti.cast(pixels.shape[0], ti.f32)
    height_val = ti.cast(pixels.shape[1], ti.f32)
    resolution = vec2(width_val, height_val)

    for i, j in pixels:
        coord = vec2(ti.cast(i, ti.f32) + 0.5, ti.cast(j, ti.f32) + 0.5)
        color = mainImage(coord, resolution, time)
        pixels[i, j] = vec3(color.x, color.y, color.z)


# --- 5. Main Execution Loop ---
def main():
    width, height = 1280, 720
    pixels = ti.Vector.field(n=3, dtype=ti.f32, shape=(width, height))

    gui = ti.GUI("Pure Taichi Wave Shader", res=(width, height))

    start_time = time.time()

    print("Running pure Taichi Wave Shader GUI. Close the window to exit.")
    while gui.running:
        current_time = time.time() - start_time
        paint_shader(pixels, current_time)
        gui.set_image(pixels)
        gui.show()


if __name__ == "__main__":
    main()
