"""Read-only inventory, reviewable selections, and exact-target update plans for KSU."""
import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import tempfile
import time
import uuid

SCHEMA = 1
CATEGORIES = ['harnesses', 'packages', 'skills', 'programs']
HARNESS_NAMES = {
    'codex': 'Codex', '@openai/codex': 'Codex', 'claude': 'Claude Code',
    'claude-code': 'Claude Code', '@anthropic-ai/claude-code': 'Claude Code',
    'cursor': 'Cursor', 'cursor-agent': 'Cursor Agent', 'gemini-cli': 'Gemini CLI',
    '@google/gemini-cli': 'Gemini CLI', 'gemini': 'Gemini CLI', 'grok': 'Grok',
    'grok-cli': 'Grok', '@vibe-kit/grok-cli': 'Grok', 'pi': 'Pi',
    '@mariozechner/pi-coding-agent': 'Pi', 'hermes': 'Hermes',
    'hermes-agent': 'Hermes', 'opencode': 'OpenCode', 'opencode-ai': 'OpenCode',
    'aider-chat': 'Aider', 'aider': 'Aider', 'openclaw': 'OpenClaw',
    'goose': 'Goose', 'amp': 'Amp', '@sourcegraph/amp': 'Amp',
}
SKILL_ROOTS = ['.agents/skills', '.agents/exported-skills', '.codex/skills',
               '.claude/skills', '.cursor/skills', '.hermes/skills', '.gemini/skills',
               '.config/opencode/skills', '.pi/agent/skills']
HARNESS_REPOS = ['.hermes/hermes-agent', '.hermes/hermes-agent-maintained', '.openclaw']
SAFE_NAME = re.compile(r'^[A-Za-z0-9@][A-Za-z0-9@._+/:=-]*$')


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n'); tmp.chmod(0o600)
    os.replace(tmp, path)


def load(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def public_origin(url):
    # Never retain embedded credentials, query tokens, or fragments from remotes.
    from urllib.parse import urlsplit, urlunsplit
    if '://' in url:
        u = urlsplit(url)
        return urlunsplit((u.scheme, u.hostname or '', u.path, '', ''))
    return re.sub(r'^[^/@]+@', '', url).split('?')[0].split('#')[0]


def offline_file(path):
    # macOS File Provider placeholders can block a normal read indefinitely.
    return bool(getattr(path.stat(), 'st_flags', 0) & 0x40000000)


class Probe:
    def __init__(self, home=None, system=None, path=None):
        self.home = Path(home or Path.home())
        self.system = system or platform.system()
        initial = path if path is not None else os.environ.get('PATH', '')
        extras = [self.home / p for p in ['.local/bin', '.cargo/bin', '.bun/bin', '.npm-global/bin', '.local/share/mise/shims', '.asdf/shims']]
        extras += list((self.home / '.nvm/versions/node').glob('*/bin'))
        extras += list((self.home / '.local/share/fnm/node-versions').glob('*/installation/bin'))
        if self.system == 'Darwin': extras += [Path('/opt/homebrew/bin'), Path('/usr/local/bin')]
        self.path = os.pathsep.join(dict.fromkeys([*initial.split(os.pathsep), *[str(p) for p in extras if p.is_dir()]]))
        self.errors = []
        self.git_cache = {}
        self.deadline = time.monotonic() + 120

    def command(self, argv):
        if time.monotonic() > self.deadline:
            self.errors.append({'probe': Path(argv[0]).name, 'error': 'scan time budget exhausted'})
            return None
        env = os.environ.copy()
        env.update(PATH=self.path, HOMEBREW_NO_AUTO_UPDATE='1', HOMEBREW_NO_ANALYTICS='1',
                   GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', PIP_DISABLE_PIP_VERSION_CHECK='1',
                   UV_NO_PROGRESS='1', NO_COLOR='1', LC_ALL='C')
        if Path(argv[0]).is_absolute(): env['PATH'] = str(Path(argv[0]).parent) + os.pathsep + self.path
        try:
            p = subprocess.run(argv, capture_output=True, text=True, errors='replace',
                               stdin=subprocess.DEVNULL, env=env, timeout=20)
            if p.returncode:
                # Error text may contain registry credentials: retain only the command class and code.
                self.errors.append({'probe': Path(argv[0]).name + ' ' + ' '.join(argv[1:3]), 'error': 'exit ' + str(p.returncode)})
                return None
            return p.stdout
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.errors.append({'probe': Path(argv[0]).name, 'error': type(exc).__name__})
            return None

    def json_command(self, argv):
        text = self.command(argv)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            self.errors.append({'probe': Path(argv[0]).name, 'error': 'invalid JSON'})
            return None

    def executables(self, name):
        found = []
        directories = self.path.split(os.pathsep)
        if self.system == 'Darwin': directories += ['/opt/homebrew/bin', '/usr/local/bin']
        suffixes = ['', '.exe', '.cmd'] if self.system == 'Windows' else ['']
        seen = set()
        for directory in directories:
            if not directory: continue
            for suffix in suffixes:
                p = Path(directory) / (name + suffix)
                if p.is_file() and os.access(p, os.X_OK):
                    real = str(p.resolve())
                    if real not in seen:
                        found.append(str(p.absolute())); seen.add(real)
        return found

    def git(self, path):
        root = Path(path).resolve()
        if root.is_file(): root = root.parent
        while root != root.parent and not (root / '.git').exists(): root = root.parent
        if not (root / '.git').exists(): return None
        if str(root) in self.git_cache: return self.git_cache[str(root)]
        git = shutil.which('git', path=self.path)
        if not git: return None
        def ask(*args): return self.command([git, '-C', str(root), *args])
        branch = ask('symbolic-ref', '--quiet', '--short', 'HEAD')
        origin = ask('config', '--get', 'remote.origin.url')
        status = ask('status', '--porcelain', '--untracked-files=normal')
        upstream = ask('rev-parse', '--abbrev-ref', '@{upstream}') if branch else None
        head = ask('rev-parse', '--short', 'HEAD')
        data = dict(root=str(root), branch=(branch or '').strip(), origin=public_origin((origin or '').strip()),
                    dirty=status != '', upstream=(upstream or '').strip(), head=(head or '').strip())
        self.git_cache[str(root)] = data
        return data


def item(kind, name, location, version='unknown', category='packages', source='', blocked='', **extra):
    row = dict(kind=kind, name=name, location=str(location), version=str(version), category=category,
               source=source, blocked=blocked, **extra)
    row['id'] = kind + ':' + digest([kind, str(location), name])[:16]
    return row


def winget_pin_block(text, name):
    if text is None or '…' in text or '...' in text:
        return 'WinGet pin state unavailable or truncated'
    if re.search(r'(?im)^no .*pin|^no pins', text.strip()): return ''
    if not re.search(r'(?m)^-{3,}', text): return 'Unrecognized WinGet pin output; review pins before enabling'
    return 'Pinned by WinGet' if name in text.split() else ''


def packages(probe):
    rows = []
    for brew in probe.executables('brew'):
        data = probe.json_command([brew, 'info', '--json=v2', '--installed'])
        if not isinstance(data, dict): continue
        for key, kind in [('formulae', 'brew_formula'), ('casks', 'brew_cask')]:
            for package in data.get(key, []):
                name = package.get('full_name') or package.get('token') or package.get('name')
                if not isinstance(name, str): continue
                installed = package.get('installed')
                version = ', '.join(str(x.get('version', '?')) for x in installed) if isinstance(installed, list) else installed or 'unknown'
                rows.append(item(kind, name, brew, version, 'programs' if kind == 'brew_cask' else 'packages',
                                 source=package.get('tap', ''), blocked='Pinned by Homebrew' if package.get('pinned') else '', manager=brew))
    for npm in probe.executables('npm'):
        data = probe.json_command([npm, 'ls', '--global', '--depth=0', '--json'])
        if not isinstance(data, dict): continue
        root = data.get('path', str(Path(npm).parent.parent))
        registry = public_origin((probe.command([npm, 'config', 'get', 'registry']) or '').strip())
        for name, package in data.get('dependencies', {}).items():
            rows.append(item('npm', name, root, package.get('version', 'unknown'), source=registry,
                             blocked='Registry source unavailable' if not registry else 'Linked/local package; preserve its source checkout' if package.get('link') or package.get('resolved', '').startswith('file:') else '', manager=npm))
    for pipx in probe.executables('pipx'):
        data = probe.json_command([pipx, 'list', '--json'])
        if not isinstance(data, dict): continue
        for name, value in data.get('venvs', {}).items():
            package = value.get('metadata', {}).get('main_package', {})
            spec = package.get('package_or_url', name)
            blocked = 'Non-registry or constrained install; review its existing source/constraint' if spec != package.get('package', name) else ''
            rows.append(item('pipx', name, pipx, package.get('package_version', 'unknown'),
                             source=public_origin(spec), blocked=blocked, manager=pipx))
    for uv in probe.executables('uv'):
        text = probe.command([uv, 'tool', 'list'])
        directory = probe.command([uv, 'tool', 'dir'])
        if text is None: continue
        for line in text.splitlines():
            match = re.match(r'^([A-Za-z0-9][\w.-]*) v([^\s]+)', line)
            if match:
                name, version = match.groups()
                blocked, constraints, source = '', None, 'uv tool environment'
                location = (directory or '').strip() or uv
                try:
                    import tomllib
                    receipt = Path(location) / name / 'uv-receipt.toml'
                    if offline_file(receipt): raise OSError('placeholder')
                    requirements = tomllib.loads(receipt.read_text())['tool']['requirements']
                    constraints = digest(requirements)
                    source = 'uv requirements: ' + ', '.join(r.get('name', '?') + r.get('specifier', '') for r in requirements)
                    if any(set(r) - {'name', 'specifier', 'extras', 'marker', 'index'} for r in requirements):
                        blocked = 'Local, VCS, or direct-source tool; preserve its source/deployment procedure'
                except (ImportError, OSError, ValueError, KeyError, TypeError):
                    blocked = 'Cannot verify uv requirements; Python 3.11+ and a readable receipt are needed'
                rows.append(item('uv', name, location, version, source=source, blocked=blocked, constraints=constraints, manager=uv))
    for cargo in probe.executables('cargo'):
        text = probe.command([cargo, 'install', '--list'])
        cargo_home = Path(os.environ.get('CARGO_HOME', str(probe.home / '.cargo')))
        try: metadata = load(cargo_home / '.crates2.json', {}).get('installs', {})
        except (OSError, ValueError): metadata = {}
        for line in (text or '').splitlines():
            match = re.match(r'^([A-Za-z0-9][\w.-]*) v([^\s:]+)(.*):$', line)
            if match:
                name, version, source = match.groups()
                installed = next((v for key, v in metadata.items() if key.split()[0] == name), None)
                options, blocked = [], ''
                if source: blocked = 'Non-registry source; preserve its existing install procedure'
                elif not installed: blocked = 'Cargo build options unavailable; review features/target before enabling'
                else:
                    required = ['version_req', 'features', 'all_features', 'no_default_features', 'profile', 'target']
                    if not all(key in installed for key in required): blocked = 'Incomplete Cargo install metadata'
                    else:
                        options = ['--version=' + str(installed['version_req']), '--profile=' + str(installed['profile']), '--target=' + str(installed['target'])]
                        if installed['features']: options += ['--features=' + ','.join(installed['features'])]
                        if installed['all_features']: options += ['--all-features']
                        if installed['no_default_features']: options += ['--no-default-features']
                        if any(any(ord(c) < 32 for c in value) for value in options): blocked = 'Invalid Cargo install metadata'
                rows.append(item('cargo', name, cargo_home, version, source='crates.io' if not source else public_origin(source.strip()),
                                 blocked=blocked, manager=cargo, options=options))
    for rustup in probe.executables('rustup'):
        text = probe.command([rustup, 'toolchain', 'list'])
        for line in (text or '').splitlines():
            if not line.strip(): continue
            name = line.split()[0]
            version = probe.command([rustup, 'run', name, 'rustc', '--version'])
            rows.append(item('rustup', name, rustup, (version or 'unknown').strip(), source='Rust toolchain channel',
                             blocked='' if name.startswith(('stable-', 'beta-', 'nightly-')) else 'Version-pinned or custom toolchain', manager=rustup))
    if probe.system == 'Linux':
        dpkg = probe.executables('dpkg-query')
        apt = probe.executables('apt-get')
        if dpkg and apt:
            marks = probe.executables('apt-mark')
            held = probe.command([marks[0], 'showhold']) if marks else None
            text = probe.command([dpkg[0], '-W', '-f=${binary:Package}\t${Version}\t${db:Status-Status}\n'])
            for line in (text or '').splitlines():
                parts = line.split('\t')
                if len(parts) != 3 or parts[2] != 'installed': continue
                name, version, _ = parts
                blocked = 'Hold status unavailable' if held is None else ('Held by APT' if name in held.splitlines() else '')
                rows.append(item('apt', name, apt[0], version, source='configured APT sources', blocked=blocked, manager=apt[0]))
        rpm = probe.executables('rpm'); dnf = probe.executables('dnf')
        if rpm and dnf:
            text = probe.command([rpm[0], '-qa', '--qf', '%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n'])
            for line in (text or '').splitlines():
                fields = line.split('\t')
                if len(fields) == 3:
                    name, version, arch = fields
                    rows.append(item('dnf', name + '.' + arch, dnf[0], version, source='configured DNF repositories (excludes retained)', manager=dnf[0]))
    if probe.system == 'Windows':
        for winget in probe.executables('winget'):
            pins = probe.command([winget, 'pin', 'list', '--disable-interactivity'])
            with tempfile.TemporaryDirectory(prefix='ksu-winget-') as tmp:
                export = Path(tmp) / 'packages.json'
                text = probe.command([winget, 'export', '--output', str(export), '--include-versions', '--disable-interactivity'])
                if text is None or not export.exists(): continue
                data = load(export, {})
                for source in data.get('Sources', []):
                    source_name = source.get('SourceDetails', {}).get('Name', '')
                    for package in source.get('Packages', []):
                        name = package.get('PackageIdentifier')
                        if name:
                            rows.append(item('winget', name, winget, package.get('Version', 'unknown'), 'programs', source=source_name,
                                             blocked=winget_pin_block(pins, name) or ('' if source_name else 'Unresolved WinGet source'), manager=winget))
    for row in rows:
        label = HARNESS_NAMES.get(row['name'].lower()) or HARNESS_NAMES.get(row['name'].lower().rsplit('/', 1)[-1])
        if label: row.update(category='harnesses', label=label)
    # Several npm launchers can address one global prefix; it is one update target.
    return list({r['id']: r for r in rows}.values())


def harnesses(probe, managed):
    rows = []
    for name in ['codex', 'claude', 'cursor', 'cursor-agent', 'gemini', 'grok', 'pi', 'hermes', 'opencode', 'aider', 'openclaw', 'goose', 'amp']:
        for exe in probe.executables(name):
            resolved = str(Path(exe).resolve())
            label = HARNESS_NAMES[name]
            owner = next((r for r in managed if r.get('label') == label and (
                (r['kind'] == 'npm' and resolved.startswith(r['location'] + os.sep)) or
                (r['kind'].startswith('brew_') and resolved.startswith(str(Path(r['manager']).parent.parent) + os.sep)))), None)
            repo = None if owner else probe.git(Path(exe).resolve())
            blocked = 'Verify installation ownership and native update procedure'
            if repo: blocked = 'Source checkout; preserve branch, local changes, dependencies and service deployment'
            if owner: blocked = 'Updated through the package entry: ' + owner['id']
            version = owner['version'] if owner else repo['head'] if repo else 'unknown'
            if version == 'unknown':
                text = probe.command([exe, '--version'])
                if text: version = text.strip().splitlines()[0][:160]
            rows.append(item('harness_binary', label, exe, version, 'harnesses',
                             source=repo['origin'] if repo else '', blocked=blocked, git=repo, owner_id=owner['id'] if owner else None))
    for relative in HARNESS_REPOS:
        path = probe.home / relative
        if path.exists():
            repo = probe.git(path)
            rows.append(item('harness_checkout', relative.split('/')[-1], path, repo['head'] if repo else 'unknown', 'harnesses',
                             source=repo['origin'] if repo else '', blocked='Requires the maintained harness update/deploy procedure', git=repo))
    return rows


def authority(probe):
    warehouse = probe.home / '.agents' / 'exported-skills'
    if not warehouse.exists(): return None
    root = warehouse.resolve().parent.parent
    manifest = root / 'contracts' / 'skills-system.json'
    if not manifest.exists(): return dict(warehouse=str(warehouse.resolve()), role='unknown', root=str(root))
    try:
        if offline_file(manifest): raise OSError('placeholder')
        data = load(manifest, {})
        expected = data.get('authority', {}).get('hostname')
        return dict(warehouse=str(warehouse.resolve()), root=str(root), hostname=expected,
                    role='authority' if expected in (platform.node(), platform.node().split('.')[0]) else 'consumer')
    except (OSError, ValueError):
        probe.errors.append({'probe': 'skill authority', 'error': 'manifest unavailable'})
        return dict(warehouse=str(warehouse.resolve()), root=str(root), role='unknown')


def skill_rows(probe, roots, governance):
    rows, seen, groups = [], set(), {}
    candidates = [probe.home / p for p in SKILL_ROOTS] + [Path(r).expanduser() for r in roots]
    def children(root):
        try: return sorted(root.iterdir())
        except OSError:
            probe.errors.append({'probe': 'skill directory', 'error': 'unreadable directory'}); return []
    # At most two category levels; no recursive crawl of arbitrary home directories.
    for root in candidates:
        if not root.is_dir(): continue
        for a in children(root):
            for p in ([a] if (a / 'SKILL.md').exists() else children(a) if a.is_dir() and not a.name.startswith('.') else []):
                if not (p / 'SKILL.md').exists(): continue
                real = p.resolve()
                if str(real) in seen:
                    next(r for r in rows if r['location'] == str(real))['projections'].append(str(p)); continue
                seen.add(str(real))
                governed = governance and (real == Path(governance['warehouse']) or Path(governance['warehouse']) in real.parents)
                repo = None if governed else probe.git(real)
                blocked = 'No tracked upstream; configure a source before enabling updates'
                owner_id = None
                if governed:
                    blocked = 'Shared skill authority owns updates; never update a consumer projection directly'
                elif repo:
                    group = groups.get(repo['root'])
                    if group is None:
                        reason = ''
                        if repo['dirty']: reason = 'Repository has local changes or untracked files'
                        elif repo['branch'] not in ('main', 'master') or repo['upstream'] != 'origin/' + repo['branch']:
                            reason = 'Custom/detached branch or nonstandard upstream; requires maintained update procedure'
                        elif not repo['origin']: reason = 'No origin remote'
                        group = item('skill_repo', Path(repo['root']).name, repo['root'], repo['head'], 'skills', repo['origin'], reason,
                                     git=repo, members=[], manager=shutil.which('git', path=probe.path) or 'git')
                        groups[repo['root']] = group
                    group['members'].append(p.name); owner_id = group['id']
                    blocked = 'Select its source repository; that updates every skill in that source'
                rows.append(item('skill', p.name, real, repo['head'] if repo else 'unknown', 'skills', repo['origin'] if repo else '',
                                 blocked, owner_id=owner_id, governance=governance if governed else None, projections=[str(p)]))
    rows.extend(groups.values())
    if governance:
        rows.append(item('skill_authority', 'Shared skill authority', governance['root'], category='skills',
                         blocked='Use the authority publication workflow; no generic repository updater is safe here', governance=governance))
    # Plugin cache roots are inventory evidence, never standalone update targets.
    for relative in ['.codex/plugins', '.claude/plugins', '.hermes/plugins']:
        root = probe.home / relative
        if root.is_dir():
            for entry in children(root):
                if entry.is_dir():
                    rows.append(item('plugin', entry.name, entry, category='skills', blocked='Harness-managed plugin; use its plugin update mechanism'))
    return rows


def other_programs(probe):
    rows = []
    if probe.system == 'Darwin':
        for root in [Path('/Applications'), probe.home / 'Applications']:
            for path in sorted(root.glob('*.app')):
                version = 'unknown'
                try:
                    info = path / 'Contents/Info.plist'
                    if offline_file(info): raise OSError('placeholder')
                    data = plistlib.loads(info.read_bytes())
                    version = data.get('CFBundleShortVersionString', 'unknown')
                except (OSError, ValueError, plistlib.InvalidFileException): pass
                label = path.stem
                category = 'harnesses' if label.lower() in HARNESS_NAMES else 'programs'
                rows.append(item('application', label, path, version, category,
                                 blocked='App inventory only; select a verified package entry or configure its vendor updater'))
    return rows


def environment_rows(probe):
    rows = []
    # Record environment ownership without attempting mass dependency upgrades.
    for relative in ['.virtualenvs', '.local/share/pipx/venvs', '.local/share/uv/tools', '.pyenv/versions', '.nvm/versions/node']:
        root = probe.home / relative
        if root.is_dir():
            for p in sorted(root.iterdir()):
                if p.is_dir():
                    rows.append(item('environment', p.name, p, category='packages',
                                     blocked='Isolated runtime/environment; update through its owner, not a blanket dependency upgrade'))
    for market in (probe.home / '.codex/plugins/cache').glob('*'):
        for plugin in market.glob('*'):
            if not plugin.is_dir(): continue
            versions = [p for p in plugin.iterdir() if p.is_dir()]
            if versions:
                rows.append(item('plugin_cache', plugin.name, plugin, ', '.join(p.name for p in versions), 'skills',
                                 blocked='Cached plugin versions; active installation and updates belong to the harness'))
    return rows


def schedulers(probe):
    result = []
    if probe.system == 'Darwin':
        for root in [probe.home / 'Library/LaunchAgents', Path('/Library/LaunchAgents'), Path('/Library/LaunchDaemons')]:
            for p in root.glob('*.plist'):
                if re.search(r'ksu|updat|upgrad|hermes|codex|claude|cursor|grok|openclaw', p.name, re.I):
                    result.append({'name': p.stem, 'scope': str(root), 'state': 'definition found; runtime not checked'})
    elif probe.system == 'Linux':
        for root in [probe.home / '.config/systemd/user', Path('/etc/systemd/system')]:
            for p in root.glob('*'):
                if p.suffix in ('.service', '.timer') and re.search(r'ksu|updat|upgrad|hermes|codex|claude|cursor|grok|openclaw', p.name, re.I):
                    result.append({'name': p.name, 'scope': str(root), 'state': 'definition found; runtime not checked'})
    if probe.system == 'Darwin':
        launchctl = probe.executables('launchctl')
        text = probe.command([launchctl[0], 'list']) if launchctl else None
        states = {}
        for line in (text or '').splitlines()[1:]:
            parts = line.split()
            if len(parts) == 3: states[parts[2]] = 'running' if parts[0] != '-' else 'loaded; last exit ' + parts[1]
        for record in result: record['state'] = states.get(record['name'], 'not in this user session; system scope may differ')
    elif probe.system == 'Linux':
        systemctl = probe.executables('systemctl')
        text = probe.command([systemctl[0], '--user', 'list-units', '--all', '--plain', '--no-legend', '--no-pager']) if systemctl else None
        states = {}
        for line in (text or '').splitlines():
            fields = line.split()
            if len(fields) >= 4: states[fields[0]] = fields[2] + '/' + fields[3]
        for record in result:
            if '/user' in record['scope']: record['state'] = states.get(record['name'], 'not loaded in this user manager')
    return result


def fingerprint(row):
    return digest({key: row.get(key) for key in ['id', 'kind', 'name', 'location', 'source', 'manager', 'governance', 'constraints', 'options']} |
                  {'branch': (row.get('git') or {}).get('branch'), 'upstream': (row.get('git') or {}).get('upstream')})


def scan(state, roots=None, probe=None):
    probe = probe or Probe()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    identity = load(state / 'host.json')
    current_host = {'hostname': platform.node(), 'home': str(probe.home), 'system': probe.system}
    if identity and any(identity.get(k) != v for k, v in current_host.items()):
        raise ValueError('State belongs to a different host/account; use a fresh state directory')
    if not identity:
        identity = dict(id=str(uuid.uuid4()), **current_host); write_json(state / 'host.json', identity)
    previous = load(state / 'inventory.json', {})
    choices = load(state / 'selections.json', {'choices': {}})
    roots = roots if roots is not None else previous.get('extra_roots', [])
    rows = []
    for label, collector in [('packages', lambda: packages(probe)),
                             ('harnesses', lambda: harnesses(probe, rows)),
                             ('skills', lambda: skill_rows(probe, roots, authority(probe))),
                             ('programs', lambda: other_programs(probe)),
                             ('environments', lambda: environment_rows(probe))]:
        try: rows.extend(collector())
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
            probe.errors.append({'probe': label, 'error': 'incomplete collection: ' + type(exc).__name__})
    old = {r['id']: r for r in previous.get('items', [])}
    for row in rows:
        row['fingerprint'] = fingerprint(row)
        choice = choices.get('choices', {}).get(row['id'])
        row['recommended'] = not row['blocked'] and row['category'] in ('harnesses', 'packages', 'skills') and row['kind'] not in ('apt', 'dnf')
        row['selection'] = 'new'
        if choice:
            row['selection'] = ('selected' if choice['enabled'] else 'excluded') if choice['fingerprint'] == row['fingerprint'] and not choice.get('missing') else 'changed'
        row['discovery'] = 'new' if row['id'] not in old else ('changed' if old[row['id']]['fingerprint'] != row['fingerprint'] else 'known')
    present = {r['id'] for r in rows}
    for ident, choice in choices.get('choices', {}).items():
        if ident not in present: choice['missing'] = True
    if (state / 'selections.json').exists(): write_json(state / 'selections.json', choices)
    inventory = dict(schema=SCHEMA, host=identity, scanned=stamp(), extra_roots=list(roots),
                     items=sorted(rows, key=lambda r: (CATEGORIES.index(r['category']), r['name'].lower(), r['location'])),
                     errors=probe.errors, schedulers=schedulers(probe),
                     limitations=['Current account and reachable local paths only; no SSH fleet traversal.',
                                  'Project dependencies and arbitrary virtual environments are not crawled; extra roots enumerate skills only.',
                                  'Unavailable probes are reported; missing software is not proof of absence.'])
    inventory['revision'] = digest([identity['id'], inventory['scanned'], [(r['id'], r['fingerprint']) for r in inventory['items']]])
    write_json(state / 'inventory.json', inventory)
    render(state, inventory)
    return inventory


def choose(state, enable=(), exclude=(), document=None):
    inv = load(state / 'inventory.json')
    if not inv: raise ValueError('Run discover first')
    rows = {r['id']: r for r in inv['items']}
    if document is not None:
        if document.get('host_id') != inv['host']['id'] or document.get('revision') != inv['revision']:
            raise ValueError('Selection export is stale or belongs to another machine; reopen inventory.html')
        selected = document.get('selected')
        if not isinstance(selected, list) or any(not isinstance(x, str) for x in selected): raise ValueError('Invalid selected IDs')
        enable, exclude = selected, [ident for ident in rows if ident not in selected]
    if set(enable) & set(exclude): raise ValueError('An item cannot be enabled and excluded together')
    for ident in [*enable, *exclude]:
        if ident not in rows: raise ValueError('Unknown item ID: ' + ident)
    for ident in enable:
        if rows[ident]['blocked']: raise ValueError(rows[ident]['name'] + ': ' + rows[ident]['blocked'])
    choices = load(state / 'selections.json', {'schema': SCHEMA, 'host_id': inv['host']['id'], 'choices': {}})
    if choices['host_id'] != inv['host']['id']: raise ValueError('Selections belong to another host')
    for enabled, ids in [(True, enable), (False, exclude)]:
        for ident in ids:
            choices['choices'][ident] = {'enabled': enabled, 'fingerprint': rows[ident]['fingerprint'], 'at': stamp()}
            rows[ident]['selection'] = 'selected' if enabled else 'excluded'
    write_json(state / 'selections.json', choices)
    write_json(state / 'inventory.json', inv)
    render(state, inv)
    return choices


def action(row):
    name, manager, kind = row['name'], row.get('manager'), row['kind']
    if row['blocked']: raise ValueError(row['blocked'])
    if kind != 'skill_repo' and not SAFE_NAME.fullmatch(name): raise ValueError('Unsafe package identifier')
    if not manager: raise ValueError('No verified manager')
    refresh = []
    if kind in ('brew_formula', 'brew_cask'):
        refresh = [[manager, 'update']]
        argv = [manager, 'upgrade', '--formula' if kind == 'brew_formula' else '--cask', name]
        if kind == 'brew_cask': argv.append('--greedy')
    elif kind == 'npm': argv = [manager, 'update', '--global', name]
    elif kind == 'pipx': argv = [manager, 'upgrade', name]
    elif kind == 'uv': argv = [manager, 'tool', 'upgrade', name]
    elif kind == 'cargo': argv = [manager, 'install', '--locked', name, *row.get('options', [])]
    elif kind == 'rustup': argv = [manager, 'update', name]
    elif kind == 'apt':
        refresh = [['sudo', '-n', manager, 'update']]
        argv = ['sudo', '-n', manager, 'install', '--only-upgrade', '--no-remove', '-y', name]
    elif kind == 'dnf': argv = ['sudo', '-n', manager, 'upgrade', '-y', name]
    elif kind == 'winget': argv = [manager, 'upgrade', '--id', name, '--exact', '--source', row['source'], '--silent', '--disable-interactivity']
    elif kind == 'skill_repo':
        # No checkout/reset/stash. Branch/upstream/dirty checks are repeated by every fresh scan.
        argv = [manager, '-C', row['location'], 'pull', '--ff-only']
    else: raise ValueError('No exact-target adapter for ' + kind)
    return dict(id=row['id'], name=name, category=row['category'], before=row['version'], refresh=refresh, argv=argv,
                scope=('Whole source repository, including non-skill files' if kind == 'skill_repo' else 'Selected package and required dependencies'),
                restart=('May restart programs/services through its native updater' if kind not in ('skill_repo', 'uv', 'pipx') else 'No KSU-requested restart'))


def plan(state, inv=None, restart_policy='services'):
    inv = inv or load(state / 'inventory.json')
    choices = load(state / 'selections.json', {})
    if not inv or choices.get('host_id') != inv['host']['id']: raise ValueError('Discover and save selections first')
    rows = {r['id']: r for r in inv['items']}
    actions, blocked = [], []
    for ident, choice in choices.get('choices', {}).items():
        if not choice['enabled']: continue
        row = rows.get(ident)
        reason = 'No longer discovered; inspect probe errors' if not row else row['blocked']
        if row and (choice.get('missing') or choice['fingerprint'] != row['fingerprint']): reason = 'Source or installation changed; select it again'
        if row and restart_policy == 'defer' and row['kind'] not in ('skill_repo', 'uv', 'pipx'): reason = 'Strict no-restart policy needs a provider-specific procedure'
        if reason:
            blocked.append({'id': ident, 'name': row['name'] if row else ident, 'reason': reason}); continue
        try: actions.append(action(row))
        except ValueError as exc: blocked.append({'id': ident, 'reason': str(exc)})
    result = dict(host=inv['host'], created=stamp(), actions=actions, blocked=blocked,
                  new_items=[r['id'] for r in inv['items'] if r['selection'] in ('new', 'changed')], discovery_errors=inv['errors'])
    write_json(state / 'plan.json', result)
    return result


def render(state, inv):
    # Data is textContent/escaped HTML only; inventory fields are never executable markup.
    data = json.dumps(inv).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KSU · Choose what stays updated</title><style>
:root{color-scheme:dark;font-family:system-ui;background:#10151d;color:#e9edf4}body{max-width:1160px;margin:40px auto;padding:0 24px}h1{font-size:34px;margin-bottom:8px}p{color:#b8c3d5;line-height:1.55}.bar{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:24px 0}button,input{font:inherit;padding:11px;border-radius:8px;border:1px solid #41516b;background:#1b2739;color:inherit}button{cursor:pointer}button.primary{background:#245bcb}input[type=search]{flex:1;min-width:200px}details{background:#18212e;border:1px solid #344257;border-radius:10px;margin:14px 0;padding:16px}summary{cursor:pointer;font-weight:650;font-size:19px}.row{display:grid;grid-template-columns:26px 1fr;gap:12px;padding:16px 0;border-top:1px solid #344257}.row:first-of-type{margin-top:16px}.meta{font-size:13px;color:#afbed3;overflow-wrap:anywhere;margin:5px 0}.blocked{color:#efbd83}.tag{font-size:12px;border:1px solid #526680;padding:2px 7px;border-radius:20px;margin-left:8px}.selected{color:#8bdfb4}label{cursor:pointer}#count{font-weight:600}footer{margin:35px 0;font-size:13px;color:#b8c3d5}#feedback{color:#8bdfb4}input[type=checkbox]{width:18px;height:18px;margin:3px 0}
</style><h1>KSU</h1><p>Choose what stays updated. Harnesses first, then packages and skills. New discoveries always wait for your selection.</p><p id="host"></p><div class="bar"><input id="search" type="search" aria-label="Filter inventory" placeholder="Filter by name, source, or path"><button id="recommend">Select recommended</button><button id="clear">Clear choices</button><button id="export" class="primary">Export selections</button><span id="count"></span></div><p>Selections take effect after you import the exported file with your agent or KSU CLI. Exporting does not install or update anything. Package updates may also change required dependencies. Selecting a skill repository updates the whole repository, including non-skill files. OS packages are available but not pre-recommended.</p><p id="feedback" role="status"></p><main id="groups"></main><details><summary>Discovery gaps and existing services</summary><pre id="gaps" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></details><footer>Local inventory only. No account, network service, telemetry, or external resources. Keep inventory and exported selections private.</footer><script id="data" type="application/json">DATA</script><script>
const inv=JSON.parse(document.getElementById('data').textContent), chosen=new Set(inv.items.filter(x=>x.selection==='selected'&&!x.blocked).map(x=>x.id));
const names={harnesses:'AI harnesses',packages:'Packages and tools',skills:'Skills and plugins',programs:'Other programs'};
document.getElementById('host').textContent=inv.host.hostname+' · '+inv.host.system+' · '+inv.host.home+' · '+inv.scanned;
document.getElementById('gaps').textContent=JSON.stringify({errors:inv.errors,limitations:inv.limitations,services:inv.schedulers},null,2);
function count(){document.getElementById('count').textContent=chosen.size+' selected'}
function draw(){const q=document.getElementById('search').value.toLowerCase(), root=document.getElementById('groups');root.replaceChildren();for(const cat of Object.keys(names)){const items=inv.items.filter(x=>x.category===cat&&JSON.stringify(x).toLowerCase().includes(q));if(!items.length)continue;const group=document.createElement('details');group.open=cat==='harnesses'||!!q;const title=document.createElement('summary');title.textContent=names[cat]+' ('+items.length+')';group.append(title);for(const x of items){const row=document.createElement('div');row.className='row';const box=document.createElement('input');box.type='checkbox';box.id=x.id;box.disabled=!!x.blocked;box.checked=chosen.has(x.id);box.onchange=()=>{box.checked?chosen.add(x.id):chosen.delete(x.id);count()};const info=document.createElement('div'), label=document.createElement('label');label.htmlFor=x.id;label.textContent=(x.label?x.label+' · ':'')+x.name+'  '+x.version;info.append(label);const tag=document.createElement('span');tag.className='tag';tag.textContent=x.selection+(x.recommended?' · recommended':'');info.append(tag);for(const text of [x.kind+' · '+x.location,x.source,x.git?'Branch: '+x.git.branch+' · '+(x.git.dirty?'local changes':'clean'):'',x.members?'Source contains '+x.members.length+' skills: '+x.members.join(', '):'',x.blocked]){if(!text)continue;const line=document.createElement('div');line.className='meta'+(text===x.blocked?' blocked':'');line.textContent=text;info.append(line)}row.append(box,info);group.append(row)}root.append(group)}count()}
document.getElementById('search').oninput=draw;document.getElementById('recommend').onclick=()=>{inv.items.filter(x=>x.recommended&&!x.blocked&&x.selection!=='excluded').forEach(x=>chosen.add(x.id));draw()};document.getElementById('clear').onclick=()=>{chosen.clear();draw()};document.getElementById('export').onclick=()=>{const blob=new Blob([JSON.stringify({schema:1,host_id:inv.host.id,revision:inv.revision,selected:[...chosen]},null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='ksu-selections.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);document.getElementById('feedback').textContent='Exported. Import this file with: python3 scripts/ksu.py select --file /path/to/ksu-selections.json'};draw();
</script></html>'''.replace('DATA', data)
    (state / 'inventory.html').write_text(page)
    lines = ['# KSU inventory', '', inv['host']['hostname'] + ' — ' + inv['scanned'], '', 'Import selections before scheduling. Unsupported rows cannot be enabled.', '']
    for cat in CATEGORIES:
        lines += ['## ' + cat.title(), '']
        for r in inv['items']:
            if r['category'] != cat: continue
            clean = lambda s: str(s).replace('\n', ' ').replace('`', "'")
            lines.append('- [' + ('x' if r['selection'] == 'selected' else ' ') + '] ' + clean(r['name']) + ' (' + clean(r['version']) + ') — `' + r['id'] + '` — ' + clean(r['blocked'] or r['kind']))
        lines.append('')
    (state / 'inventory.md').write_text('\n'.join(lines))
