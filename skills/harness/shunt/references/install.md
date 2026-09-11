# Install shunt CLI

```bash
cd ~/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp -n .env.example .env   # set OPENROUTER_API_KEY locally; never commit
shunt doctor
shunt install --dry-run
# When adapter scripts exist under adapters/<harness>/:
shunt install
shunt uninstall --dry-run
```

Without editable install: `python -m shunt.cli --help` from this directory with `PYTHONPATH=src`.

Hermes skill link (optional):

```bash
ln -s "$(pwd)" ~/.hermes/skills/shunt
```
