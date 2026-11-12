#!/usr/bin/env python3
"""Character collider maths for CRYENGINE living entities (PE_LIVING), in both directions.

The living entity keeps its collision capsule/cylinder FLOATING above the entity pivot
(the feet). The gap underneath is what lets it walk up steps and terrain bumps:
CLivingEntity::Step (CryPhysics/livingentity.cpp) allows a step-up of

    maxUpStep = heightCollider - size.z - size.x * bUseCapsule          (~line 1414)

and finds the ground with a ray from (heightCollider - size.z) down 1.5*(heightCollider -
size.z) (~line 1016), snapping to ground found within (heightCollider - size.z) below
(~line 1681). A collider whose bottom touches the pivot has ZERO step-up height and
collides with every bump.

pe_player_dimensions: sizeCollider = (radius, radius, cylinder HALF-height), heightCollider =
height of the collider CENTRE above the pivot. With bUseCapsule the hemispherical caps (radius
size.x) are added above and below the cylinder part.

CCharacterControllerComponent (CryDefaultEntities) maps its editor fields as:
    sizeCollider = (Radius*0.5, 1, Height*0.5)  and for a capsule size.z *= 0.5
    heightCollider = the component's transform translation z
so Radius/Height in the component are TWICE the physical radius / twice (capsule: four times)
the cylinder half-height.

    character_collider.py from-dims  --radius 0.41 --half-height 0.3 --center 1.25 [--cylinder]
    character_collider.py from-component --radius-param 0.7 --height-param 2.2 --center 0.9 [--cylinder]
    character_collider.py design --height 1.9 --radius 0.4 --step 0.45 [--cylinder]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import _paths  # noqa: F401,E402  (puts every tool folder on sys.path; _paths.ROOT is the repository root)
import argparse
import json


def from_dims(radius, half_height, center, capsule=True, pivot=0.0):
    cap = radius if capsule else 0.0
    return {
        "sizeCollider": [radius, radius, half_height],
        "heightCollider": center,
        "bUseCapsule": int(capsule),
        "colliderBottom": round(center - pivot - half_height - cap, 4),
        "colliderTop": round(center - pivot + half_height + cap, 4),
        "stepUpHeight": round(center - half_height - cap, 4),
        "groundRayStart": round(center - half_height, 4),
        "groundRayLength": round(1.5 * (center - half_height), 4),
        "warning": None if center - half_height - cap > 0.05 else
        "step-up height is ~0: the collider touches the ground, the character will catch on every bump",
    }


def component_to_dims(radius_param, height_param, center, capsule=True):
    r = radius_param * 0.5
    hh = height_param * 0.5 * (0.5 if capsule else 1.0)
    return r, hh


def from_component(radius_param, height_param, center, capsule=True):
    r, hh = component_to_dims(radius_param, height_param, center, capsule)
    out = from_dims(r, hh, center, capsule)
    out["component"] = {"Radius": radius_param, "Height": height_param, "transform_z": center, "Capsule": capsule}
    return out


def design(total_height, radius, step, capsule=True):
    """Choose parameters for a character `total_height` tall (pivot to top of collider)
    with collider radius `radius` that can step up `step` metres."""
    cap = radius if capsule else 0.0
    body = total_height - step                      # vertical extent of the collider
    half_height = (body - 2 * cap) / 2.0            # cylinder half-height
    if half_height <= 0:
        raise ValueError("radius too large for that height and step")
    center = step + cap + half_height
    out = from_dims(radius, half_height, center, capsule)
    out["component"] = {
        "Radius": round(radius * 2.0, 4),
        "Height": round(half_height * (4.0 if capsule else 2.0), 4),
        "transform_z": round(center, 4),
        "Capsule": capsule,
        "note": "CCharacterControllerComponent: set Physics.Radius/Height and the component transform z to these",
    }
    return out


GAMESDK_PLAYER_STAND = dict(radius=0.41, half_height=0.3, center=1.25)   # Scripts/Entities/actor/player.lua


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("from-dims"); p.add_argument("--radius", type=float, required=True)
    p.add_argument("--half-height", type=float, required=True); p.add_argument("--center", type=float, required=True)
    p.add_argument("--cylinder", action="store_true")
    p = sub.add_parser("from-component"); p.add_argument("--radius-param", type=float, required=True)
    p.add_argument("--height-param", type=float, required=True); p.add_argument("--center", type=float, required=True)
    p.add_argument("--cylinder", action="store_true")
    p = sub.add_parser("design"); p.add_argument("--height", type=float, required=True)
    p.add_argument("--radius", type=float, required=True); p.add_argument("--step", type=float, required=True)
    p.add_argument("--cylinder", action="store_true")
    a = ap.parse_args()
    if a.cmd == "from-dims":
        r = from_dims(a.radius, a.half_height, a.center, not a.cylinder)
    elif a.cmd == "from-component":
        r = from_component(a.radius_param, a.height_param, a.center, not a.cylinder)
    else:
        r = design(a.height, a.radius, a.step, not a.cylinder)
    print(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
