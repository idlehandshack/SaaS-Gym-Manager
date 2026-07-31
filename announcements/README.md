# EnterGYM Announcement & Notice Management System

A new Django app, `announcements`, delivering popup notices, an Announcement
Center, website banners, push notifications, and analytics — all scoped per
gym in the existing multi-tenant architecture.

## What's included

```
announcements/
  models.py                 Announcement, AnnouncementRead
  admin.py                  Django admin, tenant-scoped for GymOwner
  forms.py                  AnnouncementForm (Bootstrap 5)
  permissions.py            Role gates (SuperAdmin / GymOwner / Trainer / Receptionist / Member)
  views.py                  Owner CRUD + analytics + member Announcement Center
  api.py                    JSON endpoints (home popup/banner, list, read, dismiss, unread-count)
  utils.py                  Push fan-out — reuses Shop.notifications + notifications.utils, no new channel code
  urls.py                   All routes for this app
  apps.py
  tests.py                  Tenant isolation, permissions, popup eligibility, read tracking, push mocking
  management/commands/publish_scheduled_announcements.py   Cron: push on schedule-elapse
  templates/announcements/
    owner/list.html, form.html, analytics.html, archive.html
    member/center.html
    _popup_and_banner.html  Include partial for home.html
  static/announcements/css/announcements.css
  static/announcements/js/announcements.js
```

## 1. Install the app

**settings.py**
```python
INSTALLED_APPS = [
    ...
    "announcements",
]
```

## 2. Wire URLs

**urls.py** (project root, alongside the existing `AuthFit`/`Gym` includes)
```python
urlpatterns = [
    ...
    path('', include('announcements.urls')),
]
```

## 3. Migrations

```bash
python manage.py makemigrations announcements
python manage.py migrate
```

No changes to any existing model or migration are required — `Announcement`
and `AnnouncementRead` only add FKs *to* `Gym`, `User`, `MembershipPlan`,
and `AuthFit.Trainer`; nothing existing is touched.

## 4. Templates you need to adjust

- `owner/base_owner.html` — the two owner templates (`list.html`,
  `form.html`, `analytics.html`, `archive.html`) `{% extends %}` this. If
  your project's actual base template has a different name/path, update the
  four `{% extends %}` lines in `templates/announcements/owner/*.html`.
- `base.html` — `member/center.html` extends this for member-facing pages.
  Adjust if your member base template is named differently.

## 5. Sidebar menu (owner_dashboard)

Add a **Communication** section pointing at the Announcements list, per the
spec's `owner_dashboard.sidebar`:

```html
<li class="dropdown">
  <a href="#">Communication ▼</a>
  <ul class="dropdown-menu">
    <li><a href="{% url 'announcement_list' %}">Announcements</a></li>
  </ul>
</li>
```

This mirrors the existing `home.html` dropdown pattern (`Analytics ▼`,
`Members ▼`, `Operations ▼`) — drop the block in next to those.

## 6. Popup + banner on the member-facing home page

In `home.html`, right where `push_subscribe.js` / `sw_register.js` are
conditionally included for authenticated users, add:

```django
{% if enrolled %}
  {% include 'announcements/_popup_and_banner.html' %}
{% endif %}
```

This pulls in `announcements.css` + `announcements.js`, which calls
`GET /api/announcements/home/` on `DOMContentLoaded` and renders the
highest-priority eligible popup plus the scrolling banner. Because the
Android app is a WebView wrapper around this same site (see
`app/index.tsx`), **no separate native implementation is needed** — the
popup and banner work identically inside the app.

## 7. Announcement Center link

Add a nav item pointing at `{% url 'announcement_center' %}` for members
(e.g. next to "Attendance" in `home.html`'s member nav block).

## 8. Push notifications — how it reuses the existing system

`announcements/utils.py` calls:
- `Shop.notifications.send_push_to_tokens` (FCM) — the same helper used by
  `notify_staff_new_order`, `notify_staff_new_enrollment`, etc.
- `notifications.utils.send_web_push` — the same helper used by
  `AuthFit/notifications.py`'s expiry reminders.

No new Firebase/VAPID/channel code was written. A new Android notification
channel `entergym_announcements` is referenced (mirroring the existing
`entergym_orders` / `entergym_expiry` channels) — register it in
`useOwnerNotifications.ts` / the member device hook's `ensureAndroidChannels()`
equivalent:

```ts
await Notifications.setNotificationChannelAsync('entergym_announcements', {
  name: 'Announcements',
  importance: Notifications.AndroidImportance.HIGH,
  vibrationPattern: [0, 250, 250, 250],
  lightColor: '#ff4d00',
});
```

Push fires:
- **Immediately** when an owner creates/edits a live, `send_push=True`
  announcement (`views.announcement_create` / `announcement_edit`).
- **On schedule** for a future-dated announcement, via the management
  command below.
- **Manually** via the "Send Push Now" button on the owner list page.

## 9. Scheduled publishing (cron)

Add alongside whatever already triggers `send_expiry_reminders()`:

```bash
*/5 * * * * cd /path/to/project && python manage.py publish_scheduled_announcements
```

This only sends push for announcements that just crossed `publish_at` and
haven't been pushed yet — it does **not** need to "expire" anything, because
every visibility query (`is_live`, the API's `_live_visible_queryset`, the
owner list filters) already excludes expired rows at query time. Expired
announcements simply stop appearing everywhere the next time they're
queried — no batch job required, no risk of a missed cron run leaving stale
content visible.

## 10. Permissions summary

| Role | Access |
|---|---|
| Super Admin | Every gym, every announcement, full CRUD |
| Gym Owner | Own gym only, full CRUD |
| Trainer | `403 Forbidden` on every announcement route |
| Receptionist | Read-only list/analytics **only if** `StaffPermission.can_manage_notifications=True`; no create/edit/delete (`announcement_write_required` still blocks them) |
| Member | Center + popup + push + mark-read/dismiss only |

`can_send_expiry_notifications` / `can_manage_notifications` already exist
in `Gym.models.PERMISSION_DEFINITIONS` — this app reuses
`can_manage_notifications` rather than adding a new permission flag, so
existing receptionist permission grants continue to work unchanged.

## 11. Rich text sanitization

`description` is stored as HTML from a rich-text editor (spec:
"Description (Rich Text)"). `utils.sanitize_rich_text()` is a minimal
allow-list stripper (keeps `p/br/b/strong/i/em/u/ul/ol/li/a/h1-4/span/blockquote`,
drops everything else including `<script>`/`<style>`). **Recommended**: call
`sanitize_rich_text()` in `AnnouncementForm.clean_description()` before this
ships to production, and/or swap in `bleach`/`nh3` if the project adds that
dependency later — the hook point is already isolated in `utils.py`.

## 12. Testing

```bash
python manage.py test announcements
```

Covers: cross-gym isolation (owner A can't see/edit gym B's announcements —
404, not leakage), trainer 403, popup eligibility (low priority never pops,
expired/future excluded, specific-member targeting), read-tracking
timestamps, and push fan-out using the *real* `send_announcement_push` path
with `Shop.notifications.send_push_to_tokens` / `notifications.utils.send_web_push`
mocked (no live Firebase/VAPID credentials needed in CI).

Still recommended before go-live (needs a running deployment, not covered
by unit tests): manual QA of "Verify push notifications are delivered" and
"Verify website and mobile behavior" from the spec's `testing_requirements`,
against real devices/tokens.

## Design notes / deviations

- **No DRF dependency was introduced.** The codebase's existing APIs
  (`mark_attendance_api`, `get_users`, etc.) are plain `JsonResponse` views,
  so `api.py` follows that convention rather than adding
  `djangorestframework` as a new dependency.
- **`GymManager`/tenant isolation** follows the same pattern as `Contact`,
  `Trainer`, `MembershipPlan`, `UserDevice` in `AuthFit/models.py` — `gym`
  FK + `objects = GymManager()`.
- **Cache invalidation** mirrors the existing `GymNotification`/
  `MembershipPlan` post_save/post_delete signal pattern already in
  `AuthFit/models.py`.
- **"Show only active announcements" / "respect publish/expiry date"**
  are enforced identically in three places (owner analytics excluded on
  purpose — owners should still see expired items in Archive): member
  Announcement Center (`views.announcement_center`), the home popup/banner
  API (`api._live_visible_queryset`), and the REST list API (`api.api_list`).
  If you add a fourth surface, reuse `api._live_visible_queryset` rather
  than re-deriving the filter, to avoid the three-way logic drifting apart.
