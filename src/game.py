"""Main Panda3D app and fixed-step game loop."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, radians, sin
import random

from direct.showbase.ShowBase import ShowBase
from panda3d.bullet import BulletDebugNode, BulletWorld
from panda3d.core import (
    AmbientLight,
    DirectionalLight,
    Filename,
    OrthographicLens,
    Vec3,
    Vec4,
    loadPrcFileData,
)

from combat import (
    ProjectileState,
    TankState,
    apply_elastic_contact_impulse,
    clamp_turret_pitch,
    cooldown_remaining,
    remove_projectile,
    spawn_chain_projectiles,
    spawn_projectile,
    spawn_tank,
    tick_cooldowns,
    update_chain_visual,
    update_turret_visual,
)
from config import (
    ASSET_DIR,
    CAMERA_FILM_HEIGHT,
    CAMERA_FILM_WIDTH,
    CAMERA_LOOK_AT,
    CAMERA_POS,
    CHAIN_LINK_HALF_LENGTH,
    CHAIN_SPIN_SPEED,
    FIXED_DT,
    MAX_FRAME_DT,
    PLAYER_CONTROLS,
    PLAYER_NAMES,
    PROJECTILE_KNOCKBACK_SCALE,
    PROJECTILE_GROUND_DESPAWN_S,
    PROJECTILE_MIN_CRATER_IMPACT_SPEED,
    PROJECTILE_PROJECTILE_RESTITUTION,
    PROJECTILE_REMOVAL_BUFFER,
    PROJECTILE_TANK_CONTROL_LOCK,
    PROJECTILE_TANK_RESTITUTION,
    SKY_COLOR,
    TANK_AIR_ACCEL,
    TANK_COLLISION_BUFFER,
    TANK_CONTACT_RESTITUTION,
    TANK_GROUND_ACCEL,
    TANK_GROUNDED_EPSILON,
    TANK_GROUND_SPEED_EPSILON,
    TANK_HALF_HEIGHT,
    TANK_HALF_WIDTH,
    TANK_HITBOX_LOWER_Z_PAD,
    TANK_HITBOX_X_PAD,
    TANK_IDLE_DECEL,
    TANK_JUMP_IMPULSE,
    TANK_KNOCKBACK_DECAY,
    TANK_LOWER_HALF_HEIGHT,
    TANK_MAX_SPEED,
    TANK_MOVE_SPEED,
    TANK_RIDE_HEIGHT,
    TANK_SUPPORT_PROBE,
    TANK_SURFACE_SMOOTHING,
    TANK_TURRET_AIM_SPEED,
    WEAPONS,
    WINDOW_TITLE,
    WORLD_GRAVITY_Z,
)
from hud import GameHud
from terrain import (
    TerrainState,
    carve_crater,
    create_terrain_state,
    pixel_to_world_x,
    rebuild_terrain_body,
    sample_surface_height,
    surface_angle_degrees,
    surface_normal_at_x,
)


loadPrcFileData("", f"window-title {WINDOW_TITLE}")


@dataclass
class ChainShotState:
    """Shared state for one chain shot."""

    group_id: int
    projectile_keys: tuple[int, int]
    center_pos: Vec3
    center_velocity: Vec3
    angle_rad: float
    angular_velocity: float
    half_length: float


class TanksHotseatGame(ShowBase):
    """The running game."""

    def __init__(self):
        super().__init__()
        self.disableMouse()
        self.setBackgroundColor(*SKY_COLOR)
        self.setFrameRateMeter(True)

        # Everything tied to a single match gets reset in ``reset_match``.
        self.assets = self._load_assets()
        self.hud = GameHud()
        self.debug_np = None
        self.world = None
        self.terrain: TerrainState | None = None
        self.tanks: list[TankState] = []
        self.projectiles: list[ProjectileState] = []
        self.active_contact_pairs: set[tuple[int, int]] = set()
        self.key_state: dict[str, bool] = {}
        self.button_states: dict[str, bool] = {}
        self.accumulator = 0.0
        self.rng = random.Random()
        self.match_over = False
        self.winner_text: str | None = None
        self.next_chain_group_id = 1
        self.chain_shots: dict[int, ChainShotState] = {}

        self._configure_camera()
        self._configure_lighting()
        self._bind_events()
        self.reset_match()
        self.taskMgr.add(self._update_task, "update-game")

    def reset_match(self):
        """Start a new round from scratch."""

        self._clear_world()
        self.projectiles = []
        self.tanks = []
        self.active_contact_pairs.clear()
        for key in list(self.key_state):
            self.key_state[key] = False
        self.button_states.clear()
        self.accumulator = 0.0
        self.match_over = False
        self.winner_text = None
        self.next_chain_group_id = 1
        self.chain_shots.clear()

        self.world = BulletWorld()
        self.world.setGravity(Vec3(0.0, 0.0, WORLD_GRAVITY_Z))

        debug_node = BulletDebugNode("debug")
        self.debug_np = self.render.attachNewNode(debug_node)
        self.debug_np.hide()
        self.world.setDebugNode(debug_node)

        # Build both the visible terrain and the Bullet collision terrain.
        self.terrain = create_terrain_state(self.world, self.render, self.camera, self.rng)

        for index, pixel in enumerate(self.terrain.spawn_pixels, start=1):
            spawn_x = pixel_to_world_x(pixel, self.terrain.image)
            spawn_z = sample_surface_height(self.terrain, spawn_x)
            slope_angle = surface_angle_degrees(self.terrain, spawn_x)
            tank = spawn_tank(
                self.world,
                self.render,
                self.assets,
                index,
                spawn_x,
                spawn_z,
                slope_angle,
            )
            self.tanks.append(tank)
            self._align_tank_to_surface(tank, spawn_x)

        self._refresh_hud()

    def destroy(self):
        """Clean up world state and UI on exit."""

        self._clear_world()
        self.hud.destroy()
        super().destroy()

    def _load_assets(self):
        """Load the simple reusable models used by the game."""

        cube_path = Filename.fromOsSpecific(str(ASSET_DIR / "Cube.egg"))
        sphere_path = Filename.fromOsSpecific(str(ASSET_DIR / "sphere.egg.pz"))
        return {
            "cube": self.loader.loadModel(cube_path),
            "sphere": self.loader.loadModel(sphere_path),
        }

    def _configure_camera(self):
        """Set up the side-view camera."""

        lens = OrthographicLens()
        lens.setFilmSize(CAMERA_FILM_WIDTH, CAMERA_FILM_HEIGHT)
        lens.setNearFar(-250.0, 250.0)
        self.cam.node().setLens(lens)
        self.cam.setPos(*CAMERA_POS)
        self.cam.lookAt(*CAMERA_LOOK_AT)

    def _configure_lighting(self):
        """Add basic lighting so the scene reads clearly."""

        ambient = AmbientLight("ambient")
        ambient.setColor(Vec4(0.60, 0.60, 0.62, 1.0))
        ambient_np = self.render.attachNewNode(ambient)

        directional = DirectionalLight("directional")
        directional.setDirection(Vec3(0.4, 0.2, -1.0))
        directional.setColor(Vec4(0.94, 0.94, 0.90, 1.0))
        directional_np = self.render.attachNewNode(directional)

        self.render.clearLight()
        self.render.setLight(ambient_np)
        self.render.setLight(directional_np)

    def _bind_events(self):
        """Register global commands and gameplay keys."""

        self.accept("escape", self.userExit)
        self.accept("r", self.reset_match)
        self.accept("f3", self._toggle_debug)
        for player_controls in PLAYER_CONTROLS.values():
            for action, key in player_controls.items():
                self._bind_key(key)

    def _toggle_debug(self):
        """Show or hide Bullet debug shapes."""

        if self.debug_np.isHidden():
            self.debug_np.show()
        else:
            self.debug_np.hide()

    def _update_task(self, task):
        """Convert frame time into fixed simulation steps."""

        frame_dt = min(globalClock.getDt(), MAX_FRAME_DT)
        self.accumulator += frame_dt
        while self.accumulator >= FIXED_DT:
            self._fixed_update(FIXED_DT)
            self.accumulator -= FIXED_DT
        return task.cont

    def _fixed_update(self, dt: float):
        """Advance one fixed gameplay step."""

        if not self.world or not self.terrain:
            return

        if not self.match_over:
            self._process_player_inputs(dt)

        # Save the previous projectile state before Bullet advances.
        for projectile in self.projectiles:
            projectile.last_pos = Vec3(projectile.body_np.getPos())
            projectile.previous_velocity = Vec3(projectile.body_np.node().getLinearVelocity())

        self.world.doPhysics(dt, 1, dt)
        # Chain shot has its own tether update on top of Bullet.
        self._advance_chain_shots(dt)
        self._update_grounding(dt)
        self._clamp_tank_speeds()
        self._resolve_tank_tank_collision()
        self._update_projectiles(dt)
        self._update_chain_visuals()
        if not self.match_over:
            self._handle_contacts()
            self._check_win_state()
        self._refresh_hud()

    def _process_player_inputs(self, dt: float):
        """Handle movement, aiming, jumping, and firing."""

        for tank in self.tanks:
            controls = PLAYER_CONTROLS[tank.player_id]
            tank.cooldowns = tick_cooldowns(tank.cooldowns, dt)
            tank.movement_lock_s = max(0.0, tank.movement_lock_s - dt)

            move_dir = 0
            if tank.movement_lock_s > 0.0 and abs(tank.knockback_velocity_x) > 0.01:
                # Let knockback play out for a moment before handing control back.
                self._apply_knockback_motion(tank, dt)
            elif tank.movement_lock_s <= 0.0:
                tank.knockback_velocity_x = 0.0
                if self._is_key_down(controls["left"]):
                    move_dir -= 1
                if self._is_key_down(controls["right"]):
                    move_dir += 1
                self._apply_horizontal_movement(tank, move_dir, dt)

            aim_delta = 0.0
            if self._is_key_down(controls["aim_up"]):
                aim_delta += TANK_TURRET_AIM_SPEED * dt
            if self._is_key_down(controls["aim_down"]):
                aim_delta -= TANK_TURRET_AIM_SPEED * dt
            if aim_delta:
                tank.turret_pitch_deg = clamp_turret_pitch(tank.turret_pitch_deg + aim_delta)
                update_turret_visual(tank)

            if self._pressed_once(controls["jump"]) and tank.grounded:
                tank.body_np.node().applyCentralImpulse(Vec3(0.0, 0.0, TANK_JUMP_IMPULSE))
                tank.body_np.node().setActive(True)
                tank.grounded = False
                tank.airborne = True
                tank.support_frames = 0

            if self._is_key_down(controls["rapid"]):
                self._try_fire_weapon(tank, WEAPONS["rapid"])
            if self._is_key_down(controls["heavy"]):
                self._try_fire_weapon(tank, WEAPONS["heavy"])
            if self._is_key_down(controls["chain"]):
                self._try_fire_weapon(tank, WEAPONS["chain"])

    def _try_fire_weapon(self, tank: TankState, weapon):
        """Fire a weapon if it is ready."""

        if cooldown_remaining(tank.cooldowns, weapon.name) > 0.0:
            return
        if weapon.name == "chain":
            projectiles = spawn_chain_projectiles(
                self.world,
                self.render,
                self.assets,
                tank,
                self.next_chain_group_id,
            )
            self.next_chain_group_id += 1
            self.projectiles.extend(projectiles)
            self._register_chain_shot(projectiles)
        else:
            projectile = spawn_projectile(self.world, self.render, self.assets, tank, weapon)
            self.projectiles.append(projectile)
        tank.cooldowns[weapon.name] = weapon.cooldown_s

    def _update_grounding(self, dt: float):
        """Figure out whether each tank is supported by terrain."""

        for tank in self.tanks:
            surface_height = self._support_surface_height(tank.body_np.getX())
            body_bottom = tank.body_np.getZ() - TANK_HALF_HEIGHT
            vertical_speed = tank.body_np.node().getLinearVelocity().z
            supported_now = (
                surface_height is not None
                and self._has_support_at(tank.body_np.getX())
                and
                body_bottom <= surface_height + TANK_GROUNDED_EPSILON
                and vertical_speed <= TANK_GROUND_SPEED_EPSILON
            )
            if supported_now:
                tank.support_frames = min(6, tank.support_frames + 1)
            else:
                tank.support_frames = max(0, tank.support_frames - 1)

            tank.grounded = tank.support_frames >= 2
            if tank.grounded:
                # Smooth the ride height so tanks do not jitter over the terrain.
                target_z = surface_height + TANK_HALF_HEIGHT + TANK_RIDE_HEIGHT
                current_z = tank.body_np.getZ()
                smooth_factor = min(1.0, TANK_SURFACE_SMOOTHING * dt)
                tank.body_np.setZ(current_z + (target_z - current_z) * smooth_factor)
                velocity = tank.body_np.node().getLinearVelocity()
                if velocity.z < 0.0:
                    velocity.z *= 0.25
                    tank.body_np.node().setLinearVelocity(velocity)
                self._align_tank_to_surface(tank, tank.body_np.getX())
                tank.airborne = False
            elif surface_height is None or vertical_speed < -0.1:
                tank.airborne = True

    def _clamp_tank_speeds(self):
        """Keep tanks in the gameplay plane and cap their speed."""

        for tank in self.tanks:
            velocity = tank.body_np.node().getLinearVelocity()
            velocity.y = 0.0
            velocity.x = max(-TANK_MAX_SPEED, min(TANK_MAX_SPEED, velocity.x))
            tank.body_np.node().setLinearVelocity(velocity)

    def _update_projectiles(self, dt: float):
        """Age projectiles out and remove stray shots."""

        removal_keys: set[int] = set()
        for projectile in self.projectiles:
            projectile.lifetime_s -= dt
            if projectile.touching_ground:
                projectile.ground_time_s += dt
            else:
                projectile.ground_time_s = 0.0
            if projectile.lifetime_s <= 0.0:
                self._mark_projectile_group_for_removal(projectile, removal_keys)
                continue
            if projectile.ground_time_s >= PROJECTILE_GROUND_DESPAWN_S:
                self._mark_projectile_group_for_removal(projectile, removal_keys)
                continue

            pos = projectile.body_np.getPos()
            if (
                pos.z < self.terrain.kill_z - PROJECTILE_REMOVAL_BUFFER
                or abs(pos.x) > self.terrain.world_width / 2.0 + PROJECTILE_REMOVAL_BUFFER
            ):
                self._mark_projectile_group_for_removal(projectile, removal_keys)

        if removal_keys:
            survivors: list[ProjectileState] = []
            removed: list[ProjectileState] = []
            for projectile in self.projectiles:
                if self._node_key(projectile.body_np.node()) in removal_keys:
                    removed.append(projectile)
                else:
                    survivors.append(projectile)
            self._dispose_projectiles(removed)
            self.projectiles = survivors

    def _handle_contacts(self):
        """Handle new projectile contacts against tanks, terrain, and other shots."""

        current_pairs: set[tuple[int, int]] = set()
        handled_new_pairs: set[tuple[int, int]] = set()
        crater_requests: list[tuple[ProjectileState, float, float]] = []
        projectiles_to_remove: set[int] = set()

        projectile_nodes = {
            self._node_key(projectile.body_np.node()): projectile for projectile in self.projectiles
        }
        tank_nodes = {self._node_key(tank.body_np.node()): tank for tank in self.tanks}

        for projectile in self.projectiles:
            projectile_key = self._node_key(projectile.body_np.node())
            if projectile_key in projectiles_to_remove:
                continue
            terrain_touch = False
            result = self.world.contactTest(projectile.body_np.node())
            for contact in result.getContacts():
                node_a = contact.getNode0()
                node_b = contact.getNode1()
                other = (
                    node_b
                    if self._node_key(node_a) == self._node_key(projectile.body_np.node())
                    else node_a
                )
                pair = self._pair_key(projectile.body_np.node(), other)
                current_pairs.add(pair)

                if pair in self.active_contact_pairs or pair in handled_new_pairs:
                    if other.getName() == "terrain":
                        terrain_touch = True
                    continue

                other_key = self._node_key(other)
                if other_key in tank_nodes:
                    # If one chain-shot ball hits, remove the whole pair.
                    tank = tank_nodes[other_key]
                    self._handle_projectile_tank_contact(projectile, tank)
                    self._mark_projectile_group_for_removal(projectile, projectiles_to_remove)
                    handled_new_pairs.add(pair)
                    break
                elif other_key in projectile_nodes:
                    other_projectile = projectile_nodes[other_key]
                    if projectile is not other_projectile:
                        if (
                            projectile.chain_group_id is not None
                            and projectile.chain_group_id == other_projectile.chain_group_id
                        ):
                            continue
                        self._handle_projectile_projectile_contact(projectile, other_projectile)
                        handled_new_pairs.add(pair)
                elif other.getName() == "terrain":
                    terrain_touch = True
                    if projectile.crater_armed:
                        # Only carve a crater on the first impact, not every
                        # frame while the shell is rolling.
                        impact_speed = self._projectile_ground_impact_speed(projectile)
                        crater_requests.append((projectile, projectile.body_np.getX(), impact_speed))
                        if projectile.chain_group_id is not None:
                            self._mark_projectile_group_for_removal(projectile, projectiles_to_remove)
                        handled_new_pairs.add(pair)
            if terrain_touch:
                projectile.touching_ground = True
                projectile.crater_armed = False
            else:
                projectile.touching_ground = False
                projectile.crater_armed = True

        for projectile, world_x, impact_speed in crater_requests:
            if impact_speed < PROJECTILE_MIN_CRATER_IMPACT_SPEED:
                continue
            if carve_crater(
                self.terrain,
                world_x,
                projectile.weapon.radius,
                impact_speed,
                projectile.weapon.crater_radius_scale,
                projectile.weapon.crater_depth_scale,
            ):
                rebuild_terrain_body(self.world, self.terrain)
                self._reactivate_nearby_bodies(world_x, projectile.weapon.radius * 8.0)

        if projectiles_to_remove:
            survivors: list[ProjectileState] = []
            removed: list[ProjectileState] = []
            for projectile in self.projectiles:
                if self._node_key(projectile.body_np.node()) in projectiles_to_remove:
                    removed.append(projectile)
                    continue
                survivors.append(projectile)
            self._dispose_projectiles(removed)
            self.projectiles = survivors

        self.active_contact_pairs = current_pairs

    def _handle_projectile_tank_contact(self, projectile: ProjectileState, tank: TankState):
        """Apply damage and knockback when a shell hits a tank."""

        normal = tank.body_np.getPos() - projectile.body_np.getPos()
        normal.y = 0.0
        if normal.length_squared() <= 1e-9:
            normal = Vec3(1.0 if projectile.owner_id != tank.player_id else -1.0, 0.0, 0.0)
        apply_elastic_contact_impulse(
            projectile.body_np.node(),
            tank.body_np.node(),
            normal,
            PROJECTILE_TANK_RESTITUTION,
        )
        normal.normalize()
        impact_speed = abs(projectile.previous_velocity.dot(normal))
        knockback = (
            projectile.weapon.mass * impact_speed * 0.50
            + projectile.damage * PROJECTILE_KNOCKBACK_SCALE
        )
        tank.body_np.node().applyCentralImpulse(normal * knockback)
        tank_velocity = tank.body_np.node().getLinearVelocity()
        if abs(normal.x) > 1e-6:
            direction = 1.0 if normal.x >= 0.0 else -1.0
            minimum_speed = min(TANK_MAX_SPEED, projectile.damage * 1.1 + impact_speed * 0.18)
            if direction * tank_velocity.x < minimum_speed:
                tank_velocity.x = direction * minimum_speed
                tank.body_np.node().setLinearVelocity(tank_velocity)
            tank.knockback_velocity_x = direction * minimum_speed
        tank.body_np.node().setActive(True)
        tank.movement_lock_s = max(tank.movement_lock_s, PROJECTILE_TANK_CONTROL_LOCK)
        tank.hp = max(0, tank.hp - projectile.damage)
        projectile.damaged_targets.add(tank.player_id)

    def _handle_projectile_projectile_contact(
        self,
        projectile_a: ProjectileState,
        projectile_b: ProjectileState,
    ):
        """Bounce two projectiles off each other."""

        normal = projectile_b.body_np.getPos() - projectile_a.body_np.getPos()
        normal.y = 0.0
        if normal.length_squared() <= 1e-9:
            normal = Vec3(1.0, 0.0, 0.0)
        apply_elastic_contact_impulse(
            projectile_a.body_np.node(),
            projectile_b.body_np.node(),
            normal,
            PROJECTILE_PROJECTILE_RESTITUTION,
        )

    def _projectile_ground_impact_speed(self, projectile: ProjectileState) -> float:
        """Measure impact speed along the terrain normal."""

        surface_normal = surface_normal_at_x(self.terrain, projectile.body_np.getX())
        velocity = projectile.previous_velocity
        return max(0.0, -velocity.dot(surface_normal))

    def _reactivate_nearby_bodies(self, crater_x: float, radius: float):
        """Wake nearby objects after the terrain changes."""

        for tank in self.tanks:
            if abs(tank.body_np.getX() - crater_x) <= radius:
                tank.body_np.node().setActive(True)
        for projectile in self.projectiles:
            if abs(projectile.body_np.getX() - crater_x) <= radius:
                projectile.body_np.node().setActive(True)

    def _check_win_state(self):
        """End the round if a tank dies or falls off the map."""

        losers = [
            tank
            for tank in self.tanks
            if tank.hp <= 0 or tank.body_np.getZ() < self.terrain.kill_z
        ]
        if not losers:
            return
        self.match_over = True
        if len(losers) == 2:
            self.winner_text = "Draw! Press R to restart"
        else:
            winner_id = 1 if losers[0].player_id == 2 else 2
            self.winner_text = f"{PLAYER_NAMES[winner_id]} wins! Press R to restart"
        for tank in self.tanks:
            tank.body_np.node().setLinearVelocity(Vec3(0.0, 0.0, 0.0))
            tank.body_np.node().setAngularVelocity(Vec3(0.0, 0.0, 0.0))
        for projectile in list(self.projectiles):
            remove_projectile(self.world, projectile)
        self.projectiles.clear()

    def _refresh_hud(self):
        """Push the latest state into the HUD."""

        if len(self.tanks) == 2:
            self.hud.update(self.tanks[0], self.tanks[1], self.winner_text)

    def _clear_world(self):
        """Remove everything that belongs to the previous match."""

        if not self.world:
            return

        self._dispose_projectiles(list(self.projectiles))
        self.projectiles.clear()

        for tank in self.tanks:
            self.world.removeRigidBody(tank.body_np.node())
            tank.body_np.removeNode()
        self.tanks.clear()

        if self.terrain:
            self.world.removeRigidBody(self.terrain.terrain_body_np.node())
            self.terrain.terrain_body_np.removeNode()
            if self.terrain.profile_visual_np:
                self.terrain.profile_visual_np.removeNode()
            self.terrain.terrain_root.removeNode()
            self.terrain = None

        if self.debug_np:
            self.debug_np.removeNode()
            self.debug_np = None

        self.world = None

    def _pressed_once(self, input_name: str) -> bool:
        """Return True only on the frame a key is first pressed."""

        now = self._is_key_down(input_name)
        was = self.button_states.get(input_name, False)
        self.button_states[input_name] = now
        return now and not was

    def _bind_key(self, key: str):
        """Register down/up handlers for one key."""

        if key in self.key_state:
            return
        self.key_state[key] = False
        self.accept(key, self._set_key_state, [key, True])
        self.accept(f"{key}-up", self._set_key_state, [key, False])

    def _set_key_state(self, key: str, is_down: bool):
        """Store the current pressed state for one key."""

        self.key_state[key] = is_down

    def _is_key_down(self, key: str) -> bool:
        """Return whether a key is currently held."""

        return self.key_state.get(key, False)

    def _apply_horizontal_movement(self, tank: TankState, move_dir: int, dt: float):
        """Move a tank either along the ground or through the air."""

        body = tank.body_np.node()
        velocity = body.getLinearVelocity()
        current_x = velocity.x

        if (
            tank.grounded
            and move_dir != 0
            and abs(tank.body_np.getX()) <= self.terrain.world_width / 2.0 + 1.0
        ):
            current_x = tank.body_np.getX()
            desired_x = current_x + move_dir * TANK_MOVE_SPEED * dt
            desired_surface = self._support_surface_height(desired_x)
            if desired_surface is not None:
                # Move along the sampled hill directly so the tank feels smooth.
                target_z = desired_surface + TANK_HALF_HEIGHT + TANK_RIDE_HEIGHT
                smooth_factor = min(1.0, TANK_SURFACE_SMOOTHING * dt)
                current_z = tank.body_np.getZ()
                smoothed_z = current_z + (target_z - current_z) * smooth_factor
                tank.body_np.setPos(desired_x, 0.0, smoothed_z)
                tank.body_np.setR(0.0)
                self._align_tank_to_surface(tank, desired_x)
                velocity.x = move_dir * TANK_MOVE_SPEED
                velocity.y = 0.0
                velocity.z = min(velocity.z, 0.0)
                body.setLinearVelocity(velocity)
                body.setAngularVelocity(Vec3(0.0, 0.0, 0.0))
                body.setActive(True)
                return
            tank.grounded = False
            tank.airborne = True
            tank.support_frames = 0

        if move_dir != 0:
            target_x = move_dir * TANK_MOVE_SPEED
            accel = TANK_GROUND_ACCEL if tank.grounded else TANK_AIR_ACCEL
        else:
            target_x = 0.0
            accel = TANK_IDLE_DECEL if tank.grounded else TANK_AIR_ACCEL * 0.35

        max_delta = accel * dt
        delta = target_x - current_x
        if abs(delta) <= max_delta:
            velocity.x = target_x
        else:
            velocity.x = current_x + max_delta * (1.0 if delta > 0.0 else -1.0)

        body.setLinearVelocity(velocity)
        if move_dir != 0:
            body.setActive(True)

    def _apply_knockback_motion(self, tank: TankState, dt: float):
        """Move a tank backward after it has been hit."""

        body = tank.body_np.node()
        desired_x = tank.body_np.getX() + tank.knockback_velocity_x * dt
        surface_height = self._support_surface_height(desired_x)

        if tank.grounded and surface_height is not None:
            target_z = surface_height + TANK_HALF_HEIGHT + TANK_RIDE_HEIGHT
            current_z = tank.body_np.getZ()
            smooth_factor = min(1.0, TANK_SURFACE_SMOOTHING * dt)
            smoothed_z = current_z + (target_z - current_z) * smooth_factor
            tank.body_np.setPos(desired_x, 0.0, smoothed_z)
            tank.body_np.setR(0.0)
            self._align_tank_to_surface(tank, desired_x)
            velocity = body.getLinearVelocity()
            velocity.x = tank.knockback_velocity_x
            velocity.y = 0.0
            velocity.z = min(velocity.z, 0.0)
            body.setLinearVelocity(velocity)
            body.setAngularVelocity(Vec3(0.0, 0.0, 0.0))
        else:
            if surface_height is None:
                tank.grounded = False
                tank.airborne = True
                tank.support_frames = 0
            velocity = body.getLinearVelocity()
            velocity.x = tank.knockback_velocity_x
            body.setLinearVelocity(velocity)

        body.setActive(True)
        tank.knockback_velocity_x *= max(0.0, 1.0 - TANK_KNOCKBACK_DECAY * dt)

    def _resolve_tank_tank_collision(self):
        """Resolve the custom lower-hull collision between the tanks."""

        if len(self.tanks) != 2:
            return

        tank_a, tank_b = self.tanks
        pos_a = tank_a.body_np.getPos()
        pos_b = tank_b.body_np.getPos()
        dx = pos_b.x - pos_a.x
        vel_a = tank_a.body_np.node().getLinearVelocity()
        vel_b = tank_b.body_np.node().getLinearVelocity()
        relative_vx = vel_b.x - vel_a.x
        closing = dx * relative_vx < 0.0
        if not self._lower_hulls_intersect(tank_a, tank_b, closing):
            return

        direction = 1.0 if dx >= 0.0 else -1.0
        half_width_a, _ = self._lower_hull_extents(tank_a)
        half_width_b, _ = self._lower_hull_extents(tank_b)
        min_sep = half_width_a + half_width_b + TANK_COLLISION_BUFFER
        overlap = min_sep - abs(dx)
        separation = max(0.0, overlap) * 0.5 + 0.02

        new_ax = pos_a.x - direction * separation
        new_bx = pos_b.x + direction * separation
        tank_a.body_np.setX(new_ax)
        tank_b.body_np.setX(new_bx)
        surface_a = self._support_surface_height(new_ax)
        surface_b = self._support_surface_height(new_bx)
        if surface_a is not None:
            tank_a.body_np.setZ(surface_a + TANK_HALF_HEIGHT + TANK_RIDE_HEIGHT)
        if surface_b is not None:
            tank_b.body_np.setZ(surface_b + TANK_HALF_HEIGHT + TANK_RIDE_HEIGHT)
        tank_a.body_np.setR(0.0)
        tank_b.body_np.setR(0.0)
        if surface_a is not None:
            self._align_tank_to_surface(tank_a, new_ax)
        if surface_b is not None:
            self._align_tank_to_surface(tank_b, new_bx)

        u1 = vel_a.x
        u2 = vel_b.x
        e = TANK_CONTACT_RESTITUTION
        v1 = ((1.0 - e) * u1 + (1.0 + e) * u2) * 0.5
        v2 = ((1.0 + e) * u1 + (1.0 - e) * u2) * 0.5
        if abs(v1 - v2) < 0.15:
            v1 = -direction * 2.8
            v2 = direction * 2.8
        vel_a.x = v1
        vel_b.x = v2
        vel_a.y = 0.0
        vel_b.y = 0.0
        tank_a.body_np.node().setLinearVelocity(vel_a)
        tank_b.body_np.node().setLinearVelocity(vel_b)
        tank_a.body_np.node().setAngularVelocity(Vec3(0.0, 0.0, 0.0))
        tank_b.body_np.node().setAngularVelocity(Vec3(0.0, 0.0, 0.0))
        tank_a.body_np.node().setActive(True)
        tank_b.body_np.node().setActive(True)

    def _support_surface_height(self, world_x: float) -> float | None:
        """Sample a weighted support height under a tank."""

        if not self._has_support_at(world_x):
            return None
        probe = TANK_HALF_WIDTH * TANK_SUPPORT_PROBE
        left = sample_surface_height(self.terrain, world_x - probe)
        center = sample_surface_height(self.terrain, world_x)
        right = sample_surface_height(self.terrain, world_x + probe)
        return 0.2 * left + 0.6 * center + 0.2 * right

    def _align_tank_to_surface(self, tank: TankState, world_x: float):
        """Tilt the visible tank body to match the hill."""

        if not self._has_support_at(world_x):
            return
        tank.visual_np.setR(surface_angle_degrees(self.terrain, world_x))

    @staticmethod
    def _lower_hull_extents(tank: TankState) -> tuple[float, float]:
        """Return the bounds of the sloped lower hull."""

        angle = radians(abs(tank.visual_np.getR()))
        collision_half_width = TANK_HALF_WIDTH + TANK_HITBOX_X_PAD
        collision_half_height = TANK_LOWER_HALF_HEIGHT + TANK_HITBOX_LOWER_Z_PAD
        half_width = collision_half_width * cos(angle) + collision_half_height * sin(angle)
        half_height = collision_half_height * cos(angle) + collision_half_width * sin(angle)
        return half_width, half_height

    def _lower_hulls_intersect(self, tank_a: TankState, tank_b: TankState, closing: bool) -> bool:
        """Check overlap between the two lower hulls."""

        early_margin = 0.0
        if closing:
            # Start the bounce a touch early so the tanks do not sink into each other.
            speed = abs(tank_b.body_np.node().getLinearVelocity().x - tank_a.body_np.node().getLinearVelocity().x)
            early_margin = max(0.10, min(0.28, speed * FIXED_DT * 1.8))

        rect_a = self._lower_hull_rect(tank_a, TANK_COLLISION_BUFFER * 0.5 + early_margin)
        rect_b = self._lower_hull_rect(tank_b, TANK_COLLISION_BUFFER * 0.5 + early_margin)
        delta_x = rect_b["center"][0] - rect_a["center"][0]
        if abs(delta_x) > rect_a["half_width"] + rect_b["half_width"] + TANK_COLLISION_BUFFER + early_margin * 2.0:
            return False

        for axis in rect_a["axes"] + rect_b["axes"]:
            distance = abs(self._dot_2d(rect_b["center"], axis) - self._dot_2d(rect_a["center"], axis))
            radius_a = self._project_rect_radius(rect_a, axis)
            radius_b = self._project_rect_radius(rect_b, axis)
            if distance > radius_a + radius_b:
                return False
        return True

    def _lower_hull_rect(self, tank: TankState, extra_width: float) -> dict[str, object]:
        """Build the rotated rectangle used for lower-hull overlap tests."""

        angle = radians(tank.visual_np.getR())
        axis_width = (cos(angle), sin(angle))
        axis_height = (-sin(angle), cos(angle))
        return {
            "center": (tank.body_np.getX(), tank.body_np.getZ()),
            "axes": (axis_width, axis_height),
            "half_width": TANK_HALF_WIDTH + TANK_HITBOX_X_PAD + extra_width,
            "half_height": TANK_LOWER_HALF_HEIGHT + TANK_HITBOX_LOWER_Z_PAD + 0.03,
        }

    @staticmethod
    def _project_rect_radius(rect: dict[str, object], axis: tuple[float, float]) -> float:
        """Project a rotated rectangle onto an axis."""

        axis_width, axis_height = rect["axes"]
        return (
            rect["half_width"] * abs(TanksHotseatGame._dot_2d(axis_width, axis))
            + rect["half_height"] * abs(TanksHotseatGame._dot_2d(axis_height, axis))
        )

    @staticmethod
    def _dot_2d(a: tuple[float, float], b: tuple[float, float]) -> float:
        """Small 2D dot-product helper."""

        return a[0] * b[0] + a[1] * b[1]

    def _has_support_at(self, world_x: float) -> bool:
        """Return whether this x position is still over the map."""

        limit = self.terrain.world_width * 0.5
        return abs(world_x) <= limit

    def _update_chain_visuals(self):
        """Redraw every active chain-shot rope."""

        if not self.projectiles:
            return

        projectile_nodes = {
            self._node_key(projectile.body_np.node()): projectile for projectile in self.projectiles
        }
        processed_groups: set[int] = set()
        for projectile in self.projectiles:
            if projectile.chain_group_id is None or projectile.chain_group_id in processed_groups:
                continue
            partner = projectile_nodes.get(projectile.partner_node_key)
            if partner is None:
                continue
            update_chain_visual(projectile, partner)
            processed_groups.add(projectile.chain_group_id)

    def _advance_chain_shots(self, dt: float):
        """Advance the custom chain-shot motion."""

        if not self.chain_shots:
            return

        projectile_nodes = {
            self._node_key(projectile.body_np.node()): projectile for projectile in self.projectiles
        }
        stale_groups: list[int] = []
        for group_id, chain_state in self.chain_shots.items():
            projectile_a = projectile_nodes.get(chain_state.projectile_keys[0])
            projectile_b = projectile_nodes.get(chain_state.projectile_keys[1])
            if projectile_a is None or projectile_b is None:
                stale_groups.append(group_id)
                continue

            # Treat the pair as a moving center plus a rotating offset so the
            # distance between the two balls always stays fixed.
            chain_state.center_velocity.z += WORLD_GRAVITY_Z * dt
            chain_state.center_pos += chain_state.center_velocity * dt
            chain_state.center_pos.y = 0.0
            chain_state.angle_rad += chain_state.angular_velocity * dt

            radial = Vec3(cos(chain_state.angle_rad), 0.0, sin(chain_state.angle_rad)) * chain_state.half_length
            tangential = Vec3(-sin(chain_state.angle_rad), 0.0, cos(chain_state.angle_rad))
            tangential *= chain_state.angular_velocity * chain_state.half_length

            pos_a = chain_state.center_pos + radial
            pos_b = chain_state.center_pos - radial
            vel_a = chain_state.center_velocity + tangential
            vel_b = chain_state.center_velocity - tangential
            pos_a.y = 0.0
            pos_b.y = 0.0
            vel_a.y = 0.0
            vel_b.y = 0.0

            projectile_a.body_np.setPos(pos_a)
            projectile_b.body_np.setPos(pos_b)
            projectile_a.body_np.node().setLinearVelocity(vel_a)
            projectile_b.body_np.node().setLinearVelocity(vel_b)
            projectile_a.body_np.node().setActive(True)
            projectile_b.body_np.node().setActive(True)

        for group_id in stale_groups:
            self.chain_shots.pop(group_id, None)

    def _register_chain_shot(self, projectiles: list[ProjectileState]):
        """Capture the starting state of a new chain shot."""

        if len(projectiles) != 2:
            return

        projectile_a, projectile_b = projectiles
        midpoint = (projectile_a.body_np.getPos() + projectile_b.body_np.getPos()) * 0.5
        velocity_a = projectile_a.body_np.node().getLinearVelocity()
        velocity_b = projectile_b.body_np.node().getLinearVelocity()
        center_velocity = (velocity_a + velocity_b) * 0.5
        radial = projectile_a.body_np.getPos() - midpoint
        half_length = max(CHAIN_LINK_HALF_LENGTH, radial.length())
        angle_rad = atan2(radial.z, radial.x)
        tangential = velocity_a - center_velocity
        if half_length <= 1e-6:
            angular_velocity = CHAIN_SPIN_SPEED / CHAIN_LINK_HALF_LENGTH
        else:
            angular_velocity = (radial.x * tangential.z - radial.z * tangential.x) / (half_length * half_length)

        self.chain_shots[projectile_a.chain_group_id] = ChainShotState(
            group_id=projectile_a.chain_group_id,
            projectile_keys=(
                self._node_key(projectile_a.body_np.node()),
                self._node_key(projectile_b.body_np.node()),
            ),
            center_pos=Vec3(midpoint),
            center_velocity=Vec3(center_velocity),
            angle_rad=angle_rad,
            angular_velocity=angular_velocity,
            half_length=half_length,
        )

    def _mark_projectile_group_for_removal(self, projectile: ProjectileState, removal_keys: set[int]):
        """Mark either one projectile or a whole chain-shot pair for removal."""

        if projectile.chain_group_id is None:
            removal_keys.add(self._node_key(projectile.body_np.node()))
            return

        for candidate in self.projectiles:
            if candidate.chain_group_id == projectile.chain_group_id:
                removal_keys.add(self._node_key(candidate.body_np.node()))

    def _dispose_projectiles(self, projectiles: list[ProjectileState]):
        """Clean up projectile bodies, rope visuals, and chain state."""

        if not self.world or not projectiles:
            return

        removed_ropes: set[int] = set()
        removed_bodies: set[int] = set()
        removed_groups: set[int] = set()
        for projectile in projectiles:
            if projectile.chain_group_id is not None and projectile.chain_group_id not in removed_groups:
                self.chain_shots.pop(projectile.chain_group_id, None)
                removed_groups.add(projectile.chain_group_id)
            if projectile.rope_np is not None:
                rope_id = id(projectile.rope_np)
                if rope_id not in removed_ropes and not projectile.rope_np.isEmpty():
                    projectile.rope_np.removeNode()
                    removed_ropes.add(rope_id)

            body_key = self._node_key(projectile.body_np.node())
            if body_key in removed_bodies:
                continue
            remove_projectile(self.world, projectile)
            removed_bodies.add(body_key)

    @staticmethod
    def _pair_key(node_a, node_b) -> tuple[int, int]:
        """Build an order-independent key for a contact pair."""

        a_id = TanksHotseatGame._node_key(node_a)
        b_id = TanksHotseatGame._node_key(node_b)
        return (a_id, b_id) if a_id <= b_id else (b_id, a_id)

    @staticmethod
    def _node_key(node) -> int:
        """Turn a Panda/Bullet node into a stable integer key."""

        return int(node.this)


def main():
    """Start the game."""

    game = TanksHotseatGame()
    game.run()
