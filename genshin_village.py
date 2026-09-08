bl_info = {
    "name": "Genshin Village Generator",
    "author": "v0",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar (N) > Village",
    "description": "Procedurally builds a stylized, cel-shaded Genshin-inspired village for Cycles.",
    "category": "Add Mesh",
}

# ---------------------------------------------------------------------------
# Genshin Village Generator  --  Blender 3.6 LTS  --  Cycles stylized (Toon BSDF)
#
# USE AS A SCRIPT:
#   1. Open Blender 3.6, go to the "Scripting" tab.
#   2. Open this file (or paste it), press "Run Script".
#   3. A "Village" collection appears; the camera frames it.
#
# USE AS AN ADD-ON:
#   1. Edit > Preferences > Add-ons > Install... > pick this .py file.
#   2. Enable "Add Mesh: Genshin Village Generator".
#   3. In the 3D View press N, open the "Village" tab, tweak, hit "Generate Village".
#
# Notes:
#   * Materials use the native Toon BSDF, which renders in Cycles (Shader-to-RGB
#     is EEVEE-only and is intentionally avoided so the toon look survives in Cycles).
#   * Regenerating is idempotent: the old "Village" collection is deleted first.
# ---------------------------------------------------------------------------

import bpy
import bmesh
import math
import random
from mathutils import Vector

ROOT_COLLECTION = "Village"

# ---------------------------------------------------------------------------
# Palette (linear-ish RGBA). 3-5 stylized hues + neutrals.
# ---------------------------------------------------------------------------
PALETTE = {
    "wall_cream":   (0.92, 0.86, 0.72, 1.0),
    "wall_warm":    (0.86, 0.74, 0.55, 1.0),
    "roof_teal":    (0.10, 0.45, 0.48, 1.0),
    "roof_blue":    (0.13, 0.34, 0.52, 1.0),
    "wood_trim":    (0.35, 0.22, 0.13, 1.0),
    "wood_light":   (0.55, 0.38, 0.22, 1.0),
    "stone":        (0.62, 0.60, 0.56, 1.0),
    "stone_dark":   (0.42, 0.41, 0.40, 1.0),
    "window_glow":  (0.98, 0.85, 0.55, 1.0),
    "foliage":      (0.24, 0.52, 0.24, 1.0),
    "foliage_lt":   (0.38, 0.64, 0.30, 1.0),
    "foliage_dk":   (0.16, 0.38, 0.20, 1.0),
    "trunk":        (0.34, 0.24, 0.16, 1.0),
    "grass":        (0.40, 0.60, 0.30, 1.0),
    "path":         (0.78, 0.70, 0.55, 1.0),
    "water":        (0.30, 0.62, 0.72, 1.0),
    "gold":         (0.85, 0.68, 0.28, 1.0),
    "flag_red":     (0.72, 0.20, 0.18, 1.0),
    "cloth_cream":  (0.90, 0.84, 0.70, 1.0),
}

_material_cache = {}


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def get_or_make_collection(name, parent=None):
    parent = parent or bpy.context.scene.collection
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    if col.name not in [c.name for c in parent.children]:
        parent.children.link(col)
    return col


def clear_previous():
    """Remove the old Village collection and its objects for a clean rebuild."""
    root = bpy.data.collections.get(ROOT_COLLECTION)
    if root is None:
        return

    def gather(col, acc):
        for obj in col.objects:
            acc.add(obj)
        for child in col.children:
            gather(child, acc)

    objs = set()
    gather(root, objs)
    for obj in objs:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass

    def remove_col(col):
        for child in list(col.children):
            remove_col(child)
        try:
            bpy.data.collections.remove(col)
        except Exception:
            pass

    remove_col(root)


def make_toon_material(name, color, size=0.55, smooth=0.35, emit=0.0):
    """Cel-shaded material via native Toon BSDF (Cycles-compatible)."""
    key = (name, color, round(emit, 3))
    if key in _material_cache:
        return _material_cache[key]

    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    nodes.clear()

    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (500, 0)

    toon = nodes.new("ShaderNodeBsdfToon")
    toon.location = (100, 120)
    toon.inputs["Color"].default_value = color
    toon.inputs["Size"].default_value = size
    toon.inputs["Smooth"].default_value = smooth
    # component: 0 = diffuse toon
    try:
        toon.component = "DIFFUSE"
    except Exception:
        pass

    if emit > 0.0:
        emission = nodes.new("ShaderNodeEmission")
        emission.location = (100, -160)
        emission.inputs["Color"].default_value = color
        emission.inputs["Strength"].default_value = emit
        mix = nodes.new("ShaderNodeMixShader")
        mix.location = (320, 0)
        mix.inputs["Fac"].default_value = 0.85
        links.new(toon.outputs[0], mix.inputs[1])
        links.new(emission.outputs[0], mix.inputs[2])
        links.new(mix.outputs[0], out.inputs["Surface"])
    else:
        links.new(toon.outputs[0], out.inputs["Surface"])

    _material_cache[key] = mat
    return mat


def mat(name_key, **kw):
    return make_toon_material(name_key, PALETTE[name_key], **kw)


def new_mesh_object(name, verts, faces, collection, material=None, shade_smooth=False):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    if shade_smooth:
        for p in mesh.polygons:
            p.use_smooth = True
    obj = bpy.data.objects.new(name, mesh)
    if material:
        obj.data.materials.append(material)
    collection.objects.link(obj)
    return obj


def add_primitive(kind, collection, material, name, location=(0, 0, 0),
                  rotation=(0, 0, 0), scale=(1, 1, 1), **kw):
    """Wrapper around bpy.ops mesh primitives with clean linking to our collection."""
    if kind == "cube":
        bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    elif kind == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=kw.get("verts", 16), radius=kw.get("radius", 1),
            depth=kw.get("depth", 1), location=location)
    elif kind == "cone":
        bpy.ops.mesh.primitive_cone_add(
            vertices=kw.get("verts", 16), radius1=kw.get("radius1", 1),
            radius2=kw.get("radius2", 0), depth=kw.get("depth", 1), location=location)
    elif kind == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=kw.get("segments", 16), ring_count=kw.get("rings", 8),
            radius=kw.get("radius", 1), location=location)
    elif kind == "ico":
        bpy.ops.mesh.primitive_ico_sphere_add(
            subdivisions=kw.get("subdivisions", 1),
            radius=kw.get("radius", 1), location=location)
    elif kind == "plane":
        bpy.ops.mesh.primitive_plane_add(size=1, location=location)
    else:
        raise ValueError(kind)

    obj = bpy.context.active_object
    obj.name = name
    obj.rotation_euler = rotation
    obj.scale = scale
    # move from the scene collection into ours
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)
    if material:
        if obj.data.materials:
            obj.data.materials[0] = material
        else:
            obj.data.materials.append(material)
    return obj


def prism_roof(name, collection, material, cx, cy, base_z, width, depth, height,
               overhang=0.35):
    """A gable/hip roof as a triangular prism sitting on top of a building body."""
    hw = width / 2 + overhang
    hd = depth / 2 + overhang
    z0 = base_z
    z1 = base_z + height
    verts = [
        (cx - hw, cy - hd, z0),
        (cx + hw, cy - hd, z0),
        (cx + hw, cy + hd, z0),
        (cx - hw, cy + hd, z0),
        (cx, cy - hd, z1),
        (cx, cy + hd, z1),
    ]
    faces = [
        (0, 1, 4),
        (2, 3, 5),
        (1, 2, 5, 4),
        (3, 0, 4, 5),
        (0, 3, 2, 1),
    ]
    return new_mesh_object(name, verts, faces, collection, material)


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
def build_house(col, rng, x, y, rot_z, mats):
    """A single stylized house: body, roof, chimney, door, windows, trim."""
    floors = rng.choice([1, 1, 2, 2, 3])
    w = rng.uniform(2.4, 3.4)
    d = rng.uniform(2.4, 3.6)
    floor_h = rng.uniform(1.5, 1.8)
    h = floor_h * floors

    wall_mat = rng.choice([mats["wall_cream"], mats["wall_warm"]])
    roof_mat = rng.choice([mats["roof_teal"], mats["roof_blue"]])

    parent = bpy.data.objects.new(f"House_{len(col.objects)}", None)
    col.objects.link(parent)
    parent.location = (x, y, 0)
    parent.rotation_euler = (0, 0, rot_z)

    def parent_to(obj):
        obj.parent = parent
        obj.matrix_parent_inverse = parent.matrix_world.inverted()

    # body
    body = add_primitive("cube", col, wall_mat, "Body",
                         location=(x, y, h / 2), scale=(w, d, h))
    parent_to(body)

    # stone base band
    base = add_primitive("cube", col, mats["stone"], "Base",
                        location=(x, y, 0.22), scale=(w + 0.12, d + 0.12, 0.44))
    parent_to(base)

    # roof
    roof_h = rng.uniform(1.1, 1.6)
    roof = prism_roof("Roof", col, roof_mat, x, y, h, w, d, roof_h)
    parent_to(roof)

    # chimney
    if rng.random() < 0.7:
        cxo = rng.uniform(-w * 0.25, w * 0.25)
        chimney = add_primitive("cube", col, mats["stone_dark"], "Chimney",
                              location=(x + cxo, y + d * 0.2, h + roof_h * 0.6),
                              scale=(0.4, 0.4, 1.1))
        parent_to(chimney)

    # door
    door = add_primitive("cube", col, mats["wood_trim"], "Door",
                       location=(x, y - d / 2 - 0.01, 0.75),
                       scale=(0.7, 0.12, 1.4))
    parent_to(door)

    # windows with glow, distributed per floor
    for fl in range(floors):
        wz = 0.85 + fl * floor_h
        for wx in (-w * 0.28, w * 0.28):
            win = add_primitive("cube", col, mats["window_glow"], "Window",
                              location=(x + wx, y - d / 2 - 0.02, wz),
                              scale=(0.55, 0.1, 0.65), )
            parent_to(win)
            frame = add_primitive("cube", col, mats["wood_light"], "WinFrame",
                                location=(x + wx, y - d / 2 - 0.015, wz),
                                scale=(0.72, 0.08, 0.82))
            parent_to(frame)
        # side windows
        for wy in (-d * 0.28, d * 0.28):
            win = add_primitive("cube", col, mats["window_glow"], "WindowS",
                              location=(x + w / 2 + 0.02, y + wy, wz),
                              scale=(0.1, 0.5, 0.6))
            parent_to(win)

    # corner trim beams
    for sx in (-1, 1):
        for sy in (-1, 1):
            beam = add_primitive("cube", col, mats["wood_trim"], "Beam",
                               location=(x + sx * w / 2, y + sy * d / 2, h / 2),
                               scale=(0.14, 0.14, h))
            parent_to(beam)

    return parent


def build_windmill(col, rng, x, y, mats):
    parent = bpy.data.objects.new("Windmill", None)
    col.objects.link(parent)

    tower = add_primitive("cone", col, mats["wall_cream"], "MillTower",
                        location=(x, y, 2.6), radius1=1.9, radius2=1.2,
                        depth=5.2, verts=14)
    tower.parent = parent
    band = add_primitive("cylinder", col, mats["stone"], "MillBase",
                       location=(x, y, 0.3), radius=2.05, depth=0.6, verts=14)
    band.parent = parent
    cap = add_primitive("cone", col, mats["roof_blue"], "MillCap",
                      location=(x, y, 5.7), radius1=1.5, radius2=0.0,
                      depth=1.6, verts=14)
    cap.parent = parent

    hub = add_primitive("cylinder", col, mats["wood_trim"], "Hub",
                      location=(x, y - 1.25, 4.4), radius=0.28, depth=0.6, verts=10,
                      rotation=(math.pi / 2, 0, 0))
    hub.parent = parent

    blades = bpy.data.objects.new("Blades", None)
    col.objects.link(blades)
    blades.location = (x, y - 1.55, 4.4)
    blades.parent = parent
    for i in range(4):
        ang = i * math.pi / 2
        sail = add_primitive("cube", col, mats["cloth_cream"], "Sail",
                           location=(x, y - 1.6, 4.4), scale=(0.5, 0.08, 3.2))
        sail.location = (x + math.cos(ang) * 0.0, y - 1.6,
                         4.4 + math.sin(ang) * 0.0)
        sail.rotation_euler = (0, ang, 0)
        # offset outward along blade direction
        sail.location = (x + math.sin(ang) * 1.6, y - 1.6, 4.4 + math.cos(ang) * 1.6)
        sail.parent = blades
    return parent


def build_bell_tower(col, rng, x, y, mats):
    parent = bpy.data.objects.new("BellTower", None)
    col.objects.link(parent)

    base = add_primitive("cube", col, mats["stone"], "TowerBase",
                       location=(x, y, 0.4), scale=(2.4, 2.4, 0.8))
    base.parent = parent
    shaft = add_primitive("cube", col, mats["wall_cream"], "TowerShaft",
                        location=(x, y, 4.0), scale=(1.8, 1.8, 6.4))
    shaft.parent = parent
    belfry = add_primitive("cube", col, mats["wall_warm"], "Belfry",
                         location=(x, y, 7.6), scale=(2.1, 2.1, 1.4))
    belfry.parent = parent
    roof = add_primitive("cone", col, mats["roof_teal"], "TowerRoof",
                       location=(x, y, 9.2), radius1=1.7, radius2=0.0,
                       depth=2.2, verts=4, rotation=(0, 0, math.pi / 4))
    roof.parent = parent
    finial = add_primitive("sphere", col, mats["gold"], "Finial",
                         location=(x, y, 10.5), radius=0.28, segments=12, rings=8)
    finial.parent = parent
    # clock face
    clock = add_primitive("cylinder", col, mats["cloth_cream"], "Clock",
                        location=(x, y - 0.95, 7.6), radius=0.6, depth=0.12, verts=20,
                        rotation=(math.pi / 2, 0, 0))
    clock.parent = parent
    return parent


def build_fountain(col, rng, x, y, mats):
    parent = bpy.data.objects.new("Fountain", None)
    col.objects.link(parent)
    ring = add_primitive("cylinder", col, mats["stone"], "FountRing",
                       location=(x, y, 0.3), radius=2.2, depth=0.6, verts=24)
    ring.parent = parent
    water = add_primitive("cylinder", col, mats["water"], "Water",
                        location=(x, y, 0.45), radius=1.95, depth=0.35, verts=24)
    water.parent = parent
    stem = add_primitive("cylinder", col, mats["stone_dark"], "Stem",
                       location=(x, y, 0.9), radius=0.3, depth=1.2, verts=12)
    stem.parent = parent
    bowl = add_primitive("cone", col, mats["stone"], "Bowl",
                      location=(x, y, 1.5), radius1=0.9, radius2=0.5,
                      depth=0.4, verts=16)
    bowl.parent = parent
    top = add_primitive("sphere", col, mats["water"], "Spout",
                     location=(x, y, 1.9), radius=0.25, segments=12, rings=8)
    top.parent = parent
    return parent


def build_tree(col, rng, x, y, mats):
    parent = bpy.data.objects.new("Tree", None)
    col.objects.link(parent)
    h = rng.uniform(1.6, 2.6)
    trunk = add_primitive("cylinder", col, mats["trunk"], "Trunk",
                       location=(x, y, h / 2), radius=rng.uniform(0.14, 0.22),
                       depth=h, verts=8)
    trunk.parent = parent
    fol_mat = rng.choice([mats["foliage"], mats["foliage_lt"], mats["foliage_dk"]])
    layers = rng.randint(2, 3)
    for i in range(layers):
        r = rng.uniform(1.0, 1.4) * (1 - i * 0.22)
        z = h + i * 0.7
        blob = add_primitive("ico", col, fol_mat, "Canopy",
                           location=(x + rng.uniform(-0.15, 0.15),
                                     y + rng.uniform(-0.15, 0.15), z),
                           radius=r, subdivisions=1,
                           scale=(1, 1, rng.uniform(0.85, 1.1)))
        blob.parent = parent
    return parent


def build_bush(col, rng, x, y, mats):
    fol_mat = rng.choice([mats["foliage"], mats["foliage_lt"]])
    return add_primitive("ico", col, fol_mat, "Bush",
                       location=(x, y, rng.uniform(0.3, 0.5)),
                       radius=rng.uniform(0.4, 0.7), subdivisions=1,
                       scale=(1, 1, 0.7))


def build_rock(col, rng, x, y, mats):
    r = rng.uniform(0.3, 0.7)
    rock = add_primitive("ico", col, mats["stone"], "Rock",
                       location=(x, y, r * 0.4), radius=r, subdivisions=1,
                       scale=(1, rng.uniform(0.7, 1.1), rng.uniform(0.5, 0.8)),
                       rotation=(0, 0, rng.uniform(0, math.pi)))
    return rock


def build_lamp(col, rng, x, y, mats):
    parent = bpy.data.objects.new("Lamp", None)
    col.objects.link(parent)
    post = add_primitive("cylinder", col, mats["wood_trim"], "LampPost",
                      location=(x, y, 1.2), radius=0.08, depth=2.4, verts=8)
    post.parent = parent
    glow = add_primitive("cube", col, mats["window_glow"], "LampGlow",
                      location=(x, y, 2.4), scale=(0.3, 0.3, 0.4))
    glow.parent = parent
    # a soft point light inside the lantern
    light_data = bpy.data.lights.new("LampLight", type="POINT")
    light_data.energy = 60
    light_data.color = (1.0, 0.82, 0.5)
    light_data.shadow_soft_size = 0.3
    light_obj = bpy.data.objects.new("LampLight", light_data)
    light_obj.location = (x, y, 2.4)
    col.objects.link(light_obj)
    light_obj.parent = parent
    return parent


def build_stall(col, rng, x, y, rot, mats):
    parent = bpy.data.objects.new("Stall", None)
    col.objects.link(parent)
    parent.location = (x, y, 0)
    parent.rotation_euler = (0, 0, rot)

    def p(o):
        o.parent = parent
        o.matrix_parent_inverse = parent.matrix_world.inverted()

    counter = add_primitive("cube", col, mats["wood_light"], "Counter",
                         location=(x, y, 0.5), scale=(2.0, 1.0, 1.0))
    p(counter)
    for sx in (-1, 1):
        for sy in (-1, 1):
            leg = add_primitive("cube", col, mats["wood_trim"], "Post",
                             location=(x + sx * 0.9, y + sy * 0.45, 1.2),
                             scale=(0.1, 0.1, 2.4))
            p(leg)
    canopy_mat = rng.choice([mats["flag_red"], mats["roof_teal"], mats["cloth_cream"]])
    canopy = prism_roof("Canopy", col, canopy_mat, x, y, 2.1, 2.0, 1.0, 0.5, overhang=0.4)
    p(canopy)
    return parent


def build_fence(col, rng, x0, y0, x1, y1, mats):
    segs = max(2, int(math.hypot(x1 - x0, y1 - y0) / 1.2))
    for i in range(segs + 1):
        t = i / segs
        px = x0 + (x1 - x0) * t
        py = y0 + (y1 - y0) * t
        post = add_primitive("cube", col, mats["wood_light"], "FencePost",
                          location=(px, py, 0.5), scale=(0.12, 0.12, 1.0))
    # rails
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    length = math.hypot(x1 - x0, y1 - y0)
    ang = math.atan2(y1 - y0, x1 - x0)
    for rz in (0.4, 0.75):
        rail = add_primitive("cube", col, mats["wood_light"], "Rail",
                          location=(cx, cy, rz), scale=(length, 0.06, 0.1),
                          rotation=(0, 0, ang))


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
def build_ground(col, mats, size):
    ground = add_primitive("plane", col, mats["grass"], "Ground",
                         location=(0, 0, 0), scale=(size, size, 1))
    # central plaza disc
    plaza = add_primitive("cylinder", col, mats["path"], "Plaza",
                        location=(0, 0, 0.02), radius=size * 0.16, depth=0.04, verts=32)
    # cross paths
    for ang in (0, math.pi / 2):
        path = add_primitive("cube", col, mats["path"], "Path",
                          location=(0, 0, 0.02), scale=(size * 0.9, 2.4, 0.04),
                          rotation=(0, 0, ang))
    return ground


def setup_world_and_camera(rng, size):
    scene = bpy.context.scene

    # --- Cycles ---
    scene.render.engine = "CYCLES"
    try:
        scene.cycles.samples = 96
        scene.cycles.use_denoising = True
        scene.cycles.max_bounces = 4
        scene.cycles.caustics_reflective = False
        scene.cycles.caustics_refractive = False
    except Exception:
        pass
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    try:
        scene.view_settings.view_transform = "Filmic"
        scene.view_settings.look = "Medium High Contrast"
    except Exception:
        pass

    # --- gradient sky world ---
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    wnt = world.node_tree
    wnt.nodes.clear()
    wout = wnt.nodes.new("ShaderNodeOutputWorld")
    wout.location = (400, 0)
    bg = wnt.nodes.new("ShaderNodeBackground")
    bg.location = (200, 0)
    grad = wnt.nodes.new("ShaderNodeTexGradient")
    grad.gradient_type = "EASING"
    grad.location = (-200, 0)
    ramp = wnt.nodes.new("ShaderNodeValToRGB")
    ramp.location = (-60, 0)
    ramp.color_ramp.elements[0].color = (0.55, 0.72, 0.88, 1.0)  # horizon
    ramp.color_ramp.elements[1].color = (0.20, 0.42, 0.75, 1.0)  # zenith
    mapping = wnt.nodes.new("ShaderNodeMapping")
    mapping.location = (-420, 0)
    mapping.inputs["Rotation"].default_value = (math.radians(90), 0, 0)
    texco = wnt.nodes.new("ShaderNodeTexCoord")
    texco.location = (-620, 0)
    wl = wnt.links
    wl.new(texco.outputs["Generated"], mapping.inputs["Vector"])
    wl.new(mapping.outputs["Vector"], grad.inputs["Vector"])
    wl.new(grad.outputs["Fac"], ramp.inputs["Fac"])
    wl.new(ramp.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 1.1
    wl.new(bg.outputs["Background"], wout.inputs["Surface"])

    # --- sun key light ---
    sun_data = bpy.data.lights.new("Sun", type="SUN")
    sun_data.energy = 3.2
    sun_data.color = (1.0, 0.95, 0.82)
    sun_data.angle = math.radians(2.0)
    sun = bpy.data.objects.new("Sun", sun_data)
    sun.rotation_euler = (math.radians(52), math.radians(18), math.radians(35))
    bpy.context.scene.collection.objects.link(sun)

    # --- camera ---
    cam_data = bpy.data.cameras.new("VillageCamera")
    cam_data.lens = 40
    cam = bpy.data.objects.new("VillageCamera", cam_data)
    dist = size * 1.15
    cam.location = (dist, -dist, size * 0.75)
    # aim at plaza
    target = Vector((0, 0, 2.0))
    direction = target - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.collection.objects.link(cam)
    scene.camera = cam
    return sun, cam


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------
def generate(seed=7, building_count=15, tree_density=1.0, plaza_size=26.0):
    rng = random.Random(seed)
    clear_previous()
    _material_cache.clear()

    root = get_or_make_collection(ROOT_COLLECTION)
    c_env = get_or_make_collection("Village_Environment", root)
    c_build = get_or_make_collection("Village_Buildings", root)
    c_land = get_or_make_collection("Village_Landmarks", root)
    c_props = get_or_make_collection("Village_Props", root)
    c_nature = get_or_make_collection("Village_Nature", root)

    mats = {k: make_toon_material(k, v,
                                  emit=(2.2 if k == "window_glow" else 0.0))
            for k, v in PALETTE.items()}

    size = plaza_size
    build_ground(c_env, mats, size)

    # ---- landmarks around plaza ----
    build_fountain(c_land, rng, 0, 0, mats)
    build_bell_tower(c_land, rng, -size * 0.28, size * 0.26, mats)
    build_windmill(c_land, rng, size * 0.30, size * 0.28, mats)

    # ---- houses placed in a ring / rows, avoiding the plaza ----
    placed = []
    inner_r = size * 0.20
    outer_r = size * 0.46
    attempts = 0
    while len(placed) < building_count and attempts < building_count * 40:
        attempts += 1
        ang = rng.uniform(0, math.tau)
        r = rng.uniform(inner_r, outer_r)
        x = math.cos(ang) * r + rng.uniform(-1.5, 1.5)
        y = math.sin(ang) * r + rng.uniform(-1.5, 1.5)
        # keep clear of landmarks
        if math.hypot(x + size * 0.28, y - size * 0.26) < 5:
            continue
        if math.hypot(x - size * 0.30, y - size * 0.28) < 5:
            continue
        # spacing between houses
        if any(math.hypot(x - px, y - py) < 4.2 for px, py in placed):
            continue
        placed.append((x, y))
        rot = math.atan2(-y, -x) + math.pi / 2 + rng.uniform(-0.25, 0.25)
        build_house(c_build, rng, x, y, rot, mats)

    # ---- market stalls near plaza ----
    for i in range(4):
        ang = i * math.tau / 4 + math.pi / 4
        sx = math.cos(ang) * inner_r * 0.75
        sy = math.sin(ang) * inner_r * 0.75
        build_stall(c_props, rng, sx, sy, ang + math.pi / 2, mats)

    # ---- lamps ringing the plaza ----
    for i in range(6):
        ang = i * math.tau / 6
        lx = math.cos(ang) * (inner_r * 0.95)
        ly = math.sin(ang) * (inner_r * 0.95)
        build_lamp(c_props, rng, lx, ly, mats)

    # ---- nature scatter ----
    tree_target = int(18 * tree_density)
    trees = 0
    natt = 0
    while trees < tree_target and natt < tree_target * 30:
        natt += 1
        x = rng.uniform(-size * 0.5, size * 0.5)
        y = rng.uniform(-size * 0.5, size * 0.5)
        if math.hypot(x, y) < inner_r * 1.1:  # keep plaza open
            continue
        if any(math.hypot(x - px, y - py) < 3.0 for px, py in placed):
            continue
        build_tree(c_nature, rng, x, y, mats)
        trees += 1

    for _ in range(int(14 * tree_density)):
        x = rng.uniform(-size * 0.48, size * 0.48)
        y = rng.uniform(-size * 0.48, size * 0.48)
        if math.hypot(x, y) < inner_r:
            continue
        (build_bush if rng.random() < 0.6 else build_rock)(c_nature, rng, x, y, mats)

    # ---- perimeter fences on a couple of edges ----
    e = size * 0.5
    build_fence(c_props, rng, -e, -e, e, -e, mats)
    build_fence(c_props, rng, -e, -e, -e, e, mats)

    setup_world_and_camera(rng, size)

    print(f"[Genshin Village] Generated {len(placed)} houses, "
          f"{trees} trees (seed={seed}).")
    return root


# ---------------------------------------------------------------------------
# Add-on: properties, operator, panel
# ---------------------------------------------------------------------------
class VILLAGE_Props(bpy.types.PropertyGroup):
    seed: bpy.props.IntProperty(name="Seed", default=7, min=0, max=9999)
    building_count: bpy.props.IntProperty(name="Buildings", default=15, min=4, max=40)
    tree_density: bpy.props.FloatProperty(name="Tree Density", default=1.0, min=0.0, max=3.0)
    plaza_size: bpy.props.FloatProperty(name="Town Size", default=26.0, min=16.0, max=48.0)


class VILLAGE_OT_generate(bpy.types.Operator):
    bl_idname = "village.generate"
    bl_label = "Generate Village"
    bl_description = "Build (or rebuild) the Genshin-inspired village"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        p = context.scene.village_props
        generate(seed=p.seed, building_count=p.building_count,
                 tree_density=p.tree_density, plaza_size=p.plaza_size)
        self.report({"INFO"}, "Village generated")
        return {"FINISHED"}


class VILLAGE_PT_panel(bpy.types.Panel):
    bl_label = "Genshin Village"
    bl_idname = "VILLAGE_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Village"

    def draw(self, context):
        layout = self.layout
        p = context.scene.village_props
        col = layout.column(align=True)
        col.prop(p, "seed")
        col.prop(p, "building_count")
        col.prop(p, "tree_density")
        col.prop(p, "plaza_size")
        layout.separator()
        layout.operator("village.generate", icon="MOD_BUILD")


_classes = (VILLAGE_Props, VILLAGE_OT_generate, VILLAGE_PT_panel)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.village_props = bpy.props.PointerProperty(type=VILLAGE_Props)


def unregister():
    del bpy.types.Scene.village_props
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


# ---------------------------------------------------------------------------
# When run from the Text Editor / Scripting tab, register the add-on UI AND
# immediately build a village so you see a result right away.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        unregister()
    except Exception:
        pass
    register()
    generate()
