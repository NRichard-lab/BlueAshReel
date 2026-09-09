"""Provider-neutral metadata boundaries; all requests originate on the Agent."""

from app.metadata.provider import MetadataProvider, ProviderError
from app.metadata.types import ArtworkSource, Candidate, Credit, MetadataDetails

__all__ = ["ArtworkSource", "Candidate", "Credit", "MetadataDetails", "MetadataProvider", "ProviderError"]
