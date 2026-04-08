"""Helpers for building tanks, shells, and combat state."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, degrees
from typing import Any

from panda3d.bullet import BulletBoxShape, BulletRigidBodyNode, BulletSphereShape
from panda3d.core import BitMask32, CardMaker, Point3, TransformState, Vec3

from config import (
    CHAIN_FIRE,
    CHAIN_LINK_HALF_LENGTH,
    CHAIN_LINK_THICKNESS,
    CHAIN_SPIN_SPEED,
    HEAVY_FIRE,
    PLAYER_COLORS,
    PROJECTILE_LIFETIME,
    RAPID_FIRE,
    SPHERE_VISUAL_SCALE,
    TANK_HALF_HEIGHT,
    TANK_HALF_WIDTH,
    TANK_HITBOX_LOWER_Z_PAD,
    TANK_HITBOX_UPPER_Z_PAD,
    TANK_HITBOX_X_PAD,
    TANK_LOWER_HALF_HEIGHT,
    TANK_MAX_HP,
    TANK_MASS,
    TANK_TURRET_PITCH_MAX,
    TANK_TURRET_PITCH_MIN,
    WEAPONS,
    WeaponSpec,
)


@dataclass
class TankState:
    """Runtime state for one tank."""

    player_id: int
    hp: int
    facing_sign: int
    turret_pitch_deg: float
    body_np: Any
    visual_np: Any
    turret_np: Any
    barrel_np: Any
    grounded: bool
    airborne: bool = False
    support_frames: int = 0
    movement_lock_s: float = 0.0
    knockback_velocity_x: float = 0.0
    cooldowns: dict[str, float] = field(default_factory=dict)


@dataclass
class ProjectileState:
    """Runtime state for one projectile."""

    owner_id: int
    weapon_name: str
    damage: int
    body_np: Any
    last_pos: Vec3
    previous_velocity: Vec3
    lifetime_s: float
    crater_armed: bool
    damaged_targets: set[int] = field(default_factory=set)
    touching_ground: bool = False
    ground_time_s: float = 0.0
    chain_group_id: int | None = None
    partner_node_key: int | None = None
    chain_rest_length: float = 0.0
    rope_np: Any = None

    @property
    def weapon(self) -> WeaponSpec:
        """Look up the weapon data from the projectile name."""

        return WEAPONS[self.weapon_name]


def clamp_turret_pitch(angle_deg: float) -> float:
    """Keep the turret angle within the allowed range."""

    return max(TANK_TURRET_PITCH_MIN, min(TANK_TURRET_PITCH_MAX, angle_deg))


def tick_cooldowns(cooldowns: dict[str, float], dt: float) -> dict[str, float]:
    """Advance every weapon cooldown by one fixed step."""

    return {name: max(0.0, time_left - dt) for name, time_left in cooldowns.items()}


def cooldown_remaining(cooldowns: dict[str, float], name: str) -> float:
    """Read the current cooldown for one weapon."""

    return max(0.0, cooldowns.get(name, 0.0))


def spawn_tank(
    world,
    render,
    assets: dict[str, Any],
    player_id: int,
    spawn_x: float,
    spawn_z: float,
    slope_angle_deg: float,
) -> TankState:
    """Build one tank: collider, visuals, turret, and muzzle point."""

    facing_sign = 1 if player_id == 1 else -1

    # Two box shapes are enough for the tank body and are much easier to tune
    # than a detailed mesh collider.
    lower_shape = BulletBoxShape(Vec3(TANK_HALF_WIDTH + TANK_HITBOX_X_PAD, 0.45, 0.30 + TANK_HITBOX_LOWER_Z_PAD))
    upper_shape = BulletBoxShape(Vec3(0.875 + TANK_HITBOX_X_PAD, 0.375, 0.24 + TANK_HITBOX_UPPER_Z_PAD))

    body_np = render.attachNewNode(BulletRigidBodyNode(f"tank-{player_id}"))
    body = body_np.node()
    body.setMass(TANK_MASS)
    body.addShape(lower_shape)
    body.addShape(upper_shape, TransformState.makePos(Point3(0.0, 0.0, 0.575)))
    body.setFriction(0.38)
    body.setRestitution(0.05)
    body.setLinearDamping(0.18)
    body.setAngularDamping(0.92)
    body.setDeactivationEnabled(False)
    body.setLinearFactor(Vec3(1.0, 0.0, 1.0))
    body.setAngularFactor(Vec3(0.0, 1.0, 0.0))
    body_np.setPos(spawn_x, 0.0, spawn_z + TANK_HALF_HEIGHT + 0.09)
    body_np.setR(0.0)
    body_np.setCollideMask(BitMask32.allOn())
    body_np.setTag("kind", "tank")
    body_np.setPythonTag("player_id", player_id)
    world.attachRigidBody(body)

    # The tank model is assembled from reused cube parts in code.
    visual_root = body_np.attachNewNode(f"tank-visual-{player_id}")
    visual_root.setR(slope_angle_deg)
    colors = PLAYER_COLORS[player_id]

    _copy_model(assets["cube"], visual_root, (0.0, 0.0, 0.0), (TANK_HALF_WIDTH * 2.0, 0.46, 0.31), colors["body"])
    _copy_model(assets["cube"], visual_root, (0.0, 0.0, 0.39), (1.725, 0.425, 0.275), colors["accent"])
    _copy_model(assets["cube"], visual_root, (-0.825, 0.0, -0.10), (0.41, 0.50, 0.20), (0.12, 0.16, 0.12, 1.0))
    _copy_model(assets["cube"], visual_root, (0.825, 0.0, -0.10), (0.41, 0.50, 0.20), (0.12, 0.16, 0.12, 1.0))

    turret_base = _copy_model(
        assets["cube"],
        visual_root,
        (0.0, 0.0, 0.71),
        (1.10, 0.66, 0.38),
        (0.08, 0.10, 0.08, 1.0),
    )
    _copy_model(
        assets["cube"],
        turret_base,
        (0.0, 0.0, 0.0),
        (1.18, 0.72, 0.42),
        colors["body"],
    )

    facing_np = turret_base.attachNewNode(f"tank-{player_id}-facing")
    if facing_sign < 0:
        facing_np.setH(180.0)

    # The turret is visual-only, so aiming never disturbs the collider.
    turret_np = facing_np.attachNewNode(f"tank-{player_id}-turret")
    _make_barrel_card(
        turret_np,
        start_x=0.58,
        length=4.54,
        thickness=0.40,
        color=(0.05, 0.05, 0.05, 1.0),
        y_offset=0.03,
    )
    barrel_np = _make_barrel_card(
        turret_np,
        start_x=0.74,
        length=4.18,
        thickness=0.28,
        color=colors["accent"],
        y_offset=0.0,
    )
    muzzle_np = turret_np.attachNewNode(f"tank-{player_id}-muzzle")
    muzzle_np.setPos(4.92, 0.0, 0.0)
    barrel_np.setPythonTag("muzzle_np", muzzle_np)

    tank = TankState(
        player_id=player_id,
        hp=TANK_MAX_HP,
        facing_sign=facing_sign,
        turret_pitch_deg=28.0,
        body_np=body_np,
        visual_np=visual_root,
        turret_np=turret_np,
        barrel_np=barrel_np,
        grounded=False,
        airborne=False,
        support_frames=0,
        movement_lock_s=0.0,
        knockback_velocity_x=0.0,
        cooldowns={RAPID_FIRE.name: 0.0, HEAVY_FIRE.name: 0.0, CHAIN_FIRE.name: 0.0},
    )
    update_turret_visual(tank)
    return tank


def update_turret_visual(tank: TankState) -> None:
    """Apply the tank's stored aim angle to the visible turret."""

    tank.turret_pitch_deg = clamp_turret_pitch(tank.turret_pitch_deg)
    # Positive gameplay angles should aim upward on screen.
    tank.turret_np.setR(-tank.turret_pitch_deg)


def spawn_projectile(
    world,
    render,
    assets: dict[str, Any],
    tank: TankState,
    weapon: WeaponSpec,
) -> ProjectileState:
    """Spawn a regular one-ball shot."""

    muzzle_np = tank.barrel_np.getPythonTag("muzzle_np")
    start = muzzle_np.getPos(render)
    pivot = tank.turret_np.getPos(render)
    direction = start - pivot
    if direction.length_squared() <= 1e-9:
        direction = Vec3(tank.facing_sign, 0.0, 0.0)
    direction.normalize()

    body_np = render.attachNewNode(BulletRigidBodyNode(f"{weapon.name}-shell-p{tank.player_id}"))
    body = body_np.node()
    body.setMass(weapon.mass)
    body.addShape(BulletSphereShape(weapon.radius))
    body.setLinearFactor(Vec3(1.0, 0.0, 1.0))
    body.setAngularFactor(Vec3(0.0, 1.0, 0.0))
    body.setFriction(0.95)
    body.setRestitution(0.72)
    body.setLinearDamping(0.05)
    body.setAngularDamping(0.12)
    body.setDeactivationEnabled(False)
    body.setCcdMotionThreshold(1e-7)
    body.setCcdSweptSphereRadius(max(0.15, weapon.radius * 0.75))
    body_np.setCollideMask(BitMask32.allOn())
    body_np.setPos(start)
    body_np.setTag("kind", "projectile")
    body_np.setPythonTag("owner_id", tank.player_id)
    body_np.setPythonTag("weapon_name", weapon.name)
    world.attachRigidBody(body)

    # Give the shot a little of the tank's current motion.
    velocity = direction * weapon.muzzle_speed + tank.body_np.node().getLinearVelocity() * 0.2
    body.setLinearVelocity(velocity)

    _copy_model(
        assets["sphere"],
        body_np,
        (0.0, 0.0, 0.0),
        (weapon.radius * SPHERE_VISUAL_SCALE,) * 3,
        PLAYER_COLORS[tank.player_id]["projectile"],
    )

    return ProjectileState(
        owner_id=tank.player_id,
        weapon_name=weapon.name,
        damage=weapon.damage,
        body_np=body_np,
        last_pos=Vec3(start),
        previous_velocity=Vec3(velocity),
        lifetime_s=PROJECTILE_LIFETIME,
        crater_armed=True,
        ground_time_s=0.0,
    )


def spawn_chain_projectiles(
    world,
    render,
    assets: dict[str, Any],
    tank: TankState,
    chain_group_id: int,
) -> list[ProjectileState]:
    """Spawn the two balls used by the chain-shot weapon."""

    start, direction, inherited_velocity = _muzzle_launch_setup(tank, render)
    # Start the pair offset from the barrel so they do not overlap.
    offset = Vec3(-direction.z, 0.0, direction.x)
    if offset.length_squared() <= 1e-9:
        offset = Vec3(0.0, 0.0, 1.0)
    offset.normalize()
    offset *= CHAIN_LINK_HALF_LENGTH

    body_a_np = _spawn_projectile_body(
        world,
        render,
        assets,
        tank.player_id,
        CHAIN_FIRE,
        start + offset,
        kinematic=True,
    )
    body_b_np = _spawn_projectile_body(
        world,
        render,
        assets,
        tank.player_id,
        CHAIN_FIRE,
        start - offset,
        kinematic=True,
    )

    # Move the pair forward and give the two ends opposite spin velocities.
    forward_velocity = direction * CHAIN_FIRE.muzzle_speed + inherited_velocity
    spin_velocity = direction * CHAIN_SPIN_SPEED
    velocity_a = forward_velocity + spin_velocity
    velocity_b = forward_velocity - spin_velocity
    body_a_np.node().setLinearVelocity(velocity_a)
    body_b_np.node().setLinearVelocity(velocity_b)

    rope_np = _make_chain_link(render)
    damage_each = max(1, CHAIN_FIRE.damage // 2)

    projectile_a = ProjectileState(
        owner_id=tank.player_id,
        weapon_name=CHAIN_FIRE.name,
        damage=damage_each,
        body_np=body_a_np,
        last_pos=Vec3(body_a_np.getPos()),
        previous_velocity=Vec3(velocity_a),
        lifetime_s=PROJECTILE_LIFETIME,
        crater_armed=True,
        ground_time_s=0.0,
        chain_group_id=chain_group_id,
        chain_rest_length=CHAIN_LINK_HALF_LENGTH * 2.0,
        rope_np=rope_np,
    )
    projectile_b = ProjectileState(
        owner_id=tank.player_id,
        weapon_name=CHAIN_FIRE.name,
        damage=damage_each,
        body_np=body_b_np,
        last_pos=Vec3(body_b_np.getPos()),
        previous_velocity=Vec3(velocity_b),
        lifetime_s=PROJECTILE_LIFETIME,
        crater_armed=True,
        ground_time_s=0.0,
        chain_group_id=chain_group_id,
        chain_rest_length=CHAIN_LINK_HALF_LENGTH * 2.0,
        rope_np=rope_np,
    )
    projectile_a.partner_node_key = int(projectile_b.body_np.node().this)
    projectile_b.partner_node_key = int(projectile_a.body_np.node().this)
    update_chain_visual(projectile_a, projectile_b)
    return [projectile_a, projectile_b]


def apply_elastic_contact_impulse(
    body_a,
    body_b,
    normal: Vec3,
    restitution: float,
) -> bool:
    """Apply an elastic impulse along a contact normal."""

    if normal.length_squared() <= 1e-9:
        return False
    normal = Vec3(normal)
    normal.normalize()

    inv_mass_a = _inverse_mass(body_a)
    inv_mass_b = _inverse_mass(body_b)
    if inv_mass_a + inv_mass_b <= 0.0:
        return False

    velocity_a = body_a.getLinearVelocity()
    velocity_b = body_b.getLinearVelocity()
    relative_velocity = velocity_b - velocity_a
    normal_speed = relative_velocity.dot(normal)
    if normal_speed >= 0.0:
        return False

    impulse_magnitude = -(1.0 + restitution) * normal_speed / (inv_mass_a + inv_mass_b)
    impulse = normal * impulse_magnitude
    if inv_mass_a > 0.0:
        body_a.applyCentralImpulse(-impulse)
        body_a.setActive(True)
    if inv_mass_b > 0.0:
        body_b.applyCentralImpulse(impulse)
        body_b.setActive(True)
    return True


def remove_projectile(world, projectile: ProjectileState) -> None:
    """Remove a projectile from the world and scene graph."""

    world.removeRigidBody(projectile.body_np.node())
    projectile.body_np.removeNode()


def update_chain_visual(projectile_a: ProjectileState, projectile_b: ProjectileState) -> None:
    """Stretch the rope so it sits between the two chain-shot balls."""

    rope_np = projectile_a.rope_np or projectile_b.rope_np
    if not rope_np or rope_np.isEmpty():
        return

    start = projectile_a.body_np.getPos()
    end = projectile_b.body_np.getPos()
    delta = end - start
    length = delta.length()
    if length <= 1e-6:
        rope_np.hide()
        return

    rope_np.show()
    midpoint = (start + end) * 0.5
    angle_deg = degrees(atan2(delta.z, delta.x))
    rope_np.setPos(midpoint)
    rope_np.setR(-angle_deg)
    rope_np.setScale(length, 1.0, 1.0)


def _inverse_mass(body) -> float:
    """Return inverse mass, with 0 for static bodies."""

    mass = body.getMass()
    return 0.0 if mass <= 0.0 else 1.0 / mass


def _copy_model(model, parent, pos, scale, color):
    """Copy a reusable model with a position, scale, and color."""

    node = model.copyTo(parent)
    node.setPos(*pos)
    if isinstance(scale, tuple):
        node.setScale(*scale)
    else:
        node.setScale(scale)
    node.setColor(*color)
    return node


def _make_barrel_card(parent, start_x: float, length: float, thickness: float, color, y_offset: float):
    """Build one flat barrel segment."""

    maker = CardMaker("tank-barrel")
    maker.setFrame(0.0, length, -thickness * 0.5, thickness * 0.5)
    node = parent.attachNewNode(maker.generate())
    node.setPos(start_x, y_offset, 0.0)
    node.setColor(*color)
    node.setTwoSided(True)
    return node


def _make_chain_link(parent):
    """Build the flat strip used to draw the chain."""

    maker = CardMaker("chain-link")
    maker.setFrame(-0.5, 0.5, -CHAIN_LINK_THICKNESS * 0.5, CHAIN_LINK_THICKNESS * 0.5)
    node = parent.attachNewNode(maker.generate())
    node.setColor(0.14, 0.14, 0.14, 1.0)
    node.setTwoSided(True)
    return node


def _muzzle_launch_setup(tank: TankState, render) -> tuple[Vec3, Vec3, Vec3]:
    """Return the muzzle position, shot direction, and inherited velocity."""

    muzzle_np = tank.barrel_np.getPythonTag("muzzle_np")
    start = muzzle_np.getPos(render)
    pivot = tank.turret_np.getPos(render)
    direction = start - pivot
    if direction.length_squared() <= 1e-9:
        direction = Vec3(tank.facing_sign, 0.0, 0.0)
    direction.normalize()
    inherited_velocity = tank.body_np.node().getLinearVelocity() * 0.2
    return start, direction, inherited_velocity


def _spawn_projectile_body(
    world,
    render,
    assets,
    player_id: int,
    weapon: WeaponSpec,
    start: Vec3,
    *,
    kinematic: bool = False,
):
    """Build a projectile body, optionally in kinematic mode."""

    body_np = render.attachNewNode(BulletRigidBodyNode(f"{weapon.name}-shell-p{player_id}"))
    body = body_np.node()
    body.setMass(weapon.mass)
    body.addShape(BulletSphereShape(weapon.radius))
    body.setLinearFactor(Vec3(1.0, 0.0, 1.0))
    body.setAngularFactor(Vec3(0.0, 1.0, 0.0))
    body.setFriction(0.95)
    body.setRestitution(0.72)
    body.setLinearDamping(0.05)
    body.setAngularDamping(0.12)
    body.setDeactivationEnabled(False)
    if kinematic:
        # Bullet still reports contacts, but the game loop drives the motion.
        body.setKinematic(True)
    body.setCcdMotionThreshold(1e-7)
    body.setCcdSweptSphereRadius(max(0.15, weapon.radius * 0.75))
    body_np.setCollideMask(BitMask32.allOn())
    body_np.setPos(start)
    body_np.setTag("kind", "projectile")
    body_np.setPythonTag("owner_id", player_id)
    body_np.setPythonTag("weapon_name", weapon.name)
    world.attachRigidBody(body)

    _copy_model(
        assets["sphere"],
        body_np,
        (0.0, 0.0, 0.0),
        (weapon.radius * SPHERE_VISUAL_SCALE,) * 3,
        PLAYER_COLORS[player_id]["projectile"],
    )
    return body_np
