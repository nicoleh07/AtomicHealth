from datetime import datetime, timezone, date
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

PROVIDERS = ('oura','renpho_scale','renpho_food','apple_watch','lumen')
# The shared ingestion contract deliberately uses one canonical unit per metric.
METRICS = {
    'sleep_hours': ('h', 0, 24), 'sleep_score': ('score', 0, 100), 'hrv': ('ms', 0, 500),
    'resting_hr': ('bpm', 20, 250), 'readiness': ('score', 0, 100),
    'weight_kg': ('kg', 20, 400), 'body_fat_pct': ('%', 1, 80), 'muscle_kg': ('kg', 1, 200),
    'steps': ('steps', 0, 200000), 'active_kcal': ('kcal', 0, 10000), 'exercise_min': ('min', 0, 1440),
    'stand_hours': ('h', 0, 24), 'lumen_level': ('level', 1, 5), 'flex_score': ('score', 0, 100),
    'bmr_kcal': ('kcal', 500, 5000), 'visceral_fat': ('index', 0, 100), 'body_score': ('score', 0, 100),
    'bmi': ('index', 5, 100), 'bone_kg': ('kg', 0, 20), 'protein_kg': ('kg', 0, 100),
    'body_water_kg': ('kg', 0, 300), 'skeletal_muscle_kg': ('kg', 0, 200),
    'subcutaneous_fat_pct': ('%', 0, 80), 'metabolic_age': ('years', 1, 120), 'whr': ('ratio', 0, 3),
}
SOURCES = {
    'oura': {'sleep_hours','sleep_score','hrv','resting_hr','readiness'},
    'apple_watch': {'steps','active_kcal','exercise_min','stand_hours','resting_hr'},
    'renpho_scale': set(METRICS) - {'sleep_hours','sleep_score','hrv','resting_hr','readiness','steps','active_kcal','exercise_min','stand_hours','lumen_level','flex_score'},
    'lumen': {'lumen_level','flex_score'}, 'renpho_food': set(),
}
class MetricInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    type: str
    value: float
    unit: str
    timestamp: datetime
    external_id: str | None = Field(None, max_length=200)
    @field_validator('timestamp')
    @classmethod
    def utc(cls, v):
        return v.replace(tzinfo=v.tzinfo or timezone.utc).astimezone(timezone.utc)
    @model_validator(mode='after')
    def check(self):
        spec=METRICS.get(self.type)
        if not spec or self.unit != spec[0] or not spec[1] <= self.value <= spec[2]:
            raise ValueError('Unknown metric, non-canonical unit, or out-of-range value')
        return self
class IngestRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    metrics: list[MetricInput] = Field(min_length=1, max_length=500)
class Profile(BaseModel):
    model_config=ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(min_length=1,max_length=60)
    age: int = Field(ge=18,le=120)
    height: float = Field(ge=80,le=250)
    conditions: str = Field('',max_length=1000)
    meds: str = Field('',max_length=1000)
    target_weight: float = Field(ge=35,le=300)
    protein_goal: float = Field(ge=20,le=300)
    calorie_goal: float = Field(ge=500,le=6000)
    move_goal: float = Field(ge=50,le=2000)
    twin_name: str = Field(min_length=1,max_length=40)
    tone: Literal['warm','direct','clinical']='warm'
    nudge_freq: Literal['off','daily','real-time']='daily'
    quiet_start: int = Field(22,ge=0,le=23)
    quiet_end: int = Field(8,ge=0,le=23)
    avatar: Literal['garden','journal']='garden'
    timezone: str = 'America/Los_Angeles'
    @field_validator('name','twin_name')
    @classmethod
    def not_blank(cls,v):
        if not v.strip(): raise ValueError('A name is required')
        return v.strip()
    @field_validator('timezone')
    @classmethod
    def valid_zone(cls,v):
        from zoneinfo import ZoneInfo
        try: ZoneInfo(v)
        except Exception: raise ValueError('Choose an IANA time zone')
        return v
class FoodInput(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    food_name:str=Field(min_length=1,max_length=200)
    meal:Literal['breakfast','lunch','dinner','snack']
    grams:float=Field(ge=0,le=3000)
    kcal:float=Field(ge=0,le=5000)
    protein_g:float=Field(ge=0,le=300)
    carbs_g:float=Field(ge=0,le=500)
    fat_g:float=Field(ge=0,le=300)
    timestamp:datetime|None=None
class ConnectionChange(BaseModel):
    status:Literal['connected','disconnected']
    mode:Literal['demo','live']='demo'
class PrivacyChange(BaseModel):
    provider:Literal['oura','renpho_scale','renpho_food','apple_watch','lumen']
    allowed:bool
class ChatInput(BaseModel):
    message:str=Field(min_length=1,max_length=4000)
class ReportFields(BaseModel):
    model_config=ConfigDict(extra='allow',allow_inf_nan=False)
    test_date:date
    weight_kg:float=Field(ge=20,le=400)
    body_fat_pct:float=Field(ge=1,le=80)
    muscle_kg:float=Field(ge=1,le=200)
    visceral_fat:float=Field(ge=0,le=100)
    bmr_kcal:float=Field(ge=500,le=5000)
    @model_validator(mode='after')
    def extra_metrics(self):
        for key,v in (self.model_extra or {}).items():
            if key in METRICS and v is not None:
                _,lo,hi=METRICS[key]
                if isinstance(v,bool) or not isinstance(v,(int,float)) or not lo<=v<=hi: raise ValueError(f'Invalid {key}')
        return self
class ConfirmReport(BaseModel):
    fields:ReportFields
    mode:Literal['sample','manual','extracted']
    reviewed:Literal[True]
    upload_id:str|None=Field(None,pattern=r'^[a-f0-9-]{36}$')
