"""Entry point: ``python -m tui``."""

from tui.app import AgentApp


def main() -> None:
    app = AgentApp()
    app.run()


if __name__ == "__main__":
    main()
