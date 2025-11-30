import google.generativeai as genai
from google.generativeai import protos

try:
    gs = protos.Tool.GoogleSearch()
    print("Created GoogleSearch object:", gs)
    tool = protos.Tool(google_search=gs)
    print("Created Tool object:", tool)
except Exception as e:
    print("Error:", e)
