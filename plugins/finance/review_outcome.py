from dataclasses import dataclass, field


@dataclass
class TurnReview:
    action: str = 'none'
    message: dict | None = None
    pending_tokens: list[str] = field(default_factory=list)
    events: list = field(default_factory=list)
    pending_text: str | None = None
    error: str = ''
