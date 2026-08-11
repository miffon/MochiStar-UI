#!/usr/bin/env bash
set -euo pipefail

dmg_path="${1:?DMG path is required}"
result_dir="${2:?Result directory is required}"
media_url="${3:?Media test URL is required}"
mount_path="$(mktemp -d)"
test_home="$(mktemp -d)"
mkdir -p "$result_dir"

cleanup() {
  if [ -n "${media_server_pid:-}" ] && kill -0 "$media_server_pid" 2>/dev/null; then kill "$media_server_pid" || true; fi
  hdiutil detach "$mount_path" >/dev/null 2>&1 || true
  rmdir "$mount_path" 2>/dev/null || true
}
trap cleanup EXIT

# 驗證並掛載 DMG
hdiutil attach -readonly -nobrowse -mountpoint "$mount_path" "$dmg_path"
test -d "$mount_path/MochiStar.app"
test -L "$mount_path/Applications"
test "$(readlink "$mount_path/Applications")" = "/Applications"
codesign --verify --deep --strict "$mount_path/MochiStar.app"
codesign -dvvv "$mount_path/MochiStar.app" >"$result_dir/codesign.txt" 2>&1
spctl --assess --type execute --verbose=4 "$mount_path/MochiStar.app" >"$result_dir/gatekeeper.txt" 2>&1 || true
file "$mount_path/MochiStar.app/Contents/MacOS/MochiStar" >"$result_dir/binary.txt"

# 從含空白和 Unicode 的路徑執行 packaged probes
installed_dir="$test_home/Applications 測試"
mkdir -p "$installed_dir"
cp -R "$mount_path/MochiStar.app" "$installed_dir/MochiStar 測試.app"
binary="$installed_dir/MochiStar 測試.app/Contents/MacOS/MochiStar"
test -x "$binary"

run_probe() {
  local mode="$1"
  local url="$2"
  local label="$3"
  local attempt
  for attempt in 1 2 3; do
    if env HOME="$test_home" PATH="/usr/bin:/bin" MOCHISTAR_SYSTEM_TEST="$mode" \
      MOCHISTAR_SYSTEM_TEST_URL="$url" "$binary" \
      >"$result_dir/${label}-${attempt}.json" 2>"$result_dir/${label}-${attempt}.log"; then
      cp "$result_dir/${label}-${attempt}.json" "$result_dir/${label}.json"
      return 0
    fi
    if [ "$attempt" -lt 3 ]; then sleep $((attempt * 5)); fi
  done

  echo "::error::Packaged probe failed after 3 attempts: $label"
  for attempt in 1 2 3; do
    echo "::group::$label attempt $attempt"
    cat "$result_dir/${label}-${attempt}.log"
    cat "$result_dir/${label}-${attempt}.json"
    echo "::endgroup::"
  done
  return 1
}

# packaged application update 和 yt-dlp probes
run_probe update "" update
uv run python macTest/local_media_server.py --port 38473 >"$result_dir/media-server.log" 2>&1 &
media_server_pid=$!
for _attempt in 1 2 3 4 5; do
  if curl --fail --silent --head http://127.0.0.1:38473/fixture.mp4 >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent --head http://127.0.0.1:38473/fixture.mp4 >/dev/null
run_probe media-analysis "http://127.0.0.1:38473/fixture.mp4" media-local
run_probe media-analysis "$media_url" media-external
