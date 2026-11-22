"""Render de um GLB com fundo transparente, para a arte 2D das goblins (ADR 030).

Uso (roda dentro do Blender, sempre com caminhos absolutos):
    blender -b --factory-startup --python-exit-code 1 -P scripts/arte/render_glb.py -- \\
        IN.glb OUT.png PX [--yaw=GRAUS] [--realista]

Saída: PNG RGBA quadrado de PX px, com fundo transparente. Código 0 com o PNG gravado; argumento
faltando ou inválido sai com 2 antes de abrir a cena. Sem `--python-exit-code 1`, o Blender sai
com 0 mesmo se o script quebrar no meio.

Câmera: ortográfica, enquadrando a caixa das malhas (`ortho_scale` = 1,1 x o maior lado), girada
`--yaw` graus (padrão 20) em torno de Z a partir da frente do modelo (-Y no Blender, +Z no glTF),
com leve plongée (0,25 x o tamanho), como no `render_preview.py` que gerou os previews aprovados
dos modelos do Hi3D Quality.

Modos:
- padrão: EEVEE, tom AgX e um sol (a luz das goblins dos estados);
- `--realista`: Cycles (Metal se houver GPU, senão CPU), tom Standard, mundo cinza e três luzes de
  área nos ângulos do ADR 030, item 3, que a cena do three imita (web/src/oficina3d/oficina-3d.ts):
  principal a -40° com 35° de altura, preenchimento a 60° com 10° e recorte a 170° com 40°. As
  luzes ficam a 3 raios do centro, com potência x raio², o que dá a irradiância P / (9π) no
  centro qualquer que seja a escala do modelo.

Calibração (22/11, Tarefa 1 do plano dos goblins): DSSIM contra a arte publicada dos GLBs antigos,
com o recorte 4:5 do gerar-imagens.sh, os dois achatados sobre cinza #808080. A meta era 0,02; o
piso fica na câmera, não na luz: a arte antiga saiu de uma câmera em perspectiva, mais perto, e
esta é ortográfica (como os previews aprovados do Hi3D Quality). Variar a luz move o DSSIM em
menos de 0,02 nos dois modos.
- padrão, Ferrugem a 768 px contra goblins/ferrugem-768w.webp: 0,0634 com o sol a 2,0 e o mundo
  a 0,08 (0,0765 com o sol a 3,0 e o mundo a 0,25, o ponto de partida);
- realista, Nyx a 640 px contra oficina-3d/oficina-3d-nyx-640w.webp: 0,0536 com as potências e o
  mundo do ADR 030 (o melhor da varredura, 0,0525, pedia potências x 0,6). Ficam as do ADR, que a
  cena do three converte (LIGHTS em oficina-3d.ts): mudar aqui sem mudar lá descasaria as duas.
"""

import argparse
import math
import sys
from dataclasses import dataclass

import bpy
from mathutils import Vector

DEFAULT_YAW_DEGREES = 20.0
FRAME_MARGIN = 1.1
CAMERA_DISTANCE_SIZES = 3.0
CAMERA_RISE_SIZES = 0.25

SUN_STRENGTH = 2.0
SUN_YAW_DEGREES = -40.0
SUN_PITCH_DEGREES = 35.0
DEFAULT_WORLD_GRAY = 0.08

REALISTIC_WORLD_GRAY = 0.25
REALISTIC_SAMPLES = 128
LIGHT_DISTANCE_RADII = 3.0
# (nome, potência em W para raio 1, ângulo a partir da frente, altura), em graus (ADR 030, item 3).
REALISTIC_LIGHTS: tuple[tuple[str, float, float, float], ...] = (
    ("principal", 250.0, -40.0, 35.0),
    ("preenchimento", 80.0, 60.0, 10.0),
    ("recorte", 200.0, 170.0, 40.0),
)


@dataclass(frozen=True)
class Options:
    source: str
    out: str
    px: int
    yaw: float
    realistic: bool


@dataclass(frozen=True)
class Bounds:
    center: Vector
    size: float
    radius: float


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"PX deve ser positivo: {text}")
    return value


def parse_args(argv: list[str]) -> Options:
    """Lê os argumentos depois de `--`; com erro, o argparse escreve no stderr e sai com 2."""
    parser = argparse.ArgumentParser(prog="render_glb.py")
    parser.add_argument("source", metavar="IN.glb")
    parser.add_argument("out", metavar="OUT.png")
    parser.add_argument("px", metavar="PX", type=_positive_int)
    parser.add_argument("--yaw", type=float, default=DEFAULT_YAW_DEGREES, metavar="GRAUS")
    parser.add_argument("--realista", dest="realistic", action="store_true")
    args = parser.parse_args(argv)
    return Options(args.source, args.out, args.px, args.yaw, args.realistic)


def toward(yaw_degrees: float, pitch_degrees: float) -> Vector:
    """Direção unitária do centro para fora, com o ângulo medido a partir da frente (-Y)."""
    yaw = math.radians(yaw_degrees)
    pitch = math.radians(pitch_degrees)
    return Vector(
        (math.sin(yaw) * math.cos(pitch), -math.cos(yaw) * math.cos(pitch), math.sin(pitch))
    )


def mesh_bounds(scene: bpy.types.Scene) -> Bounds:
    meshes = [o for o in scene.objects if o.type == "MESH"]
    points = [o.matrix_world @ Vector(corner) for o in meshes for corner in o.bound_box]
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return Bounds(center=(lo + hi) / 2, size=max(hi - lo), radius=(hi - lo).length / 2)


def aim(obj: bpy.types.Object, target: Vector) -> None:
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def set_world(scene: bpy.types.Scene, gray: float) -> None:
    world = bpy.data.worlds.new("mundo")
    background = world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (gray, gray, gray, 1.0)
    background.inputs["Strength"].default_value = 1.0
    scene.world = world


def add_camera(scene: bpy.types.Scene, bounds: Bounds, yaw_degrees: float) -> None:
    yaw = math.radians(yaw_degrees)
    distance = bounds.size * CAMERA_DISTANCE_SIZES
    camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = bounds.size * FRAME_MARGIN
    camera.data.clip_end = distance * 4
    camera.location = bounds.center + Vector(
        (math.sin(yaw) * distance, -math.cos(yaw) * distance, bounds.size * CAMERA_RISE_SIZES)
    )
    scene.collection.objects.link(camera)
    aim(camera, bounds.center)
    scene.camera = camera


def add_light(
    scene: bpy.types.Scene, light: bpy.types.Light, location: Vector, target: Vector
) -> None:
    obj = bpy.data.objects.new(light.name, light)
    obj.location = location
    scene.collection.objects.link(obj)
    aim(obj, target)


def use_eevee(scene: bpy.types.Scene, bounds: Bounds) -> None:
    """Modo padrão: EEVEE, AgX e um sol."""
    engines = {e.identifier for e in type(scene.render).bl_rna.properties["engine"].enum_items}
    scene.render.engine = (
        "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    )
    scene.view_settings.view_transform = "AgX"
    set_world(scene, DEFAULT_WORLD_GRAY)
    sun = bpy.data.lights.new("sol", "SUN")
    sun.energy = SUN_STRENGTH
    location = bounds.center + toward(SUN_YAW_DEGREES, SUN_PITCH_DEGREES) * bounds.size
    add_light(scene, sun, location, bounds.center)


def use_cycles(scene: bpy.types.Scene, bounds: Bounds) -> None:
    """Modo `--realista`: Cycles na GPU (Metal) quando houver uma, Standard e três áreas."""
    scene.render.engine = "CYCLES"
    scene.cycles.samples = REALISTIC_SAMPLES
    scene.cycles.use_denoising = True
    scene.cycles.device = "CPU"
    preferences = bpy.context.preferences.addons["cycles"].preferences
    try:
        preferences.compute_device_type = "METAL"
    except TypeError:
        pass
    else:
        preferences.refresh_devices()
        gpus = [d for d in preferences.devices if d.type == "METAL"]
        for device in gpus:
            device.use = True
        if gpus:
            scene.cycles.device = "GPU"
    scene.view_settings.view_transform = "Standard"
    set_world(scene, REALISTIC_WORLD_GRAY)
    for name, power, yaw, pitch in REALISTIC_LIGHTS:
        area = bpy.data.lights.new(name, "AREA")
        area.energy = power * bounds.radius**2
        area.size = bounds.radius
        distance = bounds.radius * LIGHT_DISTANCE_RADII
        add_light(scene, area, bounds.center + toward(yaw, pitch) * distance, bounds.center)


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    options = parse_args(argv)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=options.source)
    scene = bpy.context.scene
    if not any(o.type == "MESH" for o in scene.objects):
        print(f"nenhuma malha em {options.source}", file=sys.stderr)
        sys.exit(1)
    bounds = mesh_bounds(scene)

    render = scene.render
    render.film_transparent = True
    render.resolution_x = options.px
    render.resolution_y = options.px
    render.resolution_percentage = 100
    render.image_settings.file_format = "PNG"
    render.image_settings.color_mode = "RGBA"
    add_camera(scene, bounds, options.yaw)
    if options.realistic:
        use_cycles(scene, bounds)
    else:
        use_eevee(scene, bounds)

    render.filepath = options.out
    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
