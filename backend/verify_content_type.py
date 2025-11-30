
from google.adk.runners import types as adk_types
import inspect

print("Checking Content type...")
try:
    print(f"Content type: {adk_types.Content}")
    print(f"Content signature: {inspect.signature(adk_types.Content)}")
except Exception as e:
    print(f"Error checking Content: {e}")

print("\nChecking Part type...")
try:
    print(f"Part type: {adk_types.Part}")
    print(f"Part signature: {inspect.signature(adk_types.Part)}")
except Exception as e:
    print(f"Error checking Part: {e}")

print("\nTest instantiation:")
try:
    part = adk_types.Part(text="test")
    content = adk_types.Content(role="user", parts=[part])
    print(f"Successfully created Content: {content}")
    print(f"Content role: {content.role}")
except Exception as e:
    print(f"Instantiation failed: {e}")
