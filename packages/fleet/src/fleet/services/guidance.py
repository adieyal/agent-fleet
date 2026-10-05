"""Guidance authoring workflows shared by presentation adapters."""
from fleet.projections.guidance import guidance_view


def write_guidance(services, project, markdown, epic, base):
    services.records.write_guidance(project, markdown, epic=epic, actor='web-user', base=base)
    return guidance_view(services, project, epic)


def promote_guidance(services, epic, decision, promote):
    promote(epic=epic, decision=decision, actor='web-user')
    return guidance_view(services, services.work.get(epic).project, epic)
