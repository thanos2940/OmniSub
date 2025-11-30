import asyncio
import json
import os
from typing import List, Dict, Set
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables FIRST
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# Now import agents that depend on env vars
from agents.cartographer import CartographerAgent

class AgentEvaluator:
    def __init__(self, eval_set_path: str):
        self.eval_set_path = eval_set_path
        self.agent = CartographerAgent()

    def load_eval_set(self) -> Dict:
        with open(self.eval_set_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def calculate_metrics(self, expected: Set[str], actual: Set[str]):
        # Normalize for comparison (case-insensitive)
        expected_norm = {t.lower() for t in expected}
        actual_norm = {t.lower() for t in actual}

        true_positives = len(expected_norm.intersection(actual_norm))
        false_positives = len(actual_norm - expected_norm)
        false_negatives = len(expected_norm - actual_norm)

        precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0
        recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0

        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "found": list(actual),
            "missed": list(expected_norm - actual_norm),
            "extra": list(actual_norm - expected_norm)
        }

    async def run_evaluation(self):
        print("Starting Agent Evaluation...")
        data = self.load_eval_set()
        cases = data.get("cases", [])
        
        total_f1 = 0
        results = []

        for case in cases:
            print(f"\nEvaluating Case: {case['id']}")
            input_text = case['input_text']
            expected_terms = set(case['expected_terms'])
            
            # Run Agent
            # Note: We pass empty existing_glossary to force full extraction
            glossary, _ = await self.agent.generate_glossary(
                text_lines=input_text,
                show_name="Evaluation Test",
                target_language="English"
            )
            
            actual_terms = {t['term'] for t in glossary.get('terms', [])}
            
            metrics = self.calculate_metrics(expected_terms, actual_terms)
            metrics['id'] = case['id']
            results.append(metrics)
            
            total_f1 += metrics['f1']
            
            print(f"  Precision: {metrics['precision']:.2f}")
            print(f"  Recall:    {metrics['recall']:.2f}")
            print(f"  F1 Score:  {metrics['f1']:.2f}")
            if metrics['missed']:
                print(f"  Missed:    {metrics['missed']}")
            if metrics['extra']:
                print(f"  Extra:     {metrics['extra']}")

        avg_f1 = total_f1 / len(cases) if cases else 0
        print(f"\n========================================")
        print(f"Evaluation Complete")
        print(f"Average F1 Score: {avg_f1:.2f}")
        print(f"========================================")

if __name__ == "__main__":
    # Use absolute path or correct relative path
    script_dir = Path(__file__).parent
    eval_path = script_dir / "test_data" / "evalset.json"
    evaluator = AgentEvaluator(str(eval_path))
    asyncio.run(evaluator.run_evaluation())
