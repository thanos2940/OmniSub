
from google.adk.runners import Event
import inspect
from pprint import pprint

print("Inspecting Event class...")
print(f"Class: {Event}")
print("\nAnnotations:")
if hasattr(Event, '__annotations__'):
    pprint(Event.__annotations__)

print("\nFields (if Pydantic):")
if hasattr(Event, 'model_fields'):
    pprint(Event.model_fields)

print("\nSource (if available):")
try:
    print(inspect.getsource(Event))
except Exception as e:
    print(f"Could not get source: {e}")
