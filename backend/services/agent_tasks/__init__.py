"""The agent task service's parts (``services.agent_task_service.AgentTaskService`` inherits the mixins):

* common.py     — constants and helpers (re-exported by services.agent_task_service)
* workspaces.py — claiming queued episodes and building their workspaces and briefs
* submission.py — reading, checking and ingesting a translated work file
* knowledge.py  — glossary terms and context guide a submission adds to the show
* audit.py      — the translation audit of a project or the library, and its repairs
* lifecycle.py  — reconciling, releasing, cancelling and pruning claims
"""
