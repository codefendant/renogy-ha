"""Build a text-only diagnostic overlay for a checksummed installed baseline."""

from __future__ import annotations

import argparse
import base64
import hashlib
from pathlib import Path

CHANGED = (
    "__init__.py",
    "ble.py",
    "config_flow.py",
    "const.py",
    "sensor.py",
    "strings.json",
    "translations/en.json",
)
ADDED = (
    "inverter_diagnostics.py",
    "riv_diagnostic_reader.py",
    "riv_diagnostic_protocol.py",
)

HEADER = r"""#!/usr/bin/env bash
# Read-only RIV4835CSH1S diagnostics overlay for the supplied installed code.
# No inverter setting writes, library replacements, or automatic HA restart.
set -euo pipefail
root=${RENOGY_CONFIG_DIR:-/config}
root=$(cd -- "$root" && pwd -P)
target="$root/custom_components/renogy"
[[ -d "$target" && ! -L "$target" ]] || {
  echo "Renogy directory missing or symlinked." >&2; exit 1;
}
for command in sha256sum base64 tar mktemp cp mv awk cat chmod dirname date rm mkdir; do
  command -v "$command" >/dev/null || {
    echo "Required command missing: $command" >&2; exit 1;
  }
done
declare -A old_hash new_hash
"""

BODY = r"""
digest() { sha256sum "$1" | awk '{print $1}'; }
verify_baseline() {
  local name actual expected
  for name in "${baseline[@]}"; do
    [[ -f "$target/$name" && ! -L "$target/$name" ]] || {
      echo "Missing or symlinked source file: $name" >&2; return 1;
    }
    actual=$(digest "$target/$name")
    expected=${old_hash[$name]}
    [[ "$actual" == "$expected" || "$actual" == "${new_hash[$name]-}" ]] || {
      echo "Source file differs from your uploaded installation: $name" >&2
      echo "Stopped before replacing any files. Send a fresh code archive." >&2
      return 1
    }
  done
  for name in "${added[@]}"; do
    if [[ -e "$target/$name" || -L "$target/$name" ]]; then
      [[ -f "$target/$name" && ! -L "$target/$name" &&
         $(digest "$target/$name") == "${new_hash[$name]}" ]] || {
        echo "Conflicting diagnostic file: $name. No files replaced." >&2; return 1;
      }
    fi
  done
}
fully_installed() {
  local name
  for name in "${overlay[@]}"; do
    [[ -f "$target/$name" &&
       $(digest "$target/$name") == "${new_hash[$name]}" ]] || return 1
  done
}
staging=""
backup=""
mutating=0
done_ok=0
restore_overlay() {
  local name actual
  mkdir -p "$staging/restore" || return 1
  local members=()
  for name in "${changed[@]}"; do members+=("custom_components/renogy/$name"); done
  tar -xzf "$backup" -C "$staging/restore" "${members[@]}" || return 1
  for name in "${changed[@]}"; do
    actual=$(digest "$staging/restore/custom_components/renogy/$name")
    [[ "$actual" == "${old_hash[$name]}" ]] || {
      echo "Rollback source failed its checksum: $name" >&2; return 1;
    }
  done
  for name in "${changed[@]}"; do
    cp -p "$staging/restore/custom_components/renogy/$name" "$target/$name" || return 1
  done
  for name in "${added[@]}"; do rm -f -- "$target/$name" || return 1; done
}
cleanup() {
  local status=$?
  trap - EXIT
  if (( mutating == 1 && done_ok == 0 )); then
    echo "Installation failed; restoring the saved code." >&2
    if ! restore_overlay; then
      echo "Automatic restoration failed. Backup: $backup" >&2
    fi
  fi
  for name in "${overlay[@]}"; do rm -f -- "$target/$name.renogy-new-$$"; done
  [[ -z "$staging" ]] || rm -rf -- "$staging"
  exit "$status"
}
trap cleanup EXIT
verify_baseline
if [[ ${1:-} == --rollback ]]; then
  [[ $# == 2 ]] || { echo "Usage: bash $0 --rollback BACKUP.tar.gz" >&2; exit 1; }
  backup=$2
  [[ "$backup" == "$root"/renogy-code-before-lcd-diagnostics-*.tar.gz &&
     -f "$backup" && ! -L "$backup" ]] || {
    echo "Use the backup path printed by this installer." >&2; exit 1;
  }
  sha256sum -c "$backup.sha256" >/dev/null
  staging=$(mktemp -d "$root/.renogy-diagnostics-restore.XXXXXX")
  restore_overlay
  echo "Original integration code restored. Run: ha core restart"
  done_ok=1
  exit 0
fi
[[ $# == 0 ]] || { echo "Usage: bash $0 [--rollback BACKUP.tar.gz]" >&2; exit 1; }
if fully_installed; then
  echo "This diagnostic update is already installed. No files changed."
  exit 0
fi
# Stop rather than make a mixed-version backup of an interrupted earlier update.
for name in "${changed[@]}"; do
  [[ $(digest "$target/$name") == "${old_hash[$name]}" ]] || {
    echo "Partial installation detected. Roll back its original backup." >&2; exit 1;
  }
done
for name in "${added[@]}"; do
  [[ ! -e "$target/$name" ]] || {
    echo "Partial diagnostic installation detected. Roll back first." >&2; exit 1;
  }
done
staging=$(mktemp -d "$root/.renogy-diagnostics-stage.XXXXXX")
mkdir -p "$staging/translations"
write_payload
for name in "${overlay[@]}"; do
  [[ $(digest "$staging/$name") == "${new_hash[$name]}" ]] || {
    echo "Installer payload failed its checksum: $name" >&2; exit 1;
  }
done
verify_baseline
backup="$root/renogy-code-before-lcd-diagnostics-$(date -u +%Y%m%dT%H%M%SZ)-$$.tar.gz"
tar -czf "$backup" -C "$root" custom_components/renogy
sha256sum "$backup" > "$backup.sha256"
mutating=1
for name in "${overlay[@]}"; do
  mkdir -p "$(dirname "$target/$name")"
  temporary="$target/$name.renogy-new-$$"
  if [[ -f "$target/$name" ]]; then
    cp -p "$target/$name" "$temporary"
    cat "$staging/$name" > "$temporary"
  else
    cp "$staging/$name" "$temporary"
    chmod 644 "$temporary"
  fi
  mv -f "$temporary" "$target/$name"
done
fully_installed
verify_baseline
done_ok=1
echo "Diagnostics installed; existing controls, cells, and library retained."
echo "Backup: $backup"
printf 'Rollback: bash %q --rollback %q\n' "$0" "$backup"
echo "Next: ha core check; then ha core restart"
echo 'After restart, enable Read LCD settings and faults (experimental) in Renogy.'
exit 0
"""


def generate(
    baseline: Path,
    source: Path,
    output: Path,
    *,
    changed: tuple[str, ...] = CHANGED,
    added: tuple[str, ...] = ADDED,
) -> None:
    """Embed only the approved overlay, with original and resulting checksums."""
    baseline_names = sorted(
        p.relative_to(baseline).as_posix()
        for p in baseline.rglob("*")
        if p.is_file()
        and (p.suffix in {".py", ".json"})
        and "__pycache__" not in p.parts
    )
    overlay = changed + added
    if not overlay or len(set(overlay)) != len(overlay):
        raise ValueError("Specify a nonempty overlay of distinct files")
    if any(name not in baseline_names for name in changed):
        raise ValueError("Changed files must exist in the installed baseline")
    if any(name in baseline_names for name in added):
        raise ValueError("Added files must not exist in the installed baseline")
    lines = [HEADER]
    for name in baseline_names:
        digest = hashlib.sha256((baseline / name).read_bytes()).hexdigest()
        lines.append(f"old_hash['{name}']='{digest}'\n")
    for name in overlay:
        digest = hashlib.sha256((source / name).read_bytes()).hexdigest()
        lines.append(f"new_hash['{name}']='{digest}'\n")
    for key, values in (
        ("baseline", baseline_names),
        ("changed", changed),
        ("added", added),
        ("overlay", overlay),
    ):
        lines.append(f"{key}=(" + " ".join(f"'{v}'" for v in values) + ")\n")
    lines.append("write_payload() {\n")
    for index, name in enumerate(overlay):
        payload = base64.b64encode((source / name).read_bytes()).decode()
        marker = f"RENOGY_DIAGNOSTIC_PAYLOAD_{index}"
        lines.append(f"base64 -d > \"$staging/{name}\" <<'{marker}'\n")
        lines.append("\n".join(payload[i : i + 76] for i in range(0, len(payload), 76)))
        lines.append(f"\n{marker}\n")
    lines.append("}\n" + BODY)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate(args.baseline, args.source, args.output)
