import sys, json, os
from pathlib import Path

def main():
    from graphify.extract import collect_files, extract
    
    detect = json.loads(Path('graphify-out/.graphify_detect.json').read_text())

    code_files = []
    for f in detect.get('files', {}).get('code', []):
        code_files.extend(collect_files(Path(f)) if Path(f).is_dir() else [Path(f)])

    if code_files:
        result = extract(code_files, cache_root=Path('.'))
        Path('graphify-out/.graphify_ast.json').write_text(json.dumps(result, indent=2))
        print(f"AST: {len(result['nodes'])} nodes, {len(result['edges'])} edges")
    else:
        Path('graphify-out/.graphify_ast.json').write_text(json.dumps({'nodes':[],'edges':[],'input_tokens':0,'output_tokens':0}))
        print('No code files - skipping AST extraction')

    docs = [Path(f) for f in detect.get('files', {}).get('document', [])]
    papers = [Path(f) for f in detect.get('files', {}).get('paper', [])]
    all_docs = docs + papers

    if not all_docs:
        print('No docs - skipping semantic')
        Path('graphify-out/.graphify_semantic.json').write_text(json.dumps({'nodes':[],'edges':[],'input_tokens':0,'output_tokens':0}))
    else:
        from dotenv import load_dotenv
        load_dotenv()
        if not os.environ.get('GOOGLE_API_KEY') and not os.environ.get('GEMINI_API_KEY'):
            print('No GOOGLE_API_KEY found.')
            Path('graphify-out/.graphify_semantic.json').write_text(json.dumps({'nodes':[],'edges':[],'input_tokens':0,'output_tokens':0}))
        else:
            try:
                from graphify.llm import extract_corpus_parallel
                print(f"Starting semantic extraction on {len(all_docs)} files using Gemini...")
                os.environ['GRAPHIFY_GEMINI_MODEL'] = 'gemini-2.5-flash'
                sem_result = extract_corpus_parallel(all_docs, backend='gemini')
                Path('graphify-out/.graphify_semantic.json').write_text(json.dumps(sem_result, indent=2))
                print("Semantic extraction done.")
            except Exception as e:
                print("Error in semantic extraction:", e)
                import traceback
                traceback.print_exc()
                Path('graphify-out/.graphify_semantic.json').write_text(json.dumps({'nodes':[],'edges':[],'input_tokens':0,'output_tokens':0}))

if __name__ == '__main__':
    main()
