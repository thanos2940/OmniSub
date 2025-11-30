import os
import subprocess
import sys
import shutil
from pathlib import Path

def run_command(command, cwd=None):
    print(f"Running: {command}")
    try:
        subprocess.check_call(command, shell=True, cwd=cwd)
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {e}")
        sys.exit(1)

def main():
    # Paths
    root_dir = Path(__file__).parent.resolve()
    frontend_dir = root_dir / "frontend"
    backend_dir = root_dir / "backend"
    dist_dir = root_dir / "dist"

    print("="*50)
    print("OmbiSub Builder")
    print("="*50)

    # 1. Build Frontend
    print("\n[1/3] Building Frontend...")
    if not (frontend_dir / "node_modules").exists():
        print("Installing frontend dependencies...")
        run_command("npm install", cwd=frontend_dir)
    
    run_command("npm run build", cwd=frontend_dir)

    # Verify build
    frontend_dist = frontend_dir / "dist"
    if not frontend_dist.exists():
        print("Error: Frontend build failed. 'dist' directory not found.")
        sys.exit(1)

    # 2. Clean previous builds
    print("\n[2/3] Cleaning previous builds...")
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    
    build_work_dir = root_dir / "build"
    if build_work_dir.exists():
        shutil.rmtree(build_work_dir)

    # 3. Package with PyInstaller
    print("\n[3/3] Packaging with PyInstaller...")
    
    # PyInstaller arguments
    # --onefile: Create a single exe
    # --name: Name of the exe
    # --add-data: Include the frontend build as 'static'
    # --hidden-import: Ensure uvicorn/fastapi dependencies are found
    
    sep = ";" if os.name == 'nt' else ":"
    add_data = f"{frontend_dist}{sep}static"
    
    cmd = [
        "pyinstaller",
        "--name=OmbiSub",
        "--onefile",
        f"--add-data={add_data}",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols",
        "--hidden-import=uvicorn.protocols.http",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.lifespan",
        "--hidden-import=uvicorn.lifespan.on",
        "--clean",
        str(backend_dir / "main.py")
    ]
    
    run_command(" ".join(cmd), cwd=root_dir)

    print("\n" + "="*50)
    print(f"Build Complete! Executable is in: {dist_dir}")
    print("="*50)
    print("NOTE: Don't forget to place your .env file next to the executable!")

if __name__ == "__main__":
    main()
