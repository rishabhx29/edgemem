# Start a Qdrant server for development, without a container runtime.

# Docker's daemon is not startable without an elevated prompt on this machine,
# so the server binary runs directly in WSL2 instead. Windows reaches it on
# 127.0.0.1:6333 through the WSL2 loopback.

# First time only:
#   wsl -d Ubuntu -e bash -c "mkdir -p ~/qdrant && cd ~/qdrant && \
#     curl -fsSL -o q.tar.gz \
#       https://github.com/qdrant/qdrant/releases/latest/download/qdrant-x86_64-unknown-linux-musl.tar.gz \
#     && tar xzf q.tar.gz"

# Start it:
wsl -d Ubuntu -e bash -lc "pkill -f 'qdrant\$' 2>/dev/null; sleep 1; cd ~/qdrant && nohup ./qdrant > qdrant.log 2>&1 & sleep 8; curl -s http://127.0.0.1:6333/; echo"

# Check from Windows:
#   Invoke-WebRequest -Uri http://127.0.0.1:6333/collections -UseBasicParsing

# Stop it:
#   wsl -d Ubuntu -e bash -c "pkill -f 'qdrant\$'"

# Note: partial snapshots are NOT available against this release.
# See docs/gates.md, Gate 5, for the measured reason.
