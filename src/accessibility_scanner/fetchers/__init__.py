from .base import BaseFetcher
from .playwright import PlaywrightFetcher
from .static import StaticFetcher, StaticPage

__all__ = ["BaseFetcher", "PlaywrightFetcher", "StaticFetcher", "StaticPage"]
