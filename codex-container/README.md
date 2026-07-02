# Codex Container

Universal Docker environment for running Codex and Claude Code against any local repository.

The image contains:

- OpenAI Codex CLI: `codex`
- Anthropic Claude Code CLI: `claude`
- GitHub CLI: `gh`
- Node.js 20 and `npm`
- Common development tools: `git`, `git-lfs`, `ripgrep`, `fd`, `python3`, `pip`, `venv`, `build-essential`, `docker`, `jq`, `sqlite3`, `curl`, `wget`, `rsync`, `tree`, `zip`, `unzip`, `openssh-client`, and basic editors
- Troubleshooting tools: `file`, `htop`, `ip`, `ping`, `nc`, `lsof`, and `ps`

The image does not bake in Codex, Claude, GitHub, SSH, Git, or GnuPG credentials. Runtime configuration is mounted from the host.

## Build

From this directory:

```bash
docker build -t codex-universal:latest .
```

From any directory:

```bash
docker build -t codex-universal:latest /path/to/codex-container
```

## Run Codex

In any repository:

```bash
cd /path/to/repo
/path/to/codex-container/codex-container
```

If the launcher is on your `PATH`:

```bash
cd /path/to/repo
codex-container
```

## Run Claude Code

```bash
cd /path/to/repo
/path/to/codex-container/codex-container --agent claude
```

If the launcher is on your `PATH`:

```bash
cd /path/to/repo
codex-container --agent claude
```

## Open A Shell

```bash
cd /path/to/repo
/path/to/codex-container/codex-container bash
```

## Full Access Codex

For a fully unattended local session, pass Codex its approval and sandbox flags inside the container:

```bash
codex-container codex --ask-for-approval never --sandbox danger-full-access
```

This gives the agent broad access to the mounted repository and container environment. Only use it in repositories and containers where that level of access is acceptable.

## Install The Launcher

Optional, but convenient:

```bash
sudo ln -sf /path/to/codex-container/codex-container /usr/local/bin/codex-container
```

Then use:

```bash
cd /path/to/repo
codex-container
codex-container --agent claude
codex-container bash
```

## Runtime Layout

The selected repository is mounted here:

```text
/workspace/repo
```

The container runs with this working directory:

```text
/workspace/repo
```

The container home is mounted here:

```text
/home/codex
```

By default, the host path for the persistent container home is:

```text
~/.cache/codex-container/home
```

This keeps agent state across `--rm` container runs.

## Default Mounts

The launcher mounts these host paths when available:

```text
current repo                          -> /workspace/repo
~/.cache/codex-container/home          -> /home/codex
~/.codex                               -> /home/codex/.codex
~/.claude                              -> /home/codex/.claude
~/.config/gh                           -> /home/codex/.config/gh
~/.ssh                                 -> /home/codex/.ssh:ro
~/.gitconfig                           -> /home/codex/.gitconfig:ro
~/.git-credentials                     -> /home/codex/.git-credentials:ro
~/.gnupg                               -> /home/codex/.gnupg:ro
~/.claude.json                         -> /home/codex/.claude.json
~/.cache/codex-container/npm           -> /home/codex/.npm
~/.cache/codex-container/pip           -> /home/codex/.cache/pip
~/.cache/codex-container/cargo         -> /home/codex/.cargo
~/.cache/codex-container/go            -> /home/codex/go
~/.cache/codex-container/maven         -> /home/codex/.m2
~/.cache/codex-container/gradle        -> /home/codex/.gradle
~/.cache/codex-container/cache         -> /cache
```

SSH, Git config, Git credentials, GnuPG, and Claude JSON mounts are conditional. Missing files or directories are skipped. Codex, Claude, and GitHub CLI config directories are created on the host if they do not exist.

## Permissions

The launcher passes the host user's UID and GID into the container:

```text
CODEX_UID=$(id -u)
CODEX_GID=$(id -g)
```

The container entrypoint creates a `codex` user with those IDs and runs the command as that user.

This prevents files created inside the mounted repository from becoming owned by root on the host.

## Run As Root

The default entrypoint intentionally drops from root to the `codex` user. To start a one-off root shell, bypass the entrypoint:

```bash
docker run --rm -it \
  --user root \
  --entrypoint bash \
  -v /path/to/repo:/workspace/repo \
  -w /workspace/repo \
  codex-universal:latest
```

To enter an already running container as root:

```bash
docker exec -it --user root <container-name-or-id> bash
```

## Options

Show help:

```bash
codex-container --help
```

Use Claude Code:

```bash
codex-container --agent claude
```

Run against another repository:

```bash
codex-container --repo /path/to/repo
```

Use another image:

```bash
codex-container --image my-codex:dev
```

Set the container name:

```bash
codex-container --name my-codex-session
```

Use another persistent home:

```bash
codex-container --home ~/.cache/my-codex-home
```

Skip SSH mounting:

```bash
codex-container --no-ssh
```

Skip Git config mounting:

```bash
codex-container --no-gitconfig
```

Mount the host Docker socket:

```bash
codex-container --docker
```

The Docker socket is not mounted by default. Mounting it gives the container broad control over the host Docker daemon.

## Environment Variables

The launcher also supports environment variables:

```text
CODEX_IMAGE             Docker image name. Default: codex-universal:latest
CODEX_REPO_DIR          Repository directory. Default: current directory
CODEX_AGENT             Agent to start: codex or claude. Default: codex
CODEX_CONTAINER_NAME    Container name. Default: codex-<repo-name>
CODEX_CONTAINER_HOME    Persistent /home/codex path. Default: ~/.cache/codex-container/home
CODEX_CACHE_ROOT        Cache root. Default: ~/.cache/codex-container
CODEX_MOUNT_DOCKER      Set to 1 to mount /var/run/docker.sock
```

Examples:

```bash
CODEX_AGENT=claude codex-container
CODEX_IMAGE=codex-universal:dev codex-container
CODEX_MOUNT_DOCKER=1 codex-container
```

## Authentication

Codex configuration is expected at:

```text
~/.codex
```

Claude Code configuration is expected at:

```text
~/.claude
~/.claude.json
```

GitHub CLI configuration is expected at:

```text
~/.config/gh
```

These files and directories are mounted from the host. They are not copied into the image.

If an agent or tool needs initial login, start it once through the container and complete the login flow:

```bash
codex-container
codex-container --agent claude
codex-container bash -lc 'gh auth status || gh auth login'
```

The resulting state persists in the mounted host directories.

## Smoke Test

After building the image:

```bash
mkdir -p /tmp/codex-container-test
cd /tmp/codex-container-test
codex-container bash -lc 'id && pwd && command -v codex && command -v claude && command -v gh && command -v rg && command -v fd && command -v node && command -v python3 && touch permission-test && ls -l permission-test'
```

Expected checks:

- `pwd` is `/workspace/repo`
- `codex` is available
- `claude` is available
- `gh` is available
- `rg` and `fd` are available
- `node` and `python3` are available
- `permission-test` is owned by the host user UID/GID, not root

## Files

```text
Dockerfile              Builds the universal Codex and Claude Code image
codex-entrypoint.sh     Creates the non-root runtime user and drops privileges
codex-container         Host launcher that mounts the current repository and user config
README.md               English usage guide
README.zh-CN.md         Chinese usage guide
```
