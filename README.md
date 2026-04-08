# Tanks Hotseat

Two-player same-keyboard artillery game built with Panda3D and Bullet Physics for a 2VG3 class project.

## Overview

Tanks Hotseat is a local hotseat game: two people share one computer and play at the same time on the same keyboard. Each player controls one tank, drives across procedurally generated hills, aims a turret, jumps, and fires rapid shots, heavy shells, or a spinning chain shot. The goal is to destroy the opposing tank or force it to fall off the edge of the map.

The game is presented as a 2D side-view match, but it uses Panda3D and Bullet underneath for terrain collision, rigid body motion, projectile bounce, rolling, and destructible terrain.

## Requirements

- Windows
- Python 3.12

## Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run

```powershell
python main.py
```

## Tests

```powershell
python -m unittest discover -s tests -p "test_*.py"
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
- `Rapid fire` deals `5` damage and has a `0.5s` cooldown.
- `Heavy fire` deals `20` damage and has a `3.0s` cooldown.
- `Chain shot` fires a linked pair of spinning projectiles; each ball deals `10` damage, so a full two-ball hit deals `20` total damage. Cooldown is `1.6s`.
- Shells can bounce, roll, and collide with other shells.
- Ground impacts can carve craters into the terrain.
- A tank loses if its HP reaches `0` or if it falls below the terrain kill plane.

## Current Features

- Orthographic side-view presentation
- Procedural finite terrain with spawn pads
- Destructible terrain with live collision rebuilds
- Same-keyboard 2-player hotseat play
- Turret aiming with on-screen angle display
- Rapid and heavy projectile types
- Chain shot with two linked projectiles and a rope visual
- Tank jumping
- Tank-vs-tank elastic collision handling
- Projectile-vs-tank damage and knockback
- Projectile bounce and rolling behavior
- HUD with HP, cooldowns, controls, and winner display

## Write-Up

- `Assignment_Writeup.pdf`: compiled write-up PDF

## Project Structure

- `main.py`: entrypoint
- `requirements.txt`: Python dependency list
- `assets/`: local models used by the game
- `src/config.py`: gameplay constants and weapon specs
- `src/terrain.py`: terrain generation and crater deformation
- `src/combat.py`: tank and projectile creation/state helpers
- `src/hud.py`: HUD rendering
- `src/game.py`: main Panda3D game loop
- `tests/`: automated checks
