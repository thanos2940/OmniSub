import subprocess
import os
import tempfile
from pathlib import Path
from typing import List, Dict
from utils.srt_parser import reconstruct_srt, parse_srt
from utils.storage import load_global_config

def fix_subtitles_with_se(parsed_data: List[Dict]) -> List[Dict]:
    """
    Run SubtitleEdit fixes on parsed subtitle data.
    
    This applies 'Fix common errors' and 'Split long lines' using the 
    SubtitleEdit CLI.
    """
    config = load_global_config()
    se_path = config.get("subtitle_edit_path")
    
    if not se_path or not os.path.exists(se_path):
        return parsed_data # Skip if SE not configured or not found

    # 1. Reconstruct to temporary file
    srt_content = reconstruct_srt(parsed_data)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = Path(tmpdir) / "input.srt"
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        
        # Use utf-8-sig for SRT files to ensure proper BOM handling if SE expects it, 
        # but SE is usually fine with utf-8. OmbiSub uses utf-8.
        with open(input_file, 'w', encoding='utf-8') as f:
            f.write(srt_content)
            
        # 2. Run SubtitleEdit
        # Flags:
        # /FixCommonErrors - Standard SE fix logic
        # /SplitLongLines - Breaks lines exceeding standard length
        cmd = [
            se_path,
            "/convert", "subrip",
            str(input_file),
            "/FixCommonErrors",
            "/SplitLongLines",
            "/outputfolder", str(output_dir)
        ]
        
        try:
            # We use shell=False for security and subprocess.run for blocking execution
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            # 3. Read result back
            # SE usually keeps the same filename in the output folder
            result_file = output_dir / "input.srt"
            if result_file.exists():
                with open(result_file, 'r', encoding='utf-8-sig') as f:
                    fixed_content = f.read()
                
                fixed_data = parse_srt(fixed_content)
                
                # Map fixed text to 'translated' and try to restore 'original' for context
                # We use a map for quick lookup from the input data
                input_map = {entry['id']: entry for entry in parsed_data}
                
                for entry in fixed_data:
                    # The text returned by parse_srt is in 'original'
                    entry["translated"] = entry["original"]
                    
                    # Try to restore the actual original source text if the ID still matches
                    if entry["id"] in input_map:
                        entry["original"] = input_map[entry["id"]].get("original", "")
                    else:
                        # For split lines or renumbered lines, we don't have a direct 1:1 original
                        # We could leave it or set it to a placeholder.
                        # For now, keeping it as the translated text (as parse_srt did) is okay,
                        # but let's at least clear it if it's clearly not the same.
                        pass
                
                return fixed_data
        except Exception as e:
            print(f"Error running SubtitleEdit: {e}")
            if hasattr(e, 'stderr'):
                print(f"SE Stderr: {e.stderr}")
            
    return parsed_data
