"""Static presentation resources supplied by the library container."""
from fleet.container import Container

def resources(container):
    return container.package_resources(package=__package__, source=__file__,
                                       member_root='packages/fleet-web/src/fleet_web')


_resources = resources(Container)
read_static = _resources.read_static
static_directory = _resources.static_directory
checkout_folders = _resources.checkout_folders
build_id = _resources.build_id
app_files = _resources.app_files
