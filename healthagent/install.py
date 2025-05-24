import os
import shutil

def main():
    etc_dir = "/etc/healthagent"
    os.makedirs(etc_dir, exist_ok=True)

    # Copy example scripts
    for fname in ["health.sh.example", "epilog.sh.example"]:
        src = os.path.join(os.path.dirname(__file__), "etc", fname)
        dst = os.path.join(etc_dir, fname)
        shutil.copy2(src, dst)
        print(f"Copied {src} to {dst}")
    # Find the installed 'health' script
    import shutil as sh
    health_path = sh.which("health")
    if health_path:
        dst = "/usr/bin/health"
        shutil.copy2(health_path, dst)
        print(f"Copied {health_path} to {dst}")
    else:
        print("Could not find 'health' script in PATH.")

