# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Field Shift engine: pure functions, no web code.

Pipeline: power_client -> climate / forecast -> water_balance -> scoring
-> planner -> explain. The backend calls these; nothing here imports FastAPI.
"""
