"""Geospatial ACECQA competitor matching for backend market audit.

The frontend map already uses Supabase RPC `get_nearby_centres`. This module
lets the backend compute the same radius-based supply while keeping postcode
fallbacks available for conservative scoring migration.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from typing import Any, Optional


VALID_SOURCE = {"geospatial_supabase", "postcode_fallback", "unavailable"}


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _as_int(value: Any) -> Optional[int]:
    parsed = _as_float(value)
    if parsed is None:
        return None
    return int(parsed) if parsed >= 0 else None


def _normalize(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _similarity(a: Any, b: Any) -> float:
    left = _normalize(a)
    right = _normalize(b)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _get(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row.get(key) is not None:
            return row.get(key)
    return None


def _execute(query: Any) -> list[dict[str, Any]]:
    result = query.execute()
    return list(getattr(result, "data", None) or [])


def _map_centre(row: dict[str, Any]) -> dict[str, Any]:
    distance_m = _as_float(_get(row, "distance_m", "distance"))
    street_address = _clean_text(_get(row, "address", "service_address", "street_address"))
    suburb = _clean_text(_get(row, "suburb", "town", "locality"))
    postcode = _clean_text(_get(row, "postcode", "post_code"))
    return {
        "name": _clean_text(_get(row, "service_name", "name")),
        "address": street_address,
        "suburb": suburb,
        "postcode": postcode,
        "lat": _as_float(_get(row, "lat", "latitude")),
        "lng": _as_float(_get(row, "lng", "longitude")),
        "distance_km": round(distance_m / 1000, 3) if distance_m is not None else _as_float(_get(row, "distance_km")),
        "licensed_places": _as_int(_get(row, "licensed_places", "places", "approved_places")),
        "provider_name": _clean_text(_get(row, "provider_name", "provider")),
        "service_approval_number": _clean_text(_get(row, "service_approval_number", "service_id", "approval_number")),
        "nqs_rating": _clean_text(_get(row, "nqs_rating")),
    }


def _subject_identity(
    *,
    subject_name: Optional[str],
    subject_address: Optional[str],
    service_approval_number: Optional[str],
    subject_licensed_places: Optional[int],
) -> dict[str, Any]:
    return {
        "name": _clean_text(subject_name),
        "address": _clean_text(subject_address),
        "service_approval_number": _clean_text(service_approval_number),
        "licensed_places": subject_licensed_places,
    }


def _should_exclude_subject(centre: dict[str, Any], subject: dict[str, Any]) -> tuple[bool, Optional[str], bool]:
    approval = _normalize(subject.get("service_approval_number"))
    centre_approval = _normalize(centre.get("service_approval_number"))
    if approval and centre_approval and approval == centre_approval:
        return True, "service_approval_number", False

    address = _normalize(subject.get("address"))
    centre_address = _normalize(centre.get("address"))
    if address and centre_address and address == centre_address:
        return True, "exact_address", False

    name_score = _similarity(subject.get("name"), centre.get("name"))
    address_score = _similarity(subject.get("address"), centre.get("address"))
    postcode_match = bool(subject.get("postcode") and subject.get("postcode") == centre.get("postcode"))
    if name_score >= 0.86 and (address_score >= 0.72 or postcode_match):
        return True, "name_address_similarity", False

    distance = centre.get("distance_km")
    same_places = (
        subject.get("licensed_places") is not None
        and centre.get("licensed_places") is not None
        and subject.get("licensed_places") == centre.get("licensed_places")
    )
    if isinstance(distance, (int, float)) and distance <= 0.05:
        if name_score >= 0.65 or address_score >= 0.55 or same_places:
            return True, "distance_similarity", False
        return False, None, True

    return False, None, False


def _filter_subject(centres: list[dict[str, Any]], subject: dict[str, Any]) -> tuple[list[dict[str, Any]], Optional[str], list[str]]:
    filtered: list[dict[str, Any]] = []
    methods: list[str] = []
    warnings: list[str] = []
    for centre in centres:
        exclude, method, ambiguous = _should_exclude_subject(centre, subject)
        if exclude:
            if method:
                methods.append(method)
            continue
        if ambiguous:
            warnings.append(
                f"Nearby centre '{centre.get('name') or centre.get('address') or 'unknown'}' is within 50m but was not excluded because identity is ambiguous."
            )
        filtered.append(centre)
    return filtered, methods[0] if methods else None, warnings


def _query_first_match(supabase: Any, *, service_approval_number: Optional[str], subject_name: Optional[str], subject_address: Optional[str], postcode: Optional[str]) -> tuple[Optional[dict[str, Any]], Optional[str], list[str]]:
    warnings: list[str] = []
    select_fields = "service_id,service_approval_number,service_name,name,address,service_address,suburb,postcode,lat,lng,latitude,longitude,licensed_places,provider_name,provider,nqs_rating"

    if service_approval_number:
        for field in ("service_approval_number", "service_id"):
            try:
                rows = _execute(
                    supabase.from_("acecqa_centres")
                    .select(select_fields)
                    .eq(field, service_approval_number)
                    .limit(1)
                )
                if rows:
                    return _map_centre(rows[0]), "supabase_match", warnings
            except Exception as exc:
                warnings.append(f"Supabase service approval lookup failed on {field}: {exc}")

    if postcode and (subject_name or subject_address):
        try:
            rows = _execute(
                supabase.from_("acecqa_centres")
                .select(select_fields)
                .eq("postcode", postcode)
                .limit(50)
            )
            candidates = [_map_centre(row) for row in rows]
            best: Optional[dict[str, Any]] = None
            best_score = 0.0
            for candidate in candidates:
                score = max(
                    _similarity(subject_name, candidate.get("name")),
                    _similarity(subject_address, candidate.get("address")),
                )
                if score > best_score:
                    best_score = score
                    best = candidate
            if best and best_score >= 0.82 and best.get("lat") is not None and best.get("lng") is not None:
                return best, "supabase_match", warnings
        except Exception as exc:
            warnings.append(f"Supabase postcode match lookup failed: {exc}")

    return None, None, warnings


def _google_geocode(address: str) -> tuple[Optional[dict[str, float]], list[str]]:
    warnings: list[str] = []
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key or not address:
        return None, warnings
    url = "https://maps.googleapis.com/maps/api/geocode/json?" + urllib.parse.urlencode({
        "address": f"{address}, Australia",
        "key": api_key,
    })
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        warnings.append(f"Google geocode failed: {exc}")
        return None, warnings
    if data.get("status") == "OK" and data.get("results"):
        loc = data["results"][0]["geometry"]["location"]
        return {"lat": float(loc["lat"]), "lng": float(loc["lng"])}, warnings
    warnings.append(f"Google geocode returned {data.get('status') or 'no result'}.")
    return None, warnings


def _postcode_fallback(supabase: Any, postcode: Optional[str], subject_licensed_places: Optional[int], warnings: list[str]) -> dict[str, Any]:
    if not postcode:
        return {
            "centres": [],
            "total_licensed_places": subject_licensed_places or 0,
            "competitor_count": 0,
            "source": "unavailable",
            "confidence": "low",
            "radius_km": None,
            "target_lat": None,
            "target_lng": None,
            "target_geocode_method": "none",
            "exclusion_method": None,
            "warnings": [*warnings, "No postcode was available for competitor fallback."],
        }
    try:
        rows = _execute(
            supabase.from_("acecqa_centres")
            .select("service_id,service_approval_number,service_name,name,address,service_address,suburb,postcode,lat,lng,latitude,longitude,licensed_places,provider_name,provider,nqs_rating")
            .eq("postcode", postcode)
        )
    except Exception as exc:
        return {
            "centres": [],
            "total_licensed_places": subject_licensed_places or 0,
            "competitor_count": 0,
            "source": "unavailable",
            "confidence": "low",
            "radius_km": None,
            "target_lat": None,
            "target_lng": None,
            "target_geocode_method": "none",
            "exclusion_method": None,
            "warnings": [*warnings, f"Postcode competitor fallback failed: {exc}"],
        }
    centres = [_map_centre(row) for row in rows]
    total_places = sum(c.get("licensed_places") or 0 for c in centres) + (subject_licensed_places or 0)
    return {
        "centres": centres,
        "total_licensed_places": int(total_places),
        "competitor_count": max(len(centres) - 1, 0) if centres else 0,
        "source": "postcode_fallback",
        "confidence": "medium" if centres else "low",
        "radius_km": None,
        "target_lat": None,
        "target_lng": None,
        "target_geocode_method": "postcode_fallback",
        "exclusion_method": None,
        "warnings": [*warnings, "Used postcode fallback because geospatial competitor matching was unavailable."],
    }


def get_nearby_competitors(
    supabase: Any,
    address: Optional[str] = None,
    suburb: Optional[str] = None,
    state: Optional[str] = None,
    postcode: Optional[str] = None,
    radius_km: Optional[float] = None,
    subject_name: Optional[str] = None,
    subject_address: Optional[str] = None,
    service_approval_number: Optional[str] = None,
    subject_licensed_places: Optional[int] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    radius = radius_km or 3.0
    postcode = _clean_text(postcode)
    subject_address = _clean_text(subject_address or address)
    full_address = ", ".join(part for part in [subject_address or address, suburb, state, postcode] if _clean_text(part))
    target_lat = _as_float(lat)
    target_lng = _as_float(lng)
    geocode_method = "none"

    if target_lat is not None and target_lng is not None:
        geocode_method = "provided_coordinates"
    else:
        match, method, match_warnings = _query_first_match(
            supabase,
            service_approval_number=_clean_text(service_approval_number),
            subject_name=_clean_text(subject_name),
            subject_address=subject_address,
            postcode=postcode,
        )
        warnings.extend(match_warnings)
        if match and match.get("lat") is not None and match.get("lng") is not None:
            target_lat = match.get("lat")
            target_lng = match.get("lng")
            geocode_method = method or "supabase_match"

    if target_lat is None or target_lng is None:
        coords, geocode_warnings = _google_geocode(full_address)
        warnings.extend(geocode_warnings)
        if coords:
            target_lat = coords["lat"]
            target_lng = coords["lng"]
            geocode_method = "google"

    if target_lat is None or target_lng is None:
        return _postcode_fallback(supabase, postcode, subject_licensed_places, warnings)

    try:
        result = supabase.rpc("get_nearby_centres", {
            "target_lat": target_lat,
            "target_lng": target_lng,
            "radius_m": int(radius * 1000),
        }).execute()
        rows = list(getattr(result, "data", None) or [])
    except Exception as exc:
        return _postcode_fallback(supabase, postcode, subject_licensed_places, [*warnings, f"Supabase geospatial RPC failed: {exc}"])

    centres = [_map_centre(row) for row in rows]
    subject = _subject_identity(
        subject_name=subject_name,
        subject_address=subject_address,
        service_approval_number=service_approval_number,
        subject_licensed_places=subject_licensed_places,
    )
    subject["postcode"] = postcode
    filtered, exclusion_method, exclusion_warnings = _filter_subject(centres, subject)
    warnings.extend(exclusion_warnings)
    total_places = sum(c.get("licensed_places") or 0 for c in filtered) + (subject_licensed_places or 0)
    confidence = "high" if geocode_method in {"provided_coordinates", "supabase_match", "google"} else "medium"

    return {
        "centres": filtered,
        "total_licensed_places": int(total_places),
        "competitor_count": len(filtered),
        "source": "geospatial_supabase",
        "confidence": confidence,
        "radius_km": radius,
        "target_lat": target_lat,
        "target_lng": target_lng,
        "target_geocode_method": geocode_method,
        "exclusion_method": exclusion_method,
        "warnings": warnings,
    }


def material_supply_difference(
    *,
    geospatial: dict[str, Any],
    postcode_supply: dict[str, Any],
    geospatial_edr: Optional[float],
    postcode_edr: Optional[float],
    geospatial_zone: Optional[str],
    postcode_zone: Optional[str],
) -> bool:
    geo_count = geospatial.get("competitor_count") or 0
    pc_count = postcode_supply.get("competitor_count") or 0
    geo_places = geospatial.get("total_licensed_places") or 0
    pc_places = postcode_supply.get("total_licensed_places") or 0

    count_diff = abs(geo_count - pc_count)
    if count_diff >= 3 or (max(geo_count, pc_count) > 0 and count_diff / max(geo_count, pc_count) >= 0.40):
        return True

    places_diff = abs(geo_places - pc_places)
    if places_diff >= 120 or (max(geo_places, pc_places) > 0 and places_diff / max(geo_places, pc_places) >= 0.30):
        return True

    if geospatial_edr is not None and postcode_edr is not None:
        if abs(geospatial_edr - postcode_edr) >= 0.20:
            return True
    if geospatial_zone and postcode_zone and geospatial_zone != postcode_zone:
        return True
    return False
