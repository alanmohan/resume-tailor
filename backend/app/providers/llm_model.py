"""Base class for every model exchanged with the language model."""

from pydantic import BaseModel, ConfigDict


class LLMModel(BaseModel):
    """Strict structured outputs require a closed schema in which every field
    is required. Subclasses therefore declare no default values (use
    ``X | None`` for optional data) and no free-form dictionaries;
    ``extra="forbid"`` emits ``additionalProperties: false``."""

    model_config = ConfigDict(extra="forbid")
