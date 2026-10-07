# Sandbox for the agent bench: the same Pi SDK lockfile and bridge as the
# Windows product, on Linux, with PowerShell 7 as the agent's shell.
# Build context: a directory holding this file, the two tarballs and the two
# pi-runtime*.json files (bench/run.py --build prepares it).
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git libicu76 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY node-v22.23.3-linux-x64.tar.xz powershell-7.6.6-linux-x64.tar.gz /tmp/
RUN mkdir -p /opt/node /opt/pwsh \
 && python -c "import tarfile; tarfile.open('/tmp/node-v22.23.3-linux-x64.tar.xz').extractall('/opt', filter='tar')" \
 && rmdir /opt/node && mv /opt/node-v22.23.3-linux-x64 /opt/node \
 && tar xzf /tmp/powershell-7.6.6-linux-x64.tar.gz -C /opt/pwsh \
 && chmod +x /opt/pwsh/pwsh \
 && ln -s /opt/pwsh/pwsh /usr/local/bin/pwsh \
 && ln -s /opt/node/bin/node /usr/local/bin/node \
 && rm /tmp/*.tar.*

COPY pi-runtime.json /opt/edgeagent/.runtime/pi/package.json
COPY pi-runtime-lock.json /opt/edgeagent/.runtime/pi/package-lock.json
RUN cd /opt/edgeagent/.runtime/pi \
 && node /opt/node/lib/node_modules/npm/bin/npm-cli.js ci --ignore-scripts --no-audit --no-fund

# The only runtime difference from the laptop: Pi refuses its PowerShell tool
# off Windows. Let it use pwsh here; tool name, prompt and arguments are
# unchanged. Fail the build if the upstream text moved.
RUN f=/opt/edgeagent/.runtime/pi/node_modules/@earendil-works/pi-coding-agent/dist/utils/shell.js \
 && grep -q 'if (process.platform !== "win32") {' "$f" \
 && sed -i 's/if (process.platform !== "win32") {/if (false) {/' "$f" \
 && sed -i 's/findExecutableOnPath("pwsh.exe") ?? findExecutableOnPath("powershell.exe")/findExecutableOnPath("pwsh")/' "$f" \
 && grep -q 'findExecutableOnPath("pwsh")' "$f"

# The Pi bridge (edge_agent/bridge.py) starts Node from the Windows deployment path.
RUN mkdir -p /opt/edgeagent/.runtime/node/node-v22.23.3-win-x64 \
 && ln -s /opt/node/bin/node /opt/edgeagent/.runtime/node/node-v22.23.3-win-x64/node.exe

# Pi's grep/find tools need ripgrep and fd; offline Pi cannot download them.
# They live outside PATH; agent_runner.py adds them only when the config
# says the product provides them.
COPY ripgrep-15.2.0-x86_64-unknown-linux-musl.tar.gz fd-v10.5.0-x86_64-unknown-linux-musl.tar.gz /tmp/
RUN mkdir -p /opt/searchtools/bin \
 && tar xzf /tmp/ripgrep-15.2.0-x86_64-unknown-linux-musl.tar.gz -C /opt/searchtools/bin --strip-components=1 ripgrep-15.2.0-x86_64-unknown-linux-musl/rg \
 && tar xzf /tmp/fd-v10.5.0-x86_64-unknown-linux-musl.tar.gz -C /opt/searchtools/bin --strip-components=1 fd-v10.5.0-x86_64-unknown-linux-musl/fd \
 && rm /tmp/*.tar.gz

RUN pip install --no-cache-dir pypdf==6.19.0 pytest==8.4.2
