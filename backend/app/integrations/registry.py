import importlib
from app.config import Settings
from .oura import OuraProvider

class ProviderRegistry:
    def __init__(self,settings:Settings):
        self.settings=settings
        self._custom=importlib.import_module(settings.provider_module) if settings.provider_module else None
        self.overrides={}
    def get(self,provider):
        if provider in self.overrides:return self.overrides[provider]
        if self._custom:
            adapter=self._custom.get_provider(provider)
            if adapter is not None:return adapter
        if provider=='oura' and self.settings.oura_access_token:return OuraProvider(self.settings.oura_access_token)
        return None
    def status(self):
        from app.models import PROVIDERS
        return {p:{'configured':self.get(p) is not None,'supports_push':True} for p in PROVIDERS}
