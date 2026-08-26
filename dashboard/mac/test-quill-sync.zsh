#!/bin/zsh
set -eu

script_dir="${0:A:h}"
test_root=$(mktemp -d "${TMPDIR:-/tmp}/quill-sync-test.XXXXXX")
trap 'rm -R "$test_root"' EXIT

recordings="$test_root/recordings"
fake_bin="$test_root/bin"
events="$test_root/events"
mkdir -p "$recordings" "$fake_bin"
: > "$events"

for id in 2026.08.01-1000 2026.08.02-1000 2026.08.03-1000; do
  mkdir -p "$recordings/$id"
  printf '{"segments":[{"text":"%s"}]}\n' "$id" > "$recordings/$id/transcript.json"
  printf '{}\n' > "$recordings/$id/meta.json"
  : > "$recordings/$id/mic.m4a"
  : > "$recordings/$id/system.m4a"
done

cat > "$fake_bin/rsync" <<'STUB'
#!/bin/zsh
source_dir="${${@[-2]}%/}"
id="${source_dir:t}"
print -r -- "rsync $id" >> "$QUILL_TEST_EVENTS"
[[ "$id" == 2026.08.02-1000 ]] && exit 23
exit 0
STUB

cat > "$fake_bin/ssh" <<'STUB'
#!/bin/zsh
command_line="$*"
[[ "$command_line" == *sha256sum* ]] && exit 1
id="${command_line##*/api/ingest/}"
id="${id%% *}"
print -r -- "ingest $id" >> "$QUILL_TEST_EVENTS"
exit 0
STUB
chmod +x "$fake_bin/rsync" "$fake_bin/ssh"

config="$test_root/sync.conf"
cat > "$config" <<EOF
REMOTE="test@example"
REMOTE_DIR="/srv/quill/sessions"
DASH_URL="http://localhost:7435"
RECORDINGS="$recordings"
EOF

export QUILL_SYNC_CONF="$config"
export QUILL_SYNC_LOG="$test_root/sync.log"
export QUILL_SYNC_LOCK="$test_root/sync.lock"
export QUILL_RSYNC_BIN="$fake_bin/rsync"
export QUILL_SSH_BIN="$fake_bin/ssh"
export QUILL_SHASUM_BIN="/usr/bin/shasum"
export QUILL_NO_NOTIFY=1
export QUILL_TEST_EVENTS="$events"

set +e
zsh "$script_dir/quill-sync"
first_status=$?
set -e
[[ "$first_status" -eq 1 ]]

cat > "$test_root/expected-first" <<'EOF'
rsync 2026.08.03-1000
ingest 2026.08.03-1000
rsync 2026.08.02-1000
rsync 2026.08.01-1000
ingest 2026.08.01-1000
EOF
diff -u "$test_root/expected-first" "$events"

[[ -f "$recordings/2026.08.03-1000/.quill-synced.sha256" ]]
[[ ! -f "$recordings/2026.08.02-1000/.quill-synced.sha256" ]]
[[ -f "$recordings/2026.08.01-1000/.quill-synced.sha256" ]]

: > "$events"
set +e
zsh "$script_dir/quill-sync"
second_status=$?
set -e
[[ "$second_status" -eq 1 ]]
[[ "$(<"$events")" == "rsync 2026.08.02-1000" ]]

echo "quill-sync regression OK"
