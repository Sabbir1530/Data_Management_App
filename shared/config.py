from dataclasses import dataclass

@dataclass(frozen=True)
class AppConfig:
    # From EU Dedup
    eu_window_title: str = "MDM — End User Dedup & Remap"
    eu_window_geometry: str = "1400x850"
    page_size: int = 50
    default_fuzzy_threshold: int = 85
    default_ai_min_confidence: int = 90
    
    # From Project Data Laundry & Matching
    project_window_title: str = "MDM — Data Laundry & Matching"
    project_window_geometry: str = "980x620"
    progress_update_every: int = 5

APP_CONFIG = AppConfig()
