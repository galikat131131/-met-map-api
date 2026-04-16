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
    polygon: Optional[dict] = Field(
        None,
        description="GeoJSON Polygon/MultiPolygon outlining the gallery. Use for map rendering.",
    )


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
    method: str = Field(
        ...,
        description="`polygon` = user is inside the gallery boundary. `nearest-centroid` = fallback (user was outside all polygons, e.g. in a corridor).",
    )
    note: str = "If method is 'polygon' the point is inside the gallery; 'nearest-centroid' is a best-guess fallback."


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


class TransitionRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=64, description="Anonymous per-visit UUID from the client's sessionStorage.")
    from_gallery: int = Field(..., description="Gallery number the user just left.")
    to_gallery: int = Field(..., description="Gallery number the user just entered.")
    floor_from: Optional[str] = None
    floor_to: Optional[str] = None
    client_ts: Optional[int] = Field(None, description="Client-side ms epoch when the transition was observed.")
    locate_method: Optional[str] = Field(None, description='"polygon" or "nearest-centroid" from the /locate call that resolved the new gallery.')


class EdgeCount(BaseModel):
    from_gallery: int
    to_gallery: int
    count: int


class GalleryVisitCount(BaseModel):
    gallery: int
    visits: int


class QuietRouteStop(BaseModel):
    gallery: Gallery
    visits: int = Field(..., description="Recorded visit count from transitions. 0 means no one has walked there in the tracked dataset yet.")
    popularity_rank: int = Field(..., description="1 = most-visited gallery in the wing; higher = quieter.")


class QuietRouteResponse(BaseModel):
    from_gallery: Gallery
    stops: List[QuietRouteStop] = Field(
        ...,
        description="Ordered walk through the adjacency graph, biased toward under-visited rooms. Does NOT include the starting gallery.",
    )
    total_distance_m: float = Field(..., description="Straight-line sum of centroid-to-centroid distances. A proxy for walking distance.")
    avg_visits_per_stop: float = Field(..., description="Average visit count across the recommended stops. Compare to the wing-wide average shown in `baseline_avg_visits`.")
    baseline_avg_visits: float = Field(..., description="Average visit count across all galleries that have been visited at least once. Lets the client say 'these stops see ~X% less traffic than average'.")


class Artwork(BaseModel):
    object_id: int
    gallery_number: int = Field(..., description="Met gallery number, 200–253 for Asian Art.")
    title: str
    artist: str = ""
    artist_bio: str = ""
    culture: str = ""
    period: str = ""
    dynasty: str = ""
    reign: str = ""
    date: str = ""
    date_begin: Optional[int] = None
    date_end: Optional[int] = None
    medium: str = ""
    classification: str = ""
    object_name: str = ""
    credit_line: str = ""
    accession_number: str = ""
    image_small: Optional[str] = None
    image: Optional[str] = None
    is_highlight: bool = False
    is_public_domain: bool = False
    object_url: Optional[str] = None


class HighlightRouteRequest(BaseModel):
    from_gallery: int = Field(..., description="Starting gallery number.")
    object_ids: Optional[List[int]] = Field(
        None,
        description="Optional list of Met objectIDs to visit. If omitted, uses all Asian Art highlights on view.",
    )
    limit: int = Field(10, ge=1, le=30, description="Cap on number of stops.")


class HighlightRouteStop(BaseModel):
    gallery: int
    artwork: Artwork
    note: str = ""


class HighlightRouteResponse(BaseModel):
    """Shaped to match the PWA's tour data structure so the client can reuse its tour renderer."""
    id: str = "highlights"
    title: str = "Must-see route"
    summary: str = ""
    duration_min: Optional[int] = None
    stops: List[HighlightRouteStop]
    total_distance_m: float
