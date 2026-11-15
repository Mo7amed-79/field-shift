# AI-assisted: generated with Claude (Anthropic) and reviewed by the Field Shift team. See AI_USAGE.md.
"""Project next year's monthly temperature and ET0.

Input:  raw dict from power_client.fetch_monthly (multi-year history).
Output: dict with a 12-month projection of temp_c and et0_mm, plus an
        uncertainty indication. Meant as a simple trend-based outlook,
        not a weather forecast.
"""


def project_next_year(raw: dict) -> dict:
    """Return a 12-month projection of temperature and ET0 for the coming year."""
    raise NotImplementedError
