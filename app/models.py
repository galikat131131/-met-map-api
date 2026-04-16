from typing import List, Optional

from pydantic import BaseModel, Field


class Floor(BaseModel):
    id: int
    short_name: str
    name: str
    level: float
    default: bool = False


class Gallery(BaseModel):
    id: str
    number: int
    name: str
    summary: str
    description: str
    lat: float
    lon: float
    floor: str
    floor_id: int
    image_url: Optional[str] = None
    is_closed: bool = False
    distance_m: Optional[float] = Field(None, description="Populated by /nearby and /locate responses.")


class Amenity(BaseModel):
    id: str
    type: str
    name: str
    description: str = ""
    lat: float
    lon: float
    floor: str
    floor_id: int
    is_closed: bool = False
    distance_m: Optional[float] = None


class LocateResponse(BaseModel):
    gallery: Gallery
    note: str = "Nearest-centroid estimate on the given floor. Accuracy depends on GPS and gallery size."


class RouteRequest(BaseModel):
    from_gallery: int = Field(..., description="Starting gallery number (e.g. 207)")
    to_gallery: int = Field(..., description="Destination gallery number (e.g. 219)")


class RouteStep(BaseModel):
    instruction: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    floor: Optional[str] = None


class RouteResponse(BaseModel):
    from_gallery: Gallery
    to_gallery: Gallery
    distance_m: float
    steps: List[RouteStep]
    upstream: Optional[dict] = Field(None, description="Raw Living Map response if proxy succeeded, else null.")
