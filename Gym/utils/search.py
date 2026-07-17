"""
Reusable, index-friendly search utility for list views across EnterGYM.

Rule: search ONLY the field the user selected — never OR multiple fields
together. Exact match for identifier fields (phone, unique_id), icontains
only for free-text name search.

Usage (model has fullname/phone/unique_id directly, e.g. Enrollment):
    from Gym.utils.search import apply_search, get_search_context
    qs = apply_search(qs, search_by, search)

Usage (fields live on a related object, e.g. Attendence -> user -> enrollment):
    qs = apply_related_search(qs, search_by, search,
                               relation_prefix="user__enrollment", gym=gym)
"""

# Canonical "Search By" choices — reused by every template's dropdown.
SEARCH_BY_CHOICES = [
    ("name", "Name"),
    ("phone", "Phone Number"),
    ("unique_id", "Unique ID"),
]

# Default lookups for models exposing these fields directly (Enrollment, etc.)
DEFAULT_FIELD_MAP = {
    "name": "fullname__icontains",
    "phone": "phone",
    "unique_id": "unique_id",
}


def apply_search(queryset, search_by, search, field_map=None):
    """
    Filter `queryset` on a single field chosen by the user.

    - search_by: "name" | "phone" | "unique_id" (defaults to "name" if unknown)
    - search: raw search string from the request
    - field_map: optional override dict {search_by: orm_lookup}
    """
    search = (search or "").strip()
    if not search:
        return queryset

    fields = field_map or DEFAULT_FIELD_MAP
    lookup = fields.get(search_by)
    if not lookup:
        return queryset

    return queryset.filter(**{lookup: search})


def apply_related_search(queryset, search_by, search, relation_prefix, gym=None, gym_field="gym"):
    """
    Same contract as apply_search, but for models where the searchable
    fields (fullname/phone/unique_id) live on a related object reached via
    `relation_prefix` (e.g. "user__enrollment").

    IMPORTANT: when `gym` is provided, the gym constraint is folded into the
    SAME .filter() call as the search field. Django only guarantees a single
    join across a multi-valued relation (like Enrollment, which can have
    several rows per User across gyms) when both conditions are in one
    filter() call — splitting them into two .filter() calls would let the
    gym match one related row and the search field match a *different*
    related row for the same user.
    """
    search = (search or "").strip()
    if not search:
        return queryset

    fields = {
        "name": f"{relation_prefix}__fullname__icontains",
        "phone": f"{relation_prefix}__phone",
        "unique_id": f"{relation_prefix}__unique_id",
    }
    lookup = fields.get(search_by)
    if lookup is None:
        return queryset
    filters = {lookup: search}
    if gym is not None:
        filters[f"{relation_prefix}__{gym_field}"] = gym

    return queryset.filter(**filters)


def get_search_context(request):
    """
    Consistent GET-param contract for every view + template.
    Include this dict's contents in every list view's render context so
    the template can render the dropdown/input and preserve state.
    """
    return {
        "search_by": request.GET.get("search_by", "name"),
        "search": request.GET.get("search", ""),
        "search_by_choices": SEARCH_BY_CHOICES,
    }