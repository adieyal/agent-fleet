"""HTML decoration of container-provided guidance projections."""
from fleet_web.documents import render_markdown


def guidance_view(services, project, epic=None, number=None):
    view = services.container.guidance_view(project=project, epic=epic, number=number)
    return {**view, **render_markdown(view['markdown'])} if view['guidance'] is not None else view
