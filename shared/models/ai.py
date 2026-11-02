from pydantic import BaseModel, Field
from typing import Optional

class EntityMatchDecision(BaseModel):
    result: str = Field(description="Must be strictly one of: 'SAME', 'AFFILIATED', or 'DIFFERENT'.")
    reason: str = Field(
        description="2-3 lines citing specific evidence from authoritative sources — name registries, "
        "official websites, CMS data, or Google results. Mention the source."
    )
    confidence: int = Field(description="Confidence level of the decision as an integer percentage from 0 to 100.")
    entity_1_parent: Optional[str] = Field(
        default=None, description="Parent organization/healthcare system for ENTITY 1 (UPD), or null."
    )
    entity_2_parent: Optional[str] = Field(
        default=None, description="Parent organization/healthcare system for ENTITY 2 (CMPNY), or null."
    )
