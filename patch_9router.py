#!/usr/bin/env python3
r"""
9Router OpenCode Zen Dual-Gate Hotfix
=====================================
Universal hotfix tool for 9Router installations (Local, Docker, Linux, macOS, Windows).
Resolves upstream `403 FreeTierError: Free tier only available in official client` when routing
to OpenCode Zen free-tier models (oc/*) through 9Router.

Verified against:
  - 9Router: v0.5.99 (npm global & Docker container)
  - OpenCode CLI: v1.19.0 (source of truth for Zen Dual-Gate reverse engineering)

Repository: https://github.com/NaxonM/9router-opencode-patch
License: MIT
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.stdout.reconfigure(encoding="utf-8")

# =====================================================================
# EXACT BYTE-PERFECT SIGNATURES & REPLACEMENTS
# =====================================================================

# 1. Stock 5330.js Session ID generator (function B)
TARGET_SESSION_B = (
    'function B(a,b=""){if("string"==typeof a&&s.test(a.trim()))return a.trim();'
    'let c=e().createHash("sha256").update(`opencode\\0${b||"generic"}\\0${a||""}`).digest(),'
    'd=c.subarray(0,6).toString("hex"),f="";for(let a=6;a<20;a++)f+=u[c[a]%62];return`ses_${d}${f}`}'
)
REPLACEMENT_SESSION_B = (
    'function B(a,b=""){if("string"==typeof a&&s.test(a.trim()))return a.trim();'
    'let t=Date.now(),v=~(4096n*BigInt(t)+1n),'
    'c=Array.from({length:6},(a,c)=>Number(v>>BigInt(40-8*c)&255n).toString(16).padStart(2,"0")).join("");'
    'return`ses_${c}${z()}`}'
)

# 2. Stock 5330.js Request ID generator
TARGET_REQUEST_ID = (
    'let d=e().createHash("sha256").update(`opencode-req\\0${a||""}\\0${c}`).digest(),'
    'f=d.subarray(0,6).toString("hex"),g="";for(let a=6;a<20;a++)g+=u[d[a]%62];'
    'let h=`msg_${f}${g}`;return t.test(h)?h:A()'
)
REPLACEMENT_REQUEST_ID = 'return A()'

# 3. Stock 6249.js Inline Session ID generator
TARGET_SESSION_INLINE = (
    'function(a,b=""){if("string"==typeof a&&l.test(a.trim()))return a.trim();'
    'let c=e().createHash("sha256").update(`opencode\\0${b||"generic"}\\0${a||""}`).digest(),'
    'd=c.subarray(0,6).toString("hex"),f="";for(let a=6;a<20;a++)f+=m[c[a]%62];'
    'return`ses_${d}${f}`}(i,d)'
)
REPLACEMENT_SESSION_INLINE = '("string"==typeof i&&l.test(i.trim())?i.trim():o())'

# Fallback regex patterns in case variable minification names vary across releases
REGEX_SESSION_B = re.compile(
    r'function\s+B\([a-zA-Z0-9_,\s="]*\)\{if\("string"==typeof\s+[a-zA-Z0-9_]+&&[a-zA-Z0-9_.]+\.test\([a-zA-Z0-9_.]+\.trim\(\)\)\)return\s+[a-zA-Z0-9_.]+\.trim\(\);'
    r'let\s+[a-zA-Z0-9_]+=e\(\)\.createHash\("sha256"\)\.update\(`opencode\\0.*?\`ses_\$\{[a-zA-Z0-9_]+\}\$\{[a-zA-Z0-9_]+\}\`\}'
)

REGEX_REQUEST_ID = re.compile(
    r'let\s+[a-zA-Z0-9_]+=e\(\)\.createHash\("sha256"\)\.update\(`opencode-req\\0.*?\breturn\s+[a-zA-Z0-9_.]+\.test\([a-zA-Z0-9_]+\)\?[a-zA-Z0-9_]+:A\(\)'
)

REGEX_SESSION_INLINE = re.compile(
    r'function\([a-zA-Z0-9_,\s="]*\)\{if\("string"==typeof\s+[a-zA-Z0-9_]+&&[a-zA-Z0-9_.]+\.test\([a-zA-Z0-9_.]+\.trim\(\)\)\)return\s+[a-zA-Z0-9_.]+\.trim\(\);'
    r'let\s+[a-zA-Z0-9_]+=e\(\)\.createHash\("sha256"\)\.update\(`opencode\\0.*?\`ses_\$\{[a-zA-Z0-9_]+\}\$\{[a-zA-Z0-9_]+\}\`\}\([a-zA-Z0-9_,\s]+\)'
)

# =====================================================================
# AUTO-DISCOVERY ENGINE
# =====================================================================

def get_npm_global_root() -> Optional[str]:
    """Dynamically query npm global root directory."""
    npm_bin = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm_bin:
        return None
    try:
        res = subprocess.run(
            [npm_bin, "root", "-g"],
            shell=(sys.platform == "win32"),
            capture_output=True,
            text=True,
            timeout=8,
        )
        if res.returncode == 0 and res.stdout.strip():
            p = res.stdout.strip()
            if os.path.isdir(p):
                return p
    except Exception:
        pass
    return None


def resolve_candidate_paths(explicit_path: Optional[str] = None) -> List[str]:
    """Generates an ordered list of candidate 9Router chunk directories."""
    candidates: List[str] = []

    if explicit_path:
        p = os.path.abspath(os.path.expanduser(os.path.expandvars(explicit_path)))
        # Direct chunks directory
        if os.path.isdir(p) and any(glob.glob(os.path.join(p, "*.js"))):
            candidates.append(p)
        # 9Router app / root directory
        for sub in [
            os.path.join("app", ".next-cli-build", "server", "chunks"),
            os.path.join(".next-cli-build", "server", "chunks"),
            os.path.join("app", ".next", "server", "chunks"),
            os.path.join(".next", "server", "chunks"),
            os.path.join("server", "chunks"),
            "chunks",
        ]:
            c = os.path.join(p, sub)
            if os.path.isdir(c):
                candidates.append(c)

    # 1. Query npm root -g
    npm_root = get_npm_global_root()
    if npm_root:
        candidates.extend([
            os.path.join(npm_root, "9router", "app", ".next-cli-build", "server", "chunks"),
            os.path.join(npm_root, "9router", ".next-cli-build", "server", "chunks"),
            os.path.join(npm_root, "9router", "app", ".next", "server", "chunks"),
        ])

    # 2. Standard system locations by OS
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        progfiles = os.environ.get("ProgramFiles", "")
        if appdata:
            candidates.append(os.path.join(appdata, "npm", "node_modules", "9router", "app", ".next-cli-build", "server", "chunks"))
        if localappdata:
            candidates.append(os.path.join(localappdata, "npm", "node_modules", "9router", "app", ".next-cli-build", "server", "chunks"))
        if progfiles:
            candidates.append(os.path.join(progfiles, "nodejs", "node_modules", "9router", "app", ".next-cli-build", "server", "chunks"))
    else:
        # Linux & macOS paths
        home = os.path.expanduser("~")
        candidates.extend([
            "/usr/lib/node_modules/9router/app/.next-cli-build/server/chunks",
            "/usr/local/lib/node_modules/9router/app/.next-cli-build/server/chunks",
            "/opt/homebrew/lib/node_modules/9router/app/.next-cli-build/server/chunks",
            os.path.join(home, ".npm-global", "lib", "node_modules", "9router", "app", ".next-cli-build", "server", "chunks"),
            os.path.join(home, "node_modules", "9router", "app", ".next-cli-build", "server", "chunks"),
            # Docker container default directories
            "/app/.next-cli-build/server/chunks",
            "/app/.next/server/chunks",
            "/9router/app/.next-cli-build/server/chunks",
        ])
        # NVM candidate check
        nvm_glob = os.path.join(home, ".nvm", "versions", "node", "*", "lib", "node_modules", "9router", "app", ".next-cli-build", "server", "chunks")
        candidates.extend(glob.glob(nvm_glob))

    # Deduplicate while preserving order
    seen = set()
    valid_existing = []
    for c in candidates:
        norm = os.path.normpath(c)
        if norm not in seen and os.path.isdir(norm):
            seen.add(norm)
            valid_existing.append(norm)

    return valid_existing


def find_9router_chunks_dir(explicit_path: Optional[str] = None) -> Optional[str]:
    """Finds the first valid 9Router chunks directory containing .js files."""
    candidates = resolve_candidate_paths(explicit_path)
    for c in candidates:
        if glob.glob(os.path.join(c, "*.js")):
            return c
    return None

# =====================================================================
# CHUNK SCANNING & PATCHING LOGIC
# =====================================================================

def inspect_chunk(content: str) -> Dict[str, bool]:
    """Analyzes a chunk's content for Dual-Gate unpatched and patched signatures."""
    status = {
        "needs_session_b": False,
        "needs_request_id": False,
        "needs_session_inline": False,
        "has_session_b_patched": "4096n*BigInt(t)" in content or "Array.from({length:6}" in content,
        "has_request_id_patched": "return A()" in content and r'update(`opencode-req\0' not in content,
        "has_session_inline_patched": '("string"==typeof i&&l.test(i.trim())?i.trim():o())' in content,
    }

    if TARGET_SESSION_B in content or REGEX_SESSION_B.search(content):
        status["needs_session_b"] = True
    if TARGET_REQUEST_ID in content or REGEX_REQUEST_ID.search(content):
        status["needs_request_id"] = True
    if TARGET_SESSION_INLINE in content or REGEX_SESSION_INLINE.search(content):
        status["needs_session_inline"] = True

    return status


def patch_single_file(filepath: str, dry_run: bool = False) -> Tuple[bool, List[str]]:
    """Patches an individual .js chunk file with automatic backup and syntax verification."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    status = inspect_chunk(content)
    actions = []
    modified_content = content

    # 1. Patch session ID in function B
    if status["needs_session_b"]:
        if TARGET_SESSION_B in modified_content:
            modified_content = modified_content.replace(TARGET_SESSION_B, REPLACEMENT_SESSION_B)
            actions.append("Replaced function B with descending BigInt timestamp generator")
        elif REGEX_SESSION_B.search(modified_content):
            modified_content = REGEX_SESSION_B.sub(REPLACEMENT_SESSION_B, modified_content, count=1)
            actions.append("Replaced function B (via regex fallback) with descending BigInt timestamp generator")

    # 2. Patch request ID generator
    if status["needs_request_id"]:
        if TARGET_REQUEST_ID in modified_content:
            modified_content = modified_content.replace(TARGET_REQUEST_ID, REPLACEMENT_REQUEST_ID)
            actions.append("Replaced request ID generator with authentic ascending generator A()")
        elif REGEX_REQUEST_ID.search(modified_content):
            modified_content = REGEX_REQUEST_ID.sub(REPLACEMENT_REQUEST_ID, modified_content, count=1)
            actions.append("Replaced request ID generator (via regex fallback) with authentic generator A()")

    # 3. Patch inline session generator
    if status["needs_session_inline"]:
        if TARGET_SESSION_INLINE in modified_content:
            modified_content = modified_content.replace(TARGET_SESSION_INLINE, REPLACEMENT_SESSION_INLINE)
            actions.append("Replaced inline session generator with authentic descending generator o()")
        elif REGEX_SESSION_INLINE.search(modified_content):
            modified_content = REGEX_SESSION_INLINE.sub(REPLACEMENT_SESSION_INLINE, modified_content, count=1)
            actions.append("Replaced inline session generator (via regex fallback) with authentic generator o()")

    if not actions:
        already = []
        if status["has_session_b_patched"]:
            already.append("Session Generator (B)")
        if status["has_request_id_patched"]:
            already.append("Request ID Generator")
        if status["has_session_inline_patched"]:
            already.append("Inline Session Generator")
        if already:
            return True, [f"Already patched ({', '.join(already)})"]
        return False, ["No OpenCode Zen signatures found"]

    if dry_run:
        return True, [f"[DRY RUN] Would apply: {'; '.join(actions)}"]

    # Create safety backup
    bak_path = filepath + ".bak"
    if not os.path.exists(bak_path):
        shutil.copy2(filepath, bak_path)

    # Write patched content
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(modified_content)

    # Syntax verification if node is available
    node_bin = shutil.which("node")
    if node_bin:
        res = subprocess.run([node_bin, "--check", filepath], capture_output=True, text=True)
        if res.returncode != 0:
            # Rollback immediately on syntax error
            shutil.copy2(bak_path, filepath)
            raise RuntimeError(f"Syntax validation failed on {filepath}! Rolled back from .bak. Error: {res.stderr}")

    return True, actions

# =====================================================================
# HIGH-LEVEL OPERATIONS (Local, Docker, Rollback, Verify)
# =====================================================================

def run_local_patch(chunks_dir: str, dry_run: bool = False) -> bool:
    """Scans and patches all matching chunks in the specified directory."""
    print(f"\nScanning chunks directory: {chunks_dir}")
    files = glob.glob(os.path.join(chunks_dir, "*.js"))
    files = [f for f in files if not f.endswith(".bak")]

    if not files:
        print("Error: No .js files found in directory.")
        return False

    patched_count = 0
    already_count = 0

    for filepath in files:
        filename = os.path.basename(filepath)
        try:
            success, msgs = patch_single_file(filepath, dry_run=dry_run)
            if success:
                if any("Already patched" in m for m in msgs):
                    already_count += 1
                    print(f"  [ALREADY PATCHED] {filename}: {msgs[0]}")
                else:
                    patched_count += 1
                    print(f"  [PATCHED] {filename}:")
                    for m in msgs:
                        print(f"    -> {m}")
        except Exception as e:
            print(f"  [ERROR] Failed to patch {filename}: {e}")
            return False

    print(f"\nSummary: {patched_count} file(s) patched, {already_count} file(s) already up-to-date.")
    return True


def run_rollback(chunks_dir: str) -> bool:
    """Restores all .bak files in the chunks directory."""
    print(f"\nLooking for backups in: {chunks_dir}")
    backups = glob.glob(os.path.join(chunks_dir, "*.bak"))
    if not backups:
        print("No .bak backup files found. Nothing to restore.")
        return True

    restored = 0
    for bak in backups:
        orig = bak[:-4]
        try:
            shutil.copy2(bak, orig)
            os.remove(bak)
            print(f"  Restored {os.path.basename(orig)} from backup.")
            restored += 1
        except Exception as e:
            print(f"  Error restoring {bak}: {e}")
            return False

    print(f"\nSuccessfully restored {restored} file(s).")
    return True


def run_status_check(chunks_dir: str):
    """Prints a detailed diagnosis of the chunks directory."""
    print(f"\nChecking status of: {chunks_dir}")
    files = glob.glob(os.path.join(chunks_dir, "*.js"))
    files = [f for f in files if not f.endswith(".bak")]

    found_targets = []
    for f in files:
        with open(f, "r", encoding="utf-8", errors="ignore") as fp:
            c = fp.read()
        status = inspect_chunk(c)
        if any(status.values()):
            found_targets.append((os.path.basename(f), status))

    if not found_targets:
        print("No OpenCode Zen chunk modules detected in this directory.")
        return

    print(f"Found {len(found_targets)} relevant chunk(s):")
    for name, st in found_targets:
        print(f"\n* Chunk: {name}")
        print(f"  - Session ID Generator B:     {'[NEEDS PATCH]' if st['needs_session_b'] else ('[PATCHED OK]' if st['has_session_b_patched'] else '[N/A]')}")
        print(f"  - Request ID Generator A:     {'[NEEDS PATCH]' if st['needs_request_id'] else ('[PATCHED OK]' if st['has_request_id_patched'] else '[N/A]')}")
        print(f"  - Inline Session Generator o: {'[NEEDS PATCH]' if st['needs_session_inline'] else ('[PATCHED OK]' if st['has_session_inline_patched'] else '[N/A]')}")


def run_docker_patch(container_name: str, restart: bool = True) -> bool:
    """Patches a 9Router instance running inside a Docker container."""
    docker_bin = shutil.which("docker")
    if not docker_bin:
        print("Error: 'docker' CLI not found in system PATH.")
        return False

    print(f"\nInspecting Docker container: '{container_name}'...")
    # Find chunks inside container
    find_cmd = [
        docker_bin, "exec", container_name, "sh", "-c",
        "find /app /9router / -name '5330.js' -o -name '6249.js' 2>/dev/null"
    ]
    res = subprocess.run(find_cmd, capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout.strip():
        print(f"Could not locate 9Router chunks inside container '{container_name}'.")
        return False

    target_files = [line.strip() for line in res.stdout.splitlines() if line.strip() and line.strip().endswith(".js")]
    print(f"Found {len(target_files)} target file(s) in container: {', '.join(target_files)}")

    temp_dir = os.path.join(os.getcwd(), ".tmp_docker_patch")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        for container_path in target_files:
            fname = os.path.basename(container_path)
            local_tmp = os.path.join(temp_dir, fname)

            # Copy chunk out
            subprocess.run([docker_bin, "cp", f"{container_name}:{container_path}", local_tmp], check=True)
            # Create container-side backup
            subprocess.run([docker_bin, "exec", container_name, "cp", container_path, f"{container_path}.bak"], check=True)

            # Patch locally
            success, msgs = patch_single_file(local_tmp)
            print(f"  Patched {fname}: {'; '.join(msgs)}")

            # Copy back
            subprocess.run([docker_bin, "cp", local_tmp, f"{container_name}:{container_path}"], check=True)

        if restart:
            print(f"Restarting Docker container '{container_name}'...")
            subprocess.run([docker_bin, "restart", container_name], check=True)
            print(f"Container '{container_name}' successfully restarted.")

        print(f"\nContainer '{container_name}' patched successfully.")
        return True
    except Exception as e:
        print(f"Error patching Docker container: {e}")
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def run_verify(url: str, key: Optional[str] = None, model: str = "oc/mimo-v2.6-flash-free"):
    """Sends a minimal probe to test if 9Router and OpenCode Zen route cleanly without 403."""
    print(f"\nVerifying OpenCode Zen routing via: {url}")
    print(f"Testing model: {model}")

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Respond with the word OK."}],
        "stream": True,
    }

    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")

    try:
        start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = ""
            for line in resp:
                l = line.decode("utf-8").strip()
                if l.startswith("data: ") and l != "data: [DONE]":
                    try:
                        c = json.loads(l[6:])
                        d = c.get("choices", [{}])[0].get("delta", {})
                        text += d.get("content") or ""
                    except Exception:
                        pass
            latency = (time.perf_counter() - start) * 1000
            print(f"[SUCCESS] HTTP {resp.status} OK in {latency:.0f}ms")
            print(f"Model response: {text.strip() or 'OK'}")
            print("OpenCode Zen Dual-Gate: UNBLOCKED and accepting requests!")
            return True
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:250]
        print(f"[FAILED] HTTP {e.code}: {err_body}")
        if e.code == 403 and "FreeTierError" in err_body:
            print("Diagnosis: OpenCode Zen Dual-Gate REJECTED the request. 9Router chunk patch is missing or server was not restarted.")
        elif e.code == 401:
            print("Diagnosis: 401 Unauthorized. Provide a valid 9Router API key via --key or ROUTER_API_KEY env var.")
        return False
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}")
        return False

# =====================================================================
# CLI ENTRY POINT
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="9Router OpenCode Zen Dual-Gate Hotfix (Standalone Universal Patcher)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python patch_9router.py                        # Auto-detect 9Router & patch chunks
  python patch_9router.py --status               # Check if current chunks are patched
  python patch_9router.py --path /custom/path    # Specify custom 9Router installation path
  python patch_9router.py --docker 9router       # Patch a running Docker container
  python patch_9router.py --rollback             # Restore original .bak chunks
  python patch_9router.py --verify               # Verify routing with a live probe
        """,
    )

    parser.add_argument("--path", default=None, help="Path to 9Router directory or chunks directory")
    parser.add_argument("--docker", default=None, help="Name or ID of Docker container running 9Router")
    parser.add_argument("--status", action="store_true", help="Check patch status of 9Router chunks without modifying")
    parser.add_argument("--rollback", action="store_true", help="Restore chunks from .bak backups")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying any files")
    parser.add_argument("--verify", action="store_true", help="Verify live routing to OpenCode Zen free-tier model")
    parser.add_argument("--url", default=os.environ.get("ROUTER_URL", "http://127.0.0.1:20128/v1/chat/completions"), help="Endpoint URL for verification (default: http://127.0.0.1:20128/v1/chat/completions)")
    parser.add_argument("--key", default=os.environ.get("ROUTER_API_KEY"), help="API key for verification (or set ROUTER_API_KEY env var)")
    parser.add_argument("--model", default="oc/mimo-v2.6-flash-free", help="Model to test during verification")

    args = parser.parse_args()

    print("================================================================")
    print("   9Router OpenCode Zen Dual-Gate Standalone Hotfix Patcher    ")
    print("================================================================")

    # Docker target
    if args.docker:
        success = run_docker_patch(args.docker)
        if success and args.verify:
            run_verify(args.url, args.key, args.model)
        sys.exit(0 if success else 1)

    # Verification only
    if args.verify and not args.status and not args.rollback and not args.path:
        success = run_verify(args.url, args.key, args.model)
        sys.exit(0 if success else 1)

    # Local filesystem discovery
    chunks_dir = find_9router_chunks_dir(args.path)
    if not chunks_dir:
        print("\n[ERROR] Could not automatically locate 9Router chunks directory.")
        print("Please specify the path manually with:")
        print("  python patch_9router.py --path <path/to/9router>")
        sys.exit(1)

    if args.status:
        run_status_check(chunks_dir)
        sys.exit(0)

    if args.rollback:
        success = run_rollback(chunks_dir)
        sys.exit(0 if success else 1)

    # Default action: Patch
    success = run_local_patch(chunks_dir, dry_run=args.dry_run)

    if success and args.verify:
        run_verify(args.url, args.key, args.model)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
