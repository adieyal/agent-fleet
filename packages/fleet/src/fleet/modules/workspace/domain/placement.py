"""Placement guidance from the authoritative workspace snapshot."""

from .records import WorkspaceSnapshot


def floor_warning(snapshot: WorkspaceSnapshot, project: str) -> str | None:
    floor = snapshot.floors.get(project)
    if project not in snapshot.shuttered and floor is not None and floor <= snapshot.capacity:
        return None
    names = {item.id: item.name for item in snapshot.projects}
    occupied = {floor: identity for identity, floor in snapshot.floors.items()}
    state = " is shuttered (in the deck's storehouse) and" if project in snapshot.shuttered else ""
    lines = [f"project '{project}'{state} has no floor within the current capacity.",
             f"Current floors from the store (capacity {snapshot.capacity}):"]
    for number in sorted(set(range(1, snapshot.capacity + 1)) | occupied.keys()):
        identity = occupied.get(number)
        placement = "free" if identity is None else f"{identity} {names[identity]}"
        outside = " (outside capacity)" if number > snapshot.capacity else ""
        lines.append(f"floor {number}: {placement}{outside}")
    if any(number not in occupied for number in range(1, snapshot.capacity + 1)):
        lines.append(f"Restore with: fleet project restore {project}")
    else:
        lines.append("Choose a project to move to the storehouse; each command below frees its floor:")
        for number, identity in sorted(occupied.items()):
            if number <= snapshot.capacity:
                lines.append(f"Move {names[identity]} to the storehouse with:")
                lines.append(f"fleet project restore {project} --shutter {identity}")
    return "\n".join(lines)
