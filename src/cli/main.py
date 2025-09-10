"""Main CLI entry point as specified in tasks.md T018"""

import click
import asyncio
from ui.tmux_app import TMuxApp


@click.command()
@click.option('--refresh-interval', default=60.0, help='How often to poll tmux (seconds)')
@click.option('--sidebar-width', default=20, help='Sidebar width as percentage (10-50)')
def main(refresh_interval: float, sidebar_width: int):
    """Launch the tmux terminal UI application"""
    app = TMuxApp(sidebar_width=sidebar_width)
    app.run()


if __name__ == "__main__":
    main()