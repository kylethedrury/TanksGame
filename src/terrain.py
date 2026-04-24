"""Terrain generation, sampling, and crater carving."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, cos, degrees, pi, sin, tau
import random

from panda3d.bullet import BulletHeightfieldShape, BulletRigidBodyNode, ZUp
from panda3d.core import (
    BitMask32,
    Geom,
    GeomNode,
    GeomTriangles,
    GeomVertexData,
    GeomVertexFormat,
    GeomVertexWriter,
    LineSegs,
    PNMImage,
    Vec3,
)

from config import (
    TERRAIN_DEPTH_SAMPLES,
    TERRAIN_KILL_Z,
    TERRAIN_MAX_HEIGHT,
    TERRAIN_SPAWN_FRACTIONS,
    TERRAIN_SPAWN_PAD_RADIUS,
    TERRAIN_WIDTH_SAMPLES,
)


@dataclass
class TerrainState:
    """Everything tied to the current terrain."""

    image: PNMImage
    geomip: object
    terrain_root: object
    terrain_body_np: object
    world_width: float
    world_depth: float
    max_height: float
    kill_z: float
    spawn_pixels: tuple[int, int] = field(default_factory=tuple)
    profile_visual_np: object | None = None


PROFILE_VISUAL_Y = 4.0
PROFILE_BASE_MARGIN = 6.0


def pixel_to_world_x(pixel_x: float, image: PNMImage) -> float:
    """Turn a heightfield x index into a centered world x value."""

    return pixel_x - (image.getXSize() - 1) / 2.0


def world_x_to_heightfield_x(terrain: TerrainState, world_x: float) -> float:
    """Convert world x back into heightfield space."""

    midpoint = terrain.world_width / 2.0
    pixel_x = world_x + midpoint
    return max(0.0, min(terrain.image.getXSize() - 1, pixel_x))


def height_gray_to_world(gray: float, terrain: TerrainState) -> float:
    """Turn a grayscale height value into a world-space height."""

    gray = max(0.0, min(1.0, gray))
    return gray * terrain.max_height - terrain.max_height / 2.0


def sample_gray(image: PNMImage, pixel_x: float) -> float:
    """Sample the hill profile with simple linear interpolation."""

    x0 = int(max(0, min(image.getXSize() - 1, pixel_x)))
    x1 = min(x0 + 1, image.getXSize() - 1)
    blend = pixel_x - x0
    g0 = image.getGray(x0, image.getYSize() // 2)
    g1 = image.getGray(x1, image.getYSize() // 2)
    return g0 * (1.0 - blend) + g1 * blend


def sample_surface_height(terrain: TerrainState, world_x: float) -> float:
    """Return the terrain height at a world x position."""

    pixel_x = world_x_to_heightfield_x(terrain, world_x)
    gray = sample_gray(terrain.image, pixel_x)
    return height_gray_to_world(gray, terrain)


def surface_normal_at_x(terrain: TerrainState, world_x: float) -> Vec3:
    """Estimate the terrain normal near ``world_x``."""

    left = sample_surface_height(terrain, world_x - 1.0)
    right = sample_surface_height(terrain, world_x + 1.0)
    slope = (right - left) / 2.0
    normal = Vec3(-slope, 0.0, 1.0)
    normal.normalize()
    return normal


def surface_angle_degrees(terrain: TerrainState, world_x: float) -> float:
    """Return the local slope angle in degrees."""

    left = sample_surface_height(terrain, world_x - 1.0)
    right = sample_surface_height(terrain, world_x + 1.0)
    slope = (right - left) / 2.0
    return -degrees(atan2(slope, 1.0))


def generate_height_profile(
    width: int = TERRAIN_WIDTH_SAMPLES,
    rng: random.Random | None = None,
) -> list[float]:
    """Generate a smooth hill profile from a few blended sine waves."""

    rng = rng or random.Random()
    freq1 = rng.uniform(1.5, 2.8)
    freq2 = rng.uniform(3.8, 6.5)
    freq3 = rng.uniform(7.5, 10.5)
    phase1 = rng.uniform(0.0, tau)
    phase2 = rng.uniform(0.0, tau)
    phase3 = rng.uniform(0.0, tau)
    amp1 = rng.uniform(0.14, 0.23)
    amp2 = rng.uniform(0.07, 0.13)
    amp3 = rng.uniform(0.03, 0.08)

    values: list[float] = []
    for px in range(width):
        # Work in normalized x so the shape does not depend on sample count.
        xn = px / max(1, width - 1)
        value = 0.46
        value += amp1 * sin(freq1 * pi * xn + phase1)
        value += amp2 * sin(freq2 * pi * xn + phase2)
        value += amp3 * sin(freq3 * pi * xn + phase3)
        values.append(value)

    # Smooth out the raw waves into broader hills.
    for _ in range(12):
        smoothed = values[:]
        for px in range(1, width - 1):
            smoothed[px] = (
                values[px - 1] * 0.20
                + values[px] * 0.60
                + values[px + 1] * 0.20
            )
        values = smoothed

    values = soften_terrain_edges(values, edge_span=max(14, width // 14))
    return [max(0.30, min(0.84, value)) for value in values]


def soften_terrain_edges(values: list[float], edge_span: int) -> list[float]:
    """Soften the map edges so they do not end in sharp points."""

    softened = values[:]
    if len(values) < edge_span * 2 + 2:
        return softened

    plateau = max(4, edge_span // 3)
    left_anchor = sum(values[edge_span : edge_span + 4]) / 4.0
    right_anchor = sum(values[-edge_span - 4 : -edge_span]) / 4.0

    for index in range(edge_span):
        mirror = len(values) - 1 - index
        if index < plateau:
            softened[index] = left_anchor
            softened[mirror] = right_anchor
            continue

        t = (index - plateau) / max(1, edge_span - plateau - 1)
        blend = 0.5 - 0.5 * cos(t * pi)
        softened[index] = left_anchor * (1.0 - blend) + values[index] * blend
        softened[mirror] = right_anchor * (1.0 - blend) + values[mirror] * blend

    return softened


def flatten_spawn_pads(
    values: list[float],
    pad_radius: int = TERRAIN_SPAWN_PAD_RADIUS,
) -> tuple[list[float], tuple[int, int]]:
    """Flatten the spawn areas enough for the tanks to start cleanly."""

    flattened = values[:]
    spawn_pixels = tuple(
        int(round((len(values) - 1) * fraction)) for fraction in TERRAIN_SPAWN_FRACTIONS
    )
    for pixel in spawn_pixels:
        core_radius = max(2, pad_radius - 3)
        start = max(0, pixel - core_radius)
        end = min(len(flattened) - 1, pixel + core_radius)
        average = sum(flattened[start : end + 1]) / (end - start + 1)
        blend_radius = pad_radius + 4
        for target in range(max(0, pixel - blend_radius), min(len(flattened) - 1, pixel + blend_radius) + 1):
            distance = abs(target - pixel)
            if distance <= core_radius:
                flattened[target] = average
            else:
                alpha = 1.0 - (distance - core_radius) / max(1, blend_radius - core_radius)
                alpha = max(0.0, min(1.0, alpha))
                flattened[target] = flattened[target] * (1.0 - alpha) + average * alpha
    return flattened, spawn_pixels


def build_heightfield_image(
    profile: list[float],
    depth: int = TERRAIN_DEPTH_SAMPLES,
) -> PNMImage:
    """Copy the 1D hill profile across the shallow terrain depth."""

    image = PNMImage(len(profile), depth, 1, 65535)
    for x, value in enumerate(profile):
        for y in range(depth):
            image.setGray(x, y, value)
    return image


def create_terrain_state(world, render, camera, rng: random.Random) -> TerrainState:
    """Build the terrain visuals, collision body, and cached metadata."""

    profile = generate_height_profile(rng=rng)
    profile, spawn_pixels = flatten_spawn_pads(profile)
    image = build_heightfield_image(profile)

    # The renderer and Bullet body both read the same heightfield image.
    geomip = _create_geomip(image, camera)
    terrain_root = geomip.getRoot()
    terrain_root.reparentTo(render)
    terrain_root.setScale(1.0, 1.0, TERRAIN_MAX_HEIGHT)
    terrain_root.setPos(
        -(image.getXSize() - 1) / 2.0,
        -(image.getYSize() - 1) / 2.0,
        -TERRAIN_MAX_HEIGHT / 2.0,
    )
    terrain_root.setColor(0.22, 0.70, 0.28, 1.0)
    geomip.generate()

    terrain_body_np = create_terrain_body(world, render, image)
    terrain = TerrainState(
        image=image,
        geomip=geomip,
        terrain_root=terrain_root,
        terrain_body_np=terrain_body_np,
        world_width=image.getXSize() - 1,
        world_depth=image.getYSize() - 1,
        max_height=TERRAIN_MAX_HEIGHT,
        kill_z=TERRAIN_KILL_Z,
        spawn_pixels=spawn_pixels,
    )
    terrain.profile_visual_np = create_profile_visual(render, terrain)
    return terrain


def create_terrain_body(world, render, image: PNMImage):
    """Build the Bullet heightfield body from the current image."""

    shape = BulletHeightfieldShape(image, TERRAIN_MAX_HEIGHT, ZUp)
    shape.setUseDiamondSubdivision(True)

    body_np = render.attachNewNode(BulletRigidBodyNode("terrain"))
    body_np.node().addShape(shape)
    body_np.node().setFriction(0.62)
    body_np.node().setRestitution(0.35)
    body_np.setCollideMask(BitMask32.allOn())
    body_np.setTag("kind", "terrain")
    world.attachRigidBody(body_np.node())
    return body_np


def rebuild_terrain_body(world, terrain: TerrainState):
    """Rebuild collision and visuals after the heightfield changes."""

    world.removeRigidBody(terrain.terrain_body_np.node())
    terrain.terrain_body_np.removeNode()
    terrain.terrain_body_np = create_terrain_body(
        world,
        terrain.terrain_root.getParent(),
        terrain.image,
    )
    terrain.geomip.update()
    # The side profile is separate from GeoMipTerrain, so rebuild it too.
    if terrain.profile_visual_np:
        terrain.profile_visual_np.removeNode()
    terrain.profile_visual_np = create_profile_visual(terrain.terrain_root.getParent(), terrain)
    return terrain.terrain_body_np


def carve_crater(
    terrain: TerrainState,
    world_x: float,
    projectile_radius: float,
    impact_speed: float,
    radius_scale: float,
    depth_scale: float,
) -> bool:
    """Lower the heightfield around an impact point."""

    if impact_speed <= 0.0:
        return False

    # Faster or larger shots make bigger craters.
    radius_world = projectile_radius * radius_scale * 0.66 + impact_speed * 0.042
    depth_world = projectile_radius * depth_scale * 0.34 + impact_speed * 0.015
    if radius_world <= 0.0 or depth_world <= 0.0:
        return False

    pixel_center = world_x_to_heightfield_x(terrain, world_x)
    pixel_radius = max(1.0, radius_world)
    depth_gray = depth_world / terrain.max_height
    touched = False

    min_x = max(0, int(pixel_center - pixel_radius) - 1)
    max_x = min(terrain.image.getXSize() - 1, int(pixel_center + pixel_radius) + 1)
    for px in range(min_x, max_x + 1):
        dx = abs(px - pixel_center)
        if dx > pixel_radius:
            continue
        falloff = 1.0 - (dx / pixel_radius) ** 2
        delta = depth_gray * falloff
        if delta <= 0.0:
            continue
        for py in range(terrain.image.getYSize()):
            current = terrain.image.getGray(px, py)
            lowered = max(0.0, current - delta)
            if lowered < current:
                terrain.image.setGray(px, py, lowered)
                touched = True
    return touched


def _create_geomip(image: PNMImage, camera):
    """Create the GeoMipTerrain renderer for the current heightfield."""

    from panda3d.core import GeoMipTerrain

    geomip = GeoMipTerrain("terrain")
    geomip.setHeightfield(image)
    geomip.setBlockSize(32)
    geomip.setNear(40)
    geomip.setFar(120)
    geomip.setFocalPoint(camera)
    return geomip


def create_profile_visual(parent, terrain: TerrainState):
    """Create the filled side-view hill silhouette behind the tanks."""

    vertex_format = GeomVertexFormat.getV3n3c4()
    vdata = GeomVertexData("terrain-profile", vertex_format, Geom.UHStatic)
    vertices = GeomVertexWriter(vdata, "vertex")
    normals = GeomVertexWriter(vdata, "normal")
    colors = GeomVertexWriter(vdata, "color")
    triangles = GeomTriangles(Geom.UHStatic)

    base_z = min(terrain.kill_z - 2.0, -terrain.max_height / 2.0 - PROFILE_BASE_MARGIN)
    mid_y = PROFILE_VISUAL_Y

    for x in range(terrain.image.getXSize()):
        # Each terrain sample gets a top vertex and a matching bottom vertex so
        # the side profile can be filled with triangles.
        world_x = pixel_to_world_x(x, terrain.image)
        surface_z = sample_surface_height(terrain, world_x)

        vertices.addData3(world_x, mid_y, surface_z)
        normals.addData3(0.0, -1.0, 0.0)
        colors.addData4(0.20, 0.62, 0.25, 1.0)

        vertices.addData3(world_x, mid_y, base_z)
        normals.addData3(0.0, -1.0, 0.0)
        colors.addData4(0.12, 0.42, 0.17, 1.0)

    for x in range(terrain.image.getXSize() - 1):
        top_left = x * 2
        bottom_left = top_left + 1
        top_right = top_left + 2
        bottom_right = top_left + 3
        triangles.addVertices(bottom_left, top_left, top_right)
        triangles.addVertices(bottom_left, top_right, bottom_right)

    geom = Geom(vdata)
    geom.addPrimitive(triangles)
    geom_node = GeomNode("terrain-profile")
    geom_node.addGeom(geom)

    profile_np = parent.attachNewNode(geom_node)
    profile_np.setTwoSided(True)
    profile_np.setLightOff(1)

    # Add a brighter ridge line so the top edge stands out.
    ridge = LineSegs("terrain-ridge")
    ridge.setThickness(2.5)
    ridge.setColor(0.55, 0.85, 0.38, 1.0)
    ridge.moveTo(
        pixel_to_world_x(0, terrain.image),
        mid_y - 0.01,
        sample_surface_height(terrain, pixel_to_world_x(0, terrain.image)),
    )
    for x in range(1, terrain.image.getXSize()):
        world_x = pixel_to_world_x(x, terrain.image)
        ridge.drawTo(world_x, mid_y - 0.01, sample_surface_height(terrain, world_x))
    ridge_np = profile_np.attachNewNode(ridge.create())
    ridge_np.setLightOff(1)

    return profile_np
