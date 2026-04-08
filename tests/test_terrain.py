from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import HEAVY_FIRE, TERRAIN_DEPTH_SAMPLES, TERRAIN_MAX_HEIGHT, TERRAIN_WIDTH_SAMPLES
from terrain import (
    TerrainState,
    build_heightfield_image,
    carve_crater,
    flatten_spawn_pads,
    generate_height_profile,
    sample_surface_height,
    world_x_to_heightfield_x,
)


class TerrainTests(unittest.TestCase):
    def setUp(self):
        rng = random.Random(12345)
        profile = generate_height_profile(rng=rng)
        profile, spawn_pixels = flatten_spawn_pads(profile)
        image = build_heightfield_image(profile)
        self.terrain = TerrainState(
            image=image,
            geomip=None,
            terrain_root=None,
            terrain_body_np=None,
            world_width=image.getXSize() - 1,
            world_depth=image.getYSize() - 1,
            max_height=TERRAIN_MAX_HEIGHT,
            kill_z=-22.0,
            spawn_pixels=spawn_pixels,
        )

    def test_profile_dimensions_match_plan(self):
        self.assertEqual(self.terrain.image.getXSize(), TERRAIN_WIDTH_SAMPLES)
        self.assertEqual(self.terrain.image.getYSize(), TERRAIN_DEPTH_SAMPLES)

    def test_world_mapping_clamps_to_image_bounds(self):
        self.assertEqual(world_x_to_heightfield_x(self.terrain, -999.0), 0.0)
        self.assertEqual(world_x_to_heightfield_x(self.terrain, 999.0), self.terrain.image.getXSize() - 1)

    def test_spawn_pad_surface_is_flat_enough(self):
        for pixel in self.terrain.spawn_pixels:
            center_world = pixel - self.terrain.world_width / 2.0
            left = sample_surface_height(self.terrain, center_world - 2.0)
            right = sample_surface_height(self.terrain, center_world + 2.0)
            self.assertLess(abs(right - left), 0.35)

    def test_crater_only_lowers_terrain(self):
        before = [
            self.terrain.image.getGray(x, self.terrain.image.getYSize() // 2)
            for x in range(self.terrain.image.getXSize())
        ]
        changed = carve_crater(
            self.terrain,
            0.0,
            HEAVY_FIRE.radius,
            impact_speed=26.0,
            radius_scale=HEAVY_FIRE.crater_radius_scale,
            depth_scale=HEAVY_FIRE.crater_depth_scale,
        )
        self.assertTrue(changed)
        after = [
            self.terrain.image.getGray(x, self.terrain.image.getYSize() // 2)
            for x in range(self.terrain.image.getXSize())
        ]
        for before_value, after_value in zip(before, after):
            self.assertLessEqual(after_value, before_value + 1e-9)


if __name__ == "__main__":
    unittest.main()
