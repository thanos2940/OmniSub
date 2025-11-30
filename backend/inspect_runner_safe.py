
from google.adk.runners import Runner
import inspect
import sys

with open("runner_sig.txt", "w", encoding="utf-8") as f:
    try:
        sig = inspect.signature(Runner.run)
        f.write(f"Runner.run Signature: {sig}\n")
        f.write(f"Runner.run Docstring: {Runner.run.__doc__}\n")
        
        if hasattr(Runner, 'run_async'):
            sig_async = inspect.signature(Runner.run_async)
            f.write(f"Runner.run_async Signature: {sig_async}\n")
            f.write(f"Runner.run_async Docstring: {Runner.run_async.__doc__}\n")
        else:
            f.write("Runner.run_async not found\n")
            
        import google.adk.runners
        f.write(f"google.adk.runners members: {dir(google.adk.runners)}\n")
        
        try:
            from google.adk.runners import types
            f.write(f"google.adk.runners.types members: {dir(types)}\n")
        except ImportError:
            f.write("Could not import google.adk.runners.types\n")

        try:
            from google.adk.model import types as model_types
            f.write(f"google.adk.model.types members: {dir(model_types)}\n")
        except ImportError:
            f.write("Could not import google.adk.model.types\n")
    except Exception as e:
        f.write(f"Error: {e}\n")
