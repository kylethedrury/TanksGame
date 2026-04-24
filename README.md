# Tanks Hotseat

Two-player same-keyboard artillery game built with Panda3D and Bullet Physics for a 2VG3 class project.

## Overview

Tanks Hotseat is a local hotseat duel: two players share one keyboard, drive across procedurally generated hills, aim their turrets, jump, and trade shots until one tank is destroyed or falls off the map.

The game is presented as a 2D side view, but the simulation runs on Panda3D with Bullet rigid bodies, a heightfield terrain collider, bouncing projectiles, and live crater deformation.

## Requirements

- Windows
- Python 3.12

## Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run

```powershell
.\.venv\Scripts\python.exe main.py
```

## Tests

Run the tests with the project virtualenv so Panda3D is available:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

## Controls

### Player 1

- `A` / `D`: move left / right
- `W` / `S`: aim turret up / down
- `Q`: rapid fire
- `E`: heavy fire
- `F`: chain shot
- `Left Shift`: jump

### Player 2

- `J` / `L`: move left / right
- `I` / `K`: aim turret up / down
- `U`: rapid fire
- `O`: heavy fire
- `P`: chain shot
- `Right Shift`: jump

### Global

- `R`: restart with new random terrain
- `Esc`: quit
- `F3`: toggle Bullet debug view

## Match Rules

- Each tank starts with `100 HP`.
- `Rapid fire` deals `5` damage with a `0.5s` cooldown.
- `Heavy fire` deals `20` damage with a `3.0s` cooldown.
- `Chain shot` fires two linked projectiles that deal `10` damage each, for `20` total damage on a full hit. Cooldown is `1.6s`.
- Shells can bounce, roll, collide with each other, and carve craters into the terrain.
- A tank loses if its HP reaches `0` or if it falls below the kill plane.

## Features

- Orthographic side-view camera with constrained Bullet physics
- Procedural terrain with flattened spawn pads
- Destructible terrain with collision rebuilt after impacts
- Three weapons: rapid fire, heavy fire, and chain shot
- Tank jumping, knockback, and tank-vs-tank collision response
- HUD for HP, aim angle, cooldowns, controls, and winner display

## Repository Layout

- `main.py`: lightweight entrypoint that forwards into `src/`
- `assets/`: active game models (`Cube.egg` and `sphere.egg.pz`)
- `src/config.py`: constants, controls, and weapon specs
- `src/combat.py`: tank and projectile creation plus combat helpers
- `src/terrain.py`: terrain generation, rendering, sampling, and crater carving
- `src/hud.py`: on-screen HUD text
- `src/game.py`: Panda3D app, fixed-step loop, contacts, and win handling
- `tests/`: unit tests for cooldown logic and terrain shaping
- `Assignment_Writeup.tex`: LaTeX source for the class write-up
- `Assignment_Writeup.pdf`: compiled write-up PDF

## Dependency

```text
Panda3D==1.10.16
```
