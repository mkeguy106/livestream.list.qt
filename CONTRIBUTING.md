# Contributing

Thanks for your interest in contributing to Livestream List Qt!

## Getting Started

```bash
# Clone and set up dev environment
git clone https://github.com/mkeguy106/livestream.list.qt.git
cd livestream.list.qt
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Development Workflow

1. Create a feature branch from `main` (e.g., `fix/description`, `feature/description`)
2. Make your changes
3. Run linting and tests before committing:
   ```bash
   ruff check src/ --fix
   ruff format src/
   mypy src/
   pytest tests/
   ```
4. Push your branch and open a pull request against `main`

## Code Style

- **Formatter/Linter**: Ruff (line length 100, Python 3.10 target)
- **Rules**: `E`, `F`, `I`, `N`, `W`, `UP`
- **Type checking**: mypy

## Branch Protection

Direct commits to `main` are not allowed. All changes must go through a pull request with at least one approving review. Linear history is enforced (squash or rebase merges only).

## License

By contributing, you agree that your contributions will be licensed under the [GPL-2.0 License](LICENSE).
