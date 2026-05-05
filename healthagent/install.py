import os
import shutil
import importlib.resources

def main():
    etc_dir = "/etc/healthagent"
    os.makedirs(etc_dir, exist_ok=True)

    # Copy defaults.yaml to /etc/healthagent/ for operator reference
    defaults_src = importlib.resources.files("healthagent").joinpath("defaults.yaml")
    defaults_dst = os.path.join(etc_dir, "defaults.yaml")
    with importlib.resources.as_file(defaults_src) as src_path:
        shutil.copy2(str(src_path), defaults_dst)
    print(f"Copied defaults.yaml to {defaults_dst}")

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

