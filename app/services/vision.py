"""Photo -> structured food analysis with Gemini.

Providers (VISION_PROVIDER, auto-detected when unset):
  vertex  Gemini on Vertex AI (Cloud Run: the service account, no key; locally: gcloud ADC)
  gemini  GEMINI_API_KEY (Google AI Studio)
  demo    nothing configured: fixed, clearly-labelled sample result
"""
import json
from typing import Literal

from pydantic import BaseModel, Field

from .. import config


class FoodAnalysis(BaseModel):
    is_food: bool = Field(description="False if the photo does not show cooked/ready-to-eat food")
    dish_name: str = Field(description="Most likely dish name, e.g. 'Vegetable Biryani'")
    items: list[str] = Field(description="Distinct food items visible")
    cuisine: str
    diet: Literal["veg", "egg", "non_veg"]
    high_risk: bool = Field(description="True if it contains meat, fish, egg, dairy/paneer/curd, cooked rice or "
                                        "gravies that spoil fast")
    risk_factors: list[str]
    estimated_servings: int = Field(description="Number of adult meal portions (~350 g each)")
    estimated_weight_kg: float
    servings_reasoning: str = Field(description="One sentence: container size / depth used to estimate servings")
    safe_hours_room_temp: float = Field(description="Hours this food stays safe at Indian room temperature "
                                                    "(28-35 C) counted from cooking, conservative")
    visible_spoilage: bool
    observations: str = Field(description="Freshness, packaging, anything a food-safety officer would note")
    confidence: float = Field(description="0-1 confidence in the dish identification")


PROMPT = """You are a food-safety assistant for a surplus-food rescue service in Bengaluru, India.
A restaurant has photographed leftover cooked food it wants to donate to an NGO tonight.

Analyse the photo and return JSON only:
- Identify the dish(es) as an Indian kitchen would name them.
- Decide veg / egg / non_veg. If any meat, fish or egg is visible or typical for the dish, do not say veg.
- Estimate servings from container size and fill depth (a standard hotel pan ~ 12-15 servings,
  a large handi/degchi ~ 25-40, a single parcel box ~ 1-2). Assume ~350 g per adult serving.
- safe_hours_room_temp: be conservative. Typical guidance: dry snacks/rotis 6-8 h, dal/sabzi 4-5 h,
  cooked rice/biryani 3-4 h, dairy/paneer/curd dishes 3 h, meat/fish/egg 2-3 h.
- Flag visible spoilage (mould, separation, sour look) and anything unsafe.
If the image is not food, set is_food=false and fill other fields with neutral values."""


_CLIENT = None


def _client():
    # one long-lived client: a throwaway one gets closed (garbage-collected) before the request is sent
    global _CLIENT
    if _CLIENT is None:
        from google import genai
        if provider() == "vertex":
            _CLIENT = genai.Client(vertexai=True, project=config.GCP_PROJECT or None, location=config.GCP_LOCATION)
        else:
            _CLIENT = genai.Client(api_key=config.GEMINI_API_KEY)
    return _CLIENT


def provider():
    if config.VISION_PROVIDER:
        return config.VISION_PROVIDER
    if config.USE_VERTEX:
        return "vertex"
    if config.GEMINI_API_KEY:
        return "gemini"
    return "demo"


def gemini_enabled():
    return provider() != "demo"


def model_label():
    return f"{provider()}:{config.GEMINI_MODEL}"


def analyse(image_bytes: bytes, mime_type: str, donor_hint: str = "") -> dict:
    p = provider()
    if p == "demo":
        return _demo_result(donor_hint)
    prompt = PROMPT + (f"\nContext: the donor is {donor_hint}." if donor_hint else "")
    from google.genai import types
    resp = _client().models.generate_content(
        model=config.GEMINI_MODEL,
        contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type), prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=FoodAnalysis,
                                           temperature=0.2),
    )
    result = resp.parsed.model_dump() if resp.parsed else json.loads(resp.text)
    result["source"] = model_label()
    return result


def _demo_result(donor_hint):
    """Used only when no Gemini credentials are configured, so the rest of the flow can be exercised."""
    return FoodAnalysis(
        is_food=True, dish_name="Vegetable Biryani (demo)", items=["vegetable biryani", "raita"],
        cuisine="South Indian", diet="veg", high_risk=True, risk_factors=["cooked rice", "curd (raita)"],
        estimated_servings=25, estimated_weight_kg=8.5,
        servings_reasoning="Demo mode: no vision provider configured, fixed sample values.",
        safe_hours_room_temp=4.0, visible_spoilage=False, observations="Demo mode - configure Vertex AI or GEMINI_API_KEY.",
        confidence=0.0,
    ).model_dump() | {"source": "demo (no Gemini key)"}


def generate_text(prompt: str, max_tokens: int = 2000) -> str:
    """Plain text generation with the same provider (used for the forecast dispatch briefing)."""
    p = provider()
    if p == "demo":
        raise RuntimeError("No Gemini provider configured")
    from google.genai import types
    resp = _client().models.generate_content(model=config.GEMINI_MODEL, contents=prompt,
                                             config=types.GenerateContentConfig(temperature=0.3,
                                                                                max_output_tokens=max_tokens))
    return resp.text.strip()
