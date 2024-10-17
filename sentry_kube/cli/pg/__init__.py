import click

from . import create_user


__all__ = ("pg",)


@click.group()
def pg():
    pass


pg.command(help=create_user.cmd_help)(create_user.create_user)
