"""The agent runner's parts (``services.agent_runner.AgentRunner`` inherits the mixins):

* common.py   — constants and helpers (re-exported by services.agent_runner)
* models.py   — which model, quota pauses, what a failed request means
* claims.py   — pending episodes, claiming, keeping and giving back claims
* episode.py  — one episode: bootstrap, parts and fix rounds, extras, submit
* requests.py — a request to the CLI or the Gemini API; refusals and the rescue
* context.py  — what a request carries besides its lines
"""
