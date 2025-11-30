
from google.adk.runners import Runner
import inspect

print("Inspecting Runner methods...")
for name, method in inspect.getmembers(Runner):
    if "session" in name.lower() or "create" in name.lower():
        print(f"- {name}")

print("\nChecking if Runner has session_service attribute...")
if hasattr(Runner, 'session_service'):
    print("Runner has session_service attribute")
else:
    print("Runner does NOT have session_service attribute (might be instance attribute)")
