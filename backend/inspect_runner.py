
from google.adk.runners import Runner
import inspect

print(f"Runner type: {Runner}")
print(f"Runner.run signature: {inspect.signature(Runner.run)}")
print(f"Runner.run doc: {Runner.run.__doc__}")
