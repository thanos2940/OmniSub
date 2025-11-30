"""
Fix corrupted project.json files

This script finds and repairs project.json files that have invalid JSON.
"""

import json
from pathlib import Path

PROJECTS_DIR = Path(__file__).resolve().parent / "projects"

def fix_project(project_dir):
    """Attempt to fix a corrupted project.json file."""
    project_json = project_dir / "project.json"
    
    if not project_json.exists():
        return None
    
    try:
        # Try to load the JSON
        with open(project_json, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # If successful, no fix needed
        return "OK"
    except json.JSONDecodeError as e:
        print(f"❌ Corrupted: {project_dir.name}")
        print(f"   Error: {e}")
        
        # Create a backup
        backup_file = project_dir / "project.json.backup"
        with open(project_json, 'r', encoding='utf-8') as f:
            content = f.read()
        with open(backup_file, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"   Created backup: {backup_file.name}")
        
        # Create a fresh project.json with default structure
        default_metadata = {
            "show_name": project_dir.name,
            "glossary": {"terms": []},
            "context_guide": "",
            "target_language": "English",
            "settings": {}
        }
        
        with open(project_json, 'w', encoding='utf-8') as f:
            json.dump(default_metadata, f, indent=2, ensure_ascii=False)
        
        print(f"   ✅ Fixed with default metadata")
        return "FIXED"

def main():
    print("Scanning for corrupted project.json files...\n")
    
    results = {"ok": 0, "fixed": 0, "skipped": 0}
    
    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        
        result = fix_project(project_dir)
        if result == "OK":
            results["ok"] += 1
        elif result == "FIXED":
            results["fixed"] += 1
        elif result is None:
            results["skipped"] += 1
    
    print(f"\n{'='*50}")
    print(f"Summary:")
    print(f"  ✅ OK: {results['ok']}")
    print(f"  🛠️  Fixed: {results['fixed']}")
    print(f"  ⏭️  Skipped (no project.json): {results['skipped']}")
    print(f"{'='*50}")

if __name__ == "__main__":
    main()
