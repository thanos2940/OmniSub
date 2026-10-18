"""OmbiSub Autopilot: follows Sonarr/Radarr and makes sure new media gets Greek subtitles.

See docs/PLAN_autopilot.md. Modules are imported lazily by their users so that a
missing optional piece (Plex, notifications) never breaks the core.
"""
