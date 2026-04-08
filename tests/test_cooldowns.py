from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from combat import clamp_turret_pitch, cooldown_remaining, tick_cooldowns
from config import CHAIN_FIRE, HEAVY_FIRE, RAPID_FIRE


class CooldownTests(unittest.TestCase):
    def test_tick_cooldowns_never_goes_negative(self):
        cooldowns = {"rapid": RAPID_FIRE.cooldown_s, "heavy": HEAVY_FIRE.cooldown_s}
        cooldowns = tick_cooldowns(cooldowns, RAPID_FIRE.cooldown_s + 0.1)
        self.assertEqual(cooldown_remaining(cooldowns, "rapid"), 0.0)
        self.assertAlmostEqual(
            cooldown_remaining(cooldowns, "heavy"),
            HEAVY_FIRE.cooldown_s - RAPID_FIRE.cooldown_s - 0.1,
        )

    def test_turret_pitch_clamps_to_spec(self):
        self.assertEqual(clamp_turret_pitch(-30.0), -10.0)
        self.assertEqual(clamp_turret_pitch(220.0), 190.0)
        self.assertEqual(clamp_turret_pitch(25.0), 25.0)

    def test_chain_shot_cooldown_sits_between_rapid_and_heavy(self):
        self.assertGreater(CHAIN_FIRE.cooldown_s, RAPID_FIRE.cooldown_s)
        self.assertLess(CHAIN_FIRE.cooldown_s, HEAVY_FIRE.cooldown_s)

    def test_weapon_damage_values_match_spec(self):
        self.assertEqual(RAPID_FIRE.damage, 5)
        self.assertEqual(CHAIN_FIRE.damage, 20)
        self.assertEqual(HEAVY_FIRE.damage, 20)


if __name__ == "__main__":
    unittest.main()
