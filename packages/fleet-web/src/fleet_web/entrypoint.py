"""Standalone dashboard entrypoint and the CLI's metadata-discovered web command."""
import argparse

from fleet.container import Container, FleetError, validate_paths
from fleet_web.server import serve, serve_fixture


def run(arguments, container):
    if arguments.fixture:
        serve_fixture(arguments.fixture, container=container, port=arguments.port,
                      bind=arguments.bind, open_browser=arguments.open)
        return
    hosts = container.jobs().selected_hosts(arguments.host)
    serve(hosts, container=container, port=arguments.port, bind=arguments.bind,
          open_browser=arguments.open, **container.configuration().web_settings())


def main(argv=None):
    parser = argparse.ArgumentParser(description='Serve the Fleet web dashboard')
    parser.add_argument('--host', action='append')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--open', action='store_true', help='open a browser tab')
    parser.add_argument('--fixture', help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    container = Container()
    try:
        for message in validate_paths('web'):
            print(message)
        container.store()
        run(arguments, container)
    except FleetError as error:
        parser.exit(2, f'fleet-web: {error}\n')


if __name__ == '__main__':
    main()
